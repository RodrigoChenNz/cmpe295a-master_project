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

export type Role = 'superadmin' | 'manager' | 'staff' | 'viewer';

/** Who an invite link is for, so the join screen can greet them. */
export interface InviteInfo {
  username: string;
  email: string;
  full_name: string;
  role: Role;
}

export interface InviteIssued {
  username: string;
  invite_token: string;
  invite_path: string;
  expires_at: string;
}

export interface User {
  username: string;
  full_name: string;
  role: Role;
  email: string;
  initials: string;
}

export type StoreType =
  | 'grocery' | 'convenience' | 'pharmacy' | 'electronics' | 'hardware' | 'apparel' | 'other';

export interface Store {
  /** Null until first-run onboarding names the store. */
  store_name: string | null;
  store_code: string | null;
  store_type: StoreType | null;
  timezone: string;
  /** Always "USD": the shared price contract allows nothing else. */
  currency: string;
  /** The broker fields reach a super admin only. */
  mqtt_host?: string;
  mqtt_port?: number;
  mqtt_tls?: boolean;
  label_stale_secs: number;
  /** Set when the super admin finished the store setup wizard. */
  onboarded_at: string | null;
}

/** What the sign-in screen may show before anyone is signed in. */
export interface StoreBrand {
  name: string;
  code: string | null;
}

export interface BrokerState {
  connected: boolean;
  since: string | null;
  error: string | null;
  enabled: boolean;
  queued: number;
  /** The bridge's last traffic in either direction — not the connect time. */
  last_message_at?: string | null;
  /** What the running service is connected to, which can differ from what
   *  `store_settings` holds until it restarts. */
  host?: string;
  port?: number;
  tls?: boolean;
}

export interface Meta {
  user: User | null;
  store: Store | null;
  counts: {
    pending: number;
    labels_failed: number;
    labels_offline: number;
    out_of_stock: number;
    /** Products not linked to any location yet. */
    unassigned_products: number;
    products: number;
    locations: number;
  };
  broker: BrokerState;
  /** Non-null while the seed is in place. Drives the banner that says so. */
  sample_data: { note: string; seeded_at: string } | null;
  /** The store's own level names. Nothing else can be set up until
   *  `configured` is true. */
  layout: { configured: boolean; levels: LocationLevel[] };
  onboarding: { completed: boolean };
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
  /** Where the product lives in the store, at any level. Null until linked. */
  location_id: string | null;
  location_path: string | null;
  dead_stock_risk: number | null;
  predicted_sellby: string | null;
}

export interface Facets {
  categories: { category: string; count: number }[];
  stock: Partial<Record<StockHealth, number>>;
  views: Record<SavedView, number>;
  review: Record<PriceReview, number>;
  unassigned: number;
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
  /** Present only on a create that carried an opening price. False means the
   *  price is recorded and audited but still queued for the broker, so the UI
   *  must not say it reached a shelf. */
  price_published?: boolean;
}

/** The Add product form's payload.
 *
 * No `product_id`: that is the frozen contract key and the server allocates it.
 * `unit_cost` and `price` are strings — money is a string end to end, and
 * pydantic parses them straight to Decimal.
 */
