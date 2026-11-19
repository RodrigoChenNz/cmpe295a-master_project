#!/usr/bin/env python3
"""Generate infra/postgres/03-seed.sql from the ShelfIQ design prototype's own
sample catalogue. Run from the repo root; writes the .sql file in place.

The catalogue, labels, suggestions and decisions below are transcribed verbatim
from `design_handoff_shelfiq/ShelfIQ Instrument.dc.html` (PROD, LAB, ACT, SUG).
Price history is synthesised around each product's real current price so the
charts have something to draw. The whole file is sample data and says so.
"""
import hashlib
import random
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

NOW = datetime(2026, 9, 17, 14, 5, tzinfo=timezone.utc)

# name, display, category, price, cost, stock, label, label_status, expiry_days,
# attn, upc, units_per_day, last_change_pct, last_when, brand, supplier, sku
PROD = [
    ['Organic Whole Milk 1L', 'MILK ORGANIC 1L', 'Dairy', 3.49, 2.41, 18, 'SHLF-0142', 'synced', 2, 'Markdown to $2.99 awaiting your review', '5010024001231', 5.6, 6.1, 'Yesterday', 'Meadowfield', 'Meadowfield Dairy Co', 'DA-1007'],
    ['Sourdough Loaf 800g', 'SOURDOUGH 800G', 'Bakery', 4.25, 2.05, 12, 'SHLF-0108', 'syncing', 0, 'Markdown applied yesterday 16:40', '5010024007112', 8, -5.6, 'Yesterday', 'Baker & Son', 'Baker & Son Ltd', 'BK-1014'],
    ['Free-Range Eggs 12pk', 'EGGS FREERANGE', 'Dairy', 5.99, 4.1, 9, 'SHLF-0121', 'synced', 12, '', '5010024003451', 3.1, 4.2, '3h ago', 'Hillside Farm', 'Hillside Farm Direct', 'DA-1021'],
    ['Cherry Tomatoes 250g', 'TOMATO CHERRY', 'Produce', 2.79, 1.62, 26, 'SHLF-0134', 'failed', 2, 'Markdown queued - label not confirming', '5010024009087', 4.2, -6.7, '3h ago', 'Sunvale', 'Sunvale Produce', 'PR-1028'],
    ['Arabica Coffee Beans 500g', 'COFFEE ARABICA', 'Pantry', 12.5, 8.2, 41, 'SHLF-0090', 'synced', 210, '', '5010024005566', 1.4, 4.6, 'Yesterday', 'Northbean', 'Northbean Importers', 'PN-1035'],
    ['Greek Yogurt 500g', 'YOGURT GREEK', 'Dairy', 3.95, 2.55, 22, 'SHLF-0117', 'failed', 4, '', '5010024002198', 4.8, -7.6, '2d ago', 'Meadowfield', 'Meadowfield Dairy Co', 'DA-1042'],
    ['Atlantic Salmon Fillet', 'SALMON FILLET', 'Seafood', 11.99, 8.9, 7, 'SHLF-0151', 'synced', 1, 'Markdown to $9.99 awaiting your review', '5010024008123', 3.5, 4.3, '12 Sep', 'Coldwater Co', 'Coldwater Seafood', 'SF-1049'],
    ['Baby Spinach 200g', 'SPINACH BABY', 'Produce', 2.49, 1.35, 14, 'SHLF-0129', 'synced', 3, '', '5010024004777', 4.6, -7.4, '5h ago', 'Sunvale', 'Sunvale Produce', 'PR-1056'],
    ['Salted Butter 250g', 'BUTTER SALTED', 'Dairy', 3.15, 2.02, 33, 'SHLF-0102', 'offline', 26, '', '5010024006301', 6.2, -6, 'Yesterday', 'Meadowfield', 'Meadowfield Dairy Co', 'DA-1063'],
    ['Gala Apples 1kg', 'APPLE GALA 1KG', 'Produce', 2.79, 1.71, 58, 'SHLF-0147', 'synced', 9, '', '5010024001905', 9.4, -5.4, '2h ago', 'Orchard Lane', 'Orchard Lane Growers', 'PR-1070'],
    ['Rye Crackers 200g', 'RYE CRACKERS', 'Pantry', 2.2, 1.32, 19, 'SHLF-0166', 'offline', 48, '', '5010024008899', 2.6, 3.8, '10 Sep', 'Crispwell', 'Crispwell Foods', 'PN-1077'],
    ['Rolled Oats 1kg', 'OATS ROLLED 1KG', 'Pantry', 3.4, 2.05, 37, '', 'unassigned', 240, '', '5010024011119', 2.4, 2.1, '08 Sep', 'Goldfield', 'Goldfield Mills', 'PN-1084'],
    ['Mature Cheddar 400g', 'CHEDDAR MATURE', 'Dairy', 5.25, 3.4, 16, '', 'unassigned', 31, '', '5010024012116', 3.3, -2.8, '09 Sep', 'Meadowfield', 'Meadowfield Dairy Co', 'DA-1091'],
    ['Carrots 1kg', 'CARROTS 1KG', 'Produce', 1.45, 0.82, 42, '', 'unassigned', 11, '', '5010024013113', 7.1, 1.7, '07 Sep', 'Orchard Lane', 'Orchard Lane Growers', 'PR-1098'],
    ['55" 4K Smart TV', 'TV 4K 55IN', 'Electronics', 449, 338, 6, 'SHLF-0210', 'synced', None, '', '5010024020114', 1.2, 2.4, 'Yesterday', 'Lumira', 'Lumira Distribution', 'EL-1105'],
    ['Wireless Earbuds Pro', 'EARBUDS PRO', 'Electronics', 129, 84, 24, 'SHLF-0214', 'synced', None, 'Increase to $134 awaiting your review', '5010024020213', 2.1, 3.6, '2h ago', 'Northwave', 'Northwave Audio', 'EL-1112'],
    ['Bluetooth Speaker Mini', 'SPEAKER MINI', 'Electronics', 59.99, 37.5, 31, 'SHLF-0218', 'failed', None, 'Price change queued - label not confirming', '5010024020312', 3.4, -1.8, '4h ago', 'Northwave', 'Northwave Audio', 'EL-1119'],
    ['USB-C Charger 65W', 'CHARGER 65W', 'Electronics', 34.5, 19.8, 48, 'SHLF-0221', 'synced', None, '', '5010024020411', 4.6, 1.1, 'Yesterday', 'Voltek', 'Voltek Supply', 'EL-1126'],
    ['Robot Vacuum R30', 'ROBOVAC R30', 'Electronics', 299, 214, 4, 'SHLF-0225', 'offline', None, '', '5010024020510', 0.8, -0.6, '12 Sep', 'Lumira', 'Lumira Distribution', 'EL-1133'],
    ['Smart Doorbell Cam', 'DOORBELL CAM', 'Electronics', 89, 61, 11, '', 'unassigned', None, '', '5010024020619', 1.6, 2.2, '09 Sep', 'Voltek', 'Voltek Supply', 'EL-1140'],
    ['Merino Wool Jumper', 'JUMPER MERINO', 'Clothing', 79.99, 41, 14, 'SHLF-0302', 'synced', None, 'Markdown to $59.99 awaiting your review', '5010024030117', 1.9, -2.7, '3h ago', 'Brackenfield', 'Brackenfield Apparel', 'CL-1147'],
    ['Cotton Oxford Shirt', 'SHIRT OXFORD', 'Clothing', 44.5, 22.8, 27, 'SHLF-0306', 'synced', None, '', '5010024030216', 3.2, 1.4, 'Yesterday', 'Brackenfield', 'Brackenfield Apparel', 'CL-1154'],
    ['Selvedge Denim Jeans', 'JEANS DENIM', 'Clothing', 98, 52, 9, 'SHLF-0309', 'syncing', None, '', '5010024030315', 1.4, 0.9, '5h ago', 'Halden', 'Halden Denim Co', 'CL-1161'],
    ['Quilted Winter Coat', 'COAT QUILTED', 'Clothing', 159, 88, 5, 'SHLF-0313', 'failed', None, 'Seasonal markdown queued - label not confirming', '5010024030414', 0.6, -3.4, 'Yesterday', 'Halden', 'Halden Outerwear', 'CL-1168'],
    ['Canvas Trainers', 'TRAINERS CNVS', 'Clothing', 54.99, 26.5, 36, 'SHLF-0317', 'offline', None, '', '5010024030513', 2.8, -1.2, '11 Sep', 'Fairstep', 'Fairstep Footwear', 'CL-1175'],
    ['Linen Summer Dress', 'DRESS LINEN', 'Clothing', 68, 33, 8, '', 'unassigned', None, 'End-of-season markdown proposed', '5010024030612', 1.1, -4.8, '08 Sep', 'Brackenfield', 'Brackenfield Apparel', 'CL-1182'],
]

