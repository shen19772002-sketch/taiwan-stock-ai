"""Synthetic plans test rejection behavior; production audit reads real observations."""

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

from taiwan_stock_ai.analytics import price_plan

SPEC = importlib.util.spec_from_file_location("research_audit", Path(__file__).parents[1] / "scripts/validate_research.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_valid_cost_model_and_illegal_price_rejected():
    plan = price_plan(100, {"atr14": 2, "sma20": 99, "high20": 103})
    MODULE.validate_plan(plan)
    invalid = deepcopy(plan)
    invalid["stop"] += .001
    with pytest.raises(AssertionError, match="tick"):
        MODULE.validate_plan(invalid)


def test_forged_net_reward_ratio_is_not_trusted():
    plan = price_plan(100, {"atr14": 2, "sma20": 99, "high20": 103})
    plan["target"] = 101
    plan["net_risk_reward"] = 3
    with pytest.raises(AssertionError, match="reward/risk"):
        MODULE.validate_plan(plan)
