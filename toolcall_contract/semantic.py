"""Semantic layer: contract rules the structural layer does not carry.

These are the constraints an agent runtime declares as data next to its
tools - referential integrity against live catalogs, mutually exclusive
parameters, parameters that only make sense together or in order, and
conditionals. Some of them could be encoded in full JSON Schema drafts
(if/then, dependentRequired); they live here because in a real agent
they are runtime data (catalogs change without redeploying schemas),
and because this layer is exactly where observed tool-call failures
concentrate: a call that parses, types, and validates - and names a
waypoint that does not exist.

Vocabulary (per tool, under "semantics"):
  refs:               {"param": "catalog"} - value must be a member of
                      the named catalog; "param[]" applies the check to
                      every element of an array parameter.
  mutually_exclusive: [["a","b"], ...] - at most one of each group.
  requires_together:  [["lat","lon"], ...] - all or none of each group.
  ordered:            [["start","end"], ...] - both present implies
                      start <= end (numbers only).
  conditional:        [{"if": p, "equals": v, "then_required": [...],
                        "else_forbidden": [...]}]

Semantic checks run only on structurally valid calls; the two layers
are separate axes and are never merged into one score.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SemanticResult:
    valid: bool
    reason: str = ""


def check(semantics: dict, arguments: dict, catalogs: dict) -> SemanticResult:
    for param, catalog_name in semantics.get("refs", {}).items():
        catalog = catalogs[catalog_name]
        if param.endswith("[]"):
            base = param[:-2]
            for i, value in enumerate(arguments.get(base, []) or []):
                if value not in catalog:
                    return SemanticResult(
                        False, f"{base}[{i}]: '{value}' not in catalog '{catalog_name}'")
        elif param in arguments and arguments[param] not in catalog:
            return SemanticResult(
                False, f"{param}: '{arguments[param]}' not in catalog '{catalog_name}'")

    for group in semantics.get("mutually_exclusive", []):
        present = [p for p in group if p in arguments]
        if len(present) > 1:
            return SemanticResult(False, f"mutually exclusive: {present}")

    for group in semantics.get("requires_together", []):
        present = [p for p in group if p in arguments]
        if present and len(present) != len(group):
            missing = [p for p in group if p not in arguments]
            return SemanticResult(False, f"{present} requires {missing}")

    for lo, hi in semantics.get("ordered", []):
        if lo in arguments and hi in arguments and arguments[lo] > arguments[hi]:
            return SemanticResult(False, f"'{lo}' must not exceed '{hi}'")

    for rule in semantics.get("conditional", []):
        if arguments.get(rule["if"]) == rule["equals"]:
            for req in rule.get("then_required", []):
                if req not in arguments:
                    return SemanticResult(
                        False, f"'{rule['if']}'=='{rule['equals']}' requires '{req}'")
        else:
            for forb in rule.get("else_forbidden", []):
                if forb in arguments:
                    return SemanticResult(
                        False,
                        f"'{forb}' is only allowed when '{rule['if']}'=='{rule['equals']}'")

    return SemanticResult(True)
