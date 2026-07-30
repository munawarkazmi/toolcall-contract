#!/usr/bin/env python3
"""Evaluate a dataset of tool calls through the two-layer validator.

Works on both tiers:
  constructed (records carry 'expected'): every case must land in its
    expected bucket; a constructed-invalid case classified 'valid' is
    invalid_marked_valid - a load-bearing zero, direct analog of the
    shield's unsafe_missed. Any mismatch fails the run.
  model output (records carry 'raw' text and a 'model' name): the
    bucket distribution IS the result, reported as a fact about the
    named model at its named settings.

In both tiers the structural layer runs the hand-written checker AND
the jsonschema oracle on every call; any disagreement is the other
load-bearing zero and fails the run.

Usage: evaluate.py <dataset.jsonl> [--label "model name"]
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import jsonschema

from toolcall_contract.validator import (BUCKETS, ToolSet, classify, extract_call)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def oracle(schema, instance) -> bool:
    return jsonschema.Draft202012Validator(schema).is_valid(instance)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = pathlib.Path(sys.argv[1])
    label = None
    if "--label" in sys.argv:
        label = sys.argv[sys.argv.index("--label") + 1]

    toolset = ToolSet.load(ROOT / "tools_catalog/tools.json",
                           ROOT / "tools_catalog/catalogs.json")

    counts = {b: 0 for b in BUCKETS}
    disagreements = 0
    invalid_marked_valid = 0
    mismatches = 0
    total = 0
    is_constructed = None

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        total += 1
        if is_constructed is None:
            is_constructed = "expected" in rec

        call = rec.get("call")
        if call is None:
            call = extract_call(rec.get("raw", ""))
        verdict = classify(toolset, call, oracle=oracle)

        if verdict.bucket == "validator_disagreement":
            disagreements += 1
            print(f"  VALIDATOR DISAGREEMENT on id {rec.get('id')}: {verdict.reason}")
            continue
        counts[verdict.bucket] += 1

        if is_constructed:
            if rec["expected"] != "valid" and verdict.bucket == "valid":
                invalid_marked_valid += 1
                print(f"  INVALID MARKED VALID: id {rec.get('id')} class "
                      f"{rec.get('class')}")
            elif verdict.bucket != rec["expected"]:
                mismatches += 1
                print(f"  bucket mismatch: id {rec.get('id')} class {rec.get('class')} "
                      f"expected {rec['expected']} got {verdict.bucket} "
                      f"({verdict.reason})")

    if label:
        print(f"model under evaluation: {label}")
    print(f"{path.name}: {total} cases")
    for b in BUCKETS:
        print(f"  {b:<20} {counts[b]}")
    print(f"  validator_disagreement {disagreements}   <-- load-bearing; must be 0")
    if is_constructed:
        print(f"  invalid_marked_valid   {invalid_marked_valid}   <-- load-bearing; "
              "must be 0")
        print(f"  other bucket mismatches {mismatches}   (constructed labels must "
              "match exactly)")
    print("note: structural and semantic validity are separate axes (the bucket "
          "names which layer failed); task-appropriateness - whether the call was "
          "the right tool for the goal - is a stated non-goal.")

    ok = disagreements == 0 and (not is_constructed or
                                 (invalid_marked_valid == 0 and mismatches == 0))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
