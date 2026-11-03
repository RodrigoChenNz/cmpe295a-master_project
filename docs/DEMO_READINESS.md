# End to end demo readiness

Status checked on November 3, 2026. The pricing software demo passes live acceptance. Physical-device integration and the expanded commerce scope are incomplete. Main remains unchanged.

## What is working

- Seeded store observations, seasonal demand forecasts and an active trained DQN checkpoint.
- Rule, DQN and DDPG pricing interfaces, price explanations and manager approval or delegation.
- Transactional price history and delivery jobs, MQTT with mutual TLS, persistent emulator state and matching render acknowledgements.
- Console, role checks, current pipeline activity, label registration and pairing, audit history, additive migrations and failure recovery.
- Local verification: 38 tests pass, frontend typecheck/build pass, contracts and critical Python lint pass. The build has a bundle-size advisory.
- Live `make ready` passed: recent activity at all four stages, configured DQN selected, no pending deliveries, and a confirmed emulator render. One observed round trip took 0.259 seconds; this is not a latency benchmark or a physical-display result.

The prior GitHub CI checks on PR #6 passed. The updated workflow also runs the TLS permission tests rather than skipping them. Confirm its latest revision is green before team integration.

## Demonstrate it

1. Open http://localhost:5173/system and sign in with the local account configured in `.env` if needed. The API on this Mac uses port 18000 because 8000 was occupied.
2. Check that the broker is connected and that Observations, Forecasting, Pricing and Display receiver show Recent. The latest policy should match the selected configuration; this Mac uses DQN.
3. Open Price Suggestions, choose a recent recommendation for a product with a paired label and read its explanation. Approve it, override within bounds, or reject it. New observations can supersede an older pending recommendation, so refresh if it is no longer actionable.
4. After approval, open that label and inspect the confirmed publication and returned display text. Emulated reports are identified explicitly.
5. Run `make ready` from the repository root for an automated approval-to-render check. Keep its JSON output as evidence; the command exits nonzero when a required check fails.

For a fresh checkout, follow SOFTWARE.md. The clean-clone default is the explicit rule policy; run the documented training commands and select an existing checkpoint to demonstrate DQN/DDPG. Checkpoints and private credentials are not committed.

## What still prevents a complete physical demo

PR #5 at `463eb05` adds e-ink and gateway code plus benchmark results. Its integration code is unchanged from the reviewed `f9d12f2` version. The new `eink.json` contains 20 trials: 11 reported confirmations and 9 missed confirmations. Reported successful confirmation times span roughly 2.8 to 85.2 seconds. These are the team's recorded bench observations, not a validation performed on this Mac, and the existing price-hash correlation cannot distinguish every new decision.

Before integrating that PR, fix the additive database upgrade, publication/acknowledgement identity and concurrent adoption issues described in PR_REVIEW_2026-10-30.md. Then verify the firmware and gateway against the approved contract, provision the intended device, and run power-loss, disconnect/reconnect, duplicate/stale message and repeated-price tests. No ESP32 USB serial device was visible during this status check. A physical test needs the board/gateway connected here or a teammate to execute and return the evidence.

Do not use an emulator confirmation or a broker publish as proof that a physical label redrew.

## Expanded scope requiring a separate acceptance target

PR #3 proposes nightly orchestration, UCP checkout, storefront, procurement, orders, accounting and feedback-based retraining. These features are not implemented by the pricing integration. They require a chosen interface, implementation and acceptance tests before the full-store scope can be called complete. The agent's synthetic churn proxy is not a validated customer churn model, and real-store benefits remain unmeasured.

## Team integration

PR #6 targets dev. Contract additions need normal team review; neither draft completion nor green CI is an approval from teammates. Keep main untouched until the agreed dev-to-stage-to-main process is complete. The shared report has not been edited during this readiness work.
