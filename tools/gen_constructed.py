#!/usr/bin/env python3
"""Generate the constructed tier-one dataset, deterministically.

One class per taxonomy bucket, each case invalid (or valid) BY
CONSTRUCTION and confirmed against the independent jsonschema oracle
before it is written:
  - every structurally-invalid case is confirmed invalid by the oracle,
  - every semantic case and every valid case is confirmed structurally
    VALID by the oracle (semantic violations live strictly above the
    structural layer).
CI regenerates this file and diffs it against the committed one, so the
dataset cannot drift from the generator.

Usage: gen_constructed.py [out.jsonl]
"""
import copy
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import jsonschema  # the oracle - pinned in CI

from toolcall_contract.validator import ToolSet

ROOT = pathlib.Path(__file__).resolve().parents[1]


def oracle_valid(schema, instance) -> bool:
    return jsonschema.Draft202012Validator(schema).is_valid(instance)


def make_valid_arguments(rng, tool, catalogs):
    """A valid call for `tool`, using catalog values for ref params."""
    schema = tool["schema"]
    refs = tool.get("semantics", {}).get("refs", {})
    args = {}
    for name, sub in schema["properties"].items():
        required = name in schema.get("required", [])
        if not required and rng.random() < 0.5:
            continue
        if f"{name}[]" in refs:
            args[name] = rng.sample(catalogs[refs[f"{name}[]"]],
                                    k=rng.randint(1, min(2, len(catalogs[refs[f"{name}[]"]]))))
        elif name in refs:
            args[name] = rng.choice(catalogs[refs[name]])
        elif "enum" in sub:
            args[name] = rng.choice(sub["enum"])
        elif sub["type"] == "boolean":
            args[name] = rng.random() < 0.5
        elif sub["type"] == "integer":
            lo = sub.get("minimum", 0)
            hi = sub.get("maximum", lo + 100)
            args[name] = rng.randint(int(lo), int(hi))
        elif sub["type"] == "number":
            lo = sub.get("minimum", 0.0)
            hi = sub.get("maximum", lo + 10.0)
            args[name] = round(lo + (hi - lo) * rng.random(), 3)
        elif sub["type"] == "string":
            args[name] = f"text_{rng.randint(1, 99)}"
        elif sub["type"] == "array":
            args[name] = []
    # Repair declared orderings and conditionals so the base is fully valid.
    for lo_name, hi_name in tool.get("semantics", {}).get("ordered", []):
        if lo_name in args and hi_name in args and args[lo_name] > args[hi_name]:
            args[lo_name], args[hi_name] = args[hi_name], args[lo_name]
    for rule in tool.get("semantics", {}).get("conditional", []):
        if args.get(rule["if"]) == rule["equals"]:
            for req in rule.get("then_required", []):
                if req not in args:
                    ref = tool["semantics"].get("refs", {}).get(req)
                    args[req] = rng.choice(catalogs[ref]) if ref else "text_1"
        else:
            for forb in rule.get("else_forbidden", []):
                args.pop(forb, None)
    for group in tool.get("semantics", {}).get("mutually_exclusive", []):
        present = [p for p in group if p in args]
        for extra in present[1:]:
            args.pop(extra)
    for group in tool.get("semantics", {}).get("requires_together", []):
        present = [p for p in group if p in args]
        if present and len(present) != len(group):
            for p in present:
                args.pop(p)
    return args


