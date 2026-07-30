#!/usr/bin/env python3
"""Collect real tool-call proposals from a local model, one per task.

Standard library only; OpenAI-compatible endpoint (local Ollama by
default). Appends one record per task to the output JSONL and skips
tasks already present, so an interrupted run resumes. The raw responses
are committed as the dataset; evaluation replays them deterministically
with zero inference.

The tasks are natural robot-operations requests over the committed tool
catalog. Some reference entities or capabilities that do not exist -
because real user requests do - and what the model emits for those is
part of the phenomenon being measured, not a trap added after the fact.

Usage:
  python3 tools/collect_qwen.py --name qwen2.5-7b-instruct \
      --base-url http://localhost:11434/v1 --model qwen2.5:7b-instruct
"""
import argparse
import json
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]

PROMPT = """You are the tool-calling controller for a mobile robot.

Available tools (JSON Schemas for their arguments):
{tools}

Known waypoints: {waypoints}
Known zones: {zones}
Known objects: {objects}
Known people: {people}

Task: {task}

Choose the single most appropriate tool call for this task.
Respond with ONLY one JSON object, no other text, in this form:
{{"tool": "<tool_name>", "arguments": {{...}}}}
"""


def build_prompt(task: str) -> str:
    tools = json.loads((ROOT / "tools_catalog/tools.json").read_text(encoding="utf-8"))
    catalogs = json.loads(
        (ROOT / "tools_catalog/catalogs.json").read_text(encoding="utf-8"))
    tool_desc = [{"name": t["name"], "description": t["description"],
                  "parameters": t["schema"]} for t in tools]
    return PROMPT.format(tools=json.dumps(tool_desc, indent=1),
                         waypoints=", ".join(catalogs["waypoints"]),
                         zones=", ".join(catalogs["zones"]),
                         objects=", ".join(catalogs["objects"]),
                         people=", ".join(catalogs["people"]),
                         task=task)


def call(base_url, model, prompt, timeout_s):
    payload = {"model": model, "temperature": 0.0, "max_tokens": 500,
               "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "toolcall-contract-eval/0.1"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode())["choices"][0]["message"]["content"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--timeout-s", type=float, default=300.0)
    args = ap.parse_args()

    tasks = json.loads((ROOT / "datasets/tasks.json").read_text(encoding="utf-8"))
    out_path = ROOT / "datasets" / f"{args.name}.jsonl"
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["id"])

    with out_path.open("a", encoding="utf-8") as out:
        for i, task in enumerate(tasks):
            if i in done:
                continue
            prompt = build_prompt(task)
            t0 = time.time()
            try:
                raw = call(args.base_url, args.model, prompt, args.timeout_s)
            except Exception as exc:  # noqa: BLE001 - record and continue
                print(f"task {i}: ERROR {exc}", file=sys.stderr)
                continue
            rec = {"id": i, "model": args.model, "temperature": 0.0,
                   "task": task, "prompt": prompt, "raw": raw,
                   "elapsed_s": round(time.time() - t0, 2)}
            out.write(json.dumps(rec) + "\n")
            out.flush()
            print(f"task {i}: ok ({rec['elapsed_s']}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
