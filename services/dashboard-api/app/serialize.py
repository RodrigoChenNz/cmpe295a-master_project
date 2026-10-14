"""Row → JSON.

One rule throughout: **money leaves this service as a string.** `"3.49"`, never
`3.49`. A JSON number is a double by the time a browser has parsed it, and
`0.1 + 0.2` is the reason this project writes every price as NUMERIC in the
database. The one exception is an MQTT payload, where the contract specifies
`"type": "number"` — those go through `db.money_num`, which quantises first.

Percentages are numbers, because nobody adds them up.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

MONEY_FIELDS = {
    "price", "current_price", "proposed_price", "previous_price", "final_price",
    "unit_cost", "min_price", "max_price", "old_price", "new_price", "delta",
    "last_price",
    "base_value",
}
PCT_FIELDS = {
    "delta_pct", "change_pct", "margin_pct", "proposed_margin_pct",
    "auto_approve_pct", "min_margin_pct", "ceiling_pct",
    "max_change_pct", "category_min_margin_pct", "dead_stock_risk",
    "agent_confidence",
}


def value(key: str, val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, Decimal):
        if key in MONEY_FIELDS:
            return str(val.quantize(Decimal("0.01")))
        if key in PCT_FIELDS:
            return float(val)
        return str(val)
    if isinstance(val, datetime):
        return val.isoformat().replace("+00:00", "Z")
    if isinstance(val, date):
        return val.isoformat()
    return val


def row(record: dict[str, Any], *, drop: set[str] | None = None) -> dict[str, Any]:
    drop = drop or set()
    return {k: value(k, v) for k, v in record.items() if k not in drop}


def rows(records: list[dict[str, Any]], *, drop: set[str] | None = None) -> list[dict]:
    return [row(r, drop=drop) for r in records]


def page(records: list[dict], total: int, page_no: int, size: int) -> dict[str, Any]:
    """The envelope every paginated list uses.

    `total` is the count after filtering, which is what the "1-25 of 112"
    control needs; the client clamps the page itself when filters shrink the
    list, and `pages` is here so it does not have to divide.
    """
    return {
        "rows": rows(records),
        "total": total,
        "page": page_no,
        "page_size": size,
        "pages": max(1, -(-total // size)) if size else 1,
    }