# label_id, product name, slot, status, last seen, rssi, bars, battery, firmware
LAB = [
    ['SHLF-0142', 'Organic Whole Milk 1L', 'A3-04', 'online', 12, -52, 94, '1.4.2'],
    ['SHLF-0108', 'Sourdough Loaf 800g', 'B1-02', 'syncing', 60, -61, 88, '1.4.2'],
    ['SHLF-0121', 'Free-Range Eggs 12pk', 'A2-11', 'online', 40, -49, 91, '1.4.2'],
    ['SHLF-0117', 'Greek Yogurt 500g', 'A3-09', 'failed', 540, -78, 12, '1.3.9'],
    ['SHLF-0134', 'Cherry Tomatoes 250g', 'C4-07', 'failed', 1320, -81, 46, '1.3.9'],
    ['SHLF-0102', 'Salted Butter 250g', 'A1-06', 'offline', 25200, -95, 4, '1.3.9'],
    ['SHLF-0166', 'Rye Crackers 200g', 'D3-08', 'offline', 39600, -95, 0, '1.3.9'],
    ['SHLF-0151', 'Atlantic Salmon Fillet', 'E1-03', 'online', 18, -55, 77, '1.4.2'],
    ['SHLF-0147', 'Gala Apples 1kg', 'C1-01', 'online', 120, -58, 83, '1.4.2'],
    ['SHLF-0129', 'Baby Spinach 200g', 'C2-05', 'online', 180, -56, 86, '1.4.2'],
    ['SHLF-0090', 'Arabica Coffee Beans 500g', 'D2-01', 'online', 60, -63, 79, '1.4.2'],
    ['SHLF-0163', None, None, 'unassigned', 5, -58, 100, '1.4.2'],
    ['SHLF-0210', '55" 4K Smart TV', 'F1-01', 'online', 30, -54, 88, '1.4.2'],
    ['SHLF-0214', 'Wireless Earbuds Pro', 'F2-02', 'online', 60, -59, 92, '1.4.2'],
    ['SHLF-0218', 'Bluetooth Speaker Mini', 'F2-03', 'failed', 840, -79, 38, '1.3.9'],
    ['SHLF-0221', 'USB-C Charger 65W', 'F2-06', 'online', 120, -57, 85, '1.4.2'],
    ['SHLF-0225', 'Robot Vacuum R30', 'F1-04', 'offline', 32400, -95, 2, '1.3.9'],
    ['SHLF-0302', 'Merino Wool Jumper', 'G1-02', 'online', 50, -51, 90, '1.4.2'],
    ['SHLF-0306', 'Cotton Oxford Shirt', 'G1-03', 'online', 180, -60, 81, '1.4.2'],
    ['SHLF-0309', 'Selvedge Denim Jeans', 'G2-01', 'syncing', 40, -64, 87, '1.4.2'],
    ['SHLF-0313', 'Quilted Winter Coat', 'G1-08', 'failed', 1560, -83, 29, '1.3.9'],
    ['SHLF-0317', 'Canvas Trainers', 'G2-04', 'offline', 21600, -95, 7, '1.3.9'],
]

