"""Seeded retail dynamics shared by training and held-out evaluation.

Each step is a simulated day. Costs are fixed within an episode. All policies
see the same exogenous random stream for the same seed. Churn is a synthetic
price-sensitivity proxy, never a measured customer probability.
"""

import math
from dataclasses import dataclass

import gymnasium as gym
import numpy as np
from gymnasium import spaces

FEATURES = (
    "inventory_level",
    "competitor_price",
    "current_price",
    "forecast_demand",
    "day_of_week",
    "time_of_day",
)
DELTAS = np.array([-0.50, -0.25, 0, 0.25, 0.50])


@dataclass
class RetailConfig:
    horizon: int = 30
    cost: float = 8.0
    capacity: int = 200
    base_demand: float = 18.0
    volatility_weight: float = 0.3
    churn_weight: float = 0.5
    warmup_episodes: int = 50


class RetailPricingEnv(gym.Env):
    metadata = {"render_modes": ["ansi"]}

    def __init__(self, continuous=False, config=None):
        super().__init__()
        self.config = config or RetailConfig()
        self.continuous = continuous
        self.action_space = (
            spaces.Box(-1, 1, (1,), dtype=np.float32)
            if continuous
            else spaces.Discrete(5)
        )
        self.observation_space = spaces.Box(0, 1, (6,), dtype=np.float32)
        self.episodes = 0
        self.day = 0

    def observation(self):
        c = self.config
        return np.array(
            [
                self.inventory / c.capacity,
                self.competitor / (2 * c.cost),
                self.price / (2 * c.cost),
                min(1, self.expected_demand() / 40),
                (self.day % 7) / 6,
                0.5,
            ],
            dtype=np.float32,
        ).clip(0, 1)

    def expected_demand(self):
        return self.config.base_demand * (
            1 + 0.2 * math.sin(2 * math.pi * self.day / 7)
        )

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.day, self.inventory = 0, self.config.capacity
        self.price = round(self.config.cost * 1.3, 2)
        self.competitor = self.price
        self.episodes += 1
        return self.observation(), {}

    def step(self, action):
        delta = (
            float(np.asarray(action).reshape(-1)[0])
            if self.continuous
            else DELTAS[int(action)]
        )
        new_price = round(
            float(
                np.clip(
                    self.price + delta, self.config.cost * 1.05, self.config.cost * 1.9
                )
            ),
            2,
        )
        return self.step_price(new_price)

    def step_price(self, price, *, evaluate=False):
        c = self.config
        previous = self.price
        self.price = round(float(np.clip(price, c.cost * 1.05, c.cost * 1.9)), 2)
        # Fixed random draws per day preserve common random numbers across policies.
        shock, competitor_shock = (
            self.np_random.normal(0, 0.10),
            self.np_random.normal(0, 0.025),
        )
        demand = (
            self.expected_demand()
            * math.exp(-2 * (self.price / self.competitor - 1))
            * max(0.1, 1 + shock)
        )
        requested = max(0, int(round(demand)))
        sold = min(self.inventory, requested)
        self.inventory -= sold
        profit = (self.price - c.cost) * sold
        volatility = abs(self.price - previous)
        churn = min(
            1.0,
            max(0.0, (self.price / self.competitor - 1) * 2)
            + volatility / max(previous, 0.01),
        )
        active = evaluate or self.episodes > c.warmup_episodes
        reward = profit - (
            c.volatility_weight * volatility + c.churn_weight * churn if active else 0
        )
        self.day += 1
        if self.day % 7 == 0:
            self.inventory = c.capacity
        self.competitor = float(
            np.clip(
                self.competitor * (1 + competitor_shock), c.cost * 1.1, c.cost * 1.8
            )
        )
        info = dict(
            profit=profit,
            volatility=volatility,
            churn_proxy=churn,
            units_sold=sold,
            stockouts=max(0, requested - sold),
            price=self.price,
            changed=int(volatility > 0.001),
        )
        return self.observation(), float(reward), False, self.day >= c.horizon, info

    def render(self):
        return f"day={self.day} price={self.price:.2f} inventory={self.inventory}"
