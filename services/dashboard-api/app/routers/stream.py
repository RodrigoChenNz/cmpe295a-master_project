"""/ws/stream — change hints for the open console.

Every message is a *hint to refetch*, never the only copy of a fact. A browser
that was disconnected for ten minutes misses nothing it cannot get back from
the REST endpoints, which is why a dropped socket is a latency problem and not
a correctness one. The console refetches on reconnect regardless.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import auth, bus, db

log = logging.getLogger("shelfiq.stream")
router = APIRouter()

PING_SECONDS = 25


@router.websocket("/ws/stream")
async def stream(ws: WebSocket) -> None:
    user = await auth.user_from_request(ws)
    if user is None:
        # 1008 policy violation, so the client shows the sign-in screen rather
        # than retrying a socket that will never open.
        await ws.close(code=1008, reason="Sign in to continue.")
        return

    await ws.accept()
    queue = bus.subscribe()

    try:
        # Tell a new connection the truth about the broker straight away,
        # instead of leaving the banner blank until the next publish.
        await ws.send_json(
            {
                "type": "hello",
                "user": user.username,
                "broker": bus.broker_state() | {"queued": await db.fetchval("SELECT count(*) FROM message_outbox WHERE sent_at IS NULL")},
            }
        )

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
            except asyncio.TimeoutError:
                # A ping, so a proxy between here and the browser does not
                # quietly reap an idle socket and leave the console stale.
                await ws.send_json({"type": "ping"})
                continue
            await ws.send_json(event)

    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.debug("stream closed for %s", user.username, exc_info=True)
    finally:
        bus.unsubscribe(queue)
        with contextlib.suppress(Exception):
            await ws.close()
