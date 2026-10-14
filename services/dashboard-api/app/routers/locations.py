"""Where things are in the store: the layout tree, its levels, and the slots
where a shelf label physically hangs.

**The levels are the store's own.** A manager names them first, top level first
— Zone › Aisle › Bay for one floor, Floor › Zone › Aisle › Bay for several —
and nothing else here works until they have. A location's `depth` says which
level it is; a child is always exactly one level below its parent, and slots
attach only to the deepest level. Names can change at any time; the number of
levels only while the tree is empty.

The slot code ("A3-04") is what staff say out loud, so it is the natural key and
the thing every other screen joins against.

**Two kinds of query, for two kinds of question.** The tree itself is one flat
ordered SELECT nested in Python: it is at most six levels and a few dozen
rows, and building the nesting in SQL would be unreadable and untestable for no
gain. But "every slot underneath this zone" genuinely *is* a descendant
question, so the cascade-impact endpoint and the move-cycle guard each use a
`WITH RECURSIVE`.

**The dangerous operation here is DELETE.** `locations.parent_id` is
`ON DELETE CASCADE` and `shelf_labels.slot_code` is `ON DELETE SET NULL`, so one
statement against a zone destroys every aisle, bay and slot under it and strips
the position off every label in them. The caller therefore has to echo back the
figures it was shown, and they are rechecked inside the transaction — which
makes the confirmation an optimistic-concurrency token rather than a decoration.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .. import auth, bus, db, serialize

router = APIRouter(prefix="/api/locations", tags=["locations"])

# One arbitrary but stable key, so any two creates serialise against each other
# while each mints its id. Same shape as products.py's allocator.
_LOCATION_ID_LOCK = 0x4C4F4341  # 'LOCA'

MAX_LEVELS = 6

_SLOT_PATTERN = r"^[A-Z0-9][A-Z0-9-]{0,39}$"


def _article(word: str) -> str:
    return f"an {word}" if word[:1].lower() in "aeiou" else f"a {word}"


async def levels_of(q) -> list[dict]:
    """The store's levels, top first. `q` is the db module or a connection."""
    rows = await q.fetch("SELECT depth, name FROM location_levels ORDER BY depth")
    return [dict(r) for r in rows]


def _level_name(levels: list[dict], depth: int) -> str:
    return levels[depth - 1]["name"] if 0 < depth <= len(levels) else f"Level {depth}"


async def _require_levels(q) -> list[dict]:
    levels = await levels_of(q)
    if not levels:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Set up the store's layout levels first, on the Locations screen.",
        )
    return levels


async def _assert_slot_parent(q, location_id: str) -> None:
    """Positions go on the deepest level only."""
    levels = await _require_levels(q)
    parent = await q.fetchrow(
        "SELECT depth, name FROM locations WHERE location_id = $1", location_id
    )
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such location.")
    lowest = levels[-1]["name"]
    if parent["depth"] != len(levels):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{parent['name']} is {_article(_level_name(levels, parent['depth']).lower())}. "
            f"Positions go on the lowest level, {_article(lowest.lower())}.",
        )

# Every descendant of one node, itself included. Used by the impact report, the
# cycle guard and the labels location filter.
_DESCENDANTS = """
    WITH RECURSIVE sub AS (
        SELECT location_id, parent_id, name, depth FROM locations
         WHERE location_id = $1
        UNION ALL
        SELECT l.location_id, l.parent_id, l.name, l.depth
          FROM locations l JOIN sub ON l.parent_id = sub.location_id
    )
"""


def _slug(name: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return out or "node"


async def _mint_id(conn, level_name: str, name: str) -> str:
    """A readable id derived from the level and name, uniquified on collision."""
    base = f"{_slug(level_name)}-{_slug(name)}"[:60]
    taken = await conn.fetchval(
        "SELECT 1 FROM locations WHERE location_id = $1", base
    )
    if not taken:
        return base
    for n in range(2, 100):
        candidate = f"{base}-{n}"
        if not await conn.fetchval(
            "SELECT 1 FROM locations WHERE location_id = $1", candidate
        ):
            return candidate
    raise HTTPException(
        status.HTTP_409_CONFLICT, f"Too many locations already named like {name!r}."
    )


async def _child_depth(conn, levels: list[dict], parent_id: str | None) -> int:
    """The depth a new location under `parent_id` gets, or why it cannot exist.

    Enforced here rather than in the schema because the message matters: a
    CHECK violation is a 500 with a constraint name in it.
    """
    if parent_id is None:
        return 1
    parent = await conn.fetchrow(
        "SELECT depth, name FROM locations WHERE location_id = $1", parent_id
    )
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such parent location.")
    if parent["depth"] >= len(levels):
        lowest = _level_name(levels, parent["depth"])
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{parent['name']} is {_article(lowest.lower())}, the lowest level. "
            "Add positions to it instead.",
        )
    return parent["depth"] + 1


