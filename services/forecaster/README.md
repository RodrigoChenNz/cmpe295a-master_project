# forecaster — Layer 4

**Owner:** Aruna Samhitha Mutyala · **Status:** not started

Predicts when stock clears and scores dead-stock risk; publishes `retail/inventory/{id}`.

1. **Stub** — emits plausible random risk on the real topic, week 1.
2. **Real** — Prophet and an LSTM, compared on RMSE/MAPE (that comparison is a paper
   result, so keep both). Plus the churn-risk ensemble feeding the agent's γ term.

Prophet · PyTorch · pandas · scikit-learn · SHAP.
