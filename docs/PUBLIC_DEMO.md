# Free temporary public demo

This mode publishes the isolated `shelfiq-https-test` database through Cloudflare
Quick Tunnels. It uses synthetic products, buyers, payments and supplier data;
it does not publish the original local `retail-pricing` store or its credentials.
No hosting account, purchased domain or billing commitment is required.

## Start, inspect, test and stop

From the repository root, provision the existing local identities and HTTPS
configuration as described in DEPLOYMENT.md. Then run:

```sh
.venv/bin/python tools/public_demo.py start
.venv/bin/python tools/public_demo.py status
.venv/bin/python tools/public_demo_ready.py
.venv/bin/python tools/public_demo.py stop
```

`start` builds the existing API/frontend and a lightweight test-platform service,
starts both tunnels, reads the allocated hostnames and configures the merchant
and platform profiles. It preserves data and existing private keys. The tunnel
image is pinned to its pulled official image digest. Certificates remain verified
on the tunnel-to-store connection; `--no-tls-verify` is not used.

Private `.env.demo.public` contains configuration and a receiver-inspection token.
Do not share it. API staff credentials are used only on CA-verified
`https://localhost:8443` by the acceptance script; they are not transmitted through
the public tunnel. The public tests create fresh synthetic buyer identities and
validate response signatures. No staff credential is embedded in the public URL.

The printed `/shop` URL can be shared manually with teammates. The same origin's
staff console requires its separate demo account. This account is independent
of the original local store. This task does not create or share a teammate login.

## Separate public test platform

The second URL serves a platform profile and receives signed order notifications.
It publishes only its public signing key. The merchant business key is not mounted
into that service. Notifications are verified against the merchant's published
key and the order schema. Identical event IDs are accepted once; changed payloads
with a reused ID fail. Receipt metadata is private, and its inspection endpoint
requires the receiver token. Local files under `secrets/ucp/demo-receipts/` supply
the acceptance evidence without sending that inspection token externally.

The platform is a project test fixture. Successful signed requests and webhook
delivery demonstrate public-network integration, not commercial-platform approval.

## Availability and limits

Keep this Mac awake, Docker running and both tunnel containers running. URLs can
change when a tunnel restarts. After restarting, run `start` again so advertised
origins match the new URLs; run `status` to obtain the current link. `stop` closes
both public tunnels and preserves the local database.

Cloudflare Quick Tunnels have no uptime guarantee. The free public edge also
accepts TLS 1.2 even though the origin and verification clients require TLS 1.3.
The readiness JSON therefore distinguishes `passed` (demo workflow) from
`strict_public_conformance` (false on this free edge). Do not describe this mode
as production hosting or complete UCP conformance. Use the prepared deployment
with a controlled minimum-TLS-1.3 endpoint for strict acceptance.

Do not run `make https-test` concurrently with this mode: both use the same
isolated test project. To return to purely local HTTPS, stop the public tunnels,
then run `make https-test`. The original local software stack remains separate.

See EXTERNAL_ACCEPTANCE.md for exact upstream discovery and SDK results,
including skipped/failed legacy-suite checks.
