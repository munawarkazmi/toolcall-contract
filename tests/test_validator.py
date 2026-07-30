"""Unit tests: the oracle-edge semantics, one test per taxonomy bucket,
one per semantic constraint class, and a seeded mini-differential.

The full differential (25,000 pairs) and the constructed-dataset
evaluation run as their own CI steps; these tests pin the individual
behaviors a reader would want to see asserted by name.
"""
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import jsonschema
import pytest

from toolcall_contract import semantic, structural
from toolcall_contract.validator import ToolSet, classify, extract_call

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def toolset():
    return ToolSet.load(ROOT / "tools_catalog/tools.json",
                        ROOT / "tools_catalog/catalogs.json")


def oracle(schema, instance):
    return jsonschema.Draft202012Validator(schema).is_valid(instance)


# ---- the oracle-edge semantics, asserted by name ----

def test_bool_is_not_a_number_or_integer():
    assert not structural.check({"type": "integer"}, True).valid
    assert not structural.check({"type": "number"}, False).valid
    assert oracle({"type": "integer"}, True) is False  # and the oracle agrees


def test_float_with_zero_fraction_is_an_integer():
    assert structural.check({"type": "integer"}, 1.0).valid
    assert not structural.check({"type": "integer"}, 1.5).valid
    assert oracle({"type": "integer"}, 1.0) is True


def test_enum_distinguishes_bool_from_number():
    assert not structural.check({"enum": [1]}, True).valid
    assert not structural.check({"enum": [True]}, 1).valid
    assert structural.check({"enum": [True]}, True).valid
    assert oracle({"enum": [1]}, True) is False


def test_constraints_ignore_inapplicable_types():
    assert structural.check({"type": "string", "minimum": 5}, "ab").valid
    assert structural.check({"minLength": 5}, 42).valid


# ---- one behavior per bucket ----

def test_parse_failure_bucket(toolset):
    assert classify(toolset, extract_call("no json here")).bucket == "parse_failure"
    assert classify(toolset, extract_call('{"tool": "x"}')).bucket == "parse_failure"


def test_unknown_tool_bucket(toolset):
    call = {"tool": "navigate_to_goal", "arguments": {}}
    assert classify(toolset, call).bucket == "unknown_tool"


def test_schema_violation_bucket(toolset):
    call = {"tool": "pick_object", "arguments": {"object_id": "red_box"}}
    v = classify(toolset, call, oracle=oracle)
    assert v.bucket == "schema_violation"  # gripper_force missing


def test_semantic_violation_bucket(toolset):
    call = {"tool": "pick_object",
            "arguments": {"object_id": "green_box", "gripper_force": 20}}
    v = classify(toolset, call, oracle=oracle)
    assert v.bucket == "semantic_violation"  # green_box not in catalog


def test_valid_bucket(toolset):
    call = {"tool": "navigate_to_waypoint",
            "arguments": {"waypoint": "dock", "speed": 0.5}}
    assert classify(toolset, call, oracle=oracle).bucket == "valid"


# ---- one behavior per semantic constraint class ----

def test_mutual_exclusion(toolset):
    call = {"tool": "grasp_handoff",
            "arguments": {"person_id": "operator_1", "waypoint": "dock"}}
    assert classify(toolset, call, oracle=oracle).bucket == "semantic_violation"


def test_requires_together(toolset):
    call = {"tool": "report_status",
            "arguments": {"channel": "ops", "geofence_min": 1.0}}
    assert classify(toolset, call, oracle=oracle).bucket == "semantic_violation"


def test_ordered(toolset):
    call = {"tool": "schedule_task",
            "arguments": {"task_type": "patrol", "start_time": 100, "end_time": 50}}
    assert classify(toolset, call, oracle=oracle).bucket == "semantic_violation"


def test_conditional_required_and_forbidden(toolset):
    missing = {"tool": "set_speed_limit", "arguments": {"limit": 1.0, "scope": "zone"}}
    forbidden = {"tool": "set_speed_limit",
                 "arguments": {"limit": 1.0, "scope": "global", "zone": "wet_floor"}}
    assert classify(toolset, missing, oracle=oracle).bucket == "semantic_violation"
    assert classify(toolset, forbidden, oracle=oracle).bucket == "semantic_violation"


def test_array_element_refs(toolset):
    call = {"tool": "navigate_to_waypoint",
            "arguments": {"waypoint": "dock", "avoid_zones": ["wet_floor", "lava"]}}
    assert classify(toolset, call, oracle=oracle).bucket == "semantic_violation"


# ---- extraction ----

def test_extract_call_from_prose():
    text = 'Sure! I will call: {"tool": "stop_all", "arguments": {"reason": "test"}} done.'
    assert extract_call(text) == {"tool": "stop_all", "arguments": {"reason": "test"}}


def test_extract_call_handles_braces_in_strings():
    text = '{"tool": "t", "arguments": {"s": "a } brace {"}}'
    assert extract_call(text) == {"tool": "t", "arguments": {"s": "a } brace {"}}


# ---- mini differential (the full 25k run is a separate CI step) ----

def test_mini_differential():
    sys.path.insert(0, str(ROOT / "tools"))
    from fuzz_differential import JUNK, plausible_instance, random_schema
    rng = random.Random(99)
    for _ in range(1500):
        schema = random_schema(rng)
        instance = plausible_instance(rng, schema) if rng.random() < 0.5 \
            else rng.choice(JUNK)
        assert structural.check(schema, instance).valid == oracle(schema, instance), \
            json.dumps({"schema": schema, "instance": instance})