async def _assert_parent_depth(
    conn, levels: list[dict], depth: int, parent_id: str | None
) -> None:
    """For a move: the new parent must be exactly one level up."""
    name = _level_name(levels, depth)
    if depth == 1:
        if parent_id is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{_article(name).capitalize()} is the top level and cannot sit inside anything.",
            )
        return
    expected = _level_name(levels, depth - 1)
    if parent_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{_article(name).capitalize()} has to sit inside {_article(expected.lower())}.",
        )
    parent = await conn.fetchrow(
        "SELECT depth, name FROM locations WHERE location_id = $1", parent_id
    )
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such parent location.")
    if parent["depth"] != depth - 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{_article(name).capitalize()} has to sit inside "
            f"{_article(expected.lower())}, and {parent['name']} is "
            f"{_article(_level_name(levels, parent['depth']).lower())}.",
        )


def _clash_error(constraint: str | None) -> HTTPException:
    if constraint == "locations_sibling_name_idx":
        detail = "Something with that name is already in the same place."
    elif constraint == "location_slots_pkey":
        detail = "That code is already in use somewhere in the store."
    else:
        detail = "That name or code was taken a moment ago. Try again."
    return HTTPException(status.HTTP_409_CONFLICT, detail)


# ── Read ─────────────────────────────────────────────────────────────────────


@router.get("")
async def list_locations(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
) -> dict:
    levels = await levels_of(db)
    nodes = await db.fetch(
        """SELECT l.location_id, l.parent_id, l.name, l.depth, l.sort_order,
                  coalesce(sl.slots, 0)  AS slot_count,
                  coalesce(sl.filled, 0) AS filled_count,
                  coalesce(pr.n, 0)      AS product_count
             FROM locations l
             LEFT JOIN LATERAL (
                  SELECT count(*) AS slots, count(lab.label_id) AS filled
                    FROM location_slots s
                    LEFT JOIN shelf_labels lab ON lab.slot_code = s.slot_code
                   WHERE s.location_id = l.location_id
             ) sl ON TRUE
             LEFT JOIN LATERAL (
                  SELECT count(*) AS n FROM products p
                   WHERE p.location_id = l.location_id AND p.archived_at IS NULL
             ) pr ON TRUE
            ORDER BY l.sort_order, l.name"""
    )

    slots = await db.fetch(
        """SELECT s.slot_code, s.location_id, s.sort_order,
                  l.label_id, l.kind AS label_kind, l.status AS label_status,
                  l.last_seen_at,
                  p.product_id, p.name AS product_name, p.brand, p.category,
                  ph.price AS current_price
             FROM location_slots s
             LEFT JOIN shelf_labels l ON l.slot_code = s.slot_code
             LEFT JOIN products p ON p.product_id = l.product_id
             LEFT JOIN LATERAL (
                  SELECT price FROM price_history
                   WHERE product_id = p.product_id ORDER BY ts DESC LIMIT 1
             ) ph ON TRUE
            ORDER BY s.sort_order, s.slot_code"""
    )

    by_parent: dict[str | None, list[dict]] = {}
    for n in nodes:
        by_parent.setdefault(n["parent_id"], []).append(dict(n))

    def nest(parent_id: str | None, trail: list[str]) -> list[dict]:
        out = []
        for node in by_parent.get(parent_id, []):
            path = trail + [node["name"]]
            children = nest(node["location_id"], path)
            # A zone's own slot_count is 0, and rendering that beside "Grocery
            # hall" reads as an empty zone. The rolled-up figure is the one a
            # person means when they look at a branch.
            node["descendant_slots"] = node["slot_count"] + sum(
                c["descendant_slots"] for c in children
            )
            node["descendant_filled"] = node["filled_count"] + sum(
                c["descendant_filled"] for c in children
            )
            node["descendant_products"] = node["product_count"] + sum(
                c["descendant_products"] for c in children
            )
            node["level_name"] = _level_name(levels, node["depth"])
            node["path"] = " › ".join(path)
            node["children"] = children
            out.append(node)
        return out

    tree = nest(None, [])

    path_of = {n["location_id"]: n["path"] for n in nodes if "path" in n}

    def collect(ns: list[dict]) -> None:
        for n in ns:
            path_of[n["location_id"]] = n["path"]
            collect(n["children"])

    collect(tree)

    slot_rows = []
    for s in slots:
        row = serialize.row(s)
        row["path"] = path_of.get(s["location_id"], "")
        slot_rows.append(row)

    by_depth = {lv["depth"]: 0 for lv in levels}
    for n in nodes:
        by_depth[n["depth"]] = by_depth.get(n["depth"], 0) + 1

    return {
        "levels": levels,
        "tree": _clean(tree),
        "slots": slot_rows,
        "totals": {
            # Keyed by depth as a string, because that is what JSON keys are.
            "by_depth": {str(d): c for d, c in sorted(by_depth.items())},
            "slots": len(slots),
            "empty_slots": sum(1 for s in slots if s["label_id"] is None),
            "products": sum(n["product_count"] for n in nodes),
        },
    }


