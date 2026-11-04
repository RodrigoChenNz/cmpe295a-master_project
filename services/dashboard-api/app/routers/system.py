"""Authenticated software integration status and store observation ingestion."""

from datetime import datetime, timezone
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
    recent = await db.fetchrow("""SELECT
        (SELECT max(received_at) FROM store_observations) AS observations,
        (SELECT max(ts) FROM inventory_risk WHERE model_version NOT LIKE '%stub%') AS forecasting,
        greatest((SELECT max(ts) FROM proposals WHERE agent_version NOT LIKE 'stub%'
            AND agent_version NOT LIKE 'smoke%'),(SELECT last_seen FROM worker_heartbeats WHERE role='pricing')) AS pricing,
        (SELECT max(last_seen_at) FROM shelf_labels
            WHERE emulated OR rendered_at IS NOT NULL) AS receiver""")
    latest = await db.fetchrow("""SELECT * FROM (
        SELECT agent_version,ts FROM proposals WHERE agent_version NOT LIKE 'stub%' AND agent_version NOT LIKE 'smoke%'
        UNION ALL SELECT model_version AS agent_version,last_seen AS ts FROM worker_heartbeats WHERE role='pricing') m
        ORDER BY ts DESC LIMIT 1""")
    checked = datetime.now(timezone.utc)
    pipeline = []
    for stage, timestamp in dict(recent).items():
        age = (checked - timestamp).total_seconds() if timestamp else None
        pipeline.append(
            dict(
                stage=stage,
                last_seen=timestamp.isoformat() if timestamp else None,
                age_seconds=round(age, 1) if age is not None else None,
                recent=age is not None and -5 <= age <= 120,
            )
        )
    return dict(
        counts=serialize.row(counts),
        checked_at=checked.isoformat(),
        pipeline=pipeline,
        latest_pricing_model=latest["agent_version"] if latest else None,
        stale_after_seconds=120,
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
