"""Bounded rule policy and trained DQN/DDPG inference."""

from pathlib import Path
import numpy as np

from .simulation import DELTAS


class PricingPolicy:
    def __init__(self, algorithm="rule", model_path=None):
        self.algorithm = algorithm
        self.model = None
        if algorithm != "rule":
            if not model_path or not Path(model_path).is_file():
                raise ValueError(f"{algorithm} requires an existing model checkpoint")
            from stable_baselines3 import DQN, DDPG

            if algorithm not in {"dqn", "ddpg"}:
                raise ValueError("Unknown policy")
            self.model = {"dqn": DQN, "ddpg": DDPG}[algorithm].load(
                model_path, device="cpu"
            )
            if self.model.observation_space.shape != (6,):
                raise ValueError(
                    "Checkpoint does not match the six-feature retail environment"
                )

    def price(self, obs, *, cost, minimum, maximum):
        obs = np.asarray(obs, dtype=np.float32)
        current = float(obs[2]) * 2 * cost
        if self.model is None:
            # Small adjustments keep the rule baseline useful for live integration.
            competitor = float(obs[1]) * 2 * cost
            delta = np.clip(
                (competitor - current) * 0.25 + (0.5 - float(obs[0])) * 0.4, -0.5, 0.5
            )
        else:
            action, _ = self.model.predict(obs, deterministic=True)
            delta = (
                float(np.asarray(action).reshape(-1)[0])
                if self.algorithm == "ddpg"
                else DELTAS[int(action)]
            )
        return round(float(np.clip(current + delta, minimum, maximum)), 2)


def observation(state, forecast=None):
    cost = max(float(state["unit_cost"]), 0.01)
    demand = forecast if forecast is not None else np.mean(state["sales_history"][-7:])
    return np.array(
        [
            float(state["units_on_hand"]) / 200,
            float(state["competitor_price"]) / (2 * cost),
            float(state["current_price"]) / (2 * cost),
            float(demand) / 40,
            int(state["day_of_week"]) / 6,
            int(state["time_of_day_bucket"]) / 3,
        ],
        dtype=np.float32,
    ).clip(0, 1)
