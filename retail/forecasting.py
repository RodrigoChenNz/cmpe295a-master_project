"""Forecast baselines and optional Prophet/LSTM comparisons on chronological data."""

import math
import numpy as np


def forecast(history, horizon=7, method="seasonal"):
    y = np.asarray(history, dtype=float)
    if len(y) < 7 or not np.isfinite(y).all() or (y < 0).any():
        raise ValueError("At least seven finite nonnegative observations are required")
    if method == "seasonal":
        return np.maximum(0, np.resize(y[-7:], horizon))
    if method == "mean":
        return np.full(horizon, y[-7:].mean())
    if method == "prophet":
        import pandas as pd
        from prophet import Prophet

        ds = pd.date_range("2025-01-01", periods=len(y), freq="D")
        model = Prophet(daily_seasonality=False, yearly_seasonality=False)
        model.fit(pd.DataFrame({"ds": ds, "y": y}))
        future = pd.DataFrame(
            {"ds": pd.date_range(ds[-1] + pd.Timedelta(days=1), periods=horizon)}
        )
        return np.maximum(0, model.predict(future)["yhat"].to_numpy())
    if method == "lstm":
        import torch

        torch.manual_seed(42)
        torch.set_num_threads(1)
        scale = max(1.0, float(y.max()))
        windows = np.array(
            [y[i : i + 7] / scale for i in range(len(y) - 7)], dtype=np.float32
        )
        if len(windows) < 7:
            raise ValueError("LSTM training requires at least 21 observations")
        x = torch.tensor(windows[:, :, None])
        targets = torch.tensor(y[7:] / scale, dtype=torch.float32)[:, None]
        lstm = torch.nn.LSTM(1, 16, batch_first=True)
        head = torch.nn.Linear(16, 1)
        opt = torch.optim.Adam(
            list(lstm.parameters()) + list(head.parameters()), lr=0.01
        )
        for _ in range(100):
            opt.zero_grad()
            out, _ = lstm(x)
            loss = ((head(out[:, -1]) - targets) ** 2).mean()
            loss.backward()
            opt.step()
        tail = list(y[-7:] / scale)
        result = []
        with torch.no_grad():
            for _ in range(horizon):
                out, _ = lstm(
                    torch.tensor(tail[-7:], dtype=torch.float32)[None, :, None]
                )
                pred = max(0.0, float(head(out[:, -1]).item()))
                tail.append(pred)
                result.append(pred * scale)
        return np.array(result)
    raise ValueError(f"Unknown forecasting method {method}")


def inventory_estimate(history, units, method="seasonal"):
    predicted = forecast(history, method=method)
    daily = float(predicted.mean())
    days = min(365, math.ceil(units / max(daily, 0.01)))
    # Synthetic risk score, not a calibrated probability of actual customer behavior.
    risk = float(np.clip((units - daily * 7) / max(units, 1), 0, 1))
    return {"daily_demand": daily, "days_to_clear": days, "risk": round(risk, 3)}