# product name, slot, category, current, suggested, rationale, confidence, reason_tag
SUG = [
    ['Organic Whole Milk 1L', 'A3-04', 3.49, 2.99, 'Expires in 2 days with 18 units still on the shelf.', 92, 'Expiry 2 days'],
    ['Sourdough Loaf 800g', 'B1-02', 4.25, 3.40, 'Baked this morning, 12 unsold with 3 hours of trade left.', 88, 'Bake day end'],
    ['Free-Range Eggs 12pk', 'A2-11', 5.99, 6.49, 'Sales up 22% this week and stock down to 9 packs.', 71, 'Demand up 22%'],
    ['Cherry Tomatoes 250g', 'C4-07', 2.79, 2.19, 'Two days to expiry and a slow week for salad lines.', 84, 'Expiry 2 days'],
    ['Arabica Coffee Beans 500g', 'D2-01', 12.50, 13.25, 'Nearest competitor moved to $13.99 and stock is healthy.', 64, 'Competitor up'],
    ['Greek Yogurt 500g', 'A3-09', 3.95, 3.55, 'Four days to expiry, sales tracking 11% below plan.', 79, 'Slow vs plan'],
    ['Atlantic Salmon Fillet', 'E1-03', 11.99, 9.99, 'Fresh counter closes at 19:00 with 7 portions left.', 90, 'Counter closing'],
    ['Baby Spinach 200g', 'C2-05', 2.49, 2.29, 'Leaf quality drops tomorrow, 14 bags in stock.', 68, 'Quality drop'],
    ['Merino Wool Jumper', 'G1-02', 79.99, 59.99, 'End of the AW26 range with 14 left and sales slowing.', 81, 'End of range'],
    ['Wireless Earbuds Pro', 'F2-02', 129.00, 134.00, 'Competitor sold out and demand is up 18% this week.', 66, 'Rival sold out'],
    ['Linen Summer Dress', None, 68.00, 47.60, 'Season ends in three weeks with 8 still in stock.', 86, 'Season ending'],
    ['Bluetooth Speaker Mini', 'F2-03', 59.99, 54.99, 'Slow mover at 31 units, matching a rival promotion.', 73, 'Match promo'],
]

# ts, product, old, new, decision, label, shelf result, outcome note
ACT = [
    ['2026-09-17 14:02:11', 'Gala Apples 1kg', 2.95, 2.79, 'as suggested', 'SHLF-0147', 'confirmed'],
    ['2026-09-17 13:51:02', 'Greek Yogurt 500g', 3.95, 3.65, 'edited price', 'SHLF-0117', 'failed'],
    ['2026-09-17 13:22:40', 'Cotton Oxford Shirt', 49.00, 44.50, 'as suggested', 'SHLF-0306', 'confirmed'],
    ['2026-09-17 12:20:55', 'Gala Apples 1kg', 3.15, 2.95, 'as suggested', 'SHLF-0147', 'confirmed'],
    ['2026-09-17 12:05:18', 'USB-C Charger 65W', 32.00, 34.50, 'as suggested', 'SHLF-0221', 'confirmed'],
    ['2026-09-17 11:48:12', 'Cherry Tomatoes 250g', 2.99, 2.79, 'as suggested', 'SHLF-0134', 'pending'],
    ['2026-09-17 11:20:07', 'Free-Range Eggs 12pk', 5.75, 5.99, 'as suggested', 'SHLF-0121', 'confirmed'],
    ['2026-09-17 10:41:29', 'Quilted Winter Coat', 175.00, 159.00, 'edited price', 'SHLF-0313', 'failed'],
    ['2026-09-17 10:02:44', 'Baby Spinach 200g', 2.69, 2.49, 'edited price', 'SHLF-0129', 'confirmed'],
    ['2026-09-17 09:31:19', 'Rye Crackers 200g', 2.20, 1.95, 'as suggested', 'SHLF-0166', 'failed'],
    ['2026-09-16 19:08:55', 'Canvas Trainers', 59.99, 54.99, 'as suggested', 'SHLF-0317', 'confirmed'],
    ['2026-09-16 18:14:51', 'Salted Butter 250g', 3.35, 3.15, 'as suggested', 'SHLF-0102', 'confirmed'],
    ['2026-09-16 17:40:03', 'Sourdough Loaf 800g', 4.50, 4.25, 'edited price', 'SHLF-0108', 'confirmed'],
    ['2026-09-16 16:02:37', 'Arabica Coffee Beans 500g', 11.95, 12.50, 'as suggested', 'SHLF-0090', 'confirmed'],
    ['2026-09-16 15:30:02', '55" 4K Smart TV', 479.00, 449.00, 'as suggested', 'SHLF-0210', 'confirmed'],
    ['2026-09-16 14:00:12', 'Organic Whole Milk 1L', 3.29, 3.49, 'as suggested', 'SHLF-0142', 'confirmed'],
]

