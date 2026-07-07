"""
Eval-as-CI: a small, fixed set of "known-answer" messages, run through
the real pipeline against the real API, with hard pass/fail assertions.

This is the answer to "how do you know the next prompt change won't
reintroduce a bug you already fixed" - the deliberate near-miss cases
below (messages that superficially resemble a sensitive topic or
retention risk but aren't one) are encoded as regression cases that
must never misfire, alongside the messages that must still be caught
correctly. Run this before shipping any prompt change, or let CI run
it automatically on every push (see .github/workflows/eval.yml).

Exits non-zero on any failure, so it's usable as a CI gate, not just a
manual sanity check.
"""

import json
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from config import CONFIG
from pipeline import classify_and_extract

DATA_PATH = Path(__file__).parent / "data" / "sample_messages.json"

# Each case: message id, and the specific assertion(s) it must satisfy -
# one classify_and_extract call per unique message id, all assertions for
# that id checked against the same extraction (not re-queried per check).
# "must_flag_sensitive" / "must_not_flag_sensitive" check sensitive_topic_flags.
# "must_flag_retention" / "must_not_flag_retention" check retention_risk_language.
# "must_be_category" checks the predicted category outright.
CASES = [
    # Sensitive-topic true positives - recall must hold.
    {"id": "msg_022", "must_flag_sensitive": True},
    {"id": "msg_024", "must_flag_sensitive": True},
    {"id": "msg_025", "must_flag_sensitive": True},
    {"id": "msg_026", "must_flag_sensitive": True},
    {"id": "msg_027", "must_flag_sensitive": True},
    {"id": "held_004", "must_flag_sensitive": True},
    {"id": "held_005", "must_flag_sensitive": True},
    # Near-misses that must stay unflagged - ordinary access/billing
    # confusion that superficially resembles a sensitive topic but isn't
    # one (a locked-out teammate is not "unauthorized access"; a routine
    # billing question is not a dispute).
    {"id": "msg_017", "must_not_flag_sensitive": True},
    {"id": "msg_021", "must_not_flag_sensitive": True},
    {"id": "msg_035", "must_not_flag_sensitive": True},
    {"id": "held_009", "must_not_flag_sensitive": True},
    # Retention-risk true positives - recall must hold, both formal
    # close/cancel requests and softer switching-provider language.
    {"id": "msg_028", "must_flag_retention": True},
    {"id": "msg_030", "must_flag_retention": True},
    {"id": "msg_032", "must_flag_retention": True},
    {"id": "msg_033", "must_flag_retention": True},
    {"id": "held_006", "must_flag_retention": True},
    {"id": "held_008", "must_flag_retention": True},
    # Anger about a billing issue, with no actual leaving/switching
    # language, must NOT be flagged as retention risk. Also a
    # sensitive-topic true positive in the same message - one call
    # covering both assertions.
    {"id": "msg_034", "must_not_flag_retention": True},
    {"id": "msg_036", "must_flag_sensitive": True, "must_not_flag_retention": True},
    # Basic classification sanity checks - clean, unambiguous messages
    # should still land in the right category.
    {"id": "msg_001", "must_be_category": "Service"},
    {"id": "msg_037", "must_be_category": "Success"},
    {"id": "msg_054", "must_be_category": "Sales"},
]


def load_message(all_messages, msg_id):
    for m in all_messages:
        if m["id"] == msg_id:
            return m
    raise KeyError(f"message {msg_id} not found in sample_messages.json")


def main():
    load_dotenv()
    client = anthropic.Anthropic(max_retries=3, timeout=60.0)

    with open(DATA_PATH, encoding="utf-8") as f:
        all_messages = json.load(f)

    failures = []
    for case in CASES:
        msg = load_message(all_messages, case["id"])
        extraction, _ = classify_and_extract(
            client, msg["text"], CONFIG, entry_channel=msg.get("entry_channel"),
        )

        is_sensitive = bool(extraction["sensitive_topic_flags"])
        is_retention = extraction["retention_risk_language"]
        category = extraction["category"]

        if case.get("must_flag_sensitive") and not is_sensitive:
            failures.append(f"{case['id']}: expected sensitive_topic_flags to fire, got none")
        if case.get("must_not_flag_sensitive") and is_sensitive:
            failures.append(f"{case['id']}: expected NO sensitive flag, got {extraction['sensitive_topic_flags']}")
        if case.get("must_flag_retention") and not is_retention:
            failures.append(f"{case['id']}: expected retention_risk_language=True, got False")
        if case.get("must_not_flag_retention") and is_retention:
            failures.append(f"{case['id']}: expected retention_risk_language=False, got True")
        if case.get("must_be_category") and category != case["must_be_category"]:
            failures.append(f"{case['id']}: expected category={case['must_be_category']}, got {category}")

        status = "FAIL" if any(case["id"] in f for f in failures) else "pass"
        print(f"  [{status}] {case['id']}")

    print()
    if failures:
        print(f"{len(failures)} regression(s) found:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print(f"All {len(CASES)} eval cases passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
