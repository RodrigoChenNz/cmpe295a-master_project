# Universal Commerce Protocol schemas

These unmodified schema documents were retrieved from the official versioned
UCP site, https://ucp.dev/2026-08-25/, for offline validation. JSON whitespace
has been normalized; schema semantics and `$id` values are preserved.

Upstream: https://github.com/Universal-Commerce-Protocol/ucp
License: Apache License 2.0 (see LICENSE). Copyright belongs to the respective
upstream contributors. ShelfIQ is an independent implementation, with no claim
of upstream certification or endorsement.

`2026-08-25/manifest.json` records each source URL and the SHA-256 of its local
formatted file. Run `python tools/validate_ucp_vendor.py` to check integrity.
The application resolves references only against this vendored registry; it
does not download schemas while serving requests. Changing protocol versions
requires a deliberate schema refresh and contract-test review.