# ── Levels ───────────────────────────────────────────────────────────────────
# Declared before /{location_id} so PUT /levels is not read as a rename.


@router.get("/levels")
async def get_levels(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
) -> dict:
    levels = await levels_of(db)
    locked = await db.fetchval("SELECT EXISTS (SELECT 1 FROM locations)")
    return {"levels": levels, "configured": bool(levels), "depth_locked": locked}


class LevelsBody(BaseModel):
    # Top level first.
    names: list[str] = Field(min_length=1, max_length=MAX_LEVELS)


@router.put("/levels")
async def put_levels(
    body: LevelsBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    names = [n.strip() for n in body.names]
    if any(not n or len(n) > 30 for n in names):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Each level needs a name of 1–30 characters."
        )
    if len({n.lower() for n in names}) != len(names):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Two levels cannot share a name."
        )

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            # Serialise against location creates, which read the levels.
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCATION_ID_LOCK)
            current = await levels_of(conn)
            if len(current) != len(names) and await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM locations)"
            ):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Levels can only be added or removed while the layout is empty. "
                    "Renaming them is always allowed.",
                )
            # Park the old names first: the unique index on lower(name) would
            # otherwise refuse a swap such as Aisle ↔ Bay mid-update.
            await conn.execute(
                "UPDATE location_levels SET name = '~' || depth::text"
            )
            await conn.execute(
                "DELETE FROM location_levels WHERE depth > $1", len(names)
            )
            for depth, name in enumerate(names, start=1):
                await conn.execute(
                    """INSERT INTO location_levels (depth, name) VALUES ($1, $2)
                       ON CONFLICT (depth) DO UPDATE
                          SET name = EXCLUDED.name, updated_at = now()""",
                    depth, name,
                )
            await conn.execute(
                """INSERT INTO agent_audit_log (event_type, actor, detail)
                   VALUES ('guardrail_set', $1, $2)""",
                user.username,
                {"scope": "layout", "levels": names},
            )

    levels = await levels_of(db)
    bus.emit("locations", action="levels")
    return {"levels": levels, "configured": True, "by": user.username}


def _clean(nodes: list[dict]) -> list[dict]:
    """serialize.row over a nested structure, children included."""
    out = []
    for n in nodes:
        row = serialize.row({k: v for k, v in n.items() if k != "children"})
        row["children"] = _clean(n["children"])
        out.append(row)
    return out


