"""Train and compare models with recorded seeds and real exported metrics."""

import csv
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
import numpy as np
from .simulation import RetailConfig, RetailPricingEnv
from .pricing import PricingPolicy
from .forecasting import forecast


def train(algorithm, steps, seed, output):
    import torch
    from stable_baselines3 import DQN, DDPG
    from stable_baselines3.common.env_checker import check_env
    from stable_baselines3.common.monitor import Monitor
    from stable_baselines3.common.noise import NormalActionNoise

    if algorithm not in {"dqn", "ddpg"}:
        raise ValueError("Training supports dqn and ddpg")
    torch.set_num_threads(1)
    env = RetailPricingEnv(continuous=algorithm == "ddpg")
    check_env(env)
    kwargs = dict(
        seed=seed,
        device="cpu",
        verbose=0,
        buffer_size=20000,
        learning_starts=128,
        batch_size=64,
        policy_kwargs={"net_arch": [64, 64]},
    )
    if algorithm == "ddpg":
        kwargs["action_noise"] = NormalActionNoise(np.zeros(1), 0.15 * np.ones(1))
    p = Path(output)
    p.parent.mkdir(parents=True, exist_ok=True)
    config = asdict(env.config)
    env = Monitor(
        env,
        str(p.with_suffix(".monitor.csv")),
        info_keywords=("profit", "volatility", "churn_proxy"),
    )
    model = {"dqn": DQN, "ddpg": DDPG}[algorithm]("MlpPolicy", env, **kwargs)
    model.learn(total_timesteps=steps)
    model.save(p)
    checkpoint = p if p.suffix == ".zip" else Path(str(p) + ".zip")
    manifest = dict(
        algorithm=algorithm,
        steps=steps,
        seed=seed,
        config=config,
        features=6,
        sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        dataset="synthetic",
        evaluation="Run evaluate separately on held-out seeds",
    )
    checkpoint.with_suffix(".json").write_text(json.dumps(manifest, indent=2))
    return manifest


def evaluate(models, seeds, days, output):
    rows = []
    policies = {"fixed_markup": None, "rule": PricingPolicy()}
    for name, path in models.items():
        policies[name] = PricingPolicy(name, path)
    for name, policy in policies.items():
        for seed in seeds:
            env = RetailPricingEnv(config=RetailConfig(horizon=days, warmup_episodes=0))
            obs, _ = env.reset(seed=seed)
            totals = dict(
                profit=0.0,
                volatility=0.0,
                churn_proxy=0.0,
                units_sold=0,
                stockouts=0,
                changed=0,
            )
            for day in range(days):
                price = (
                    env.config.cost * 1.3
                    if policy is None
                    else policy.price(
                        obs,
                        cost=env.config.cost,
                        minimum=env.config.cost * 1.05,
                        maximum=env.config.cost * 1.9,
                    )
                )
                obs, _, _, _, info = env.step_price(price, evaluate=True)
                for k in totals:
                    totals[k] += info[k]
            rows.append(dict(policy=name, seed=seed, days=days, **totals))
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "pricing.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    summary = {
        name: {
            k: {
                "mean": float(np.mean([r[k] for r in rows if r["policy"] == name])),
                "std": float(np.std([r[k] for r in rows if r["policy"] == name])),
            }
            for k in totals
        }
        for name in policies
    }
    (out / "summary.json").write_text(
        json.dumps(
            dict(
                dataset="synthetic",
                seeds=seeds,
                days=days,
                note="Fixed markup has zero price changes at fixed cost; compare volatility without dividing by zero.",
                results=summary,
            ),
            indent=2,
        )
    )
    return summary


def compare_forecasts(methods, seed, output):
    rng = np.random.default_rng(seed)
    y = np.maximum(
        0, 18 + 4 * np.sin(np.arange(120) * 2 * np.pi / 7) + rng.normal(0, 1, 120)
    )
    train_data, actual = y[:-14], y[-14:]
    rows = []
    for method in methods:
        predictions = forecast(train_data, horizon=len(actual), method=method)
        rows.append(
            dict(
                method=method,
                rmse=float(np.sqrt(np.mean((predictions - actual) ** 2))),
                wape=float(np.sum(abs(predictions - actual)) / np.sum(actual)),
                seed=seed,
            )
        )
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "forecast.json").write_text(
        json.dumps(
            dict(dataset="synthetic", train_days=106, holdout_days=14, results=rows),
            indent=2,
        )
    )
    return rows
