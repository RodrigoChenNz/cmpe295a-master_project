import { useCallback, useEffect, useState } from "react";
import {
  BrowserRouter,
  Link,
  Route,
  Routes,
  useParams,
} from "react-router-dom";
import {
  shop,
  usd,
  type CatalogProduct,
  type Checkout,
  type Offer,
  type Order,
} from "../lib/commerce";
import s from "./Commerce.module.css";

function useBuyerOrder(id: string | undefined) {
  const [order, setOrder] = useState<Order | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!id) return;
    let active = true;
    const load = () =>
      shop<Order>(`/ucp/v1/orders/${id}`)
        .then((x) => {
          if (active) setOrder(x);
        })
        .catch((e) => {
          if (active) setError(String(e.message));
        });
    void load();
    const timer = setInterval(load, 10000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [id]);
  return { order, error };
}
function BuyerOrder() {
  const { id } = useParams();
  const { order, error } = useBuyerOrder(id);
  return (
    <section className={s.card}>
      <h2>Your order</h2>
      {error && (
        <p role="alert" className={s.error}>
          {error}
        </p>
      )}
      {order && (
        <>
          <p>Order {order.id}</p>
          <h3>{order.status}</h3>
          <p>Test payment received. No money was charged.</p>
          {order.line_items.map((line) => (
            <p key={line.id}>
              {line.item.title} · {line.quantity} units ·{" "}
              {usd(line.item.price * line.quantity)} · {line.fulfilled} shipped
              · {line.returned} returned
            </p>
          ))}
          <p>Fulfillment: {order.fulfillment.method}</p>
          {order.fulfillment.events.map((e) => (
            <p key={e.id}>
              {e.type} {e.tracking_number}
            </p>
          ))}
          <p className={s.small}>
            This browser session holds your private buyer token. Keep this tab
            session to access your order.
          </p>
        </>
      )}
    </section>
  );
}
function ProductWhy() {
  const { id } = useParams();
  const [why, setWhy] = useState<{ sentence: string; price: string } | null>(
    null,
  );
  const [error, setError] = useState("");
  useEffect(() => {
    shop<{ sentence: string; price: string }>(`/api/public/products/${id}/why`)
      .then(setWhy)
      .catch((e) => setError(e.message));
  }, [id]);
  return (
    <section className={s.card}>
      <h2>Why this price?</h2>
      {error && <p role="alert">{error}</p>}
      {why && (
        <>
          <p className={s.price}>${why.price}</p>
          <p>{why.sentence}</p>
        </>
      )}
      <Link to="/shop">Back to products</Link>
    </section>
  );
}
function Browse() {
  const [products, setProducts] = useState<CatalogProduct[]>([]);
  const [query, setQuery] = useState("");
  const [cart, setCart] = useState<Record<string, number>>({});
  const [email, setEmail] = useState("");
  const [method, setMethod] = useState("pickup");
  const [address, setAddress] = useState("");
  const [checkout, setCheckout] = useState<Checkout | null>(null);
  const [offer, setOffer] = useState<Offer | null>(null);
  const [discount, setDiscount] = useState(5);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [payment, setPayment] = useState("test_approved");
  const load = useCallback(
    () =>
      shop<{ products: CatalogProduct[] }>("/ucp/v1/catalog/search", {
        query,
        pagination: { limit: 100 },
      }).then((x) => setProducts(x.products)),
    [query],
  );
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [load]);
  useEffect(() => {
    if (
      !offer ||
      !checkout ||
      !["pending_review", "countered"].includes(offer.status)
    )
      return;
    const timer = setInterval(
      () =>
        shop<Offer>(
          `/ucp/v1/checkout-sessions/${checkout.id}/offers/${offer.offer_id}`,
        )
          .then(setOffer)
          .catch((e) => setError(e.message)),
      4000,
    );
    return () => clearInterval(timer);
  }, [offer, checkout]);
  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function open() {
    const value = await shop<Checkout>("/ucp/v1/checkout-sessions", {
      line_items: Object.entries(cart)
        .filter(([, n]) => n > 0)
        .map(([id, quantity]) => ({ item: { id }, quantity })),
      buyer: { email },
      fulfillment: { method, ...(method === "shipping" ? { address } : {}) },
      channel: "storefront",
    });
    setCheckout(value);
  }
  async function refreshCart() {
    if (checkout)
      setCheckout(
        await shop<Checkout>(`/ucp/v1/checkout-sessions/${checkout.id}`),
      );
  }
  return (
    <>
      <div>
        <p>YOUR NEIGHBORHOOD STORE, CONNECTED</p>
        <h1>Smart shelves. Simple shopping.</h1>
        <p>
          Browse approved prices, request a bulk deal, and place a test order.
        </p>
      </div>
      <div className={s.split}>
        <section>
          <label>
            Search products{" "}
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search the store"
            />
          </label>
          <div className={s.grid} style={{ marginTop: 20 }}>
            {products.map((p) => (
              <article key={p.id}>
                <p className={s.small}>{p.id}</p>
                <h2>{p.title}</h2>
                <p>{p.description.plain || "Available from our store."}</p>
                <div className={s.price}>{usd(p.variants[0].price.amount)}</div>
                <Link to={`/shop/p/${p.id}`}>Why this price?</Link>
                <div className={s.form}>
                  <label>
                    Quantity
                    <input
                      type="number"
                      min="0"
                      max="10000"
                      value={cart[p.id] || 0}
                      disabled={!!checkout}
                      onChange={(e) =>
                        setCart({
                          ...cart,
                          [p.id]: Math.max(
                            0,
                            Math.floor(Number(e.target.value)),
                          ),
                        })
                      }
                    />
                  </label>
                  <span>
                    {p.variants[0].availability.available
                      ? "In stock"
                      : "Out of stock"}
                  </span>
                </div>
              </article>
            ))}
          </div>
          {!products.length && !error && (
            <p>No online products are currently listed.</p>
          )}
        </section>
        <section className={s.card}>
          <h2>Your test checkout</h2>
          {error && (
            <p role="alert" className={s.error}>
              {error}
            </p>
          )}
          {!checkout ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void act(open);
              }}
            >
              <p>
                {Object.values(cart).reduce((a, b) => a + b, 0)} units selected
              </p>
              <div className={s.form}>
                <label>
                  Email
                  <input
                    required
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </label>
                <label>
                  Fulfillment
                  <select
                    value={method}
                    onChange={(e) => setMethod(e.target.value)}
                  >
                    <option value="pickup">Store pickup</option>
                    <option value="shipping">Shipping</option>
                  </select>
                </label>
                {method === "shipping" && (
                  <label>
                    Shipping address
                    <input
                      required
                      value={address}
                      onChange={(e) => setAddress(e.target.value)}
                    />
                  </label>
                )}
              </div>
              <button
                disabled={busy || !Object.values(cart).some((x) => x > 0)}
              >
                Review checkout
              </button>
            </form>
          ) : (
            <>
              <p>Status: {checkout.status}</p>
              {checkout.line_items.map((l) => (
                <p key={l.id}>
                  {l.item.title} × {l.quantity}{" "}
                  <strong>{usd(l.item.price * l.quantity)}</strong>
                </p>
              ))}
              {checkout.totals.map((t) => (
                <p key={t.type}>
                  {t.type}: <strong>{usd(t.amount)}</strong>
                </p>
              ))}
              {checkout.messages.map((m, i) => (
                <p key={i}>{m.content}</p>
              ))}
              {checkout.status === "completed" ? (
                <div className={s.actions}>
                  <Link to={`/shop/orders/${checkout.order?.id}`}>
                    View your order
                  </Link>
                  <button
                    onClick={() => {
                      setCheckout(null);
                      setOffer(null);
                      setCart({});
                    }}
                  >
                    Start another order
                  </button>
                </div>
              ) : (
                <>
                  <div className={s.note}>
                    Demonstration payments only. No card details and no real
                    charge.
                  </div>
                  {offer && (
                    <div>
                      <h3>Bulk offer: {offer.status.replaceAll("_", " ")}</h3>
                      <p>{offer.payload.response_note}</p>
                      {offer.payload.counter?.line_items.map((l) => (
                        <p key={l.id}>
                          Counter: {l.id} · {usd(l.unit_price.amount)} per unit
                        </p>
                      ))}
                      <div className={s.actions}>
                        {offer.status === "countered" && (
                          <button
                            disabled={busy}
                            onClick={() =>
                              void act(async () => {
                                setOffer(
                                  await shop<Offer>(
                                    `/ucp/v1/checkout-sessions/${checkout.id}/offers/${offer.offer_id}/accept`,
                                    {},
                                  ),
                                );
                                await refreshCart();
                              })
                            }
                          >
                            Accept counter
                          </button>
                        )}
                        {["pending_review", "countered", "accepted"].includes(
                          offer.status,
                        ) && (
                          <button
                            disabled={busy}
                            onClick={() =>
                              void act(async () => {
                                setOffer(
                                  await shop<Offer>(
                                    `/ucp/v1/checkout-sessions/${checkout.id}/offers/${offer.offer_id}/withdraw`,
                                    {},
                                  ),
                                );
                                await refreshCart();
                              })
                            }
                          >
                            Withdraw offer
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                  {(!offer ||
                    ["withdrawn", "declined", "expired"].includes(
                      offer.status,
                    )) && (
                    <div className={s.form}>
                      <label>
                        Bulk discount requested (%)
                        <input
                          type="number"
                          min="1"
                          max="50"
                          value={discount}
                          onChange={(e) => setDiscount(Number(e.target.value))}
                        />
                      </label>
                      <button
                        disabled={busy}
                        onClick={() =>
                          void act(async () =>
                            setOffer(
                              await shop<Offer>(
                                `/ucp/v1/checkout-sessions/${checkout.id}/offers`,
                                {
                                  line_items: checkout.line_items.map((l) => ({
                                    id: l.id,
                                    quantity: l.quantity,
                                    unit_price: {
                                      amount: Math.max(
                                        1,
                                        Math.round(
                                          l.item.price * (1 - discount / 100),
                                        ),
                                      ),
                                      currency: "USD",
                                    },
                                  })),
                                },
                              ),
                            ),
                          )
                        }
                      >
                        Request deal
                      </button>
                    </div>
                  )}
                  <div className={s.form}>
                    <label>
                      Test payment result
                      <select
                        value={payment}
                        onChange={(e) => setPayment(e.target.value)}
                      >
                        <option value="test_approved">
                          Approve test payment
                        </option>
                        <option value="test_declined">
                          Decline test payment
                        </option>
                      </select>
                    </label>
                  </div>
                  <div className={s.actions}>
                    <button
                      disabled={
                        busy ||
                        checkout.status !== "ready_for_complete" ||
                        (!!offer &&
                          ["pending_review", "countered"].includes(
                            offer.status,
                          ))
                      }
                      onClick={() =>
                        void act(async () => {
                          setCheckout(
                            await shop<Checkout>(
                              `/ucp/v1/checkout-sessions/${checkout.id}/complete`,
                              { payment_token: payment },
                            ),
                          );
                          await load();
                        })
                      }
                    >
                      Place test order
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          await shop(
                            `/ucp/v1/checkout-sessions/${checkout.id}/cancel`,
                            {},
                          );
                          setCheckout(null);
                          setOffer(null);
                        })
                      }
                    >
                      Cancel checkout
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
export function Storefront() {
  return (
    <BrowserRouter>
      <div className={s.shop}>
        <main>
          <header>
            <Link to="/shop">
              <strong>ShelfIQ Market</strong>
            </Link>
            <span className={s.note}>TEST STORE · No real payments</span>
            <a href="/">Staff console</a>
          </header>
          <Routes>
            <Route path="/shop" element={<Browse />} />
            <Route path="/shop/p/:id" element={<ProductWhy />} />
            <Route path="/shop/orders/:id" element={<BuyerOrder />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
