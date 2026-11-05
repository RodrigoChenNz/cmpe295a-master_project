import { request } from "./api";

export interface Page<T> {
  rows: T[];
  total: number;
  page: number;
  pages: number;
}
export interface Line {
  id: string;
  item: { id: string; title: string; price: number };
  quantity: number;
  fulfilled: number;
  delivered: number;
  canceled: number;
  returned: number;
}
export interface Checkout {
  id: string;
  status: string;
  line_items: Line[];
  totals: { type: string; amount: number }[];
  messages: { content: string }[];
  buyer: { email: string } | null;
  fulfillment: { method: string; address?: string } | null;
  channel: "storefront" | "ucp";
  order?: { id: string };
  expires_at: string;
}
export interface Order {
  adjustments?: { id: string; description: string; totals: {type: string; amount: number}[] }[];
  id: string;
  status: string;
  line_items: Line[];
  totals: { type: string; amount: number }[];
  buyer: { email: string };
  fulfillment: {
    method: string;
    address?: string;
    events: { id: string; type: string; tracking_number?: string }[];
  };
}
export interface OrderRow {
  order_id: string;
  status: string;
  channel: string;
  payload: Order;
  created_at: string;
}
export interface Product {
  product_id: string;
  name: string;
  online: boolean;
  description: string;
  online_price: string | null;
  images: string[];
  supplier_id: string | null;
  stock_on_hand: number | null;
  unit_cost: string;
  current_price: string | null;
}
export interface Supplier {
  supplier_id: string;
  name: string;
  lead_time_days: number;
}
export interface POLine {
  product_id: string;
  quantity: number;
  received: number;
  unit_cost: string;
}
export interface Purchase {
  po_id: string;
  status: string;
  supplier_id: string;
  supplier_order_id: string | null;
  lines: POLine[];
  created_at: string;
}
export interface Market {
  product_id: string;
  competitor_min: string;
  competitor_median: string;
  last_observed: string;
  observations: number;
}
export interface Movement {
  movement_id: string;
  product_id: string;
  type: string;
  quantity: number;
  note: string;
  created_at: string;
}
export interface Ledger {
  entry_id: string;
  type: string;
  amount: string;
  cogs: string;
  product_id: string | null;
  memo: string;
  created_at: string;
}
export interface Summary {
  revenue: string;
  cogs: string;
  gross_profit: string;
  refunds: string;
  purchases: string;
  margin_pct: number | null;
  units: number;
  orders: number;
  basis: string;
  groups: { label: string; revenue: string; cogs: string; purchases: string }[];
}
export interface Run {
  run_id: string;
  status: string;
  summary: string;
  created_at: string;
  steps: { name: string; status: string; summary?: string }[];
}
export interface Settings {
  commerce_enabled: boolean;
  nightly_enabled: boolean;
  price_delivery_mode: "immediate" | "daily";
  nightly_run_time: string;
  shelf_update_time: string;
  shipping_minor: number;
  tax_bps: number;
  timezone?: string;
}
export interface Policy {
  purchase_order: { mode: "ask" | "auto"; auto_max_total: string };
  deal: {
    mode: "ask" | "auto";
    min_margin_pct: string | number;
    max_discount_pct: string | number;
    min_quantity: number;
  };
  listing: { mode: "ask" | "auto" };
}
export interface Decision {
  decision_id: string;
  kind: "price" | "purchase_order" | "deal" | "listing";
  status: string;
  subject: Record<string, string>;
  proposal: Record<string, unknown>;
  explanation: {
    summary?: string;
    factors?: { name: string; value?: string | number; effect?: string }[];
  } | null;
  verdict: { reason?: string; actor: string; tags?: string[] } | null;
  expires_at: string;
}
export interface CatalogProduct {
  id: string;
  title: string;
  description: { plain: string };
  variants: {
    price: { amount: number; currency: string };
    availability: { available: boolean };
  }[];
}
export interface Offer {
  offer_id: string;
  status: string;
  decision_id: string;
  payload: {
    line_items: {
      id: string;
      quantity: number;
      unit_price: { amount: number; currency: string };
    }[];
    counter: {
      line_items: {
        id: string;
        quantity: number;
        unit_price: { amount: number; currency: string };
      }[];
    } | null;
    response_note: string;
  };
}

export const get = <T>(path: string) => request<T>(path);
export const mutate = <T>(path: string, body: unknown = {}, method = "POST") =>
  request<T>(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify(body),
  });
function token() {
  let value = sessionStorage.getItem("shelfiq-buyer");
  if (!value) {
    value = crypto.randomUUID() + crypto.randomUUID();
    sessionStorage.setItem("shelfiq-buyer", value);
  }
  return value;
}
export const shop = <T>(
  path: string,
  body?: unknown,
  method = body === undefined ? "GET" : "POST",
) =>
  request<T>(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token()}`,
      "Idempotency-Key": crypto.randomUUID(),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
export const usd = (minor: number) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
    minor / 100,
  );
export const when = (value: string) => new Date(value).toLocaleString();
