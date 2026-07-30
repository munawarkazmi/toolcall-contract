#!/usr/bin/env python3
"""Print every non-valid call in a model dataset with its reason and
the task that elicited it. Reads only committed data."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import jsonschema

from toolcall_contract.validator import ToolSet, classify, extract_call

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                        else ROOT / "datasets/qwen2.5-7b-instruct.jsonl")
    toolset = ToolSet.load(ROOT / "tools_catalog/tools.json",
                           ROOT / "tools_catalog/catalogs.json")

    def oracle(schema, instance):
        return jsonschema.Draft202012Validator(schema).is_valid(instance)

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        verdict = classify(toolset, extract_call(rec["raw"]), oracle=oracle)
        if verdict.bucket != "valid":
            print(f"task {rec['id']}: [{verdict.bucket}] {verdict.reason}")
            print(f"  task: {rec['task']}")
            print(f"  raw:  {rec['raw'][:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
