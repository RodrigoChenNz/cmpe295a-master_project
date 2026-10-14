# pricing-agent — Layer 4

**Owner:** Parth Gala · **Status:** not started

Recommends prices and explains them. **It does not set prices.**

Publishes [`PriceProposal`](../../contracts/proposal.schema.json) to
`retail/proposal/{id}` — never to `retail/price/{id}`. `dashboard-api` evaluates each
proposal against the manager's standing policy and either applies it or queues it for a
human. Nothing reaches a shelf without passing that gate.

Ships in two stages, behind the same contract:
1. **Stub** — rule-based if/else. Exists so every other layer is unblocked in week 1.
2. **Real** — DDPG/DQN over the `simulator` gym environment, reward
   `Profit − λ·Volatility − γ·ChurnRisk`, plus a SHAP TreeExplainer emitting
   `retail/explain/{id}`.

Three things the approval gate asks of you:

- Clamp proposals to the band from `retail/guardrail/{id}`. A proposal outside it will be
  queued rather than applied, which wastes a manager's attention.
- Set `expires_at` honestly — how long does your reasoning actually hold?
- **Subscribe to `retail/price/{id}`.** That is how you learn whether your recommendation
  was taken, overridden or rejected. Approval rate per `agent_version` is a feedback signal
  and an evaluation result worth reporting in the paper.

PyTorch · Stable-Baselines3 · gymnasium · SHAP.