CATEGORY_RULES = [
    # category, ceiling_pct, min_margin_pct, agent_may_suggest
    # ceiling_pct is the headroom above the current price the agent may propose;
    # min_margin_pct is the floor, from which each SKU's min_price is derived.
    ('Dairy', 15, 15, True),
    ('Bakery', 10, 12, True),
    ('Produce', 15, 14, True),
    ('Pantry', 20, 18, True),
    ('Seafood', 15, 16, True),
    ('Electronics', 10, 20, True),
    ('Clothing', 20, 30, True),
    ('Household', 15, 18, False),
]

PACK_BY_CATEGORY = {
    'Dairy': 'grocery', 'Bakery': 'grocery', 'Produce': 'grocery',
    'Pantry': 'grocery', 'Seafood': 'grocery', 'Household': 'grocery',
    'Electronics': 'electronics', 'Clothing': 'clothing',
}

# The store's levels, top first. A one-floor store; a manager can rename them
# or, on an empty layout, add a Floor level above.
LEVELS = ['Zone', 'Aisle', 'Bay']

ZONES = [
    ('zone-grocery', None, 'Grocery hall', 1, 1),
    ('zone-tech', None, 'Electronics', 1, 2),
    ('zone-apparel', None, 'Apparel', 1, 3),
]
AISLES = {
    'A': ('zone-grocery', 'Aisle A - Chilled'),
    'B': ('zone-grocery', 'Aisle B - Bakery'),
    'C': ('zone-grocery', 'Aisle C - Produce'),
    'D': ('zone-grocery', 'Aisle D - Pantry'),
    'E': ('zone-grocery', 'Aisle E - Fresh counter'),
    'F': ('zone-tech', 'Aisle F - Electronics'),
    'G': ('zone-apparel', 'Aisle G - Apparel'),
}

USERS = [
    ('rchen', 'rodrigo.chen@sjsu.edu', 'Rodrigo Chen', 'manager'),
    ('hbhalala', 'het.bhalala@sjsu.edu', 'Het Bhalala', 'staff'),
    ('pgala', 'parth.gala@sjsu.edu', 'Parth Gala', 'staff'),
    ('amutyala', 'aruna.mutyala@sjsu.edu', 'Aruna Samhitha Mutyala', 'viewer'),
]

SHAP_FEATURES = [
    'days_to_expiry', 'units_on_hand', 'sales_velocity', 'competitor_price',
    'day_of_week', 'margin_headroom',
]


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def q(s):
    if s is None:
        return 'NULL'
    return "'" + str(s).replace("'", "''") + "'"


def ts(dt: datetime) -> str:
    """Emit a timestamp RELATIVE TO now(), not an absolute one.

    This matters more than it looks. An absolute `expires_at` means the review
    queue is empty on every day after the one the seed was generated on — the
    housekeeping timer lapses all twelve proposals within thirty seconds of the
    first `make up`, and the console's primary screen comes up blank. Relative
    offsets give a freshly cloned repo a store that looks like 14:05 today,
    whenever today is.
    """
    delta = (dt - NOW).total_seconds()
    if abs(delta) < 1:
        return 'now()'
    sign = '+' if delta > 0 else '-'
    return f"now() {sign} interval '{abs(delta):.0f} seconds'"


def as_date(dt_or_date) -> str:
    """A date, also relative to now() for the same reason."""
    if isinstance(dt_or_date, datetime):
        days = (dt_or_date - NOW).days
    else:
        days = (dt_or_date - NOW.date()).days
    if days == 0:
        return '(now())::date'
    sign = '+' if days > 0 else '-'
    return f"(now() {sign} interval '{abs(days)} days')::date"


def det_uuid(*parts) -> str:
    """Stable UUIDs, so re-running the generator produces no diff noise."""
    h = hashlib.sha256('|'.join(str(p) for p in parts).encode()).digest()
    return str(uuid.UUID(bytes=h[:16], version=4))


