"""Exact single-reference interventional Shapley values of the PRICE policy.

Enumerates every coalition of six features (64 deterministic evaluations).
This explains the actual bounded pricing function, including an RL policy,
not a churn model or an unmeasured surrogate. The chosen reference and units
are explicit. Rounding residual is separately represented for additivity.
"""

from math import factorial
import numpy as np
from .simulation import FEATURES


def explain_price(policy, obs, *, cost, minimum, maximum):
    x = np.asarray(obs, dtype=np.float32)
    reference = np.array([0.5, 1.3 / 2, 1.3 / 2, 18 / 40, 0.5, 0.5], dtype=np.float32)
    n = len(x)
    values = {}
    for mask in range(1 << n):
        hybrid = np.array(
            [x[j] if mask & (1 << j) else reference[j] for j in range(n)],
            dtype=np.float32,
        )
        values[mask] = policy.price(hybrid, cost=cost, minimum=minimum, maximum=maximum)
    contributions = []
    for j, name in enumerate(FEATURES):
        value = 0.0
        for mask in range(1 << n):
            if mask & (1 << j):
                continue
            k = mask.bit_count()
            weight = factorial(k) * factorial(n - k - 1) / factorial(n)
            value += weight * (values[mask | (1 << j)] - values[mask])
        contributions.append({"feature": name, "value": round(value, 6)})
    residual = round(
        values[(1 << n) - 1] - values[0] - sum(c["value"] for c in contributions), 6
    )
    if residual:
        contributions.append({"feature": "rounding", "value": residual})
    return values[0], sorted(contributions, key=lambda c: abs(c["value"]), reverse=True)
