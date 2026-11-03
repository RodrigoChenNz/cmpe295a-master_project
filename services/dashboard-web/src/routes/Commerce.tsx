import { useCallback, useEffect, useState, type ReactNode } from "react";
import { NavLink, useParams } from "react-router-dom";
import { usePageChrome, useShell } from "../components/AppShell";
import {
  get,
  mutate,
  usd,
  when,
  type Decision,
  type Ledger,
  type Market,
  type Movement,
  type OrderRow,
  type Page,
  type Policy,
  type Product,
  type Purchase,
  type Run,
  type Settings,
  type Summary,
  type Supplier,
} from "../lib/commerce";
import s from "./Commerce.module.css";

function useData<T>(url: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setData(await get<T>(url));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [url]);
  useEffect(() => {
    void load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);
  return { data, error, load };
}
function usePaged<T>(url: string) {
  const [page, setPage] = useState(1);
  const result = useData<Page<T>>(
    url + (url.includes("?") ? "&" : "?") + "page=" + page,
  );
  const pager = (
    <div className={s.actions}>
      <button disabled={page === 1} onClick={() => setPage(page - 1)}>
        Previous page
      </button>
      <span>
        Page {page} of {result.data?.pages || 1} · {result.data?.total || 0}{" "}
        records
      </span>
      <button
        disabled={page >= (result.data?.pages || 1)}
        onClick={() => setPage(page + 1)}
      >
        Next page
      </button>
    </div>
  );
  return { ...result, pager };
}
function ErrorText({ text }: { text: string }) {
  return text ? (
    <p role="alert" className={s.error}>
      {text}
    </p>
  ) : null;
}
function useAction(refresh: () => Promise<void>) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      setError("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return { run, error, busy };
}
function Table({
  headers,
  children,
}: {
  headers: string[];
  children: ReactNode;
}) {
  return (
    <table>
      <thead>
        <tr>
          {headers.map((h) => (
            <th key={h}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
}
const titles: Record<string, string> = {
  overview: "Commerce settings",
  decisions: "Decisions inbox",
  agent: "Store agent",
  market: "Market research",
  orders: "Orders",
  offers: "Bulk offers",
  purchases: "Purchase orders",
  inventory: "Inventory",
  accounting: "Accounting",
};

function DecisionRow({
  d,
  refresh,
  manager,
}: {
  d: Decision;
  refresh: () => Promise<void>;
  manager: boolean;
}) {
  const { run, error, busy } = useAction(refresh);
  const [reason, setReason] = useState("");
  const [tag, setTag] = useState("agree");
  const [override, setOverride] = useState("");
  const p = d.proposal;
  async function verdict(action: string) {
    let changes: Record<string, unknown> = {};
    if (action === "override") {
      if (d.kind === "price") changes = { price: override };
      else if (d.kind === "deal") {
        const lines = p.line_items as {
          id: string;
          quantity: number;
          unit_price: { amount: number; currency: string };
        }[];
        changes = {
          counter_line_items: lines.map((l) => ({
            ...l,
            unit_price: {
              amount: Math.round(Number(override) * 100),
              currency: "USD",
            },
          })),
        };
      } else if (d.kind === "purchase_order") {
        const lines = p.lines as {
          product_id: string;
          quantity: number;
          unit_cost: string;
        }[];
        changes = {
          lines: lines.map((l) => ({ ...l, quantity: Number(override) })),
        };
      } else changes = { fields: { description: override } };
    }
    await mutate("/api/decisions/" + d.decision_id + "/verdict", {
      action,
      changes,
      reason,
      tags: [tag],
    });
  }
  return (
    <article className={s.card}>
      <div className={s.actions}>
        <strong>{d.kind.replace("_", " ").toUpperCase()}</strong>
        <span>{d.status}</span>
      </div>
      <p className={s.small}>{Object.values(d.subject).join(" · ")}</p>
      <h3>{d.explanation?.summary || "No explanation received yet."}</h3>
      {d.kind === "price" ? (
        <p>
          Current ${String(p.current_price)} → Proposed{" "}
          <strong>${String(p.proposed_price)}</strong>
        </p>
      ) : d.kind === "purchase_order" ? (
        <p>
          {(
            p.lines as {
              product_id: string;
              quantity: number;
              unit_cost: string;
            }[]
          )
            .map(
              (l) => `${l.product_id}: ${l.quantity} units at $${l.unit_cost}`,
            )
            .join(" · ")}
        </p>
      ) : d.kind === "listing" ? (
        <p>
          {String(p.action)} · {String(p.product_id)}
        </p>
      ) : (
        <p>
          {(
            p.line_items as {
              id: string;
              quantity: number;
              unit_price: { amount: number };
            }[]
          )
            .map(
              (l) =>
                `${l.id}: ${l.quantity} units at ${usd(l.unit_price.amount)}`,
            )
            .join(" · ")}
        </p>
      )}
      {d.explanation?.factors?.map((f, i) => (
        <p key={i} className={s.small}>
          {f.name}: {f.value ?? f.effect}
        </p>
      ))}
      <p className={s.small}>Expires {when(d.expires_at)}</p>
      {manager && d.status === "pending" && (
        <>
          <div className={s.form}>
            <label>
              Feedback reason
              <input
                maxLength={500}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <label>
              Feedback tag
              <select value={tag} onChange={(e) => setTag(e.target.value)}>
                {[
                  "agree",
                  "too_aggressive",
                  "too_timid",
                  "bad_timing",
                  "wrong_supplier",
                  "margin_too_low",
                  "stock_risk",
                  "explanation_unclear",
                ].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </label>
          </div>
          <div className={s.actions}>
            <button
              disabled={busy}
              onClick={() => void run(() => verdict("approve"))}
            >
              Approve
            </button>
            <button
              disabled={busy}
              onClick={() => void run(() => verdict("reject"))}
            >
              Reject
            </button>
          </div>
          <div className={s.form}>
            <label>
              {d.kind === "price"
                ? "Override price ($)"
                : d.kind === "deal"
                  ? "Counter unit price for all lines ($)"
                  : d.kind === "purchase_order"
                    ? "Override quantity for all lines"
                    : "New listing description"}
              <input
                value={override}
                onChange={(e) => setOverride(e.target.value)}
              />
            </label>
            <button
              disabled={busy || !override}
              onClick={() => void run(() => verdict("override"))}
            >
              {d.kind === "deal" ? "Send counter" : "Override"}
            </button>
          </div>
        </>
      )}
      <ErrorText text={error} />
    </article>
  );
}
function Decisions({ manager }: { manager: boolean }) {
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("pending");
  const [page, setPage] = useState(1);
  const { data, error, load } = useData<Page<Decision>>(
    `/api/decisions?status=${status}&kind=${kind}&page=${page}`,
  );
  return (
    <>
      <div className={s.form}>
        <label>
          Kind
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setPage(1);
            }}
          >
            {["", "price", "purchase_order", "deal", "listing"].map((x) => (
              <option key={x} value={x}>
                {x || "All decisions"}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            {[
              "pending",
              "approved",
              "auto_approved",
              "overridden",
              "rejected",
              "expired",
              "",
            ].map((x) => (
              <option key={x} value={x}>
                {x || "All statuses"}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ErrorText text={error} />
      {data?.rows.map((d) => (
        <DecisionRow
          key={d.decision_id}
          d={d}
          refresh={load}
          manager={manager}
        />
      ))}
      {data && !data.rows.length && <p>No decisions match this view.</p>}
      <div className={s.actions}>
        <button disabled={page === 1} onClick={() => setPage(page - 1)}>
          Previous
        </button>
        <span>
          Page {page} of {data?.pages || 1} · {data?.total || 0} decisions
        </span>
        <button
          disabled={page >= (data?.pages || 1)}
          onClick={() => setPage(page + 1)}
        >
          Next
        </button>
      </div>
    </>
  );
}
function Agent({ manager }: { manager: boolean }) {
  const { data, error, load, pager } = usePaged<Run>("/api/agent/runs");
  const action = useAction(load);
  return (
    <>
      {pager}
      <section className={s.card}>
        <h2>Nightly store review</h2>
        <p>
          Seven persisted steps review outcomes, import market evidence, check
          forecasts, request model prices, propose replenishment, manage
          listings and reconcile inventory.
        </p>
        {manager && (
          <div className={s.actions}>
            <button
              disabled={
                action.busy ||
                data?.rows.some((r) => ["queued", "running"].includes(r.status))
              }
              onClick={() => void action.run(() => mutate("/api/agent/runs"))}
            >
              Run store review now
            </button>
            <button
              onClick={() =>
                void action.run(async () => {
                  const first = await get<Page<Decision>>(
                    "/api/agent/feedback?page_size=200",
                  );
                  const rows = [...first.rows];
                  for (let page = 2; page <= first.pages; page++) {
                    const next = await get<Page<Decision>>(
                      `/api/agent/feedback?page_size=200&page=${page}`,
                    );
                    rows.push(...next.rows);
                  }
                  const feedback = {
                    rows,
                    total: rows.length,
                    exported_at: new Date().toISOString(),
                  };
                  const url = URL.createObjectURL(
                    new Blob([JSON.stringify(feedback, null, 2)], {
                      type: "application/json",
                    }),
                  );
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = "shelfiq-feedback.json";
                  a.click();
                  URL.revokeObjectURL(url);
                })
              }
            >
              Export feedback
            </button>
          </div>
        )}
        <ErrorText text={error || action.error} />
      </section>
      {data?.rows.map((r) => (
        <section key={r.run_id} className={s.card}>
          <h2>
            {r.status} · {when(r.created_at)}
          </h2>
          <p>{r.summary || "Waiting for the store-agent worker."}</p>
          <Table headers={["Step", "Status", "Result"]}>
            {r.steps.map((step) => (
              <tr key={step.name}>
                <td>{step.name.replaceAll("_", " ")}</td>
                <td>{step.status}</td>
                <td>{step.summary || "Working…"}</td>
              </tr>
            ))}
          </Table>
        </section>
      ))}
    </>
  );
}
function Orders({ editor }: { editor: boolean }) {
  const { data, error, load, pager } = usePaged<OrderRow>("/api/orders");
  const action = useAction(load);
  const [qty, setQty] = useState<Record<string, number>>({});
  const [event, setEvent] = useState<Record<string, string>>({});
  return (
    <>
      {pager}
      <ErrorText text={error || action.error} />
      {data?.rows.map((row) => (
        <section key={row.order_id} className={s.card}>
          <h2>
            {row.status} · {row.payload.buyer.email}
          </h2>
          <p className={s.small}>
            {row.order_id} · {row.channel} · {when(row.created_at)} · test
            payment
          </p>
          <Table
            headers={["Product", "Ordered", "Shipped", "Returned", "Event"]}
          >
            {row.payload.line_items.map((l) => {
              const key = row.order_id + l.id;
              return (
                <tr key={l.id}>
                  <td>
                    {l.item.title}
                    <br />
                    {usd(l.item.price)} per unit
                  </td>
                  <td>{l.quantity}</td>
                  <td>{l.fulfilled}</td>
                  <td>{l.returned}</td>
                  <td>
                    {editor && (
                      <div className={s.form}>
                        <select
                          aria-label={`Event ${l.id}`}
                          value={event[key] || "shipped"}
                          onChange={(e) =>
                            setEvent({ ...event, [key]: e.target.value })
                          }
                        >
                          {[
                            "shipped",
                            "delivered",
                            "canceled",
                            "returned_to_sender",
                          ].map((x) => (
                            <option key={x}>{x}</option>
                          ))}
                        </select>
                        <input
                          aria-label={`Event quantity ${l.id}`}
                          type="number"
                          min="1"
                          max={l.quantity}
                          value={qty[key] || 1}
                          onChange={(e) =>
                            setQty({ ...qty, [key]: Number(e.target.value) })
                          }
                        />
                        <button
                          disabled={action.busy}
                          onClick={() =>
                            void action.run(() =>
                              mutate(
                                "/api/orders/" + row.order_id + "/events",
                                {
                                  type: event[key] || "shipped",
                                  line_items: [
                                    { id: l.id, quantity: qty[key] || 1 },
                                  ],
                                },
                              ),
                            )
                          }
                        >
                          Record event
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </Table>
          <p>
            Total{" "}
            {usd(
              row.payload.totals.find((t) => t.type === "total")?.amount || 0,
            )}{" "}
            · {row.payload.fulfillment.method}
          </p>
        </section>
      ))}
      {data && !data.rows.length && (
        <p>No orders yet. Place a test order in the storefront.</p>
      )}
    </>
  );
}
function Procurement({
  manager,
  editor,
}: {
  manager: boolean;
  editor: boolean;
}) {
  const { data, error, load, pager } = usePaged<Purchase>(
    "/api/purchase-orders",
  );
  const suppliers = useData<{ rows: Supplier[] }>("/api/suppliers");
  const products = useData<{ rows: Product[] }>("/api/commerce/products");
  const action = useAction(load);
  const [sid, setSid] = useState("");
  const [pid, setPid] = useState("");
  const [quantity, setQuantity] = useState(10);
  const [cost, setCost] = useState("");
  const [received, setReceived] = useState<Record<string, number>>({});
  return (
    <>
      {pager}
      <ErrorText text={error || action.error} />
      {manager && (
        <section className={s.card}>
          <h2>Create a simulated purchase</h2>
          <form
            className={s.form}
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(() =>
                mutate("/api/purchase-orders", {
                  supplier_id: sid,
                  lines: [{ product_id: pid, quantity, unit_cost: cost }],
                }),
              );
            }}
          >
            <label>
              Supplier
              <select
                required
                value={sid}
                onChange={(e) => {
                  setSid(e.target.value);
                  setPid("");
                }}
              >
                <option value="">Select supplier</option>
                {suppliers.data?.rows.map((x) => (
                  <option key={x.supplier_id} value={x.supplier_id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Product
              <select
                required
                value={pid}
                onChange={(e) => {
                  setPid(e.target.value);
                  setCost(
                    products.data?.rows.find(
                      (p) => p.product_id === e.target.value,
                    )?.unit_cost || "",
                  );
                }}
              >
                <option value="">Select product</option>
                {products.data?.rows
                  .filter((p) => p.supplier_id === sid)
                  .map((p) => (
                    <option key={p.product_id}>{p.product_id}</option>
                  ))}
              </select>
            </label>
            <label>
              Quantity
              <input
                required
                type="number"
                min="1"
                value={quantity}
                onChange={(e) => setQuantity(Number(e.target.value))}
              />
            </label>
            <label>
              Unit cost ($)
              <input
                required
                type="number"
                min="0"
                step=".01"
                value={cost}
                onChange={(e) => setCost(e.target.value)}
              />
            </label>
            <button disabled={action.busy}>Create draft</button>
          </form>
          <p className={s.small}>
            Assign suppliers in Commerce settings. Ordering completes a local
            supplier test checkout and never purchases from a real company.
          </p>
        </section>
      )}
      {data?.rows.map((po) => (
        <section key={po.po_id} className={s.card}>
          <h2>
            {po.status.replaceAll("_", " ")} ·{" "}
            {suppliers.data?.rows.find((x) => x.supplier_id === po.supplier_id)
              ?.name || "Supplier"}
          </h2>
          <p className={s.small}>
            {po.po_id} · {po.supplier_order_id || "Not ordered"}
          </p>
          <Table
            headers={[
              "Product",
              "Quantity",
              "Received",
              "Unit cost",
              "Receipt",
            ]}
          >
            {po.lines.map((l) => (
              <tr key={l.product_id}>
                <td>{l.product_id}</td>
                <td>{l.quantity}</td>
                <td>{l.received}</td>
                <td>${l.unit_cost}</td>
                <td>
                  {editor &&
                    ["ordered", "partially_received"].includes(po.status) &&
                    l.received < l.quantity && (
                      <div className={s.actions}>
                        <input
                          aria-label={`Receive ${po.po_id} ${l.product_id}`}
                          type="number"
                          min="1"
                          max={l.quantity - l.received}
                          value={received[po.po_id + l.product_id] || 1}
                          onChange={(e) =>
                            setReceived({
                              ...received,
                              [po.po_id + l.product_id]: Number(e.target.value),
                            })
                          }
                        />
                        <button
                          disabled={action.busy}
                          onClick={() =>
                            void action.run(() =>
                              mutate(
                                "/api/purchase-orders/" + po.po_id + "/receive",
                                {
                                  lines: [
                                    {
                                      product_id: l.product_id,
                                      quantity_received:
                                        received[po.po_id + l.product_id] || 1,
                                    },
                                  ],
                                },
                              ),
                            )
                          }
                        >
                          Receive
                        </button>
                      </div>
                    )}
                </td>
              </tr>
            ))}
          </Table>
          {manager && (
            <div className={s.actions}>
              {po.status === "draft" && (
                <button
                  disabled={action.busy}
                  onClick={() =>
                    void action.run(() =>
                      mutate("/api/purchase-orders/" + po.po_id + "/order"),
                    )
                  }
                >
                  Place simulated supplier order
                </button>
              )}
              {["draft", "ordered"].includes(po.status) && (
                <button
                  disabled={action.busy}
                  onClick={() =>
                    void action.run(() =>
                      mutate("/api/purchase-orders/" + po.po_id + "/cancel"),
                    )
                  }
                >
                  Cancel purchase
                </button>
              )}
            </div>
          )}
        </section>
      ))}
    </>
  );
}
function Inventory({ editor }: { editor: boolean }) {
  const { data, error, load, pager } = usePaged<Movement>(
    "/api/inventory/movements",
  );
  const products = useData<{ rows: Product[] }>("/api/commerce/products");
  const action = useAction(async () => {
    await load();
    await products.load();
  });
  const [pid, setPid] = useState("");
  const [qty, setQty] = useState(1);
  const [note, setNote] = useState("");
  const [type, setType] = useState("adjustment");
  return (
    <section className={s.card}>
      <h2>Stock movements</h2>
      {pager}
      <p>
        Checkout consumes stock once. Receipts and returns add it back. Commerce
        mode prevents simulator observations from overwriting the ledger.
      </p>
      <ErrorText text={error || action.error} />
      {editor && (
        <form
          className={s.form}
          onSubmit={(e) => {
            e.preventDefault();
            void action.run(() =>
              mutate("/api/inventory/movements", {
                product_id: pid,
                type,
                quantity: qty,
                note,
              }),
            );
          }}
        >
          <label>
            Product
            <select
              required
              value={pid}
              onChange={(e) => setPid(e.target.value)}
            >
              <option value="">Select product</option>
              {products.data?.rows.map((p) => (
                <option key={p.product_id} value={p.product_id}>
                  {p.name} · {p.stock_on_hand ?? "unknown"} on hand
                </option>
              ))}
            </select>
          </label>
          <label>
            Movement
            <select value={type} onChange={(e) => setType(e.target.value)}>
              <option value="adjustment">Adjustment</option>
              <option value="shrink">Shrink</option>
            </select>
          </label>
          <label>
            Signed quantity
            <input
              type="number"
              required
              value={qty}
              onChange={(e) => setQty(Number(e.target.value))}
            />
          </label>
          <label>
            Reason
            <input
              required
              minLength={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          <button disabled={action.busy}>Record movement</button>
        </form>
      )}
      <Table headers={["When", "Product", "Type", "Quantity", "Reason"]}>
        {data?.rows.map((m) => (
          <tr key={m.movement_id}>
            <td>{when(m.created_at)}</td>
            <td>{m.product_id}</td>
            <td>{m.type}</td>
            <td>{m.quantity}</td>
            <td>{m.note}</td>
          </tr>
        ))}
      </Table>
    </section>
  );
}
function Accounting() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [group, setGroup] = useState("day");
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const report = useData<Summary>(
    "/api/accounting/summary?" + params + "&group_by=" + group,
  );
  const ledger = usePaged<Ledger>("/api/accounting/ledger?" + params);
  return (
    <>
      <section className={s.card}>
        <h2>Accounting</h2>
        <div className={s.form}>
          <label>
            From
            <input
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          </label>
          <label>
            Through
            <input
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </label>
          <label>
            Group by
            <select value={group} onChange={(e) => setGroup(e.target.value)}>
              {["day", "category", "channel"].map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
        </div>
        <ErrorText text={report.error || ledger.error} />
        {report.data && (
          <>
            <p>{report.data.basis}</p>
            <div className={s.metric}>
              {[
                ["Net revenue", report.data.revenue],
                ["Cost of goods", report.data.cogs],
                ["Gross profit", report.data.gross_profit],
                ["Refunds", report.data.refunds],
                ["Purchases", report.data.purchases],
              ].map(([k, v]) => (
                <div key={k}>
                  {k}
                  <strong>${v}</strong>
                </div>
              ))}
            </div>
            <p>
              {report.data.orders} orders · {report.data.units} net units
            </p>
            <Table headers={["Group", "Revenue", "COGS", "Purchases"]}>
              {report.data.groups.map((g) => (
                <tr key={g.label}>
                  <td>{g.label}</td>
                  <td>${g.revenue}</td>
                  <td>${g.cogs}</td>
                  <td>${g.purchases}</td>
                </tr>
              ))}
            </Table>
          </>
        )}
      </section>
      <section className={s.card}>
        <h2>Event ledger</h2>
        {ledger.pager}
        <Table headers={["When", "Type", "Product", "Amount", "Memo"]}>
          {ledger.data?.rows.map((l) => (
            <tr key={l.entry_id}>
              <td>{when(l.created_at)}</td>
              <td>{l.type}</td>
              <td>{l.product_id || "—"}</td>
              <td>${l.amount}</td>
              <td>{l.memo}</td>
            </tr>
          ))}
        </Table>
      </section>
    </>
  );
}
function MarketResearch() {
  const { data, error } = useData<{ rows: Market[] }>("/api/market/summary");
  return (
    <section className={s.card}>
      <h2>Recent market observations</h2>
      <p>
        Last seven days, latest observation per competitor. Demo evidence is
        marked synthetic. External website research requires a separately
        configured source.
      </p>
      <ErrorText text={error} />
      <Table
        headers={[
          "Product",
          "Minimum",
          "Median",
          "Competitors",
          "Latest observation",
        ]}
      >
        {data?.rows.map((m) => (
          <tr key={m.product_id}>
            <td>{m.product_id}</td>
            <td>${m.competitor_min}</td>
            <td>${m.competitor_median}</td>
            <td>{m.observations}</td>
            <td>{when(m.last_observed)}</td>
          </tr>
        ))}
      </Table>
    </section>
  );
}
function Offers() {
  const { data, error, pager } = usePaged<{
    offer_id: string;
    status: string;
    decision_id: string;
    created_at: string;
  }>("/api/offers");
  return (
    <section className={s.card}>
      <h2>Buyer offers</h2>
      {pager}
      <p>Pending offers are reviewed in the Decisions inbox.</p>
      <ErrorText text={error} />
      <Table headers={["Offer", "Status", "Decision", "Created"]}>
        {data?.rows.map((o) => (
          <tr key={o.offer_id}>
            <td>{o.offer_id}</td>
            <td>{o.status}</td>
            <td>
              <NavLink to="/commerce/decisions">{o.decision_id}</NavLink>
            </td>
            <td>{when(o.created_at)}</td>
          </tr>
        ))}
      </Table>
    </section>
  );
}
function ProductCommerce({
  p,
  suppliers,
  refresh,
  manager,
}: {
  p: Product;
  suppliers: Supplier[];
  refresh: () => Promise<void>;
  manager: boolean;
}) {
  const [supplier, setSupplier] = useState(p.supplier_id || "");
  const [online, setOnline] = useState(p.online);
  const [description, setDescription] = useState(p.description);
  const [price, setPrice] = useState(p.online_price || "");
  const action = useAction(refresh);
  return (
    <tr>
      <td>
        {p.name}
        <br />
        <span className={s.small}>
          {p.product_id} · {p.stock_on_hand ?? "unknown"} in stock
        </span>
      </td>
      <td>
        <input
          aria-label={`List ${p.product_id}`}
          type="checkbox"
          checked={online}
          disabled={!manager}
          onChange={(e) => setOnline(e.target.checked)}
        />
      </td>
      <td>
        <select
          aria-label={`Supplier ${p.product_id}`}
          disabled={!manager}
          value={supplier}
          onChange={(e) => setSupplier(e.target.value)}
        >
          <option value="">Unassigned</option>
          {suppliers.map((x) => (
            <option key={x.supplier_id} value={x.supplier_id}>
              {x.name}
            </option>
          ))}
        </select>
      </td>
      <td>
        <input
          aria-label={`Description ${p.product_id}`}
          disabled={!manager}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </td>
      <td>
        <input
          aria-label={`Online price ${p.product_id}`}
          disabled={!manager}
          type="number"
          step=".01"
          min=".01"
          placeholder={`Shelf $${p.current_price || "—"}`}
          value={price}
          onChange={(e) => setPrice(e.target.value)}
        />
      </td>
      <td>
        {manager && (
          <button
            disabled={action.busy}
            onClick={() =>
              void action.run(() =>
                mutate(
                  "/api/commerce/products/" + p.product_id,
                  {
                    supplier_id: supplier || null,
                    online,
                    description,
                    online_price: price || null,
                    images: p.images,
                  },
                  "PUT",
                ),
              )
            }
          >
            Save
          </button>
        )}
        <ErrorText text={action.error} />
      </td>
    </tr>
  );
}
function Overview({ manager }: { manager: boolean }) {
  const status = useData<{
    inventory_ledger_enabled: boolean;
    webhooks: {
      configured: boolean;
      queued: number;
      delivered: number;
      target: string;
    };
  }>("/api/commerce/status");
  const settings = useData<Settings>("/api/commerce/settings");
  const suppliers = useData<{ rows: Supplier[] }>("/api/suppliers");
  const products = useData<{ rows: Product[] }>("/api/commerce/products");
  const policy = useData<Policy>("/api/rules/autonomy");
  const [form, setForm] = useState<Settings | null>(null);
  const [rules, setRules] = useState<Policy | null>(null);
  const [name, setName] = useState("");
  useEffect(() => {
    if (settings.data)
      setForm(
        (prev) =>
          prev ?? {
            ...settings.data!,
            nightly_run_time: settings.data!.nightly_run_time.slice(0, 5),
            shelf_update_time: settings.data!.shelf_update_time.slice(0, 5),
          },
      );
  }, [settings.data]);
  useEffect(() => {
    if (policy.data) setRules((prev) => prev ?? policy.data);
  }, [policy.data]);
  const action = useAction(async () => {
    await settings.load();
    await suppliers.load();
    await policy.load();
  });
  return (
    <>
      <section className={s.card}>
        <h2>Development commerce</h2>
        <p>
          Open the public{" "}
          <a href="/shop" target="_blank" rel="noreferrer">
            test storefront
          </a>{" "}
          to place an order. Enabling commerce makes inventory movements
          authoritative.
        </p>
        <ErrorText text={settings.error || action.error} />
        {form && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const body = {
                price_delivery_mode: form.price_delivery_mode,
                commerce_enabled: form.commerce_enabled,
                nightly_enabled: form.nightly_enabled,
                nightly_run_time: form.nightly_run_time,
                shelf_update_time: form.shelf_update_time,
                shipping_minor: form.shipping_minor,
                tax_bps: form.tax_bps,
              };
              void action.run(() =>
                mutate("/api/commerce/settings", body, "PUT"),
              );
            }}
          >
            <div className={s.form}>
              <label>
                <span>
                  <input
                    type="checkbox"
                    disabled={!manager}
                    checked={form.commerce_enabled}
                    onChange={(e) =>
                      setForm({ ...form, commerce_enabled: e.target.checked })
                    }
                  />{" "}
                  Open test store
                </span>
              </label>
              <label>
                <span>
                  <input
                    type="checkbox"
                    disabled={!manager}
                    checked={form.nightly_enabled}
                    onChange={(e) =>
                      setForm({ ...form, nightly_enabled: e.target.checked })
                    }
                  />{" "}
                  Nightly review
                </span>
              </label>
              <label>
                Nightly time ({form.timezone})
                <input
                  type="time"
                  disabled={!manager}
                  value={form.nightly_run_time}
                  onChange={(e) =>
                    setForm({ ...form, nightly_run_time: e.target.value })
                  }
                />
              </label>
              <label>
                Price delivery
                <select
                  disabled={!manager}
                  value={form.price_delivery_mode}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      price_delivery_mode: e.target.value as
                        | "immediate"
                        | "daily",
                    })
                  }
                >
                  <option value="immediate">Immediately after approval</option>
                  <option value="daily">Daily shelf cutover</option>
                </select>
              </label>
              <label>
                Shelf cutover
                <input
                  type="time"
                  disabled={!manager}
                  value={form.shelf_update_time}
                  onChange={(e) =>
                    setForm({ ...form, shelf_update_time: e.target.value })
                  }
                />
              </label>
              <label>
                Shipping (cents)
                <input
                  type="number"
                  min="0"
                  disabled={!manager}
                  value={form.shipping_minor}
                  onChange={(e) =>
                    setForm({ ...form, shipping_minor: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                Tax (basis points)
                <input
                  type="number"
                  min="0"
                  max="10000"
                  disabled={!manager}
                  value={form.tax_bps}
                  onChange={(e) =>
                    setForm({ ...form, tax_bps: Number(e.target.value) })
                  }
                />
              </label>
              {manager && (
                <button disabled={action.busy}>Save commerce settings</button>
              )}
            </div>
          </form>
        )}
      </section>
      {status.data && (
        <section className={s.card}>
          <h2>Order notifications and stock integrity</h2>
          <p>
            Inventory ledger:{" "}
            {status.data.inventory_ledger_enabled
              ? "active"
              : "starts when commerce is enabled"}
          </p>
          <p>
            Signed order notifications:{" "}
            {status.data.webhooks.configured
              ? status.data.webhooks.target
              : "receiver key not configured"}{" "}
            · {status.data.webhooks.delivered} delivered ·{" "}
            {status.data.webhooks.queued} queued
          </p>
        </section>
      )}
      {rules && (
        <section className={s.card}>
          <h2>Owner autonomy policy</h2>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(() =>
                mutate("/api/rules/autonomy", rules, "PUT"),
              );
            }}
          >
            <div className={s.form}>
              {(["purchase_order", "deal", "listing"] as const).map((kind) => (
                <label key={kind}>
                  {kind.replace("_", " ")}
                  <select
                    disabled={!manager}
                    value={rules[kind].mode}
                    onChange={(e) =>
                      setRules({
                        ...rules,
                        [kind]: { ...rules[kind], mode: e.target.value },
                      })
                    }
                  >
                    <option value="ask">Ask owner</option>
                    <option value="auto">Auto within limits</option>
                  </select>
                </label>
              ))}
              <label>
                Maximum automatic purchase ($)
                <input
                  disabled={!manager}
                  type="number"
                  min="0"
                  step=".01"
                  value={rules.purchase_order.auto_max_total}
                  onChange={(e) =>
                    setRules({
                      ...rules,
                      purchase_order: {
                        ...rules.purchase_order,
                        auto_max_total: e.target.value,
                      },
                    })
                  }
                />
              </label>
              <label>
                Deal minimum margin (%)
                <input
                  disabled={!manager}
                  type="number"
                  min="0"
                  max="99"
                  value={rules.deal.min_margin_pct}
                  onChange={(e) =>
                    setRules({
                      ...rules,
                      deal: { ...rules.deal, min_margin_pct: e.target.value },
                    })
                  }
                />
              </label>
              <label>
                Maximum discount (%)
                <input
                  disabled={!manager}
                  type="number"
                  min="0"
                  max="100"
                  value={rules.deal.max_discount_pct}
                  onChange={(e) =>
                    setRules({
                      ...rules,
                      deal: { ...rules.deal, max_discount_pct: e.target.value },
                    })
                  }
                />
              </label>
              <label>
                Minimum deal quantity
                <input
                  disabled={!manager}
                  type="number"
                  min="1"
                  value={rules.deal.min_quantity}
                  onChange={(e) =>
                    setRules({
                      ...rules,
                      deal: {
                        ...rules.deal,
                        min_quantity: Number(e.target.value),
                      },
                    })
                  }
                />
              </label>
              {manager && <button disabled={action.busy}>Save autonomy</button>}
            </div>
          </form>
        </section>
      )}
      <section className={s.card}>
        <h2>Simulated suppliers</h2>
        {suppliers.data?.rows.map((x) => (
          <p key={x.supplier_id}>
            {x.name} · {x.lead_time_days} day lead time
          </p>
        ))}
        {manager && (
          <form
            className={s.form}
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                await mutate("/api/suppliers", { name });
                setName("");
              });
            }}
          >
            <label>
              Supplier name
              <input
                required
                minLength={2}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <button disabled={action.busy}>Add simulated supplier</button>
          </form>
        )}
      </section>
      <section className={s.card}>
        <h2>Online catalogue and procurement sources</h2>
        <Table
          headers={[
            "Product",
            "Online",
            "Supplier",
            "Description",
            "Online price ($)",
            "Save",
          ]}
        >
          {products.data?.rows.map((p) => (
            <ProductCommerce
              key={p.product_id}
              p={p}
              suppliers={suppliers.data?.rows || []}
              refresh={products.load}
              manager={manager}
            />
          ))}
        </Table>
        <ErrorText text={products.error} />
      </section>
    </>
  );
}
export function Commerce() {
  const { section = "overview" } = useParams();
  const { user } = useShell();
  const manager = ["manager", "superadmin"].includes(user.role);
  const editor = manager || user.role === "staff";
  usePageChrome({ title: titles[section] || "Commerce" });
  return (
    <div className={s.page}>
      <nav className={s.tabs}>
        {Object.entries(titles)
          .filter(([key]) => key !== "accounting" || manager)
          .map(([key, title]) => (
            <NavLink key={key} to={"/commerce/" + key}>
              {title}
            </NavLink>
          ))}
      </nav>
      {section === "decisions" ? (
        <Decisions manager={manager} />
      ) : section === "agent" ? (
        <Agent manager={manager} />
      ) : section === "orders" ? (
        <Orders editor={editor} />
      ) : section === "purchases" ? (
        <Procurement manager={manager} editor={editor} />
      ) : section === "inventory" ? (
        <Inventory editor={editor} />
      ) : section === "market" ? (
        <MarketResearch />
      ) : section === "offers" ? (
        <Offers />
      ) : section === "accounting" ? (
        manager ? (
          <Accounting />
        ) : (
          <p>Manager access is required.</p>
        )
      ) : (
        <Overview manager={manager} />
      )}
    </div>
  );
}