def main() -> None:
    rng = random.Random(295)
    out: list[str] = []
    w = out.append

    pid_by_name: dict[str, str] = {}
    prod_by_name: dict[str, list] = {}
    for i, p in enumerate(PROD, start=1):
        pid_by_name[p[0]] = f'SKU-{i:03d}'
        prod_by_name[p[0]] = p

    rule_by_cat = {c[0]: c for c in CATEGORY_RULES}

    w("""-- ShelfIQ sample catalogue. Applied automatically on first container start.
--
-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  EVERY ROW IN THIS FILE IS SAMPLE DATA.                                  ║
-- ║                                                                          ║
-- ║  It is the design prototype's own catalogue, transcribed so the console   ║
-- ║  has something real-shaped to render before Layers 1-4 exist. Price       ║
-- ║  history is synthesised around each product's current price. No figure    ║
-- ║  here was measured in a shop.                                            ║
-- ║                                                                          ║
-- ║  The API reports `sample_data: true` on /api/meta while this seed is      ║
-- ║  present, and the console shows a banner saying so. Delete the marker     ║
-- ║  row at the bottom of this file once real data lands.                     ║
-- ╚══════════════════════════════════════════════════════════════════════════╝
--
-- Generated by tools/gen_seed.py - edit that, not this.

BEGIN;

-- ── Store settings ──────────────────────────────────────────────────────────
-- Onboarded already, so the demo opens on the Dashboard, not the setup wizard.
INSERT INTO store_settings (id, store_name, store_code, store_type, timezone, currency,
                            mqtt_host, mqtt_port, mqtt_tls, label_stale_secs,
                            onboarded_at)
VALUES (TRUE, 'SJSU Market', 'STORE-04', 'grocery', 'America/Los_Angeles', 'USD',
        'mosquitto', 1883, FALSE, 4500, now());

-- ── People ──────────────────────────────────────────────────────────────────
-- password_hash '!' is an unusable sentinel: no password can ever verify
-- against it. dashboard-api sets a real Argon2id hash for SHELFIQ_BOOTSTRAP_USER
-- on startup from the value in .env. Everyone else must be sent an invite, which
-- is why no committed file in this repo contains a working credential.
""")

    w('INSERT INTO users (username, email, full_name, password_hash, role) VALUES')
    rows = [
        f"  ({q(u)}, {q(e)}, {q(n)}, '!', {q(r)})"
        for u, e, n, r in USERS
    ]
    w(',\n'.join(rows) + ';\n')

    # ── Locations ────────────────────────────────────────────────────────────
    w('-- ── Locations ───────────────────────────────────────────────────────────────')
    w('INSERT INTO location_levels (depth, name) VALUES')
    w(',\n'.join(f'  ({d}, {q(n)})' for d, n in enumerate(LEVELS, start=1)) + ';\n')
    w('INSERT INTO locations (location_id, parent_id, name, depth, sort_order) VALUES')
    loc_rows = [
        f'  ({q(lid)}, {q(parent)}, {q(name)}, {depth}, {order})'
        for lid, parent, name, depth, order in ZONES
    ]
    for n, (letter, (zone, name)) in enumerate(sorted(AISLES.items()), start=1):
        loc_rows.append(f"  ({q('aisle-' + letter.lower())}, {q(zone)}, {q(name)}, 2, {n})")

    # Bays, derived from the slot codes rather than invented: "A3-04" is bay 3
    # of aisle A, position 4. That gives the third level real structure instead
    # of one synthetic "Bay 1" hung under every aisle.
    slot_codes = {lab[2] for lab in LAB if lab[2]} | {s[1] for s in SUG if s[1]}
    bays: dict[str, tuple[str, str]] = {}
    for code in slot_codes:
        letter, bay_digit = code[0], code[1]
        bays[f'bay-{letter.lower()}-{bay_digit}'] = (
            'aisle-' + letter.lower(), f'Bay {letter}{bay_digit}')
    for n, (bid, (parent, name)) in enumerate(sorted(bays.items()), start=1):
        loc_rows.append(f"  ({q(bid)}, {q(parent)}, {q(name)}, 3, {n})")
    w(',\n'.join(loc_rows) + ';\n')

    # Slots hang off their bay, the lowest level, which is the only level the
    # router lets a slot attach to.
    slots = {c: f'bay-{c[0].lower()}-{c[1]}' for c in slot_codes}

    w('INSERT INTO location_slots (slot_code, location_id, sort_order) VALUES')
    w(',\n'.join(
        f'  ({q(code)}, {q(loc)}, {n})'
        for n, (code, loc) in enumerate(sorted(slots.items()), start=1)
    ) + ';\n')

    # ── Category rules + change limits ───────────────────────────────────────
    w('-- ── Pricing rules ───────────────────────────────────────────────────────────')
    w('INSERT INTO category_rules (category, ceiling_pct, min_margin_pct,'
      ' agent_may_suggest, set_by) VALUES')
    w(',\n'.join(
        f'  ({q(c)}, {ce}, {mm}, {str(ag).upper()}, \'rchen\')'
        for c, ce, mm, ag in CATEGORY_RULES
    ) + ';\n')
    w('INSERT INTO change_limits (id, max_change_pct, max_changes_per_day,'
      ' min_hours_between, proposal_ttl_hours, set_by)')
    w("VALUES (TRUE, 20, 3, 4, 4, 'rchen');\n")

    # ── Products ─────────────────────────────────────────────────────────────
    w('-- ── Catalogue ───────────────────────────────────────────────────────────────')
    w('INSERT INTO products (product_id, name, display_name, sku, upc, brand, supplier,')
    w('                      category, unit_cost, stock_on_hand, field_pack,')
    w('                      min_price, max_price, auto_approve_pct, policy_set_by,')
    w('                      policy_set_at, agent_excluded) VALUES')
    prod_rows = []
    bands: dict[str, tuple[Decimal, Decimal, Decimal | None]] = {}
    for i, p in enumerate(PROD, start=1):
        pid = f'SKU-{i:03d}'
        name, display, cat, price, cost = p[0], p[1], p[2], money(p[3]), money(p[4])
        rule = rule_by_cat[cat]
        # The band is derived from the category rule, not picked by hand:
        #   floor  = the price at which the category margin floor is exactly met
        #   ceiling = current price + the category's permitted upward headroom
        # rule = (category, ceiling_pct, min_margin_pct, agent_may_suggest)
        min_price = money(cost / (Decimal(1) - Decimal(rule[2]) / Decimal(100)))
        max_price = money(price * (Decimal(1) + Decimal(rule[1]) / Decimal(100)))
        # Big-ticket lines always ask; small grocery moves are delegated. That is
        # a policy choice a manager makes, so it is seeded as one rather than
        # applied as a formula.
        auto = None if price > money(50) else money(5)
        bands[pid] = (min_price, max_price, auto)
        prod_rows.append(
            f'  ({q(pid)}, {q(name)}, {q(display)}, {q(p[16])}, {q(p[10])}, {q(p[14])},'
            f' {q(p[15])}, {q(cat)}, {cost}, {p[5]}, {q(PACK_BY_CATEGORY[cat])},'
            f' {min_price}, {max_price}, {auto if auto is not None else "NULL"},'
            f" 'rchen', {ts(NOW - timedelta(days=21))}, FALSE)"
        )
    w(',\n'.join(prod_rows) + ';\n')

    # The store's category list is whatever the sample catalogue uses.
    w('INSERT INTO store_categories (name, sort_order)')
    w('SELECT category, row_number() OVER (ORDER BY category)')
    w('  FROM (SELECT DISTINCT category FROM products WHERE category IS NOT NULL) c;\n')

    # ── Product pack fields ──────────────────────────────────────────────────
    w('-- Field-pack values. Expiry is the one the agent actually reads.')
    w('INSERT INTO product_fields (product_id, pack, values) VALUES')
    pf_rows = []
    for i, p in enumerate(PROD, start=1):
        pid = f'SKU-{i:03d}'
        pack = PACK_BY_CATEGORY[p[2]]
        if pack == 'grocery':
            # jsonb_build_object rather than a literal, so the expiry date is
            # an expression evaluated at install time like every other
            # timestamp in this file.
            sellby = as_date(NOW + timedelta(days=p[8] or 0))
            vals = (
                "jsonb_build_object('best_before', %s::text,"
                " 'country_of_origin', 'United Kingdom',"
                " 'units_per_day', %s)" % (sellby, p[11])
            )
        elif pack == 'electronics':
            vals = q('{"warranty_months": 24, "serial_tracking": "per_unit"}') + '::jsonb'
        else:
            vals = q('{"season": "AW26"}') + '::jsonb'
        pf_rows.append(f"  ({q(pid)}, {q(pack)}, {vals})")
    w(',\n'.join(pf_rows) + ';\n')

    # ── Shelf labels ─────────────────────────────────────────────────────────
    w('-- ── Shelf labels ────────────────────────────────────────────────────────────')
    w('-- Every third label is a Heltec Vision Master E213 (e-ink, ESP32); the rest')
    w('-- are 16x2 character LCDs. Firmware strings differ because the devices do.')
    w('INSERT INTO shelf_labels (label_id, kind, product_id, slot_code, status,')
    w('                          last_seen_at, rssi_dbm, battery_pct, firmware, paired_at) VALUES')
    lab_rows = []
    label_kind: dict[str, str] = {}
    for n, lab in enumerate(LAB):
        lid, pname, slot, status, seen_s, rssi, batt, fw = lab
        kind = 'eink' if n % 3 == 1 else 'lcd'
        label_kind[lid] = kind
        firmware = ('e213-' + fw) if kind == 'eink' else fw
        pid = pid_by_name.get(pname) if pname else None
        paired = ts(NOW - timedelta(days=30)) if pid else 'NULL'
        lab_rows.append(
            f'  ({q(lid)}, {q(kind)}, {q(pid)}, {q(slot)}, {q(status)},'
            f' {ts(NOW - timedelta(seconds=seen_s))}, {rssi}, {batt},'
            f' {q(firmware)}, {paired})'
        )
    w(',\n'.join(lab_rows) + ';\n')

    # Each labelled product lives in the bay its label hangs in. Products with no
    # label stay unassigned, which is the state a real catalogue starts in.
    placed = sorted(
        (pid_by_name[pname], slots[slot])
        for _, pname, slot, *_ in LAB
        if pname and slot and pname in pid_by_name
    )
    if placed:
        w('-- Products live where their label hangs; unlabelled ones stay unassigned.')
        w('UPDATE products p SET location_id = v.location_id FROM (VALUES')
        w(',\n'.join(f'  ({q(pid)}, {q(loc)})' for pid, loc in placed))
        w(') AS v(product_id, location_id) WHERE p.product_id = v.product_id;\n')

    w('INSERT INTO label_events (label_id, event_type, detail, ts) VALUES')
    ev_rows = []
    for lab in LAB:
        lid, _, _, status, seen_s, _, batt, fw = lab
        ev_rows.append(
            f"  ({q(lid)}, 'connected', '{{\"firmware\": \"{fw}\"}}'::jsonb,"
            f' {ts(NOW - timedelta(days=30))})'
        )
        if status == 'offline':
            ev_rows.append(
                f"  ({q(lid)}, 'disconnected', '{{\"last_rssi\": {lab[5]}}}'::jsonb,"
                f' {ts(NOW - timedelta(seconds=seen_s))})'
            )
        if batt <= 15:
            ev_rows.append(
                f"  ({q(lid)}, 'battery_low', '{{\"battery_pct\": {batt}}}'::jsonb,"
                f' {ts(NOW - timedelta(hours=6))})'
            )
    w(',\n'.join(ev_rows) + ';\n')

    # ── Price history ────────────────────────────────────────────────────────
    w('-- ── Price history ───────────────────────────────────────────────────────────')
    w('-- Synthesised: six changes over 90 days, walking backwards from each')
    w("-- product's real current price. The last step uses the product's own")
    w('-- recorded last-change percentage so the up/down filters mean something.')
    w('INSERT INTO price_history (product_id, price, currency, decision_id,'
      ' reason_tag, approval, approved_by, agent_version, ts) VALUES')
    hist_rows = []
    decided: list[tuple] = []
    for i, p in enumerate(PROD, start=1):
        pid = f'SKU-{i:03d}'
        current = money(p[3])
        last_pct = Decimal(str(p[12]))
        # Walk backwards: previous price implied by the recorded last change.
        prices: list[Decimal] = [current]
        prev = money(current / (Decimal(1) + last_pct / Decimal(100)))
        prices.append(prev)
        for _ in range(4):
            step = Decimal(str(rng.choice([-6, -4, -2.5, 2.5, 4, 6])))
            prev = money(prev / (Decimal(1) + step / Decimal(100)))
            prices.append(prev)
        prices.reverse()
        n = len(prices)
        for k, price in enumerate(prices):
            days_ago = int(90 - (90 * k / (n - 1)))
            when = NOW - timedelta(days=days_ago, hours=rng.randint(0, 9))
            if k == n - 1:
                when = NOW - timedelta(hours=rng.randint(2, 30))
            auto = k % 3 == 1
            tag = 'Opening price' if k == 0 else ('Policy applied' if auto else 'Manager set')
            hist_rows.append(
                f'  ({q(pid)}, {price}, \'USD\', NULL, {q(tag)},'
                f" {q('auto' if auto else 'manager')},"
                f" {q('policy' if auto else 'rchen')},"
                f" {q('stub-0.1' if auto else None)}, {ts(when)})"
            )
    w(',\n'.join(hist_rows) + ';\n')

    # ── Decided proposals, from the activity log ─────────────────────────────
    w('-- ── Decided proposals ───────────────────────────────────────────────────────')
    w('-- The activity log the design shows, reconstructed as what it actually is:')
    w('-- a proposal, a human decision on it, the published price, and the push.')
    prop_rows, ph_rows, push_rows, audit_rows, shap_rows = [], [], [], [], []
    for n, a in enumerate(ACT):
        when = datetime.fromisoformat(a[0]).replace(tzinfo=timezone.utc)
        pname, old, new, how, lid, result = a[1], money(a[2]), money(a[3]), a[4], a[5], a[6]
        pid = pid_by_name[pname]
        did = det_uuid('act', n, pid)
        edited = how == 'edited price'
        status = 'overridden' if edited else 'approved'
        # On an override the agent wanted something else; say so rather than
        # pretending the published price is what it proposed.
        proposed = money(new * Decimal('0.97')) if edited else new
        conf = rng.randint(62, 94)
        tag = 'Markdown' if new < old else 'Demand up'
        prop_rows.append(
            f'  ({q(did)}, {q(pid)}, {proposed}, {old}, {q(tag)}, \'stub-0.1\','
            f" {q(status)}, 'rchen', {ts(when)}, {new}, NULL,"
            f' {ts(when + timedelta(hours=4))}, {ts(when - timedelta(minutes=8))},'
            f' {Decimal(conf) / 100}, NULL)'
        )
        ph_rows.append(
            f'  ({q(pid)}, {new}, \'USD\', {q(did)}, {q(tag)},'
            f" {q('override' if edited else 'manager')}, 'rchen', 'stub-0.1', {ts(when)})"
        )
        settled = 'NULL' if result == 'pending' else ts(when + timedelta(seconds=9))
        prod = prod_by_name[pname]
        line2 = f'${new}'.ljust(9)[:9] + ('WAS ' + str(old))[:7] if new < old else f'${new}'
        push_rows.append(
            f'  ({q(lid)}, {q(pid)}, {q(did)}, {new}, {q(prod[1][:16])},'
            f' {q(line2[:16])}, {q(result)}, {ts(when + timedelta(seconds=2))}, {settled})'
        )
        audit_rows.append(
            f"  ('proposal_received', {q(pid)}, {q(did)}, 'stub-0.1', '{{}}'::jsonb,"
            f' {ts(when - timedelta(minutes=8))})'
        )
        decided_detail = '{"final_price": %s}' % new
        published_detail = '{"label_id": "%s"}' % lid
        audit_rows.append(
            f"  ({q('proposal_overridden' if edited else 'proposal_approved')},"
            f" {q(pid)}, {q(did)}, 'rchen', {q(decided_detail)}::jsonb, {ts(when)})"
        )
        audit_rows.append(
            f"  ('price_published', {q(pid)}, {q(did)}, 'rchen',"
            f' {q(published_detail)}::jsonb, {ts(when + timedelta(seconds=2))})'
        )
        shap_rows.append(shap_row(did, pid, old, proposed, rng))

    # ── Pending proposals, from SUG ──────────────────────────────────────────
    for n, s in enumerate(SUG):
        pname, slot, cur, sug, prose, conf, tag = s
        pid = pid_by_name[pname]
        did = det_uuid('sug', n, pid)
        made = NOW - timedelta(minutes=5 + n * 3)
        prop_rows.append(
            f'  ({q(did)}, {q(pid)}, {money(sug)}, {money(cur)}, {q(tag)},'
            f" 'stub-0.1', 'pending', NULL, NULL, NULL, NULL,"
            f' {ts(made + timedelta(hours=4))}, {ts(made)},'
            f' {Decimal(conf) / 100}, {q(prose)})'
        )
        audit_rows.append(
            f"  ('proposal_received', {q(pid)}, {q(did)}, 'stub-0.1', '{{}}'::jsonb,"
            f' {ts(made)})'
        )
        # The design's milk suggestion is the demo centrepiece; leave its
        # explanation missing on one other SKU so the UI's "explanation has not
        # arrived" state is reachable without editing data.
        if n != 7:
            shap_rows.append(shap_row(did, pid, money(cur), money(sug), rng))

    w('INSERT INTO proposals (decision_id, product_id, proposed_price, current_price,')
    w('                       reason_tag, agent_version, status, decided_by, decided_at,')
    w('                       final_price, decline_reason, expires_at, ts,')
    w('                       agent_confidence, rationale) VALUES')
    w(',\n'.join(prop_rows) + ';\n')

    w('INSERT INTO price_history (product_id, price, currency, decision_id,'
      ' reason_tag, approval, approved_by, agent_version, ts) VALUES')
    w(',\n'.join(ph_rows) + ';\n')

    w('INSERT INTO label_pushes (label_id, product_id, decision_id, price, line1,'
      ' line2, result, published_at, settled_at) VALUES')
    w(',\n'.join(push_rows) + ';\n')

    w('-- ── SHAP explanations ───────────────────────────────────────────────────────')
    w('-- base_value + sum(shap) = proposed_price exactly. Additivity is the one')
    w('-- property the explanation panel depends on, so it is enforced here too.')
    w('INSERT INTO shap_vectors (decision_id, product_id, base_value, shap, ts) VALUES')
    w(',\n'.join(shap_rows) + ';\n')

    # ── Inventory risk ───────────────────────────────────────────────────────
    w('-- ── Dead-stock risk, from the forecaster ────────────────────────────────────')
    w('INSERT INTO inventory_risk (product_id, dead_stock_risk, predicted_sellby,')
    w('                            units_on_hand, model_version, ts) VALUES')
    ir_rows = []
    for i, p in enumerate(PROD, start=1):
        pid = f'SKU-{i:03d}'
        stock, vel = p[5], Decimal(str(p[11]))
        days = (Decimal(stock) / vel) if vel > 0 else Decimal(999)
        expiry = p[8]
        if expiry is not None and expiry < days:
            risk = min(Decimal('0.95'), Decimal('1') - (Decimal(expiry) / (days + 1)))
        else:
            risk = max(Decimal('0.02'), min(Decimal('0.6'), days / Decimal(120)))
        sellby = as_date(NOW + timedelta(days=float(min(days, Decimal(180)))))
        ir_rows.append(
            f'  ({q(pid)}, {risk.quantize(Decimal("0.001"))}, '
            f"{sellby}, {stock}, 'prophet-stub-0.1',"
            f' {ts(NOW - timedelta(hours=1))})'
        )
    w(',\n'.join(ir_rows) + ';\n')

    w('-- ── Audit trail ─────────────────────────────────────────────────────────────')
    w("INSERT INTO agent_audit_log (event_type, product_id, decision_id, actor, detail, ts) VALUES")
    audit_rows.insert(
        0,
        f"  ('agent_started', NULL, NULL, 'stub-0.1', '{{}}'::jsonb,"
        f' {ts(NOW - timedelta(days=30))})',
    )
    for i, p in enumerate(PROD, start=1):
        pid = f'SKU-{i:03d}'
        lo, hi, auto = bands[pid]
        detail = (
            '{"min_price": %s, "max_price": %s, "auto_approve_pct": %s}'
            % (lo, hi, auto if auto is not None else 'null')
        )
        audit_rows.append(
            f"  ('guardrail_set', {q(pid)}, NULL, 'rchen', {q(detail)}::jsonb,"
            f' {ts(NOW - timedelta(days=21))})'
        )
    w(',\n'.join(audit_rows) + ';\n')

    w("""-- ── Sample-data marker ──────────────────────────────────────────────────────
-- dashboard-api reads this table. While the row exists, GET /api/meta reports
-- sample_data: true and the console shows a banner. Delete the row (or the
-- table) the moment real data starts arriving, so nobody mistakes the figures
-- above for measurements.
CREATE TABLE sample_data_marker (
    id       BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    note     TEXT NOT NULL,
    seeded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO sample_data_marker (id, note) VALUES
  (TRUE, 'Catalogue, labels, suggestions and decisions are the design prototype''s sample data. Price history is synthesised.');

COMMIT;""")

    target = Path('infra/postgres/03-seed.sql')
    target.write_text('\n'.join(out) + '\n')
    print(f'wrote {target} ({len(out)} blocks)')


