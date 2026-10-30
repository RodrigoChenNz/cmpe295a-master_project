"""dashboard-api — Layer 5.

The control point of the system. It is the only publisher of `retail/price/`,
and it publishes only what a manager approved or what a manager's standing
policy auto-approved. Nothing reaches a shelf without passing through here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import auth, bus, db, ingest, migrations, outbox
from .config import settings
from .routers import (
    activity, categories, dashboard, labels, locations, meta, products, rules, stream, users,
)
from .routers import auth as auth_router
from .routers import suggestions, system

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
)
log = logging.getLogger("shelfiq")

TICK_SECONDS = 30


async def _housekeeping(stop: asyncio.Event) -> None:
    """One timer, three jobs that all have to happen whether or not anyone is
    looking at the console: lapse undecided proposals, settle label pushes that
    were never acknowledged, and age unseen labels to offline."""
    while not stop.is_set():
        try:
            await ingest.expire_lapsed()
            await ingest.settle_label_pushes()
            await ingest.mark_stale_labels()
            await auth.purge_expired_sessions()
        except Exception:  # noqa: BLE001 - a bad tick must not stop the timer
            log.exception("housekeeping tick failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)


async def _check_schema() -> None:
    """The schema only reaches a database through Postgres's init scripts, which
    run on an empty volume and never again. A volume created before a schema
    change keeps the old shape, and fails later as a confusing 500 on whichever
    screen touches the difference. Say so at startup instead."""
    # One table per schema change worth detecting; add to it, never replace.
    required = ("user_invites", "location_levels", "store_categories")
    missing = [
        t for t in required
        if not await db.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{t}")
    ]
    # Columns added to tables that already existed, which the check above
    # cannot see. Same rule: add to it, never replace.
    required_columns = (("store_settings", "onboarded_at"),)
    for table, column in required_columns:
        if not await db.fetchval(
            """SELECT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_schema = 'public'
                                 AND table_name = $1 AND column_name = $2)""",
            table, column,
        ):
            missing.append(f"{table}.{column}")
    if missing:
        raise RuntimeError(
            f"The database predates the current schema (no {', '.join(missing)}). Recreate it: "
            "`make fresh` (or `make fresh-demo` for the demo database). This "
            "deletes that database's data."
        )


async def _ensure_singletons() -> None:
    """An unseeded database has no store_settings or change_limits row, and
    the Settings and Rules screens update those in place, so a save would
    silently change nothing. Create both from the schema's defaults; the store's
    broker fields come from what this process is actually connected to, so
    Settings does not open on a spurious 'restart required'. A no-op when the
    rows exist (the demo seed, or a manager's saved settings)."""
    await db.execute(
        """INSERT INTO store_settings (id, mqtt_host, mqtt_port, mqtt_tls)
           VALUES (TRUE, $1, $2, $3)
           ON CONFLICT (id) DO NOTHING""",
        settings.mqtt_host,
        settings.mqtt_port,
        settings.mqtt_tls,
    )
    await db.execute(
        "INSERT INTO change_limits (id) VALUES (TRUE) ON CONFLICT (id) DO NOTHING"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await migrations.apply()
    await _check_schema()
    await _ensure_singletons()
    await auth.bootstrap_user()

    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(bus.run(stop), name="mqtt"),
        asyncio.create_task(outbox.run(stop), name="outbox"),
        asyncio.create_task(_housekeeping(stop), name="housekeeping"),
    ]
    log.info("dashboard-api ready")

    try:
        yield
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await db.disconnect()


app = FastAPI(
    title="ShelfIQ — dashboard-api",
    version="0.1.0",
    description=(
        "Layer 5 of the SJSU CMPE 295B multi-agent retail pricing project. "
        "The agent recommends; a human decides; this service is the gate "
        "between them and the only publisher of retail/price/."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # Credentials, because the session is an httpOnly cookie. This is also why
    # allow_origins is an explicit list and never "*": the two are not
    # permitted together, and a wildcard with credentials would let any page
    # on the internet act as the signed-in manager.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (
    auth_router.router,
    meta.router,
    dashboard.router,
    suggestions.router,
    system.router,
    products.router,
    labels.router,
    locations.router,
    activity.router,
    rules.router,
    categories.router,
    users.router,
    stream.router,
):
    app.include_router(r)


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    # gate.publish_price raises ValueError on an over-long reason_tag and on a
    # bad approval value. Those are contract violations, not server faults, so
    # they come back as 400 with the actual message rather than a bare 500.
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness plus the two dependencies, reported separately.

    The service is deliberately useful with the broker down — the console reads
    and the queue works — so a broker outage is not reported as unhealthy. It
    is reported as what it is: approved prices are being recorded and will not
    reach a shelf until the link returns.
    """
    try:
        await db.fetchval("SELECT 1")
        database = True
    except Exception:  # noqa: BLE001
        database = False

    broker = bus.broker_state()
    durable_queued = await db.fetchval("SELECT count(*) FROM message_outbox WHERE sent_at IS NULL") if database else None
    return {
        "status": "ok" if database else "degraded",
        "database": database,
        "broker": broker | {"queued": durable_queued},
        "shelf_writes": "live" if broker.get("connected") else "queued",
    }
