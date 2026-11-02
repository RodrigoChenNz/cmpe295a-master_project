#!/usr/bin/env python3
"""Performance benchmarks, for Chapter 7 of the project report.

    make demo        # or make up, once a manager account exists
    uv run --with aiomqtt --with asyncpg --with httpx --with websockets \\
        tools/bench.py all --out bench-results.json

Hardware, with a label set up and assigned to a product (edge/*/README.md):

    # e-ink over LoRa: needs `make lora` running in another terminal
    uv run --with aiomqtt --with asyncpg --with httpx --with websockets \\
        tools/bench.py eink --product SKU-019 --label SHLF-0225 --runs 10

    # LCD over Wi-Fi: the board on USB, with Thonny and `pio device monitor` closed
    uv run --with aiomqtt --with asyncpg --with httpx --with websockets --with pyserial \\
        tools/bench.py lcd --product SKU-001 --port /dev/cu.usbserial-0001 --runs 10

What each mode measures. Every time is taken on this machine's clock, inside
this one process, so no two clocks are ever compared:

    broker     MQTT through Mosquitto alone, QoS 1: one client publishes, another
               hears it. The floor every other number sits on.
    gate       proposal published -> its approved price heard on retail/price/,
               for a proposal inside the policy (auto-approved), one at a time.
               Also when the console's WebSocket hears about it.
    review     proposal published -> in the review queue (WebSocket); then a
               manager's Approve over REST -> price heard on the broker.
    bulk       one bulk-approve request for 10, 50 and 200 queued proposals.
    burst      N proposals for N products published at once -> all N prices.
    sustained  proposals at a fixed rate for a while: latency against load, and
               the containers' CPU while it runs.
    rest       response times of the console's read endpoints, and sign-in.
    eink       a manager-set price -> the e-ink label reports it on the glass
               (retail/shelf/{label_id} with in_sync), through the real gateway.
    lcd        a manager-set price -> the LCD node prints "shown:" on its serial
               console.

The software modes run on throwaway products SKU-90001 and up (the contract's
ID pattern rules out a BENCH- prefix). They are archived from the start, so the
demo's fake publisher never picks them and the console's catalogue does not
list them. The gate does not read archived_at, so their proposals take exactly
the path a real one does. `cleanup` deletes every row they produced and clears
their retained prices; `all` runs it at the end, and it is safe to run again.

The hardware modes use a real product and leave its price as they found it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    import aiomqtt
    import asyncpg
    import httpx
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    sys.exit(
        "bench needs aiomqtt, asyncpg, httpx and websockets.\n"
        "  uv run --with aiomqtt --with asyncpg --with httpx --with websockets tools/bench.py"
    )

REPO = Path(__file__).resolve().parent.parent

BENCH_FIRST = 90001
BENCH_RE = r"^SKU-9[0-9]{4}$"            # what cleanup deletes, and nothing else
AGENT_VERSION = "bench-1"

# Inside the policy every bench product gets: a 5% move against a 10% delegation.
AUTO = (Decimal("10.00"), Decimal("10.50"))
# Outside it: a 50% move, still inside the $1-$100 band, so it waits for a human.
REVIEW = (Decimal("10.00"), Decimal("15.00"))


def now_iso(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat().replace("+00:00", "Z")


def bench_ids(n: int) -> list[str]:
    return [f"SKU-{BENCH_FIRST + i}" for i in range(n)]


# ─── statistics ─────────────────────────────────────────────────────────────


def pct(xs: list[float], p: float) -> float:
    """Linear interpolation between closest ranks, the same as numpy's default."""
    s = sorted(xs)
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summary(seconds: list[float]) -> dict[str, Any]:
    """In milliseconds, rounded for the report."""
    if not seconds:
        return {"n": 0}
    ms = [s * 1000 for s in seconds]
    return {
        "n": len(ms),
        "mean": round(statistics.fmean(ms), 2),
        "std": round(statistics.stdev(ms), 2) if len(ms) > 1 else 0.0,
        "min": round(min(ms), 2),
        "p50": round(pct(ms, 0.50), 2),
        "p90": round(pct(ms, 0.90), 2),
        "p95": round(pct(ms, 0.95), 2),
        "p99": round(pct(ms, 0.99), 2),
        "max": round(max(ms), 2),
    }


def show(name: str, s: dict[str, Any]) -> None:
    if not s.get("n"):
        print(f"  {name:<34} no samples")
        return
    print(
        f"  {name:<34} n={s['n']:<5} p50={s['p50']:>8.2f}  p95={s['p95']:>8.2f}  "
        f"p99={s['p99']:>8.2f}  max={s['max']:>8.2f} ms"
    )