@router.get("/{location_id}/impact")
async def impact(
    location_id: str,
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
) -> dict:
    """What deleting this node would take with it.

    Read separately from the DELETE so the confirmation can show the figures
    before anyone commits to them.
    """
    node = await db.fetchrow(
        "SELECT location_id, name, depth FROM locations WHERE location_id = $1",
        location_id,
    )
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such location.")

    row = await db.fetchrow(
        _DESCENDANTS
        + """
        SELECT (SELECT count(*) FROM sub)                           AS locations,
               (SELECT count(*) FROM location_slots s
                 WHERE s.location_id IN (SELECT location_id FROM sub)) AS slots,
               (SELECT count(*) FROM shelf_labels l
                 WHERE l.slot_code IN (SELECT slot_code FROM location_slots
                                        WHERE location_id IN
                                              (SELECT location_id FROM sub)))
                                                                    AS labels,
               (SELECT count(*) FROM shelf_labels l
                 WHERE l.product_id IS NOT NULL
                   AND l.slot_code IN (SELECT slot_code FROM location_slots
                                        WHERE location_id IN
                                              (SELECT location_id FROM sub)))
                                                                    AS paired_labels,
               (SELECT count(*) FROM products p
                 WHERE p.archived_at IS NULL
                   AND p.location_id IN (SELECT location_id FROM sub))  AS products
        """,
        location_id,
    )
    label_ids = await db.fetch(
        _DESCENDANTS
        + """
        SELECT l.label_id FROM shelf_labels l
         WHERE l.slot_code IN (SELECT slot_code FROM location_slots
                                WHERE location_id IN (SELECT location_id FROM sub))
         ORDER BY l.label_id
        """,
        location_id,
    )

    return {
        **serialize.row(node),
        **dict(row),
        "label_ids": [r["label_id"] for r in label_ids],
    }


# ── Slots ────────────────────────────────────────────────────────────────────
# Declared above /{location_id} so the ordering is deliberate rather than
# accidental, even though the segment counts already differ.


class SlotBody(BaseModel):
    location_id: str = Field(min_length=1, max_length=60)
    slot_code: str = Field(pattern=_SLOT_PATTERN)


@router.post("/slots", status_code=status.HTTP_201_CREATED)
async def create_slot(
    body: SlotBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_editor)],
) -> dict:
    await _assert_slot_parent(db, body.location_id)

    existing = await db.fetchrow(
        """SELECT s.slot_code, l.name AS location_name
             FROM location_slots s JOIN locations l ON l.location_id = s.location_id
            WHERE s.slot_code = $1""",
        body.slot_code,
    )
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{body.slot_code} already exists in {existing['location_name']}.",
        )

    order = await db.fetchval(
        "SELECT coalesce(max(sort_order), 0) + 1 FROM location_slots WHERE location_id = $1",
        body.location_id,
    )
    try:
        await db.execute(
            "INSERT INTO location_slots (slot_code, location_id, sort_order) VALUES ($1,$2,$3)",
            body.slot_code, body.location_id, order,
        )
    except Exception as exc:  # noqa: BLE001
        constraint = db.unique_constraint(exc)
        if constraint is None:
            raise
        raise _clash_error(constraint) from exc

    bus.emit("locations", changed=[body.location_id], action="slot_created")
    return {"slot_code": body.slot_code, "location_id": body.location_id, "by": user.username}


class SlotPatch(BaseModel):
    slot_code: str | None = Field(default=None, pattern=_SLOT_PATTERN)
    location_id: str | None = Field(default=None, min_length=1, max_length=60)


@router.put("/slots/{slot_code}")
async def update_slot(
    slot_code: str,
    body: SlotPatch,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    """Rename a position, move it, or both.

    **The rename is the thing to be careful with.** `slot_code` is the primary
    key and `shelf_labels.slot_code` references it with no `ON UPDATE CASCADE`,
    so renaming the slot without updating the label in the same transaction
    silently drops the label's position — no error, no trace, just a label that
    is suddenly nowhere.
    """
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                "SELECT slot_code, location_id FROM location_slots WHERE slot_code = $1",
                slot_code,
            )
            if current is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "No such position.")

            new_code = body.slot_code or slot_code
            new_loc = body.location_id or current["location_id"]

            if new_loc != current["location_id"]:
                await _assert_slot_parent(conn, new_loc)

            if new_code != slot_code:
                clash = await conn.fetchval(
                    "SELECT 1 FROM location_slots WHERE slot_code = $1", new_code
                )
                if clash:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT, f"{new_code} already exists."
                    )
                # Insert, repoint, delete — not an in-place UPDATE of the key.
                # `shelf_labels.slot_code` references this primary key and the
                # constraint is not deferrable, so renaming the row out from
                # under a label raises a ForeignKeyViolation immediately. Adding
                # the new row first means the label is never pointing at
                # something that does not exist.
                order = await conn.fetchval(
                    "SELECT sort_order FROM location_slots WHERE slot_code = $1",
                    slot_code,
                )
                await conn.execute(
                    """INSERT INTO location_slots (slot_code, location_id, sort_order)
                       VALUES ($1, $2, $3)""",
                    new_code, new_loc, order,
                )
                await conn.execute(
                    "UPDATE shelf_labels SET slot_code = $2 WHERE slot_code = $1",
                    slot_code, new_code,
                )
                await conn.execute(
                    "DELETE FROM location_slots WHERE slot_code = $1", slot_code
                )
            else:
                await conn.execute(
                    "UPDATE location_slots SET location_id = $2 WHERE slot_code = $1",
                    slot_code, new_loc,
                )

    bus.emit("locations", changed=[new_loc], action="slot_updated")
    return {"slot_code": new_code, "location_id": new_loc, "by": user.username}


