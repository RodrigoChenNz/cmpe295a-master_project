/* The one place this app talks to dashboard-api.
 *
 * `credentials: 'include'` on every request, because the session is an httpOnly
 * cookie — there is no token in localStorage to steal, and no code here that
 * could read one if there were.
 */

import type {
  ActivityRow, ApproveResult, CategoryRule, DashboardData, Facets, Label,
  Meta, Page, Product, ProductDetail, StockHealth, PriceReview, SavedView,
  Suggestion, SuggestionDetail, User,
} from './types';

const BASE = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');

/** Thrown for any non-2xx. Carries the status so callers can tell "someone
 *  else already decided this" (409) from "you are not signed in" (401) from
 *  "the service is down" without string-matching a message. */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      credentials: 'include',
      headers: init?.body ? { 'content-type': 'application/json' } : undefined,
      ...init,
    });
  } catch {
    // A network failure is not a 500 and must not read as one: the service
    // being unreachable is a different thing to say than "something broke".
    throw new ApiError(0, `Cannot reach the dashboard service at ${BASE}.`);
  }

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }

  if (!res.ok) {
    throw new ApiError(res.status, detailOf(body) ?? `Request failed (${res.status}).`);
  }
  return body as T;
}

/** FastAPI puts a string in `detail` for raised HTTPExceptions and an array of
 *  per-field objects there for validation errors. Both have to become one
 *  readable sentence. */
function detailOf(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const item = d as { loc?: unknown[]; msg?: string };
        const field = Array.isArray(item.loc) ? item.loc.at(-1) : undefined;
        return field ? `${String(field)}: ${item.msg ?? ''}` : (item.msg ?? '');
      })
      .filter(Boolean)
      .join('; ');
  }
  return null;
}

function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    // Repeated keys for arrays, which is what FastAPI's Query(list) expects.
    if (Array.isArray(value)) value.forEach((v) => sp.append(key, String(v)));
    else sp.append(key, String(value));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) });
const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) });

export const api = {
  base: BASE,

  /* ── Session ───────────────────────────────────────────────────────── */
  signIn: (email: string, password: string) =>
    post<{ user: User; expires_at: string }>('/api/auth/signin', { email, password }),
  signOut: () => post<{ signed_out: boolean }>('/api/auth/signout'),
  me: () => request<{ user: User | null }>('/api/auth/me'),

  meta: () => request<Meta>('/api/meta'),

  /* ── Dashboard ─────────────────────────────────────────────────────── */
  dashboard: (days: number) => request<DashboardData>(`/api/dashboard${qs({ days })}`),

  /* ── Suggestions ───────────────────────────────────────────────────── */
  suggestions: (p: {
    q?: string;
    category?: string;
    direction?: 'any' | 'up' | 'down';
    size?: 'any' | 'small' | 'large';
    page?: number;
    page_size?: number;
  }) => request<Page<Suggestion>>(`/api/suggestions${qs(p)}`),

  suggestion: (id: string) => request<SuggestionDetail>(`/api/suggestions/${id}`),

  /** Omit `price` to take the agent's number; pass one to override it. */
  approve: (id: string, price?: string) =>
    post<ApproveResult>(`/api/suggestions/${id}/approve`, price ? { price } : {}),
  reject: (id: string, reason?: string) =>
    post<{ decision_id: string; status: string }>(`/api/suggestions/${id}/reject`, { reason }),

  bulkApprove: (ids: string[]) =>
    post<{ approved: ApproveResult[]; failed: { decision_id: string; error: string }[]; counts: { approved: number; failed: number } }>(
      '/api/suggestions/bulk/approve',
      { decision_ids: ids },
    ),
  bulkReject: (ids: string[]) =>
    post<{ rejected: unknown[]; failed: { decision_id: string; error: string }[]; counts: { rejected: number; failed: number } }>(
      '/api/suggestions/bulk/reject',
      { decision_ids: ids },
    ),

  /* ── Products ──────────────────────────────────────────────────────── */
  products: (p: {
    q?: string;
    category?: string[];
    stock?: StockHealth[];
    review?: PriceReview[];
    view?: SavedView;
    sort?: string;
    dir?: 'asc' | 'desc';
    page?: number;
    page_size?: number;
  }) => request<Page<Product>>(`/api/products${qs(p)}`),

  facets: (q?: string) => request<Facets>(`/api/products/facets${qs({ q })}`),
  product: (id: string) => request<ProductDetail>(`/api/products/${id}`),
  saveProduct: (id: string, body: unknown) => put<ProductDetail>(`/api/products/${id}`, body),
  createProduct: (body: unknown) => post<ProductDetail>('/api/products', body),
  setPrice: (id: string, price: string, reason_tag: string) =>
    post<{ published: boolean; queued: number }>(`/api/products/${id}/price`, {
      price,
      reason_tag,
    }),
  archiveProducts: (ids: string[]) =>
    post<{ archived: string[]; count: number }>('/api/products/bulk/archive', {
      product_ids: ids,
    }),
  recategorise: (ids: string[], category: string) =>
    post<{ updated: string[]; count: number }>('/api/products/bulk/category', {
      product_ids: ids,
      category,
    }),

  /* ── Labels ────────────────────────────────────────────────────────── */
  labels: (p: { q?: string; status?: string[]; kind?: 'any' | 'lcd' | 'eink'; page?: number; page_size?: number }) =>
    request<Page<Label> & { by_status: Record<string, number>; network: Meta['broker'] }>(
      `/api/labels${qs(p)}`,
    ),
  resendLabels: (ids: string[]) =>
    post<{ sent: string[]; skipped: { label_id: string; reason: string }[]; count: number }>(
      '/api/labels/resend',
      { label_ids: ids },
    ),

  /* ── Activity ──────────────────────────────────────────────────────── */
  activity: (p: {
    q?: string;
    range?: 'today' | '7d' | '30d' | '90d' | 'all';
    decision?: 'all' | 'suggested' | 'edited' | 'auto' | 'manual';
    shelf?: 'all' | 'confirmed' | 'pending' | 'failed' | 'none';
    page?: number;
    page_size?: number;
  }) => request<Page<ActivityRow>>(`/api/activity${qs(p)}`),

  /** A URL rather than a fetch: the browser's own download handling is better
   *  than anything this app would do with a blob. */
  activityCsvUrl: (p: Record<string, unknown>) => `${BASE}/api/activity/export.csv${qs(p)}`,

  /* ── Rules ─────────────────────────────────────────────────────────── */
  rules: () =>
    request<{
      limits: Record<string, number | string | boolean | null> | null;
      categories: CategoryRule[];
      unconfigured: { category: string; product_count: number }[];
    }>('/api/rules'),
};