def shap_row(did: str, pid: str, current: Decimal, proposed: Decimal, rng) -> str:
    """A SHAP vector whose contributions sum exactly to proposed - base."""
    base = current
    total = proposed - base
    n = rng.randint(3, 5)
    feats = rng.sample(SHAP_FEATURES, n)
    # Split `total` into n signed parts that sum to it exactly, with at least
    # one part pushing the other way so the diverging chart has both arms.
    weights = [Decimal(str(rng.uniform(0.15, 1.0))) for _ in range(n)]
    weights[-1] = -weights[-1] * Decimal('0.4')
    scale = total / sum(weights) if sum(weights) != 0 else Decimal(0)
    parts = [(w * scale).quantize(Decimal('0.0001')) for w in weights]
    parts[0] += total - sum(parts)  # absorb rounding into the largest driver
    pairs = sorted(zip(feats, parts), key=lambda fp: abs(fp[1]), reverse=True)
    arr = ', '.join(
        '{"feature": "%s", "value": %s}' % (f, v) for f, v in pairs
    )
    return (
        f'  ({q(did)}, {q(pid)}, {base.quantize(Decimal("0.0001"))},'
        f" {q('[' + arr + ']')}::jsonb, {ts(NOW - timedelta(minutes=4))})"
    )


if __name__ == '__main__':
    main()
