# Forecaster

Owner: Aruna Samhitha Mutyala. Implemented in `retail/forecasting.py` and `retail/workers.py`.

Run `python -m retail worker forecaster`. Live default: seasonal demand baseline. `make forecast` compares mean, seasonal, Prophet and LSTM using chronological held-out synthetic data and exports RMSE/WAPE.

Inventory risk is explicitly a heuristic score, not a calibrated churn probability. Real customer data and a validated churn ensemble remain research work.

See [software guide](../../docs/SOFTWARE.md).
