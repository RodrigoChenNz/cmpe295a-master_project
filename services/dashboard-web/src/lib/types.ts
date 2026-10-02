/* Shapes the API actually returns.
 *
 * **Money is a string here, everywhere, deliberately.** dashboard-api sends
 * `"3.49"`, not `3.49`, because a JSON number is a double by the time the
 * browser has parsed it and this is a system whose output lands on a price
 * label. Nothing in this app parses a price with `parseFloat` and then renders
 * the result; `money()` in format.ts formats the string it was given.
 *
 * Percentages are numbers, because nobody sums them.
 */

export type Role = 'manager' | 'staff' | 'viewer';

export interface User {
  username: string;
  full_name: string;
  role: Role;
  email: string;
  initials: string;
}

export interface Store {
  store_name: string;
  store_code: string | null;
  timezone: string;
  currency: string;
  mqtt_host: string;
  mqtt_port: number;
  mqtt_tls: boolean;
  label_stale_secs: number;
}

export interface BrokerState {
  connected: boolean;
  since: string | null;
  error: string | null;
  enabled: boolean;
  queued: number;
}

export interface Meta {
  user: User | null;
  store: Store | null;
  counts: {
    pending: number;
    labels_failed: number;
    labels_offline: number;
    out_of_stock: number;
  };
  broker: BrokerState;
  /** Non-null while the seed is in place. Drives the banner that says so. */
  sample_data: { note: string; seeded_at: string } | null;
}

/* ── Products ─────────────────────────────────────────────────────────── */

export type StockHealth = 'in' | 'low' | 'out';
export type PriceReview = 'review' | 'up' | 'down';
export type SavedView = 'all' | 'low' | 'review';
export type LabelKind = 'lcd' | 'eink';
export type LabelStatus = 'online' | 'syncing' | 'failed' | 'offline' | 'unassigned';

export interface Product {
  product_id: string;
  name: string;
  display_name: string | null;
  sku: string | null;
  upc: string | null;
  brand: string | null;
  supplier: string | null;
  category: string | null;
  unit_cost: string;
  stock_on_hand: number | null;
  image_url: string | null;
  field_pack: string | null;
  min_price: string | null;
  max_price: string | null;
  auto_approve_pct: number | null;
  agent_excluded: boolean;
  current_price: string | null;
  priced_at: string | null;
  previous_price: string | null;
  margin_pct: number | null;
  last_change_dir: 'up' | 'down' | 'flat' | null;
  stock_health: StockHealth | null;
  pending_count: number;
  label_id: string | null;
  label_kind: LabelKind | null;
  label_status: LabelStatus | null;
  slot_code: string | null;
  dead_stock_risk: number | null;
  predicted_sellby: string | null;
}

export interface Facets {
  categories: { category: string; count: number }[];
  stock: Partial<Record<StockHealth, number>>;
  views: Record<SavedView, number>;
  review: Record<PriceReview, number>;
  total: number;
}

export interface PricePoint {
  price: string;
  ts: string;
  reason_tag: string;
  approval: 'auto' | 'manager' | 'override';
  approved_by: string;
  decision_id: string | null;
}

export interface ShelfRender {
  line1: string;
  line2: string;
}

export interface ProductDetail {
  product: Product;
  fields: { pack: string; values: Record<string, unknown> } | null;
  price_history: PricePoint[];
  decisions: Decision[];
  label: Label | null;
  shelf_render: ShelfRender;
}

/* ── Suggestions ──────────────────────────────────────────────────────── */

export interface ShapEntry {
  feature: string;
  value: number;
}

/** Where the "Why" prose came from. The agent contract has no rationale field,
 *  so when the publisher sent none the API composes a sentence and marks it
 *  `composed` — the UI says which, rather than implying the agent wrote it. */
export interface Why {
  text: string;
  source: 'agent' | 'composed';
}

export interface Suggestion {
  decision_id: string;
  product_id: string;
  name: string;
  sku: string | null;
  display_name: string | null;
  category: string | null;
  brand: string | null;
  unit_cost: string;
  stock_on_hand: number | null;
  image_url: string | null;
  current_price: string;
  proposed_price: string;
  delta: string;
  delta_pct: number;
  reason_tag: string;
  agent_version: string;
  expires_at: string;
  ts: string;
  min_price: string | null;
  max_price: string | null;
  auto_approve_pct: number | null;
  out_of_band: boolean;
  base_value: string | null;
  shap: ShapEntry[] | null;
  /** A proposal with no explanation is a normal state, not an error. */
  explanation_missing: boolean;
  agent_confidence: number | null;
  confidence_reported: boolean;
  rationale: string | null;
  proposed_margin_pct: number | null;
  category_min_margin_pct: number | null;
  label_id: string | null;
  label_kind: LabelKind | null;
  label_status: LabelStatus | null;
  slot_code: string | null;
  why: Why;
  shelf_render: ShelfRender;
}

