/* The one place this app talks to dashboard-api.
 *
 * `credentials: 'include'` on every request, because the session is an httpOnly
 * cookie — there is no token in localStorage to steal, and no code here that
 * could read one if there were.
 */

import type {
  ActivityRow, ApproveResult, InviteInfo, InviteIssued, LocationLevel, BlinkResult, CategoryRule, DashboardData, Facets,
  Label, LabelDetail, LocationImpact, LocationTree, Meta, MqttTestResult,
  NewProductBody, Page, Product, ProductDetail, StockHealth, PriceReview,
  RulesPreview, RulesResponse, SavedView, SettingsResponse, Store, Suggestion,
  SuggestionDetail, User, UserRow,
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
const del = <T>(path: string) => request<T>(path, { method: 'DELETE' });

export const api = {
  base: BASE,

  /* ── Session ───────────────────────────────────────────────────────── */
  signIn: (email: string, password: string) =>
    post<{ user: User; expires_at: string }>('/api/auth/signin', { email, password }),
  signOut: () => post<{ signed_out: boolean }>('/api/auth/signout'),
  me: () => request<{ user: User | null; setup_required: boolean }>('/api/auth/me'),
  /** First-run only: creates the store's first account, as super admin. */
  setup: (body: { username: string; email: string; full_name: string; password: string }) =>
    post<{ user: User; expires_at: string }>('/api/auth/setup', body),
  invite: (token: string) => request<InviteInfo>(`/api/auth/invite/${encodeURIComponent(token)}`),
  acceptInvite: (token: string, password: string) =>
    post<{ user: User; expires_at: string }>('/api/auth/invite/accept', { token, password }),

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
    /** A location id (its whole subtree), or 'unassigned'. */
    location?: string;
    sort?: string;
    dir?: 'asc' | 'desc';
    page?: number;
    page_size?: number;
  }) => request<Page<Product>>(`/api/products${qs(p)}`),

  facets: (q?: string) => request<Facets>(`/api/products/facets${qs({ q })}`),
  product: (id: string) => request<ProductDetail>(`/api/products/${id}`),
  saveProduct: (id: string, body: unknown) => put<ProductDetail>(`/api/products/${id}`, body),
  createProduct: (body: NewProductBody) => post<ProductDetail>('/api/products', body),
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
  /** Link products to a location, or pass null to unlink them. */
  setProductsLocation: (ids: string[], location_id: string | null) =>
    post<{ updated: string[]; count: number }>('/api/products/bulk/location', {
      product_ids: ids,
      location_id,
    }),

  /* ── Labels ────────────────────────────────────────────────────────── */
  labels: (p: {
    q?: string;
    status?: string[];
    kind?: 'any' | 'lcd' | 'eink';
    paired?: 'any' | 'paired' | 'unpaired';
    location?: string;
    sort?: string;
    dir?: 'asc' | 'desc';
    page?: number;
    page_size?: number;
  }) =>
    request<Page<Label> & { by_status: Record<string, number>; network: Meta['broker'] }>(
      `/api/labels${qs(p)}`,
    ),

  labelDetail: (id: string) => request<LabelDetail>(`/api/labels/${id}`),

  /** Omit `slot_code` to leave the position alone; send null to clear it. */
  pairLabel: (id: string, body: { product_id?: string | null; slot_code?: string | null }) =>
    post<LabelDetail & { warning?: string }>(`/api/labels/${id}/pair`, body),

  blinkLabel: (id: string, seconds = 3) =>
    post<BlinkResult>(`/api/labels/${id}/blink`, { seconds }),
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

  /* ── Locations ─────────────────────────────────────────────────────── */
  locations: () => request<LocationTree>('/api/locations'),
  levels: () =>
    request<{ levels: LocationLevel[]; configured: boolean; depth_locked: boolean }>(
      '/api/locations/levels',
    ),
  /** Top level first. Renaming is always allowed; changing how many there are
   *  only while the layout is empty. */
  saveLevels: (names: string[]) =>
    put<{ levels: LocationLevel[] }>('/api/locations/levels', { names }),
  locationImpact: (id: string) => request<LocationImpact>(`/api/locations/${id}/impact`),
  /** The level comes from the parent: one below it, or the top level. */
  createLocation: (body: { parent_id: string | null; name: string }) =>
    post<{ location_id: string; depth: number; level_name: string }>('/api/locations', body),
  renameLocation: (id: string, name: string) =>
    put<{ location_id: string; name: string }>(`/api/locations/${id}`, { name }),
  moveLocation: (id: string, parent_id: string | null) =>
    put<{ location_id: string }>(`/api/locations/${id}/move`, { parent_id }),
  /** The counts are echoed back from `locationImpact` and rechecked server-side,
   *  so a stale confirmation is refused rather than acted on. */
  deleteLocation: (id: string, expect: { slots: number; labels: number; products: number }) =>
    del<{
      deleted: { locations: number; slots: number; products_unassigned: number };
      labels_cleared: string[];
    }>(
      `/api/locations/${id}${qs({
        expect_slots: expect.slots,
        expect_labels: expect.labels,
        expect_products: expect.products,
      })}`,
    ),
  createSlot: (location_id: string, slot_code: string) =>
    post<{ slot_code: string }>('/api/locations/slots', { location_id, slot_code }),
  updateSlot: (slot_code: string, body: { slot_code?: string; location_id?: string }) =>
    put<{ slot_code: string }>(`/api/locations/slots/${slot_code}`, body),
  deleteSlot: (slot_code: string) => del<{ deleted: string }>(`/api/locations/slots/${slot_code}`),

  /* ── Users ─────────────────────────────────────────────────────────── */
  users: () => request<{ rows: UserRow[]; roles: string[] }>('/api/users'),
  inviteUser: (body: { username: string; email: string; full_name: string; role: string }) =>
    post<InviteIssued & { note: string }>('/api/users', body),
  /** A fresh link for someone who has not joined yet; older links stop working. */
  reissueInvite: (username: string) => post<InviteIssued>(`/api/users/${username}/invite`),
  setUserRole: (username: string, role: string) =>
    put<{ username: string; role: string }>(`/api/users/${username}/role`, { role }),
  setUserActive: (username: string, is_active: boolean) =>
    put<{ username: string; is_active: boolean }>(`/api/users/${username}/active`, { is_active }),
  /** Only ever succeeds for an invite that was never taken up. */
  deleteUser: (username: string) => del<{ deleted: string }>(`/api/users/${username}`),

  /* ── Rules ─────────────────────────────────────────────────────────── */
  rules: () => request<RulesResponse>('/api/rules'),
  putLimits: (body: {
    max_change_pct: number;
    max_changes_per_day: number;
    min_hours_between: number;
    proposal_ttl_hours: number;
  }) => put<{ limits: RulesResponse['limits'] }>('/api/rules/limits', body),
  rulesPreview: (category: string, p: {
    min_margin_pct: number;
    ceiling_pct?: number | null;
    agent_may_suggest: boolean;
    apply_agent_flag: boolean;
  }) => request<RulesPreview>(`/api/rules/categories/${encodeURIComponent(category)}/preview${qs(p)}`),
  putCategoryRule: (category: string, body: {
    ceiling_pct: number | null;
    min_margin_pct: number;
    agent_may_suggest: boolean;
    apply_agent_flag: boolean;
  }) => put<{ rule: CategoryRule; republished: { products: number; published: number; queued: number } }>(
    `/api/rules/categories/${encodeURIComponent(category)}`, body,
  ),

  /* ── Settings ──────────────────────────────────────────────────────── */
  settings: () => request<SettingsResponse>('/api/settings/store'),
  saveSettings: (body: Partial<Store>) => put<{ store: Store }>('/api/settings/store', body),
  testMqtt: (body: { host?: string; port?: number; tls?: boolean }) =>
    post<MqttTestResult>('/api/settings/mqtt/test', body),
};
