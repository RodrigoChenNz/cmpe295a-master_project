# Software verification — 2026-10-29

Branch: `codex/software-integration`. Original `main` remains `fefdb66d39b2be6aba89813a6f551929e4f2c51e`. No push or merge performed.

## Executed checks

- 36 automated tests passed against isolated PostgreSQL and real MQTT brokers, including TLS client authentication and publisher ACL enforcement.
- Rule, DQN and DDPG each completed observation → forecast → recommendation/explanation → approval → publication → correlated emulator acknowledgement.
- Dashboard TypeScript check and production build passed; npm production audit reported zero vulnerabilities.
- Editable Python package installation, all eight contract schemas, critical Python lint, and whitespace checks passed.
- Full Docker images built and all workers connected. A missing WebSocket runtime dependency was found and fixed; authenticated browser WebSocket connections were accepted.
- Plaintext local smoke: confirmed emulator render in 0.423 seconds. TLS smoke: confirmed emulator render in 0.475 seconds. These are individual observations, not latency benchmarks.
- A store observation queued during broker outage survived an API restart and delivered when the broker returned.
- Authenticated store-input endpoint accepted a synthetic observation over TLS, and the forecaster published a matching state-correlated seasonal forecast. Broker tests verify API state publication succeeds while pricing state publication is denied.
- Browser verification: sign in, software status, register label SHLF-999001, pair with Carrots 1kg, and confirm exact rendered lines.

## Synthetic model experiments

Both RL algorithms trained for 6,000 steps with seed 42. Five held-out seeds (101–105), 30 days each, used common exogenous random streams. These short runs demonstrate reproducibility and integration; they do not establish convergence or real-store benefit.

| Policy | Mean profit, synthetic USD | Standard deviation |
|---|---:|---:|
| fixed_markup | 1373.76 | 116.74 |
| rule | 1349.02 | 244.95 |
| dqn | 1692.24 | 209.59 |
| ddpg | 1733.55 | 177.89 |

Forecast training used 106 synthetic days and a chronological 14-day holdout.

| Forecast | RMSE, units/day | WAPE |
|---|---:|---:|
| mean | 2.621 | 13.03% |
| seasonal | 1.029 | 4.19% |
| prophet | 0.846 | 3.82% |
| lstm | 2.633 | 13.16% |

No superiority claim is made from one synthetic dataset. The learned policies can increase the churn proxy even when simulated profit improves. Fixed markup has zero price volatility under constant cost, so percentage reductions relative to that baseline are undefined.

## Remaining validation

Physical firmware, real-store/POS data mapping, real customer churn labels and calibration, production HTTPS/operations, and team approval of shared contract additions remain outside the verified software prototype. The API-only state-publication permission was authorized and applied; the pricing certificate remains unable to publish state or approved prices.

Generated checkpoints, episode monitor CSVs, raw evaluation exports and screenshots are local under gitignored `artifacts/`; certificates and account credentials are also excluded from Git. Reproduce with the commands in [SOFTWARE.md](SOFTWARE.md).