def main() -> int:
    out_path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                            else ROOT / "datasets/constructed.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    toolset = ToolSet.load(ROOT / "tools_catalog/tools.json",
                           ROOT / "tools_catalog/catalogs.json")
    rng = random.Random(42)
    records = []

    def emit(cls, expected, call=None, raw=None, structural_expectation=None):
        """structural_expectation: True -> oracle must call it valid,
        False -> oracle must call it invalid, None -> not applicable."""
        if structural_expectation is not None:
            tool = toolset.tools[call["tool"]]
            ov = oracle_valid(tool["schema"], call["arguments"])
            assert ov == structural_expectation, (
                f"generator label failure for class {cls}: oracle={ov}")
        records.append({"id": len(records), "class": cls, "expected": expected,
                        **({"call": call} if call else {}),
                        **({"raw": raw} if raw is not None else {})})

    tools = list(toolset.tools.values())

    # ---- valid ----
    for i in range(80):
        tool = tools[i % len(tools)]
        args = make_valid_arguments(rng, tool, toolset.catalogs)
        emit("valid", "valid", {"tool": tool["name"], "arguments": args},
             structural_expectation=True)

    # ---- parse_failure ----
    base = json.dumps({"tool": "navigate_to_waypoint",
                       "arguments": {"waypoint": "dock"}})
    breakers = [base[:-1], base.replace('"arguments"', '"arguments'),
                "I will now call the tool.", "", "{}",
                '{"tool": "navigate_to_waypoint"}',
                '{"tool": "navigate_to_waypoint", "arguments": []}',
                base.replace("{", "[", 1)]
    for i in range(24):
        emit("parse_failure", "parse_failure", raw=breakers[i % len(breakers)])

    # ---- unknown_tool ----
    fakes = ["navigate_to_goal", "goto_waypoint", "move_robot", "pickup_object",
             "set_max_speed", "get_map", "report", "hand_off", "navigate",
             "place", "scheduleTask", "query_map_area"]
    for i in range(30):
        emit("unknown_tool", "unknown_tool",
             {"tool": fakes[i % len(fakes)], "arguments": {"waypoint": "dock"}})

    # ---- schema_violation subclasses ----
    for i in range(60):
        tool = tools[i % len(tools)]
        args = make_valid_arguments(rng, tool, toolset.catalogs)
        kind = i % 5
        mutated = copy.deepcopy(args)
        if kind == 0 and tool["schema"].get("required"):  # missing required
            mutated.pop(rng.choice(tool["schema"]["required"]), None)
        elif kind == 1:  # wrong type
            name = rng.choice(list(tool["schema"]["properties"]))
            sub = tool["schema"]["properties"][name]
            mutated[name] = [1, 2] if sub["type"] != "array" else "not_an_array"
        elif kind == 2:  # bad enum / constraint-breaking string
            enum_props = [n for n, s in tool["schema"]["properties"].items()
                          if "enum" in s]
            if enum_props:
                mutated[rng.choice(enum_props)] = "definitely_not_a_member"
            else:
                str_props = [n for n, s in tool["schema"]["properties"].items()
                             if s["type"] == "string" and "minLength" in s]
                mutated[rng.choice(str_props)] = ""
        elif kind == 3:  # hallucinated extra param
            mutated[f"extra_param_{rng.randint(1, 9)}"] = True
        else:  # out of range
            num_props = [n for n, s in tool["schema"]["properties"].items()
                         if s["type"] in ("number", "integer") and "maximum" in s]
            if num_props:
                name = rng.choice(num_props)
                mutated[name] = tool["schema"]["properties"][name]["maximum"] + 1000
            else:
                mutated["extra_param_x"] = True
        if oracle_valid(tool["schema"], mutated):
            continue  # mutation happened to stay valid; skip rather than mislabel
        emit(f"schema_{['missing_required','wrong_type','bad_enum','extra_param','out_of_range'][kind]}",
             "schema_violation", {"tool": tool["name"], "arguments": mutated},
             structural_expectation=False)

    # ---- semantic_violation subclasses (all structurally VALID) ----
    semantic_cases = []
    for i in range(20):  # unknown catalog ref
        tool = toolset.tools["navigate_to_waypoint"]
        args = make_valid_arguments(rng, tool, toolset.catalogs)
        args["waypoint"] = f"warehouse_{rng.randint(2, 9)}"  # plausible, nonexistent
        semantic_cases.append(("semantic_unknown_ref", tool, args))
    for i in range(12):  # mutual exclusion
        tool = toolset.tools["grasp_handoff"]
        args = {"person_id": rng.choice(toolset.catalogs["people"]),
                "waypoint": rng.choice(toolset.catalogs["waypoints"])}
        semantic_cases.append(("semantic_mutual_exclusion", tool, args))
    for i in range(12):  # missing partner
        tool = toolset.tools["report_status"]
        args = {"channel": rng.choice(["ops", "safety", "maintenance"]),
                "geofence_min": round(rng.random() * 10, 2)}
        semantic_cases.append(("semantic_missing_partner", tool, args))
    for i in range(12):  # unordered
        tool = toolset.tools["schedule_task"]
        start = rng.randint(500, 1000)
        args = {"task_type": rng.choice(["patrol", "charge", "deliver", "clean"]),
                "start_time": start, "end_time": rng.randint(0, start - 1)}
        semantic_cases.append(("semantic_unordered", tool, args))
    for i in range(12):  # conditional violated
        tool = toolset.tools["set_speed_limit"]
        if i % 2 == 0:
            args = {"limit": 1.0, "scope": "zone"}  # zone required, missing
        else:
            args = {"limit": 1.0, "scope": "global",
                    "zone": rng.choice(toolset.catalogs["zones"])}  # forbidden
        semantic_cases.append(("semantic_conditional", tool, args))
    for cls, tool, args in semantic_cases:
        emit(cls, "semantic_violation", {"tool": tool["name"], "arguments": args},
             structural_expectation=True)

    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True) + "\n")

    by_expected = {}
    for r in records:
        by_expected[r["expected"]] = by_expected.get(r["expected"], 0) + 1
    print(f"constructed dataset: {len(records)} cases -> "
          + ", ".join(f"{k} {v}" for k, v in sorted(by_expected.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
