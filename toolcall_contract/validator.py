"""The two-layer tool-call validator and its pre-registered taxonomy.

Every proposed call lands in exactly one bucket, fixed here BEFORE any
model output or constructed dataset was evaluated:

  valid               parses, names a real tool, passes both layers
  parse_failure       no {"tool": ..., "arguments": {...}} object
                      extractable from the text
  unknown_tool        parses, but names a tool that does not exist
                      (the hallucinated-tool case)
  schema_violation    structural layer fails: missing required, wrong
                      type, bad enum, extra property, out of range
  semantic_violation  structurally valid, but breaks a declared
                      contract rule (unknown catalog ref, mutual
                      exclusion, missing partner, ordering, conditional)

and the two load-bearing cells, each of which must be zero and fails
any evaluation when it is not:

  validator_disagreement  the hand-written structural checker and the
                          pinned jsonschema oracle differ on structural
                          validity - the differential invariant
  invalid_marked_valid    a constructed-invalid call that the validator
                          passed - the direct analog of the shield's
                          unsafe_missed

Two axes the validator deliberately does NOT merge or judge:
structural and semantic validity are reported separately (a bucket
names which layer failed), and task-appropriateness - whether the call
was the right tool for the user's goal - is a non-goal, exactly as the
navigation shield checks safety, not optimality.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import semantic, structural

BUCKETS = ("valid", "parse_failure", "unknown_tool", "schema_violation",
           "semantic_violation")
LOAD_BEARING = ("validator_disagreement", "invalid_marked_valid")


@dataclass
class Verdict:
    bucket: str
    reason: str = ""
    tool: str = ""
    # Filled when an oracle callback is provided:
    oracle_structural: bool | None = None
    ours_structural: bool | None = None
    disagreement: bool = False


@dataclass
class ToolSet:
    tools: dict = field(default_factory=dict)      # name -> {"schema", "semantics"}
    catalogs: dict = field(default_factory=dict)   # name -> list of values

    @classmethod
    def load(cls, tools_path, catalogs_path):
        with open(tools_path, encoding="utf-8") as f:
            tools = {t["name"]: t for t in json.load(f)}
        with open(catalogs_path, encoding="utf-8") as f:
            catalogs = json.load(f)
        return cls(tools, catalogs)


def extract_call(text: str):
    """First balanced JSON object in `text` with 'tool' and 'arguments'
    keys (arguments an object). Deterministic; returns None on failure -
    parse failures are data, not discards."""
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for end in range(start, len(text)):
            c = text[end]
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        break
                    if (isinstance(obj, dict) and "tool" in obj
                            and isinstance(obj.get("arguments"), dict)):
                        return obj
                    break
    return None


def classify(toolset: ToolSet, call, oracle=None) -> Verdict:
    """Classify a parsed call (or None for parse failures).

    `oracle` is an optional callable (schema, instance) -> bool from the
    independent jsonschema-based reference; when provided, the
    structural layer runs BOTH checkers and any disagreement is fatal
    (bucket 'validator_disagreement').
    """
    if call is None:
        return Verdict("parse_failure", "no tool-call object found")

    name = call.get("tool")
    if name not in toolset.tools:
        return Verdict("unknown_tool", f"no tool named '{name}'", tool=str(name))

    tool = toolset.tools[name]
    arguments = call["arguments"]

    ours = structural.check(tool["schema"], arguments)
    verdict = Verdict("", tool=name)
    verdict.ours_structural = ours.valid
    if oracle is not None:
        verdict.oracle_structural = bool(oracle(tool["schema"], arguments))
        if verdict.oracle_structural != ours.valid:
            verdict.bucket = "validator_disagreement"
            verdict.reason = (f"ours={ours.valid} oracle={verdict.oracle_structural} "
                              f"({ours.reason})")
            verdict.disagreement = True
            return verdict

    if not ours.valid:
        verdict.bucket = "schema_violation"
        verdict.reason = ours.reason
        return verdict

    sem = semantic.check(tool.get("semantics", {}), arguments, toolset.catalogs)
    if not sem.valid:
        verdict.bucket = "semantic_violation"
        verdict.reason = sem.reason
        return verdict

    verdict.bucket = "valid"
    return verdict