# ─── waiting for things to arrive ───────────────────────────────────────────


class Arrivals:
    """When each awaited thing was first heard, keyed by whatever identifies it.

    Recording every arrival, not just awaited ones, means a message that beats
    its own wait() call (it happens: the broker is fast) is not missed.
    """

    def __init__(self) -> None:
        self.seen: dict[Any, float] = {}
        self._waiting: dict[Any, asyncio.Future] = {}

    def arrive(self, key: Any, t: float) -> None:
        self.seen.setdefault(key, t)
        fut = self._waiting.pop(key, None)
        if fut is not None and not fut.done():
            fut.set_result(self.seen[key])

    async def wait(self, key: Any, timeout: float) -> float | None:
        if key in self.seen:
            return self.seen[key]
        fut = asyncio.get_running_loop().create_future()
        self._waiting[key] = fut
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._waiting.pop(key, None)
            return None

    async def wait_all(self, keys: list[Any], timeout: float) -> dict[Any, float]:
        """Wait for every key or until timeout; return what did arrive."""
        await asyncio.gather(*(self.wait(k, timeout) for k in keys))
        return {k: self.seen[k] for k in keys if k in self.seen}


class Bench:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.prices = Arrivals()      # decision_id, or (product_id, reason_tag)
        self.ws_prices = Arrivals()   # decision_id
        self.ws_queue = Arrivals()    # decision_id, needs a human
        self.rtt = Arrivals()         # sequence number on bench/rtt
        self.shelf: list[tuple[float, dict]] = []
        self.resyncs = 0
        self.results: dict[str, Any] = {}
        self._tasks: list[asyncio.Task] = []
        self.http: httpx.AsyncClient | None = None
        self.pool: asyncpg.Pool | None = None

    # ── connections ──

    async def open(self, *, ws: bool = True) -> None:
        a = self.args
        self.pool = await asyncpg.create_pool(a.db, min_size=1, max_size=4)
        # Generous in-flight limits, so a burst is limited by the system under
        # test and not by this client's own MQTT library (paho allows 20).
        self.pub = aiomqtt.Client(a.mqtt_host, a.mqtt_port, identifier=f"bench-pub-{os.getpid()}",
                                  max_inflight_messages=2000)
        self.sub = aiomqtt.Client(a.mqtt_host, a.mqtt_port, identifier=f"bench-sub-{os.getpid()}",
                                  max_inflight_messages=2000)
        await self.pub.__aenter__()
        await self.sub.__aenter__()
        for topic in ("retail/price/+", "retail/shelf/+", "bench/rtt/+"):
            await self.sub.subscribe(topic, qos=1)
        self._tasks.append(asyncio.create_task(self._mqtt_loop()))

        self.http = httpx.AsyncClient(base_url=a.api, timeout=30.0)
        login, password = credentials(a)
        r = await self.http.post("/api/auth/signin", json={"login": login, "password": password})
        if r.status_code != 200:
            sys.exit(f"sign-in as {login!r} failed ({r.status_code}): {r.text[:200]}")
        self.session = r.cookies.get("shelfiq_session")
        if ws:
            ws_url = a.api.replace("http", "ws", 1) + "/ws/stream"
            self.ws = await ws_connect(
                ws_url, additional_headers={"Cookie": f"shelfiq_session={self.session}"},
                max_size=None,
            )
            self._tasks.append(asyncio.create_task(self._ws_loop()))
        await asyncio.sleep(1.0)   # let retained messages replay before timing anything

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if getattr(self, "ws", None) is not None:
            await self.ws.close()
        if self.http is not None:
            await self.http.post("/api/auth/signout")
            await self.http.aclose()
        for c in (self.pub, self.sub):
            await c.__aexit__(None, None, None)
        if self.pool is not None:
            await self.pool.close()

    async def _mqtt_loop(self) -> None:
        async for m in self.sub.messages:
            t = time.perf_counter()
            if m.retain:
                continue   # replayed state from before this run, not news
            topic = m.topic.value
            try:
                p = json.loads(m.payload)
            except (ValueError, TypeError):
                continue
            if topic.startswith("retail/price/"):
                if p.get("decision_id"):
                    self.prices.arrive(p["decision_id"], t)
                self.prices.arrive((p.get("product_id"), p.get("reason_tag")), t)
            elif topic.startswith("retail/shelf/"):
                self.shelf.append((t, p))
            elif topic.startswith("bench/rtt/"):
                self.rtt.arrive(p["i"], t)

    async def _ws_loop(self) -> None:
        async for raw in self.ws:
            t = time.perf_counter()
            e = json.loads(raw)
            kind = e.get("type")
            if kind == "price" and e.get("decision_id"):
                self.ws_prices.arrive(e["decision_id"], t)
            elif kind == "proposal" and e.get("needs_human"):
                self.ws_queue.arrive(e["decision_id"], t)
            elif kind == "resync":
                self.resyncs += 1

    # ── bench products ──

    async def setup(self, n: int) -> None:
        ids = bench_ids(n)
        clash = await self.pool.fetchval(
            """SELECT count(*) FROM products
                WHERE product_id = ANY($1) AND policy_set_by IS DISTINCT FROM 'bench'""",
            ids,
        )
        if clash:
            sys.exit(f"{clash} of SKU-{BENCH_FIRST}.. already exist and are not bench rows; stopping")
        await self.pool.executemany(
            """INSERT INTO products
                   (product_id, name, unit_cost, min_price, max_price, auto_approve_pct,
                    policy_set_by, policy_set_at, archived_at)
               VALUES ($1, $2, 5.00, 1.00, 100.00, 10, 'bench', now(), now())
               ON CONFLICT (product_id) DO NOTHING""",
            [(pid, f"Benchmark item {i + 1:03d}") for i, pid in enumerate(ids)],
        )
        print(f"setup: {n} bench products ready ({ids[0]} to {ids[-1]})")

    def proposal(self, product_id: str, current: Decimal, proposed: Decimal) -> tuple[str, bytes]:
        did = str(uuid.uuid4())
        body = {
            "product_id": product_id,
            "proposed_price": float(proposed),
            "current_price": float(current),
            "currency": "USD",
            "decision_id": did,
            "reason_tag": "Benchmark",
            "agent_version": AGENT_VERSION,
            "expires_at": now_iso(timedelta(hours=1)),
            "ts": now_iso(),
        }
        return did, json.dumps(body, separators=(",", ":")).encode()

    # ── broker ──

    async def broker(self) -> None:
        n, gap = self.args.broker_runs, 0.01
        # About the size of a price message, so the bytes on the wire are comparable.
        pad = "x" * 240
        trip, acked = [], []
        for i in range(n):
            body = json.dumps({"i": i, "pad": pad}).encode()
            t0 = time.perf_counter()
            await self.pub.publish(f"bench/rtt/{i % 10}", body, qos=1)
            t1 = time.perf_counter()
            t2 = await self.rtt.wait(i, 5.0)
            if t2 is not None:
                trip.append(t2 - t0)
                acked.append(t1 - t0)
            await asyncio.sleep(gap)
        self.results["broker"] = {
            "one_pass": summary(trip),
            "puback": summary(acked),
            "lost": n - len(trip),
            "payload_bytes": len(json.dumps({"i": 0, "pad": pad})),
        }
        print("broker")
        show("publish -> heard by subscriber", self.results["broker"]["one_pass"])
        show("publish -> PUBACK", self.results["broker"]["puback"])

    # ── gate, auto-approved ──

    async def gate(self) -> None:
        n, gap = self.args.gate_runs, self.args.gap
        ids = bench_ids(50)
        to_price, to_ws, dids = [], [], []
        current, proposed = AUTO
        for i in range(n):
            did, body = self.proposal(ids[i % len(ids)], current, proposed)
            t0 = time.perf_counter()
            await self.pub.publish(f"retail/proposal/{ids[i % len(ids)]}", body, qos=1)
            tp, tw = await asyncio.gather(self.prices.wait(did, 10.0),
                                          self.ws_prices.wait(did, 10.0))
            if tp is not None:
                to_price.append(tp - t0)
                dids.append(did)
            if tw is not None:
                to_ws.append(tw - t0)
            await asyncio.sleep(gap)

        # Inside the API: from the proposal row's insert to the price being
        # stamped. Both times come from the same Docker VM clock.
        inside = await self.pool.fetch(
            """SELECT extract(epoch FROM ph.ts - pr.received_at)::float8 AS s
                 FROM proposals pr JOIN price_history ph USING (decision_id)
                WHERE pr.decision_id = ANY($1::uuid[])""",
            dids,
        )
        self.results["gate"] = {
            "proposal_to_price": summary(to_price),
            "proposal_to_websocket": summary(to_ws),
            "inside_api_before_publish": summary([r["s"] for r in inside]),
            "lost": n - len(to_price),
            "gap_s": gap,
        }
        print("gate (auto-approved)")
        show("proposal -> price on broker", self.results["gate"]["proposal_to_price"])
        show("proposal -> console WebSocket", self.results["gate"]["proposal_to_websocket"])
        show("inside API, insert -> stamped", self.results["gate"]["inside_api_before_publish"])

    # ── review queue and a manager's approval ──

    async def review(self) -> None:
        n, gap = self.args.review_runs, self.args.gap
        ids = bench_ids(50)
        queued, rest, to_price, to_ws, failed = [], [], [], [], 0
        current, proposed = REVIEW
        for i in range(n):
            pid = ids[i % len(ids)]
            did, body = self.proposal(pid, current, proposed)
            t0 = time.perf_counter()
            await self.pub.publish(f"retail/proposal/{pid}", body, qos=1)
            tq = await self.ws_queue.wait(did, 10.0)
            if tq is None:
                failed += 1
                continue
            queued.append(tq - t0)

            t1 = time.perf_counter()
            r = await self.http.post(f"/api/suggestions/{did}/approve", json={})
            t2 = time.perf_counter()
            if r.status_code != 200:
                failed += 1
                continue
            rest.append(t2 - t1)
            tp, tw = await asyncio.gather(self.prices.wait(did, 10.0),
                                          self.ws_prices.wait(did, 10.0))
            if tp is not None:
                to_price.append(tp - t1)
            if tw is not None:
                to_ws.append(tw - t1)
            await asyncio.sleep(gap)

        self.results["review"] = {
            "proposal_to_queue": summary(queued),
            "approve_request": summary(rest),
            "approve_to_price": summary(to_price),
            "approve_to_websocket": summary(to_ws),
            "failed": failed,
        }
        print("review (needs a human)")
        show("proposal -> in review queue", self.results["review"]["proposal_to_queue"])
        show("POST approve, response", self.results["review"]["approve_request"])
        show("approve clicked -> price on broker", self.results["review"]["approve_to_price"])

    async def bulk(self) -> None:
        out = []
        current, proposed = REVIEW
        for size in (10, 50, 200):
            for rep in range(3):
                ids = bench_ids(size)
                dids = []
                for pid in ids:
                    did, body = self.proposal(pid, current, proposed)
                    dids.append(did)
                    await self.pub.publish(f"retail/proposal/{pid}", body, qos=1)
                await self.ws_queue.wait_all(dids, 30.0)
                t0 = time.perf_counter()
                r = await self.http.post("/api/suggestions/bulk/approve",
                                         json={"decision_ids": dids})
                t1 = time.perf_counter()
                if r.status_code != 200:
                    # Say so, rather than report a fast request that did nothing.
                    print(f"  bulk approve of {size} failed: {r.status_code} {r.text[:120]}")
                    out.append({"size": size, "rep": rep, "status": r.status_code,
                                "error": r.text[:200]})
                    # Reject what is still queued, so the next size starts clean.
                    for did in dids:
                        await self.http.post(f"/api/suggestions/{did}/reject", json={})
                    continue
                got = await self.prices.wait_all(dids, 30.0)
                body = r.json()
                out.append({
                    "size": size, "rep": rep, "status": r.status_code,
                    "request_ms": round((t1 - t0) * 1000, 1),
                    "last_price_ms": round((max(got.values()) - t0) * 1000, 1) if got else None,
                    "approved": body.get("counts", {}).get("approved"),
                    "prices_heard": len(got),
                })
                await asyncio.sleep(1.0)
        self.results["bulk"] = out
        print("bulk approve")
        for row in out:
            if row["status"] != 200:
                continue
            print(f"  {row['size']:>4} proposals: request {row['request_ms']:>8.1f} ms, "
                  f"last price {row['last_price_ms']} ms, {row['prices_heard']} heard")

    # ── many at once ──

    async def burst(self) -> None:
        out = []
        current, proposed = AUTO
        for size in (10, 50, 100, 250, 500):
            for rep in range(3):
                ids = bench_ids(size)
                msgs = [self.proposal(pid, current, proposed) for pid in ids]
                t0 = time.perf_counter()
                sent: dict[str, float] = {}

                async def send(pid: str, did: str, body: bytes) -> None:
                    sent[did] = time.perf_counter()
                    await self.pub.publish(f"retail/proposal/{pid}", body, qos=1)

                await asyncio.gather(*(send(pid, did, body)
                                       for pid, (did, body) in zip(ids, msgs)))
                got = await self.prices.wait_all([d for d, _ in msgs], 120.0)
                last = max(got.values()) if got else t0
                out.append({
                    "size": size, "rep": rep,
                    "all_prices_ms": round((last - t0) * 1000, 1),
                    "throughput_per_s": round(len(got) / (last - t0), 1) if got else 0,
                    "per_message": summary([got[d] - sent[d] for d in got]),
                    "lost": size - len(got),
                })
                await asyncio.sleep(2.0)
        self.results["burst"] = out
        print("burst")
        for row in out:
            print(f"  {row['size']:>4} at once: all priced in {row['all_prices_ms']:>8.1f} ms, "
                  f"{row['throughput_per_s']:>6.1f}/s, p95 {row['per_message'].get('p95')} ms, "
                  f"lost {row['lost']}")

    # ── a steady rate ──

    async def sustained(self) -> None:
        rates = [int(r) for r in self.args.rates.split(",")]
        secs = self.args.duration
        ids = bench_ids(500)
        current, proposed = AUTO
        sampler = DockerStats()
        sampler.start()
        idle = sampler.snapshot_now()
        out = []
        for rate in rates:
            n = rate * secs
            sent: dict[str, float] = {}
            dids: list[str] = []
            tasks = []
            start = time.perf_counter()
            sampler.mark(f"rate {rate}")
            for i in range(n):
                due = start + i / rate
                delay = due - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
                pid = ids[i % len(ids)]
                did, body = self.proposal(pid, current, proposed)
                dids.append(did)
                sent[did] = time.perf_counter()
                tasks.append(asyncio.create_task(
                    self.pub.publish(f"retail/proposal/{pid}", body, qos=1)))
            await asyncio.gather(*tasks)
            offered_s = time.perf_counter() - start
            got = await self.prices.wait_all(dids, 180.0)
            sampler.mark(None)
            last = max(got.values()) if got else start
            lat = [got[d] - sent[d] for d in got]
            out.append({
                "rate_offered": rate,
                "messages": n,
                "rate_sent": round(n / offered_s, 1),
                "rate_priced": round(len(got) / (last - start), 1) if got else 0,
                "latency": summary(lat),
                "lost": n - len(got),
                "cpu": sampler.peak(f"rate {rate}"),
            })
            print(f"  {rate:>4}/s for {secs}s: priced at {out[-1]['rate_priced']:>6.1f}/s, "
                  f"p50 {out[-1]['latency'].get('p50')} ms, p95 {out[-1]['latency'].get('p95')} ms, "
                  f"lost {out[-1]['lost']}, cpu {out[-1]['cpu']}")
            await asyncio.sleep(3.0)
        sampler.stop()
        self.results["sustained"] = {"duration_s": secs, "idle": idle, "rates": out,
                                     "websocket_resyncs": self.resyncs}

    # ── the console's REST reads ──

    async def rest(self) -> None:
        n = self.args.rest_runs
        demo_decision = await self.pool.fetchval(
            f"""SELECT decision_id::text FROM proposals WHERE product_id !~ '{BENCH_RE}'
                 ORDER BY ts DESC LIMIT 1""")
        demo_product = await self.pool.fetchval(
            f"""SELECT product_id FROM products WHERE product_id !~ '{BENCH_RE}'
                 AND archived_at IS NULL ORDER BY product_id LIMIT 1""")
        paths = [
            ("/health", "Health check"),
            ("/api/auth/me", "Who am I"),
            ("/api/meta", "Store metadata"),
            ("/api/dashboard", "Dashboard"),
            ("/api/suggestions", "Review queue"),
            (f"/api/suggestions/{demo_decision}", "Suggestion detail"),
            ("/api/products", "Products list"),
            (f"/api/products/{demo_product}", "Product detail"),
            ("/api/activity", "Activity log"),
            ("/api/labels", "Shelf labels"),
            ("/api/locations", "Locations"),
            ("/api/rules", "Pricing rules"),
        ]
        out = {}
        for path, name in paths:
            times, size, code = [], 0, None
            for _ in range(5):   # warm up: first-hit caches are not the steady state
                await self.http.get(path)
            for _ in range(n):
                t0 = time.perf_counter()
                r = await self.http.get(path)
                times.append(time.perf_counter() - t0)
                size, code = len(r.content), r.status_code
            out[name] = {"path": path.split("/api/")[-1] if "/api/" in path else path,
                         "status": code, "bytes": size, **summary(times)}
            show(f"{name} ({code}, {size} B)", out[name])

        # Sign-in hashes with Argon2id on purpose, so it is slow on purpose.
        login, password = credentials(self.args)
        times = []
        async with httpx.AsyncClient(base_url=self.args.api, timeout=30.0) as other:
            for _ in range(20):
                t0 = time.perf_counter()
                r = await other.post("/api/auth/signin", json={"login": login, "password": password})
                times.append(time.perf_counter() - t0)
                await other.post("/api/auth/signout")
        out["Sign in"] = {"path": "auth/signin", "status": r.status_code, "bytes": len(r.content),
                          **summary(times)}
        show("Sign in (Argon2id)", out["Sign in"])

        sizes = {}
        for table in ("products", "proposals", "price_history", "agent_audit_log",
                      "shelf_labels", "shap_vectors", "inventory_risk"):
            sizes[table] = await self.pool.fetchval(f"SELECT count(*) FROM {table}")
        self.results["rest"] = {"endpoints": out, "table_rows": sizes}

    # ── hardware ──

    async def _set_price(self, product_id: str, price: Decimal, reason: str) -> httpx.Response:
        return await self.http.post(f"/api/products/{product_id}/price",
                                    json={"price": str(price), "reason_tag": reason})

    async def _current(self, product_id: str) -> tuple[Decimal | None, str]:
        # The last price that is not one of ours, so a run that was interrupted
        # before it could restore the price is put right by the next one.
        row = await self.pool.fetchrow(
            """SELECT price, reason_tag FROM price_history
                WHERE product_id = $1 AND reason_tag NOT LIKE 'Bench %'
                ORDER BY ts DESC LIMIT 1""", product_id)
        return (row["price"], row["reason_tag"]) if row else (None, "")

    async def eink(self) -> None:
        a = self.args
        label = await self.pool.fetchrow(
            """SELECT label_id, kind, product_id, device_id, last_seen_at,
                      now() - last_seen_at > make_interval(secs => 4500) AS stale
                 FROM shelf_labels WHERE label_id = $1""",
            a.label)
        if label is None or label["device_id"] is None or label["product_id"] != a.product:
            sys.exit(f"{a.label} must exist, have a tag set up and be assigned to {a.product}")
        # A label that has missed two regular check-ins will not answer a doorbell
        # either, and every run would just wait out its timeout.
        if label["last_seen_at"] is None or label["stale"]:
            sys.exit(f"{a.label} last checked in at {label['last_seen_at']} (UTC), so it is not "
                     "running or not heard. Press its button and watch `make lora` print a "
                     "check-in for it, then run this again.")
        base, base_reason = await self._current(a.product)
        if base is None:
            sys.exit(f"{a.product} has no price yet; set one in the console first")
        rows = []
        try:
            await self._eink_runs(base, rows)
        finally:
            # Also on Ctrl-C: the shelf goes back to the price it had.
            await self._set_price(a.product, base, base_reason)
            print(f"  {a.product} restored to ${base} \"{base_reason}\"")
        confirmed = [r["to_confirmed_ms"] / 1000 for r in rows if r["to_confirmed_ms"]]
        self.results["eink"] = {
            "label": a.label, "product": a.product, "runs": rows,
            "to_confirmed": summary(confirmed),
            "missed": sum(1 for r in rows if r["to_confirmed_ms"] is None),
        }
        show("price set -> confirmed on the glass", self.results["eink"]["to_confirmed"])

    async def _eink_runs(self, base: Decimal, rows: list[dict]) -> None:
        a = self.args
        for i in range(a.runs):
            price = base + (Decimal("0.10") if i % 2 == 0 else Decimal("0"))
            reason = f"Bench {i + 1:02d}"
            mark = len(self.shelf)
            t0 = time.perf_counter()
            r = await self._set_price(a.product, price, reason)
            t_rest = time.perf_counter()
            if r.status_code != 200:
                sys.exit(f"setting the price failed ({r.status_code}): {r.text[:200]}")
            t_pub = await self.prices.wait((a.product, reason), 10.0)
            t_checkin = t_glass = None
            rssi = None
            deadline = time.perf_counter() + a.timeout
            while time.perf_counter() < deadline and t_glass is None:
                for t, p in self.shelf[mark:]:
                    if p.get("label_id") != a.label:
                        continue
                    if t_checkin is None:
                        t_checkin = t
                    if p.get("in_sync") and p.get("price") is not None \
                            and Decimal(str(p["price"])) == price:
                        t_glass, rssi = t, p.get("rssi_dbm")
                        break
                await asyncio.sleep(0.05)
            row = {
                "run": i + 1, "price": str(price),
                "rest_ms": ms(t_rest - t0),
                "to_broker_ms": ms(t_pub - t0) if t_pub else None,
                "to_first_checkin_ms": ms(t_checkin - t0) if t_checkin else None,
                "to_confirmed_ms": ms(t_glass - t0) if t_glass else None,
                "rssi_dbm": rssi,
            }
            rows.append(row)
            print(f"  run {i + 1:>2}: broker {row['to_broker_ms']} ms, first check-in "
                  f"{row['to_first_checkin_ms']} ms, on the glass {row['to_confirmed_ms']} ms, "
                  f"rssi {rssi}")
            await asyncio.sleep(a.interval)

    async def lcd(self) -> None:
        try:
            import serial
        except ImportError:
            sys.exit("lcd needs pyserial: add --with pyserial to the uv command")
        a = self.args
        lines: list[tuple[float, str]] = []
        stop = threading.Event()

        port = serial.Serial()
        port.port, port.baudrate, port.timeout = a.port, 115200, 0.1
        # Opening the port toggles DTR/RTS, which resets many ESP32 boards.
        port.dtr = port.rts = False
        port.open()

        def reader() -> None:
            buf = b""
            while not stop.is_set():
                chunk = port.read(256)
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    lines.append((time.perf_counter(), raw.decode(errors="replace").strip()))

        th = threading.Thread(target=reader, daemon=True)
        th.start()
        print("  waiting 8 s for the node to settle (it may have reset when the port opened)")
        await asyncio.sleep(8.0)

        base, base_reason = await self._current(a.product)
        if base is None:
            sys.exit(f"{a.product} has no price yet; set one in the console first")
        rows = []
        try:
            await self._lcd_runs(base, rows, lines)
        finally:
            # Also on Ctrl-C: the shelf goes back to the price it had.
            await self._set_price(a.product, base, base_reason)
            print(f"  {a.product} restored to ${base} \"{base_reason}\"")
            stop.set()
            th.join(1.0)
            port.close()
        shown = [r["to_shown_ms"] / 1000 for r in rows if r["to_shown_ms"]]
        self.results["lcd"] = {"product": a.product, "runs": rows, "to_shown": summary(shown),
                               "missed": sum(1 for r in rows if r["to_shown_ms"] is None)}
        show("price set -> shown on the LCD", self.results["lcd"]["to_shown"])

    async def _lcd_runs(self, base: Decimal, rows: list[dict],
                        lines: list[tuple[float, str]]) -> None:
        a = self.args
        for i in range(a.runs):
            price = base + (Decimal("0.10") if i % 2 == 0 else Decimal("0"))
            reason = f"Bench {i + 1:02d}"
            mark = len(lines)
            t0 = time.perf_counter()
            r = await self._set_price(a.product, price, reason)
            if r.status_code != 200:
                sys.exit(f"setting the price failed ({r.status_code}): {r.text[:200]}")
            t_pub = await self.prices.wait((a.product, reason), 10.0)
            t_shown = None
            deadline = time.perf_counter() + a.timeout
            while time.perf_counter() < deadline and t_shown is None:
                for t, text in lines[mark:]:
                    if text.startswith("shown:") and reason in text:
                        t_shown = t
                        break
                await asyncio.sleep(0.01)
            row = {
                "run": i + 1, "price": str(price),
                "to_broker_ms": ms(t_pub - t0) if t_pub else None,
                "to_shown_ms": ms(t_shown - t0) if t_shown else None,
                "broker_to_shown_ms": ms(t_shown - t_pub) if t_shown and t_pub else None,
            }
            rows.append(row)
            print(f"  run {i + 1:>2}: broker {row['to_broker_ms']} ms, "
                  f"shown {row['to_shown_ms']} ms")
            await asyncio.sleep(a.interval)

    # ── cleanup ──

    async def cleanup(self) -> None:
        # Children first, in foreign-key order. Every statement is limited to
        # the bench range, so nothing a person entered can match.
        counts = {}
        for table in ("agent_audit_log", "label_pushes", "price_history", "shap_vectors",
                      "proposals", "inventory_risk", "product_fields", "products"):
            status = await self.pool.execute(
                f"DELETE FROM {table} WHERE product_id ~ '{BENCH_RE}'")
            counts[table] = int(status.split()[-1])
        # Their retained prices: an empty retained message deletes one.
        retained: set[str] = set()
        async with aiomqtt.Client(self.args.mqtt_host, self.args.mqtt_port,
                                  identifier=f"bench-clean-{os.getpid()}") as c:
            await c.subscribe("retail/price/+", qos=1)
            try:
                async with asyncio.timeout(2.0):
                    async for m in c.messages:
                        if m.retain and re.match(BENCH_RE, m.topic.value.rsplit("/", 1)[-1]):
                            retained.add(m.topic.value)
            except TimeoutError:
                pass
            for topic in sorted(retained):
                await c.publish(topic, b"", qos=1, retain=True)
        print(f"cleanup: deleted {counts}, cleared {len(retained)} retained prices")
        self.results["cleanup"] = {"deleted": counts, "retained_cleared": len(retained)}


def ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


# ─── container CPU, sampled while the load runs ─────────────────────────────


class DockerStats:
    """`docker stats --no-stream` in a loop, on a thread. Each call takes about
    two seconds, so this is a coarse view: peaks, not a profile."""

    NAMES = ("rp-api", "rp-postgres", "rp-mosquitto")

    def __init__(self) -> None:
        self.samples: list[tuple[str | None, dict[str, dict[str, str]]]] = []
        self._label: str | None = None
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._run, daemon=True)

    def _one(self) -> dict[str, dict[str, str]]:
        try:
            out = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}", *self.NAMES],
                capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            return {}
        rows = {}
        for line in out.splitlines():
            d = json.loads(line)
            rows[d["Name"]] = {"cpu": d["CPUPerc"], "mem": d["MemUsage"].split(" / ")[0]}
        return rows

    def _run(self) -> None:
        while not self._stop.is_set():
            label = self._label
            snap = self._one()
            self.samples.append((label, snap))

    def start(self) -> None:
        self._th.start()

    def stop(self) -> None:
        self._stop.set()

    def mark(self, label: str | None) -> None:
        self._label = label

    def snapshot_now(self) -> dict[str, dict[str, str]]:
        return self._one()

    def peak(self, label: str) -> dict[str, str]:
        best: dict[str, float] = {}
        for lab, snap in self.samples:
            if lab != label:
                continue
            for name, v in snap.items():
                best[name] = max(best.get(name, 0.0), float(v["cpu"].rstrip("%")))
        return {k: f"{v:.0f}%" for k, v in best.items()}


