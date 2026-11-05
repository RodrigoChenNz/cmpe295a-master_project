# Public hosting and external acceptance status

Checked November 5, 2026. A free temporary public demo is running through two
Cloudflare Quick Tunnels, using the isolated synthetic HTTPS-test database.
There is no paid hosting account, purchased domain or billing commitment.
Use `python tools/public_demo.py status` for the current URLs; they can change.

## Public network acceptance

`python tools/public_demo_ready.py` passed these checks over the actual public
hostnames: public CA-validated TLS 1.3, storefront and custom schema hosting,
signed profile negotiation, catalog, idempotent checkout, test payment decline
and approval, order retrieval, buyer isolation and a signed order notification
delivered to the separate public test-platform endpoint. The receiver validates
Ed25519 signatures and the standard order schema before persisting a receipt.
Receipt inspection is authenticated; the acceptance script inspects the receipt
locally and sends staff credentials only to CA-verified localhost.

Evidence: `artifacts/verification/public-demo-readiness.json` and
`artifacts/verification/public-storefront.png` (local and ignored).

**Strict public conformance is incomplete:** Cloudflare's free tunnel edge also
accepts TLS 1.2. ShelfIQ's origin and test clients require TLS 1.3, but the free
edge policy cannot be controlled by this setup. This result must not be called
production UCP compliance. The second host is a project-owned test fixture,
not a commercial shopping platform. All payments and purchases remain simulated.

## Independent upstream discovery result

The unmodified official UCP conformance suite was checked out at:

- Repository: https://github.com/Universal-Commerce-Protocol/conformance
- Commit: `f33ae5409448192604412f67cb4e78d6f46e2769`
- Declared SDK dependency: `ucp-sdk==0.4.6`
- Target: isolated ShelfIQ HTTPS deployment at `https://localhost:8443`
- Merchant input: UCP `2026-08-25`, declaring ShelfIQ's five standard capabilities
- TLS: explicit trust of the local test CA, certificate validation enabled

The `protocol_test` discovery selection ran two cases: **one passed and one was
skipped upstream** (`test_discovery_urls`). There were no failures or errors.
The passing assertion validates the business profile using the official SDK,
including the version, expected capabilities, namespace names and payment-handler
structure. This result is local independent discovery validation, not a complete
checkout suite, public reachability test or certification by a shopping platform.

The checkout suite uses a mock platform profile served over HTTP and a placeholder
`request-signature: test`; ShelfIQ's public adapter requires a public HTTPS profile,
a verifiable Ed25519 signature and separate buyer ownership. The upstream defaults
also use their sample card handler. Those transport and fixture differences must
be handled explicitly before running the full lifecycle suite. Disabling ShelfIQ's
signature verification or changing upstream assertions would not demonstrate
production interoperability.

## Public SDK compatibility

The same unmodified conformance selection was attempted on the temporary public
hostname with its declared `ucp-sdk==0.4.6` dependency. It ran two cases: one error
and one upstream skip. That SDK rejects hyphens in the namespace derived from
the tunnel hostname. The current pinned UCP 2026-08-25 schema explicitly permits
interior hyphens; the official SDK's compatibility table maps 0.4.x to the older
2026-04-08 protocol.

Both actual public profiles then passed validation with the matching official
SDK source, version 0.6.0, commit `86099bdb5befe3c8c65bbbce97dbfe6bf3cf6da8`,
using public certificate validation. No upstream assertions or schemas were
edited to obtain that result. See the SDK's version table:
https://github.com/Universal-Commerce-Protocol/python-sdk#ucp-version-compatibility
Evidence is in `artifacts/verification/official-sdk-public.json`.

## Commercial-platform preflight

The suite's bundled `google` profile was also evaluated. It **failed preflight**
because `google_pay` is absent; ShelfIQ advertises only its synthetic test-payment
handler. No checkout cases ran under that profile. This is the upstream suite's
configuration result, not a certification decision issued by Google. Actual
commercial-platform acceptance must not be claimed from the local demo.

## Next acceptance steps

1. Obtain a stable hostname and a public TLS policy that enforces minimum TLS 1.3
   if strict acceptance or always-on hosting is required. See DEPLOYMENT.md.
2. Repeat public acceptance on that stable host. The temporary-demo checks above
   already exercise the public storefront and signed platform flows.
3. Preserve separate merchant/platform signing identities and the authenticated
   receipt inspection used by this public demo.
4. Configure and run the applicable independent upstream suites with accurate
   merchant fixtures. Record every executed, failed and skipped assertion.
5. If acceptance by a commercial platform is required, complete its own merchant
   onboarding and required payment-provider setup. A passing test client or
   conformance suite is not evidence of approval by that platform.

All current payments and supplier purchases remain simulated. Main and the
shared report are unchanged.
