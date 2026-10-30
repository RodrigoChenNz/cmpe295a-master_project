"""Authenticated software integration status and store observation ingestion."""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from jsonschema import ValidationError
from retail.contracts import validate
from .. import auth, db, bus, ingest, serialize

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("")
async def status(_user: Annotated[auth.CurrentUser, Depends(auth.current_user)]):
    counts = await db.fetchrow("""SELECT
        (SELECT count(*) FROM message_outbox WHERE sent_at IS NULL) AS queued,
        (SELECT count(*) FROM message_outbox WHERE sent_at IS NOT NULL) AS delivered,
        (SELECT count(*) FROM shelf_labels WHERE emulated) AS emulated_labels,
        (SELECT count(*) FROM shelf_labels WHERE rendered_at IS NOT NULL) AS confirmed_labels,
        (SELECT count(*) FROM store_observations) AS observations,
        (SELECT max(ts) FROM store_observations) AS last_observation""")
    models = await db.fetch(
        "SELECT agent_version, count(*) AS proposals FROM proposals GROUP BY agent_version ORDER BY agent_version"
    )
    forecasts = await db.fetch(
        "SELECT model_version, count(*) AS observations FROM inventory_risk GROUP BY model_version ORDER BY model_version"
    )
    return dict(
        counts=serialize.row(counts),
        broker=bus.broker_state(),
        pricing_models=serialize.rows(models),
        forecast_models=serialize.rows(forecasts),
    )


@router.post("/observations", status_code=202)
async def observation(
    payload: dict, _user: Annotated[auth.CurrentUser, Depends(auth.require_editor)]
):
    try:
        validate("state", payload)
        if payload["source"] != "store":
            raise ValueError("External observations must identify source=store")
        ingest._when(payload["ts"])
        from .. import outbox

        async with db.pool().acquire() as conn:
            async with conn.transaction():
                await ingest._state(payload, conn)
                await outbox.enqueue(
                    conn, f"retail/state/{payload['product_id']}", payload, retained=True
                )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"accepted": True, "state_id": payload["state_id"]}
