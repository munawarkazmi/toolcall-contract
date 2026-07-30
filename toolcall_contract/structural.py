"""Hand-written structural validator over a documented JSON Schema subset.

Standard library only - the mature `jsonschema` package is deliberately
NOT a dependency of the validator itself: it is the independent oracle
the validator is differentially tested against (the same role pyperplan
and the reference checkers play in my other repositories). The two must
agree on structural validity for every fuzzed and committed case; any
disagreement is a bug in one of them, surfaced loudly, never smoothed.

Supported subset (generation and tools stay inside it; stated as a hard
boundary, not an aspiration): type (object/array/string/number/integer/
boolean), properties, required, additionalProperties (boolean), enum,
minimum/maximum, minLength/maxLength, items (single schema),
minItems/maxItems.

Semantics follow JSON Schema draft 2020-12 as implemented by the pinned
oracle, including its sharp edges:
  - Python bool is NOT a number or integer (despite bool < int),
  - a float with zero fractional part IS a valid "integer" (1.0 passes),
  - enum comparison distinguishes True from 1 and False from 0,
  - constraint keywords ignore instances of inapplicable types
    (minimum on a string says nothing).
The differential fuzzer exists to catch any place these semantics and
the oracle's disagree.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StructuralResult:
    valid: bool
    reason: str = ""


def _json_equal(a, b) -> bool:
    """Type-aware equality: True != 1, False != 0 (as in jsonschema)."""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    return a == b


def _matches_type(value, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return isinstance(value, float) and value.is_integer()
    return True  # unknown type names are out of subset; permissive like the oracle


def check(schema: dict, instance) -> StructuralResult:
    """Validate `instance` against `schema` (subset above)."""
    if "type" in schema and not _matches_type(instance, schema["type"]):
        return StructuralResult(False, f"expected type {schema['type']}")

    if "enum" in schema:
        if not any(_json_equal(instance, member) for member in schema["enum"]):
            return StructuralResult(False, f"value not in enum {schema['enum']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            return StructuralResult(False, f"below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            return StructuralResult(False, f"above maximum {schema['maximum']}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            return StructuralResult(False, f"shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            return StructuralResult(False, f"longer than maxLength {schema['maxLength']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            return StructuralResult(False, f"fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            return StructuralResult(False, f"more than maxItems {schema['maxItems']}")
        if "items" in schema:
            for i, element in enumerate(instance):
                r = check(schema["items"], element)
                if not r.valid:
                    return StructuralResult(False, f"items[{i}]: {r.reason}")

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in instance:
                return StructuralResult(False, f"missing required property '{name}'")
        if schema.get("additionalProperties", True) is False:
            for name in instance:
                if name not in properties:
                    return StructuralResult(False, f"unexpected property '{name}'")
        for name, sub in properties.items():
            if name in instance:
                r = check(sub, instance[name])
                if not r.valid:
                    return StructuralResult(False, f"{name}: {r.reason}")

    return StructuralResult(True)
