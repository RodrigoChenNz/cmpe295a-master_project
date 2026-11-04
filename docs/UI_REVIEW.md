# UI review for the final project demo

Reviewed November 4, 2026 against http://localhost:5173, using synthetic data.
This is a desktop browser review, not a mobile-device or accessibility audit.

## Verified through the UI

- Browse the listed product and create a pickup checkout with a synthetic email.
- Choose a declined test payment: a clear error states that no money was charged.
- Retry with an approved test payment: the checkout completes and the error clears.
- Open the buyer order confirmation with the original total and fulfillment counts.
- Find the same order in staff Orders and cancel its unfulfilled unit.
- Refresh the buyer page: canceled quantity, canceled status and exact test refund appear.
- Inspect Store agent: the persisted review shows all seven steps succeeded, with
  results for market evidence, forecast synchronization, pricing and reconciliation.

Screenshot evidence: `artifacts/verification/ui-order-refund.png` (local, ignored).
Automated acceptance separately checks transactional commerce, signed HTTPS UCP,
and the model/approval/display-emulator pipeline. No real payment occurs.

## Improvements made during review

- Clear a stale polling error after a successful order refresh and reset state
  when navigating to a different order.
- Show loading states for catalog and buyer orders. Distinguish an unsuccessful
  search from a store with no online listings; ignore stale search responses.
- Display readable checkout statuses and progress text while submitting.
- Show delivered/canceled counts, original test total and refunds on buyer orders.
- Allow the storefront header to wrap at narrow widths and wrap long order IDs.
  Validate these layouts on the team's actual mobile devices before submission.

## Suggestions for the team

1. Update the seed-data banner's wording. It currently describes all suggestions
   and price history as a design prototype. The running demo now combines seeded
   history with actual worker-generated proposals and transactions. Separate
   initial sample data, live simulator observations and hardware confirmations.
2. Show canceled and delivered columns alongside shipped/returned counts in the
   staff order table, with an event history and refund summary for each order.
   The backend enforces bounds, but the current staff summary hides useful counts.
3. Add status filters and compact expandable cards for orders and nightly runs.
   The current lists become long as the demo accumulates records.
4. Keep pricing vocabulary consistent across the unified inbox, Price Suggestions
   and label details: proposed, approved, scheduled, published and render confirmed.
   A broker publication must not be presented as physical display confirmation.

Suggested teammate update:

> I tested the customer checkout and staff order flow, including a declined
> payment, successful retry and cancellation/refund. Those flows work with test
> data. I improved loading/error messages and the buyer refund summary. My main
> suggestions are clearer data-source labels, more complete staff fulfillment
> counts, compact/filterable lists and consistent pricing status wording. We
> still need a real-device mobile check and the teammate's physical-label test.
