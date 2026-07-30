#!/usr/bin/env python3
"""Render the README figures by deterministically replaying the
committed qwen dataset through the validator - the same replay CI runs,
so the figures cannot diverge from the evaluation.

Usage: render_figures.py [outdir]
"""
import json
import pathlib
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import jsonschema

from toolcall_contract.validator import BUCKETS, ToolSet, classify, extract_call

ROOT = pathlib.Path(__file__).resolve().parents[1]

NAVY = "#123a5f"
GREEN = "#2e7d4f"
GOLD = "#d9a441"
RED = "#b03a2e"
GRAY = "#8a8f98"

MODEL_LABEL = "qwen2.5:7b-instruct (temperature 0)"


def replay():
    toolset = ToolSet.load(ROOT / "tools_catalog/tools.json",
                           ROOT / "tools_catalog/catalogs.json")
    oracle = lambda s, i: jsonschema.Draft202012Validator(s).is_valid(i)  # noqa: E731
    verdicts = []
    for line in (ROOT / "datasets/qwen2.5-7b-instruct.jsonl").read_text(
            encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            verdicts.append(classify(toolset, extract_call(rec["raw"]), oracle=oracle))
    return verdicts


def main():
    outdir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs/figures"
    outdir.mkdir(parents=True, exist_ok=True)
    verdicts = replay()
    counts = {b: sum(1 for v in verdicts if v.bucket == b) for b in BUCKETS}
    counts["validator_disagreement"] = sum(1 for v in verdicts if v.disagreement)

    sem_classes = {"catalog ref": 0, "ordering": 0, "conditional": 0, "other": 0}
    for v in verdicts:
        if v.bucket == "semantic_violation":
            if "catalog" in v.reason:
                sem_classes["catalog ref"] += 1
            elif "exceed" in v.reason:
                sem_classes["ordering"] += 1
            elif "requires" in v.reason or "only allowed" in v.reason:
                sem_classes["conditional"] += 1
            else:
                sem_classes["other"] += 1

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 5),
                                   gridspec_kw={"width_ratios": [3, 2]})

    order = ["valid", "parse_failure", "unknown_tool", "schema_violation",
             "semantic_violation", "validator_disagreement"]
    colors = [GREEN, GRAY, GRAY, GOLD, NAVY, RED]
    bars = ax1.barh(range(len(order)), [counts[b] for b in order], color=colors)
    ax1.set_yticks(range(len(order)), order)
    ax1.invert_yaxis()
    ax1.bar_label(bars, padding=3, fontsize=10)
    ax1.set_xlim(0, 40)
    ax1.set_xlabel("calls")
    ax1.set_title(f"All 40 {MODEL_LABEL} tool calls\nby pre-registered bucket "
                  "(replayed from the committed dataset)", fontsize=10)
    sem_note = ", ".join(f"{v} {k}" for k, v in sem_classes.items() if v)
    ax1.text(counts["semantic_violation"] + 1.5, order.index("semantic_violation") + 0.32,
             f"({sem_note})", fontsize=8.5, color=NAVY)

    # What a schema-only check would conclude vs the full contract.
    schema_only_reject = counts["parse_failure"] + counts["unknown_tool"] + \
        counts["schema_violation"]
    contract_reject = schema_only_reject + counts["semantic_violation"]
    total = len(verdicts)
    labels = ["schema-level check\n(what most frameworks do)", "full contract check\n(this repo)"]
    passes = [total - schema_only_reject, total - contract_reject]
    rejects = [schema_only_reject, contract_reject]
    x = range(2)
    ax2.bar(x, passes, color=GREEN, label="passed")
    ax2.bar(x, rejects, bottom=passes, color=RED, label="rejected")
    for i in x:
        ax2.text(i, passes[i] / 2, str(passes[i]), ha="center", va="center",
                 color="white", fontsize=13, fontweight="bold")
        if rejects[i]:
            ax2.text(i, passes[i] + rejects[i] / 2, str(rejects[i]), ha="center",
                     va="center", color="white", fontsize=13, fontweight="bold")
    ax2.set_xticks(list(x), labels, fontsize=9)
    ax2.set_ylabel("calls")
    ax2.legend(fontsize=9)
    ax2.set_title("The layer that does the catching:\nschema checks pass 39/40 - "
                  "the contract catches 7 more", fontsize=10)

    fig.suptitle(f"Real-model tier: {MODEL_LABEL} - the failures live above the "
                 "schema", fontsize=12, color=NAVY)
    fig.tight_layout()
    fig.savefig(outdir / "qwen_layers.png", dpi=110)
    plt.close(fig)
    print("figures written to", outdir)


if __name__ == "__main__":
    main()
