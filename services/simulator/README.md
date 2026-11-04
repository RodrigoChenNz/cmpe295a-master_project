# Simulator

Owner: Parth Gala. Implemented in `retail/simulation.py` and `retail/workers.py`.

`python -m retail worker simulator` publishes seeded synthetic store observations. `RetailPricingEnv` implements the Gymnasium contract for discrete DQN and continuous DDPG actions, demand response, stock, competitor movement, and profit/volatility/churn-proxy reward.

Evaluation uses common exogenous draws and held-out seeds. All generated observations identify `source=simulator`.

See [software guide](../../docs/SOFTWARE.md).
