#!/usr/bin/env python3
"""Differential fuzz: the hand-written structural checker vs jsonschema.

Seeded and deterministic on purpose - the agreement count is asserted
in CI and quoted in the README, and a count can only be asserted if it
cannot drift (property-based frameworks explore brilliantly but do not
promise bit-stable generation across versions; this repo's discipline
needs the promise more than the exploration).

Two populations, one invariant:
  1. random schemas from the documented subset grammar x random
     instances (half generated to be plausibly valid, half mutated),
  2. the committed tool schemas x random argument objects.
For every pair, ours and jsonschema.Draft202012Validator must agree on
structural validity. Any disagreement prints the schema, the instance,
and both verdicts, and exits nonzero: it is a bug in one of the two,
surfaced, never smoothed.

Usage: fuzz_differential.py [n_random] [n_tools]
"""
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import jsonschema
from importlib.metadata import version as _pkg_version

from toolcall_contract import structural
from toolcall_contract.validator import ToolSet

ROOT = pathlib.Path(__file__).resolve().parents[1]

TYPES = ["string", "number", "integer", "boolean", "array", "object"]


def random_schema(rng, depth=0):
    t = rng.choice(TYPES if depth < 2 else [x for x in TYPES if x not in ("array", "object")])
    schema = {"type": t}
    if t == "string":
        if rng.random() < 0.4:
            schema["minLength"] = rng.randint(0, 4)
        if rng.random() < 0.3:
            schema["maxLength"] = rng.randint(4, 10)
        if rng.random() < 0.25:
            schema["enum"] = rng.sample(["a", "b", "c", "d", "e"], k=rng.randint(1, 3))
    elif t in ("number", "integer"):
        if rng.random() < 0.5:
            schema["minimum"] = rng.randint(-10, 5)
        if rng.random() < 0.5:
            schema["maximum"] = rng.randint(5, 20)
        if rng.random() < 0.2:
            schema["enum"] = [rng.randint(-5, 15) for _ in range(rng.randint(1, 3))]
    elif t == "array":
        schema["items"] = random_schema(rng, depth + 1)
        if rng.random() < 0.4:
            schema["minItems"] = rng.randint(0, 2)
        if rng.random() < 0.4:
            schema["maxItems"] = rng.randint(2, 5)
    elif t == "object":
        props = {}
        for i in range(rng.randint(0, 4)):
            props[f"p{i}"] = random_schema(rng, depth + 1)
        schema["properties"] = props
        if props and rng.random() < 0.6:
            schema["required"] = rng.sample(list(props), k=rng.randint(0, len(props)))
        if rng.random() < 0.6:
            schema["additionalProperties"] = rng.random() < 0.5
    return schema


def plausible_instance(rng, schema, depth=0):
    t = schema.get("type", "string")
    if "enum" in schema and rng.random() < 0.7:
        return rng.choice(schema["enum"])
    if t == "string":
        return "abcdefgh"[: rng.randint(0, 8)]
    if t == "integer":
        return rng.randint(-12, 22)
    if t == "number":
        return rng.choice([rng.randint(-12, 22), rng.uniform(-12, 22),
                           float(rng.randint(-3, 8))])
    if t == "boolean":
        return rng.random() < 0.5
    if t == "array":
        return [plausible_instance(rng, schema.get("items", {"type": "integer"}),
                                   depth + 1)
                for _ in range(rng.randint(0, 4))]
    if t == "object":
        obj = {}
        for name, sub in schema.get("properties", {}).items():
            if name in schema.get("required", []) or rng.random() < 0.6:
                obj[name] = plausible_instance(rng, sub, depth + 1)
        if rng.random() < 0.3:
            obj[f"x{rng.randint(0, 9)}"] = rng.randint(0, 9)
        return obj
    return None


JUNK = [None, True, False, 0, 1, -1, 0.5, 1.0, "", "junk", [], {}, [1, "a"],
        {"unexpected": True}, 2 ** 40, -0.0, "0", "true"]


def main() -> int:
    n_random = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    n_tools = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    rng = random.Random(1234)
    toolset = ToolSet.load(ROOT / "tools_catalog/tools.json",
                           ROOT / "tools_catalog/catalogs.json")

    checked = 0
    disagreements = 0

    def compare(schema, instance):
        nonlocal checked, disagreements
        ours = structural.check(schema, instance).valid
        oracle = jsonschema.Draft202012Validator(schema).is_valid(instance)
        checked += 1
        if ours != oracle:
            disagreements += 1
            print("DISAGREEMENT")
            print("  schema:  ", json.dumps(schema))
            print("  instance:", json.dumps(instance))
            print(f"  ours={ours} jsonschema={oracle}")

    for _ in range(n_random):
        schema = random_schema(rng)
        if rng.random() < 0.5:
            instance = plausible_instance(rng, schema)
        else:
            instance = rng.choice(JUNK)
        compare(schema, instance)

    tools = list(toolset.tools.values())
    for i in range(n_tools):
        tool = tools[i % len(tools)]
        instance = plausible_instance(rng, tool["schema"])
        compare(tool["schema"], instance)

    print(f"fuzz: {checked} schema/instance pairs, ours vs jsonschema "
          f"{_pkg_version('jsonschema')}: {checked - disagreements} agreements, "
          f"{disagreements} disagreements")
    if disagreements:
        print("FAIL: the structural checker and the oracle disagree")
        return 1
    print("PASS: full structural agreement with the independent oracle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
