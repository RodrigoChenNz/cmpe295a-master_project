# Pricing agent

Owner: Parth Gala. Implemented in `retail/pricing.py`, `retail/workers.py`, and `retail/explain.py`.

Run `python -m retail worker pricing`. Default `PRICING_POLICY=rule`; DQN/DDPG require an explicit `PRICING_MODEL` checkpoint. Train and evaluate with `make train`.

Publishes recommendations and exact single-reference Shapley contributions in USD for the actual bounded price function. Listens to state, inventory, guardrails, approved prices and decision outcomes. The API owns approval and price publication.

See [software guide](../../docs/SOFTWARE.md). This is synthetic-data validation; no production effectiveness claim is made.