export interface NewProductBody {
  name: string;
  display_name: string | null;
  sku: string | null;
  upc: string | null;
  brand: string | null;
  supplier: string | null;
  category: string | null;
  unit_cost: string;
  stock_on_hand: number | null;
  /** The opening price. Manager only; the API refuses it from anyone else. */
  price: string | null;
  agent_excluded: boolean;
  location_id?: string | null;
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

/** One of the store's own categories, as the Add product form offers them. */
export interface StoreCategory {
  name: string;
  product_count: number;
}

export interface CategoryRule {
  category: string;
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
  location_path?: string | null;
  status: LabelStatus;
  last_seen_at: string | null;
  rssi_dbm: number | null;
  battery_pct: number | null;
  firmware: string | null;
  paired_at?: string | null;
  created_at?: string;
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
    | 'locations'
    | 'categories'
    | 'guardrail';
  ts?: string;
  [key: string]: unknown;
}


/* ── Locations ────────────────────────────────────────────────────────────── */

/** One level of the store's layout. Depth 1 is the top; positions go on the
 *  deepest. The names are the store's own (Floor, Zone, Aisle, Bay …). */
export interface LocationLevel {
  depth: number;
  name: string;
}

export interface LocationNode {
  location_id: string;
  parent_id: string | null;
  name: string;
  depth: number;
  level_name: string;
  sort_order: number;
  /** Slots directly on this node. */
  slot_count: number;
  filled_count: number;
  /** Slots anywhere beneath it — what a person means by a branch's count. */
  descendant_slots: number;
  descendant_filled: number;
  /** Products linked to this node, and to it plus everything beneath it. */
  product_count: number;
  descendant_products: number;
  path: string;
  children: LocationNode[];
}

export interface LocationSlot {
  slot_code: string;
  location_id: string;
  sort_order: number;
  path: string;
  label_id: string | null;
  label_kind: LabelKind | null;
  label_status: LabelStatus | null;
  last_seen_at: string | null;
  product_id: string | null;
  product_name: string | null;
  brand: string | null;
  category: string | null;
  current_price: string | null;
}

export interface LocationTree {
  levels: LocationLevel[];
  tree: LocationNode[];
  slots: LocationSlot[];
  totals: {
    /** Node count per depth, keyed by the depth as a string. */
    by_depth: Record<string, number>;
    slots: number;
    empty_slots: number;
    products: number;
  };
}

export interface LocationImpact {
  location_id: string;
  name: string;
  depth: number;
  locations: number;
  slots: number;
  labels: number;
  paired_labels: number;
  /** Products linked anywhere in the subtree; they become unassigned. */
  products: number;
  label_ids: string[];
}

/* ── Label detail ─────────────────────────────────────────────────────────── */

export interface LabelPush {
  id: number;
  price: string;
  line1: string;
  line2: string;
  result: 'pending' | 'confirmed' | 'failed';
  published_at: string;
  settled_at: string | null;
  decision_id: string | null;
}

export interface LabelEvent {
  event_type: string;
  detail: Record<string, unknown>;
  ts: string;
}

export interface LabelDetail {
  label: Label & {
    product_name?: string | null;
    brand?: string | null;
    unit_cost?: string | null;
    location_name?: string | null;
    location_path?: string | null;
    product_location_id?: string | null;
    product_location_path?: string | null;
  };
  pushes: LabelPush[];
  events: LabelEvent[];
  shelf_render: ShelfRender | null;
  as_published_at: string | null;
  writes_today?: number;
}

export interface BlinkResult {
  label_id: string;
  published: boolean;
  queued: number;
  note: string;
}

/* ── Users ────────────────────────────────────────────────────────────────── */

export interface UserRow {
  username: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
  /** False while the account still holds the unusable seed hash. */
  can_sign_in: boolean;
  decisions: number;
  /** Rules rows that reference this user, which block a hard delete. */
  policy_rows: number;
}

/* ── Rules ────────────────────────────────────────────────────────────────── */

export interface ChangeLimits {
  max_change_pct: number;
  max_changes_per_day: number;
  min_hours_between: number;
  proposal_ttl_hours: number;
  set_by: string | null;
  updated_at: string;
}

export interface RulesResponse {
  limits: ChangeLimits | null;
  categories: CategoryRule[];
  unconfigured: { category: string; product_count: number }[];
}

export interface RulesPreview {
  category: string;
  products: number;
  with_price: number;
  without_price: number;
  would_publish: number;
  skipped_no_band: number;
  min_price_changes: number;
  max_price_changes: number;
  individually_excluded: number;
  would_unexclude: number;
  would_exclude: number;
  applies_agent_flag: boolean;
  examples: {
    product_id: string;
    name: string;
    min_price: string | null;
    new_min_price: string | null;
    max_price: string | null;
    new_max_price: string | null;
  }[];
}

/* ── Settings ─────────────────────────────────────────────────────────────── */

export interface SettingsResponse {
  store: Store | null;
  broker: BrokerState;
  /** Super admin only, like the broker fields on `store`. */
  effective?: { host?: string; port?: number; tls?: boolean };
  /** The stored broker config differs from what the bridge is using. */
  restart_required?: boolean;
}

export interface MqttTestResult {
  ok: boolean;
  probe: 'mqtt-connect' | 'tcp';
  host: string;
  port: number;
  tls: boolean;
  elapsed_ms: number;
  detail: string;
}
