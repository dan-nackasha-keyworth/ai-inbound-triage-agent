"""
One-off comparison: does claude-opus-4-8 out-perform the claude-haiku-4-5
baseline on the hardest slice of the message set - the 25 deliberately
ambiguous/edge-case messages in the dev split?

Held-out edge cases (5 messages) are deliberately excluded here, same as
every other exploratory run in this project: the held-out split is run
once, near the very end, for final validation - not used to compare
models or tune thresholds along the way.

This is a real, API-tested comparison (not simulated) using the exact
same classify_and_extract prompt and score_confidence rubric as the main
pipeline - only the model string changes. The Haiku side is not
re-run; it's read from the last validated full dev-set run
(outputs/run_20260704T222727Z_dev.json), since nothing in
classify_and_extract has changed since that run (only draft_response/
determine_queue have) - so re-spending API cost to reproduce it would
add nothing.
"""

import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from config import CONFIG
from pipeline import classify_and_extract, score_confidence

DATA_PATH = Path(__file__).parent / "data" / "sample_messages.json"
BASELINE_RUN_PATH = Path(__file__).parent / "outputs" / "run_20260704T222727Z_dev.json"
OUTPUTS_DIR = Path(__file__).parent / "outputs"

PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
}


def compute_cost(usage):
    rates = PRICING[usage["model"]]
    return usage["input_tokens"] / 1_000_000 * rates["input"] + usage["output_tokens"] / 1_000_000 * rates["output"]


def load_edge_case_messages():
    with open(DATA_PATH, encoding="utf-8") as f:
        messages = json.load(f)
    return [m for m in messages if m.get("edge_case_type") and m["split"] == "dev"]


def load_haiku_baseline(message_ids):
    with open(BASELINE_RUN_PATH, encoding="utf-8") as f:
        data = json.load(f)
    by_id = {r["id"]: r for r in data["results"] if "extraction" in r}
    return {mid: by_id[mid] for mid in message_ids if mid in by_id}


def main():
    load_dotenv()
    client = anthropic.Anthropic(max_retries=3, timeout=60.0)

    messages = load_edge_case_messages()
    message_ids = [m["id"] for m in messages]
    haiku_baseline = load_haiku_baseline(message_ids)
    missing = set(message_ids) - set(haiku_baseline)
    if missing:
        print(f"Warning: {len(missing)} message(s) not found in baseline run, skipping: {missing}")
        messages = [m for m in messages if m["id"] not in missing]

    opus_config = copy.deepcopy(CONFIG)
    opus_config["models"]["classify_extract"] = "claude-opus-4-8"

    print(f"Running {len(messages)} dev-split edge-case message(s) through claude-opus-4-8...")
    rows = []
    total_opus_cost = 0.0
    for i, msg in enumerate(messages, start=1):
        extraction, usage = classify_and_extract(
            client, msg["text"], opus_config, entry_channel=msg.get("entry_channel"),
        )
        confidence = score_confidence(extraction, opus_config)
        cost = compute_cost(usage)
        total_opus_cost += cost

        haiku_r = haiku_baseline[msg["id"]]
        row = {
            "id": msg["id"],
            "edge_case_type": msg["edge_case_type"],
            "ground_truth_category": msg["ground_truth_category"],
            "haiku_category": haiku_r["extraction"]["category"],
            "haiku_confidence": haiku_r["confidence"]["score"],
            "haiku_correct": haiku_r["extraction"]["category"] == msg["ground_truth_category"],
            "opus_category": extraction["category"],
            "opus_confidence": confidence["score"],
            "opus_correct": extraction["category"] == msg["ground_truth_category"],
            "opus_cost_usd": round(cost, 6),
            "agree": haiku_r["extraction"]["category"] == extraction["category"],
        }
        rows.append(row)
        print(f"  [{i}/{len(messages)}] {msg['id']} ({msg['edge_case_type']}): "
              f"haiku={row['haiku_category']}({row['haiku_confidence']}) "
              f"opus={row['opus_category']}({row['opus_confidence']}) "
              f"gt={msg['ground_truth_category']}")

    n = len(rows)
    haiku_accuracy = sum(r["haiku_correct"] for r in rows) / n
    opus_accuracy = sum(r["opus_correct"] for r in rows) / n
    agreement = sum(r["agree"] for r in rows) / n
    haiku_avg_conf = sum(r["haiku_confidence"] for r in rows) / n
    opus_avg_conf = sum(r["opus_confidence"] for r in rows) / n
    disagreements = [r for r in rows if not r["agree"]]

    summary = {
        "n_messages": n,
        "haiku_accuracy": round(haiku_accuracy, 4),
        "opus_accuracy": round(opus_accuracy, 4),
        "agreement_rate": round(agreement, 4),
        "haiku_avg_confidence": round(haiku_avg_conf, 2),
        "opus_avg_confidence": round(opus_avg_conf, 2),
        "total_opus_cost_usd": round(total_opus_cost, 6),
        "avg_opus_cost_per_message_usd": round(total_opus_cost / n, 6),
        "n_disagreements": len(disagreements),
    }

    print("\n--- Summary ---")
    for k, v in summary.items():
        print(f"{k}: {v}")
    if disagreements:
        print("\nDisagreements (haiku vs opus):")
        for r in disagreements:
            print(f"  {r['id']} ({r['edge_case_type']}): gt={r['ground_truth_category']} "
                  f"haiku={r['haiku_category']} opus={r['opus_category']}")

    OUTPUTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = OUTPUTS_DIR / f"opus_comparison_{timestamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=2)
    print(f"\nWrote {out_path}")
    return out_path


if __name__ == "__main__":
    main()