@router.delete("/slots/{slot_code}")
async def delete_slot(
    slot_code: str,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    """Refuses while a label is in it.

    A single-slot delete has no cascade warning to hang a confirmation off, so a
    silent `SET NULL` would strip a label's position with nothing on screen
    saying it happened.
    """
    row = await db.fetchrow(
        """SELECT s.location_id, l.label_id
             FROM location_slots s
             LEFT JOIN shelf_labels l ON l.slot_code = s.slot_code
            WHERE s.slot_code = $1""",
        slot_code,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such position.")
    if row["label_id"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{row['label_id']} is in {slot_code}. Move that label first.",
        )

    await db.execute("DELETE FROM location_slots WHERE slot_code = $1", slot_code)
    bus.emit("locations", changed=[row["location_id"]], action="slot_deleted")
    return {"deleted": slot_code, "by": user.username}


# ── Nodes ────────────────────────────────────────────────────────────────────


class NewLocationBody(BaseModel):
    parent_id: str | None = Field(default=None, max_length=60)
    name: str = Field(min_length=1, max_length=80)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_location(
    body: NewLocationBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCATION_ID_LOCK)
            levels = await _require_levels(conn)
            depth = await _child_depth(conn, levels, body.parent_id)
            level_name = _level_name(levels, depth)

            location_id = await _mint_id(conn, level_name, body.name)
            order = await conn.fetchval(
                """SELECT coalesce(max(sort_order), 0) + 1 FROM locations
                    WHERE parent_id IS NOT DISTINCT FROM $1""",
                body.parent_id,
            )
            try:
                await conn.execute(
                    """INSERT INTO locations (location_id, parent_id, name, depth, sort_order)
                       VALUES ($1, $2, $3, $4, $5)""",
                    location_id, body.parent_id, body.name.strip(), depth, order,
                )
            except Exception as exc:  # noqa: BLE001
                constraint = db.unique_constraint(exc)
                if constraint is None:
                    raise
                raise _clash_error(constraint) from exc

    bus.emit("locations", changed=[location_id], action="created")
    return {
        "location_id": location_id,
        "parent_id": body.parent_id,
        "name": body.name.strip(),
        "depth": depth,
        "level_name": level_name,
        "by": user.username,
    }


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.put("/{location_id}")
async def rename_location(
    location_id: str,
    body: RenameBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    try:
        row = await db.fetchrow(
            """UPDATE locations SET name = $2 WHERE location_id = $1
            RETURNING location_id, parent_id, name, depth""",
            location_id, body.name.strip(),
        )
    except Exception as exc:  # noqa: BLE001
        constraint = db.unique_constraint(exc)
        if constraint is None:
            raise
        raise _clash_error(constraint) from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such location.")

    bus.emit("locations", changed=[location_id], action="renamed")
    return serialize.row(row) | {"by": user.username}


class MoveBody(BaseModel):
    parent_id: str | None = Field(default=None, max_length=60)


@router.put("/{location_id}/move")
async def move_location(
    location_id: str,
    body: MoveBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            node = await conn.fetchrow(
                "SELECT depth, name FROM locations WHERE location_id = $1", location_id
            )
            if node is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "No such location.")

            levels = await _require_levels(conn)
            await _assert_parent_depth(conn, levels, node["depth"], body.parent_id)

            # `no_self_parent` only catches parent_id = location_id. A → B → A
            # is not blocked by anything in the schema, and it would build a
            # tree with an unreachable island and make every recursive query
            # here loop. The depth rule above already makes it unreachable —
            # a parent is always shallower than its child — so this is
            # insurance, not a live code path.
            if body.parent_id is not None:
                cycles = await conn.fetchval(
                    _DESCENDANTS + "SELECT exists(SELECT 1 FROM sub WHERE location_id = $2)",
                    location_id, body.parent_id,
                )
                if cycles:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        f"That would put {node['name']} inside itself.",
                    )

            try:
                await conn.execute(
                    "UPDATE locations SET parent_id = $2 WHERE location_id = $1",
                    location_id, body.parent_id,
                )
            except Exception as exc:  # noqa: BLE001
                constraint = db.unique_constraint(exc)
                if constraint is None:
                    raise
                raise _clash_error(constraint) from exc

    bus.emit("locations", changed=[location_id], action="moved")
    return {"location_id": location_id, "parent_id": body.parent_id, "by": user.username}


@router.delete("/{location_id}")
async def delete_location(
    location_id: str,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
    expect_slots: Annotated[int | None, Query(ge=0)] = None,
    expect_labels: Annotated[int | None, Query(ge=0)] = None,
    expect_products: Annotated[int | None, Query(ge=0)] = None,
) -> dict:
    """Delete a node and everything under it.

    The caller must echo back the slot, label and product counts it was shown by
    `/impact`. They are recomputed inside the transaction and the delete is
    refused if they disagree — so a manager who read "12 positions" and clicks
    while a colleague adds a thirteenth gets a refusal rather than a surprise.
    """
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            node = await conn.fetchrow(
                "SELECT location_id, name, depth FROM locations WHERE location_id = $1",
                location_id,
            )
            if node is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "No such location.")

            # Products are not deleted with the location: ON DELETE SET NULL
            # leaves them in the catalogue, unassigned.
            product_count = await conn.fetchval(
                _DESCENDANTS
                + """SELECT count(*) FROM products
                      WHERE archived_at IS NULL
                        AND location_id IN (SELECT location_id FROM sub)""",
                location_id,
            )

            affected = await conn.fetch(
                _DESCENDANTS
                + """
                SELECT l.label_id, l.slot_code
                  FROM shelf_labels l
                 WHERE l.slot_code IN (SELECT slot_code FROM location_slots
                                        WHERE location_id IN
                                              (SELECT location_id FROM sub))
                """,
                location_id,
            )
            slot_count = await conn.fetchval(
                _DESCENDANTS
                + """SELECT count(*) FROM location_slots
                      WHERE location_id IN (SELECT location_id FROM sub)""",
                location_id,
            )
            loc_count = await conn.fetchval(
                _DESCENDANTS + "SELECT count(*) FROM sub", location_id
            )
            label_count = len(affected)

            if expect_slots is None or expect_labels is None or expect_products is None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Deleting {node['name']} removes it and "
                    f"{loc_count - 1} {'location' if loc_count == 2 else 'locations'} "
                    f"under it, {slot_count} "
                    f"{'position' if slot_count == 1 else 'positions'}, takes "
                    f"the position off {label_count} "
                    f"{'label' if label_count == 1 else 'labels'}, and leaves "
                    f"{product_count} {'product' if product_count == 1 else 'products'} "
                    "unassigned. Confirm those numbers to proceed.",
                )
            if (expect_slots, expect_labels, expect_products) != (
                slot_count, label_count, product_count
            ):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"This changed while you were looking at it: it now holds "
                    f"{slot_count} positions, {label_count} labels and "
                    f"{product_count} products, not {expect_slots}, "
                    f"{expect_labels} and {expect_products}. Check again.",
                )

            # Written before the delete: afterwards the slot is gone and the
            # only evidence a label lost its position would be absent data.
            for row in affected:
                await conn.execute(
                    """INSERT INTO label_events (label_id, event_type, detail)
                       VALUES ($1, 'slot_cleared', $2)""",
                    row["label_id"],
                    {
                        "slot_code": row["slot_code"],
                        "location": node["name"],
                        "by": user.username,
                    },
                )

            await conn.execute(
                "DELETE FROM locations WHERE location_id = $1", location_id
            )

    cleared = [r["label_id"] for r in affected]
    bus.emit("locations", deleted=[location_id], labels_cleared=cleared)
    if cleared:
        bus.emit("labels", changed=cleared, action="slot_cleared")
    if product_count:
        bus.emit("products", action="unassigned")

    return {
        "deleted": {"locations": loc_count, "slots": slot_count, "products_unassigned": product_count},
        "labels_cleared": cleared,
        "by": user.username,
    }