# ─── plumbing ───────────────────────────────────────────────────────────────


def credentials(a: argparse.Namespace) -> tuple[str, str]:
    """A manager account. The demo's comes from .env, as make demo uses it."""
    env = {}
    path = REPO / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    login = a.login or os.environ.get("SHELFIQ_BOOTSTRAP_USER") or env.get("SHELFIQ_BOOTSTRAP_USER") or "admin"
    password = (a.password or os.environ.get("SHELFIQ_BOOTSTRAP_PASSWORD")
                or env.get("SHELFIQ_BOOTSTRAP_PASSWORD") or "admin")
    return login, password


def environment() -> dict[str, Any]:
    def run(*cmd: str) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return {
        "when": now_iso(),
        "host": platform.platform(),
        "cpu": run("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor(),
        "docker": run("docker", "info", "--format",
                      "{{.ServerVersion}} cpus={{.NCPU}} mem={{.MemTotal}}"),
        "commit": run("git", "-C", str(REPO), "rev-parse", "--short", "HEAD"),
    }


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("mode", choices=["all", "setup", "broker", "gate", "review", "bulk", "burst",
                                    "sustained", "rest", "eink", "lcd", "cleanup"])
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--mqtt-host", default="localhost")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--db", default="postgresql://retail:retail_dev_only@localhost:5432/retail_pricing")
    p.add_argument("--login")
    p.add_argument("--password")
    p.add_argument("--out", help="write the results here as JSON")
    p.add_argument("--broker-runs", type=int, default=1000)
    p.add_argument("--gate-runs", type=int, default=500)
    p.add_argument("--review-runs", type=int, default=200)
    p.add_argument("--rest-runs", type=int, default=200)
    p.add_argument("--gap", type=float, default=0.05, help="seconds between sequential runs")
    p.add_argument("--rates", default="10,25,50,100,200,400", help="proposals per second")
    p.add_argument("--duration", type=int, default=10, help="seconds at each rate")
    # hardware
    p.add_argument("--product")
    p.add_argument("--label")
    p.add_argument("--port")
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--interval", type=float, default=15.0, help="seconds between hardware runs")
    p.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for the shelf")
    a = p.parse_args()

    b = Bench(a)
    b.results["environment"] = environment()
    await b.open(ws=a.mode not in ("cleanup", "setup", "eink", "lcd"))
    try:
        if a.mode in ("all", "setup"):
            await b.setup(500)
        if a.mode == "all":
            # Reads first, so they see the demo's own table sizes.
            for step in (b.rest, b.broker, b.gate, b.review, b.bulk, b.burst, b.sustained):
                print()
                await step()
        elif a.mode in ("eink", "lcd"):
            if not a.product or (a.mode == "eink" and not a.label) or (a.mode == "lcd" and not a.port):
                sys.exit("eink needs --product and --label; lcd needs --product and --port")
            await getattr(b, a.mode)()
        elif a.mode not in ("setup", "cleanup"):
            await getattr(b, a.mode)()
        if a.mode in ("all", "cleanup"):
            print()
            await b.cleanup()
    finally:
        await b.close()

    if a.out:
        Path(a.out).write_text(json.dumps(b.results, indent=2, default=str))
        print(f"\nresults written to {a.out}")


if __name__ == "__main__":
    asyncio.run(main())