export type ProposalStatus =
  | 'pending'
  | 'auto_approved'
  | 'approved'
  | 'overridden'
  | 'rejected'
  | 'expired'
  | 'superseded';

export interface Decision {
  decision_id: string;
  status: ProposalStatus;
  proposed_price: string;
  current_price?: string;
  final_price: string | null;
  reason_tag: string;
  rationale?: string | null;
  agent_confidence?: number | null;
  decided_by: string | null;
  decided_at: string | null;
  ts: string;
}

export interface CategoryRule {
  category: string;
  floor_pct: number | null;
  ceiling_pct: number | null;
  min_margin_pct: number;
  agent_may_suggest: boolean;
  set_by: string | null;
  updated_at: string;
  product_count?: number;
}

export interface SuggestionDetail {
  suggestion: Suggestion;
  price_history: PricePoint[];
  earlier_decisions: Decision[];
  category_rules: CategoryRule | null;
}

export interface ApproveResult {
  decision_id: string;
  product_id: string;
  status: 'approved' | 'overridden';
  final_price: string;
  agent_proposed: string;
  /** False means approved and audited but still queued for the broker. The UI
   *  must not say "on the shelf" in that case. */
  published: boolean;
  queued: number;
}

/* ── Labels ───────────────────────────────────────────────────────────── */

export interface Label {
  label_id: string;
  kind: LabelKind;
  product_id?: string | null;
  product_name?: string | null;
  display_name?: string | null;
  slot_code: string | null;
  location_name?: string | null;
  status: LabelStatus;
  last_seen_at: string | null;
  rssi_dbm: number | null;
  battery_pct: number | null;
  firmware: string | null;
  last_price?: string | null;
  as_published_at?: string | null;
  last_push_result?: 'pending' | 'confirmed' | 'failed' | null;
}

/* ── Activity ─────────────────────────────────────────────────────────── */

export interface ActivityRow {
  id: number;
  ts: string;
  product_id: string;
  product_name: string;
  category: string | null;
  old_price: string | null;
  new_price: string;
  change_pct: number | null;
  reason_tag: string;
  approval: 'auto' | 'manager' | 'override';
  approved_by: string;
  approved_by_name: string | null;
  decision_id: string | null;
  agent_version: string | null;
  label_id: string | null;
  shelf_result: 'pending' | 'confirmed' | 'failed' | null;
  shelf_settled_at: string | null;
}

/* ── Dashboard ────────────────────────────────────────────────────────── */

export interface AttentionItem {
  priority: 'High' | 'Medium' | 'Low';
  kind: string;
  count: number;
  title: string;
  detail: string;
  /** An instant the detail refers to, carried separately so the browser can
   *  render it in the viewer's own timezone instead of showing a UTC string. */
  detail_at?: string | null;
  detail_at_prefix?: string | null;
  action: string;
  route: string;
}

export interface Signal {
  key: string;
  label: string;
  value: number | null;
  unit: 'count' | 'percent';
  points: (number | null)[];
  days: string[];
  note: string;
}

export interface DashboardData {
  snapshot: {
    products: number;
    labels_paired: number;
    labels_online: number;
    labels_failed: number;
    labels_offline: number;
    pending: number;
    out_of_stock: number;
    low_stock: number;
    margin_pct: number | null;
  };
  pricing_health: {
    score: number | null;
    label_health_pct: number | null;
    queue_drag_pct: number;
    definition: string;
  };
  attention: AttentionItem[];
  signals: Signal[];
  recent: (ActivityRow & { slot_code: string | null })[];
  range_days: number;
}

/* ── Pagination ───────────────────────────────────────────────────────── */

export interface Page<T> {
  rows: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

/* ── Stream ───────────────────────────────────────────────────────────── */

export interface StreamEvent {
  type:
    | 'hello'
    | 'ping'
    | 'resync'
    | 'broker'
    | 'price'
    | 'proposal'
    | 'explain'
    | 'inventory'
    | 'expired'
    | 'labels'
    | 'products'
    | 'suggestions'
    | 'settings'
    | 'rules'
    | 'users'
    | 'guardrail';
  ts?: string;
  [key: string]: unknown;
}
