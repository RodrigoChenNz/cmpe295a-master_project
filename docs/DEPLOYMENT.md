# HTTPS deployment and UCP acceptance

The deployment is prepared and tested locally. No public domain, DNS record,
cloud resource or public storefront has been created. Hardware remains a
separate acceptance task. All payments and supplier purchases remain simulated.

## Run the isolated HTTPS acceptance stack

Install the Python development dependencies from SOFTWARE.md and start Docker.
On a fresh checkout, run `make secure-identities` first (it refuses to overwrite
existing broker identities). Then:

```sh
make https-keys
make https-test
make ucp-ready
```

This creates ignored private files under `secrets/ucp/` and `.env.deploy.local`.
The latter has independent random database/bootstrap credentials. No secret is
printed. The API runs with your configured numeric UID/GID so it can read its
mode-600 signing key. The CA is not installed into your computer/browser trust
store. The acceptance client explicitly trusts `secrets/ucp/ca.pem`; do not bypass
certificate verification to demonstrate HTTPS. The local server certificate
expires after 90 days. Existing keys/certificates are preserved on reruns.

Only loopback port 8443 is exposed. PostgreSQL, MQTT and API ports stay private.
The `shelfiq-https-test` project has separate database volumes from the existing
`retail-pricing` software stack. Its seed and test purchases are synthetic.
`make https-down` stops only this isolated stack and retains its data.

`make ucp-ready` creates a labelled synthetic purchase and writes
`artifacts/verification/ucp-readiness.json`. It verifies actual TLS 1.3 with hostname
and CA verification, rejects TLS 1.2 and unsigned platform requests, validates
signed discovery/catalog/checkout/order responses against pinned official
schemas, checks idempotency, test declines/approvals, buyer isolation, secure
staff cookies and the built storefront served through Caddy. It uses an
independent wire-level client in `retail/ucp_client.py`. This is local acceptance,
not certification by an external UCP platform.

## Prepared public deployment

Once an owned domain and Linux Docker host are available:

1. Create DNS for the intended store domain pointing to that host. Permit
   inbound 80/443 only for the storefront. Do not expose the database, API or
   broker directly. A physical gateway needs a separately configured authenticated
   MQTT access path; the current manifest deliberately exposes no broker port.
2. Provision the private broker identities and business Ed25519 key on the host.
   Preserve their permissions and never add them to Git or a container image.
   Keep the platform test key and local CA off the public deployment.
3. Create a separate private `.env.deploy` (mode 600), using the variable names
   below and fresh random secrets. Use the same owned origin for PUBLIC_URL and
   SCHEMA_ORIGIN so one gateway serves both protocol and extension documents.
4. Validate and start the manifest:

```sh
docker compose --env-file .env.deploy -f infra/docker-compose.deploy.yml config --quiet
docker compose --env-file .env.deploy -f infra/docker-compose.deploy.yml up -d --build --wait
```

Caddy obtains/renews public certificates automatically when DNS/ports are ready.
It requires TLS 1.3, serves the compiled React app, proxies API/WebSocket paths,
and adds browser security headers. Signed protocol bodies are not compressed
or rewritten. The API trusts forwarded headers only from the gateway's fixed
Docker IP. Change the subnet and trusted proxy IP together if the chosen subnet
conflicts with the deployment network. Public mode refuses reserved development
hosts, IP literals, nonstandard HTTPS ports and missing signing keys.

Private environment keys (replace every placeholder; do not use these literally):

```dotenv
STORE_DOMAIN=store.YOUR-OWNED-DOMAIN
PUBLIC_URL=https://store.YOUR-OWNED-DOMAIN
SCHEMA_ORIGIN=https://store.YOUR-OWNED-DOMAIN
RUN_UID=YOUR_DEPLOYMENT_USER_ID
RUN_GID=YOUR_DEPLOYMENT_GROUP_ID
DB_PASSWORD=GENERATE_A_PRIVATE_URL_SAFE_SECRET
SHELFIQ_BOOTSTRAP_EMAIL=YOUR_ADMIN_EMAIL
SHELFIQ_BOOTSTRAP_PASSWORD=GENERATE_A_PRIVATE_SECRET
COMMERCE_WEBHOOK_KEY=GENERATE_A_PRIVATE_SECRET_AT_LEAST_32_CHARACTERS
```

To include real forecasting and model pricing workers, train/copy a checkpoint
under `artifacts/`, set `PRICING_POLICY=dqn` and
`PRICING_MODEL=/srv/artifacts/dqn.zip`, then layer
`-f infra/docker-compose.deploy-agents.yml` after the deployment manifest. The
workers use their existing role-specific mutual-TLS identities. This overlay
contains no observation simulator or display emulator. Live observations must
come through the authenticated API or the teammate's gateway. Nightly pricing
requires a running worker; its absence is reported as a failed dependency.
This production-worker overlay is configuration-validated; the existing local
software stack provides live model/forecast/emulator acceptance evidence.

Before using real customer data, replace demonstration policies and test payment
instruments, arrange backups and restore tests, and verify the intended provider
and external-platform flows. No real provider integration is claimed here.

## Protocol scope and compatibility

Public `/ucp/v1/*` implements the pinned UCP 2026-08-25 catalog search/lookup/product,
checkout create/get/update/complete/cancel, fulfillment and order read bindings.
All request/response schemas resolve offline against 86 vendored official
files; `python tools/validate_ucp_vendor.py` checks integrity/reference closure.
See https://ucp.dev/2026-08-25/specification/overview/ and vendor/ucp/README.md.

The local browser uses `/api/storefront/*`, retaining its existing simpler
checkout and test-payment payloads. It is independent of the public binding.
Buyer bearer tokens are random ownership secrets and are hashed in storage;
staff authentication remains cookie based. Platform signatures authenticate the
advertised platform identity separately from buyer ownership.

Public mode fetches platform profiles over certificate-verified TLS 1.3. DNS is
resolved once and pinned for each connection; private/special IPs, redirects,
non-443 URLs and oversized responses are rejected. Profiles are cached for up
to 60 seconds. Negotiation checks exact version intersections, extension parents
and reverse-domain schema authority. Requests require UCP-Agent, Request-Id,
RFC 9421 Ed25519 signatures and Content-Digest. Mutations also require an
Idempotency-Key. Responses are signed and bind the echoed Request-Id.

The payment handler and negotiation extension namespace derive from the
controlled schema host. Local `.test` namespaces are fixtures only; they assert
no domain ownership. Only advertised test tokens are accepted. If there is no
shared handler, checkout requires escalation and completion is refused.

Order notifications use the negotiated platform order capability's webhook_url,
restricted to the platform profile host and public HTTPS on port 443. Ed25519
signatures cover the method, target, digest, webhook ID and timestamp. The
transactional outbox retains failures for retry and reuses a stable event ID;
receivers must deduplicate IDs. The private local HMAC receiver remains available
for the original storefront demonstration.

Known pinned-schema conflict: adjustment.json describes negative refund totals,
but its referenced amount schema requires nonnegative amounts. Public order
adjustments therefore omit the optional totals and state the exact refund in
their description. Original charged totals stay unchanged; negative quantities
identify affected units. Exact structured refund totals remain in the staff/
local order API and accounting ledger. This boundary is covered by a regression
test and must be revisited when the upstream schema is corrected.

Single-variant whole-item USD products and one fulfillment method per checkout
are supported. Unsupported filters/options or currency conversion fail clearly.
Split fulfillment, real payment processors and arbitrary UCP extensions are not
implemented. Actual public DNS/certificate issuance and a third-party platform
acceptance test remain pending until hosting/domain ownership is available.
