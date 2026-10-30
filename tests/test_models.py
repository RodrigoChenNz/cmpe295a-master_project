from decimal import Decimal
from pathlib import Path
import json

import numpy as np
import pytest
from gymnasium.utils.env_checker import check_env
from jsonschema import ValidationError

from retail.simulation import RetailPricingEnv, RetailConfig
from retail.pricing import PricingPolicy
from retail.explain import explain_price
from retail.forecasting import forecast, inventory_estimate
from retail.device import DisplayStore
from retail.contracts import validate, validator
from app.gate import evaluate


@pytest.mark.parametrize("continuous", [False, True])
def test_environment_gym_contract_and_seed(continuous):
    env = RetailPricingEnv(continuous=continuous)
    check_env(env, skip_render_check=True)

    def rollout():
        obs, _ = env.reset(seed=123)
        out = []
        for _ in range(30):
            obs, reward, _, done, info = env.step(np.array([0.25]) if continuous else 3)
            assert env.observation_space.contains(obs)
            out.append((reward, info))
        assert done
        return out

    assert rollout() == rollout()


def test_reward_and_inventory_limits():
    env = RetailPricingEnv(config=RetailConfig(warmup_episodes=0))
    env.reset(seed=42)
    _, reward, _, _, info = env.step(4)
    assert reward == pytest.approx(
        info["profit"] - 0.3 * info["volatility"] - 0.5 * info["churn_proxy"]
    )
    assert 0 <= env.inventory <= 200
    assert info["stockouts"] >= 0


@pytest.mark.parametrize(
    "current,proposed,threshold,expected",
    [
        ("10", "10.50", "5", True),
        ("10", "10.51", "5", False),
        ("10", "9.50", "5", True),
        ("10", "10", None, False),
        ("0", "10", "5", False),
        ("10", "10", "0", True),
        ("10", "10.01", "0", False),
    ],
)
def test_gate_decimal_boundaries(current, proposed, threshold, expected):
    assert (
        evaluate(
            current_price=Decimal(current),
            proposed_price=Decimal(proposed),
            min_price=Decimal("8"),
            max_price=Decimal("12"),
            auto_approve_pct=Decimal(threshold) if threshold is not None else None,
        ).auto_approve
        == expected
    )


def test_exact_price_shapley_reconstructs_price():
    p = PricingPolicy()
    x = np.array([0.9, 0.7, 0.65, 0.45, 0.3, 0.5], dtype=np.float32)
    base, values = explain_price(p, x, cost=8, minimum=8.4, maximum=15.2)
    assert base + sum(v["value"] for v in values) == pytest.approx(
        p.price(x, cost=8, minimum=8.4, maximum=15.2), abs=1e-5
    )
    assert all(v["feature"] for v in values)


def test_forecast_chronological_baselines():
    history = list(range(1, 29))
    assert list(forecast(history)) == history[-7:]
    assert inventory_estimate(history, 200)["risk"] >= 0
    with pytest.raises(ValueError):
        forecast([1, float("nan")] * 4)


def test_all_schemas_and_examples():
    for f in Path("contracts").glob("*.schema.json"):
        family = f.name.split(".")[0]
        validator(family)
        for example in json.loads(f.read_text()).get("examples", []):
            validate(family, example)


def test_decimal_money_and_reason_validation():
    p = json.loads(Path("contracts/proposal.schema.json").read_text())["examples"][0]
    validate("proposal", p)
    p["proposed_price"] = 12.999
    with pytest.raises(ValidationError):
        validate("proposal", p)
    p["proposed_price"] = 12.99
    p["reason_tag"] = "x" * 17
    with pytest.raises(ValidationError):
        validate("proposal", p)


def test_device_durable_replay_and_stale_update(tmp_path):
    file = tmp_path / "device.sqlite3"
    store = DisplayStore(file)
    p = dict(
        product_id="SKU-001",
        publication_id=2,
        price=12.99,
        reason_tag="Inventory high",
        display_name="MILK",
    )
    first = store.receive("SHLF-001", p)
    assert first["emulated"] is True
    assert len(first["line1"]) <= 16 and len(first["line2"]) <= 16
    store.conn.close()
    store = DisplayStore(file)
    assert store.receive("SHLF-001", p) == first
    assert store.receive("SHLF-001", p | {"publication_id": 1, "price": 9.99}) == first
    store.conn.close()
