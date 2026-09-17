# pricing-agent — Layer 4

**Owner:** Parth Gala · **Status:** not started

Decides prices and publishes them with an explanation.

Ships in two stages, behind the same contract:
1. **Stub** — rule-based if/else. Exists so every other layer is unblocked in week 1.
2. **Real** — DDPG/DQN over the `simulator` gym environment, reward
   `Profit − λ·Volatility − γ·ChurnRisk`, plus a SHAP TreeExplainer emitting
   `retail/explain/{id}`.

Must clamp every action to the band from `retail/guardrail/{id}` (pending sign-off —
see [`contracts/README.md`](../../contracts/README.md)).

PyTorch · Stable-Baselines3 · gymnasium · SHAP.
