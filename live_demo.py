"""
Live, on-the-spot demo runner: takes one message you type in and runs it
through the real pipeline in real time, exactly like every message in the
100-message dev set - same classify/extract/confidence/route/draft/
investigate logic, same live model calls. Nothing here is pre-scored or
pre-recorded.

Usage (edit the two variables below, then run):
    python live_demo.py

Or from the command line without editing the file:
    python live_demo.py "the message text" --channel Support

--channel accepts: Support, Sales, "Success mailbox" (default: Support).
"""

import argparse
import json

import anthropic
from dotenv import load_dotenv

from config import CONFIG
from pipeline import process_message

# Edit these two lines for a quick in-editor run without command-line args.
DEFAULT_TEXT = "Our workspace has been showing a sync error for a week and nobody's replied to my last two tickets - what's going on?"
DEFAULT_CHANNEL = "Support"


def main():
    parser = argparse.ArgumentParser(description="Run one live message through the real pipeline.")
    parser.add_argument("text", nargs="?", default=DEFAULT_TEXT, help="The message text.")
    parser.add_argument("--channel", default=DEFAULT_CHANNEL, help="Entry channel: Support, Sales, or 'Success mailbox'.")
    args = parser.parse_args()

    load_dotenv()
    client = anthropic.Anthropic(max_retries=3, timeout=60.0)

    message = {"id": "live_demo", "text": args.text, "entry_channel": args.channel}

    print(f"Running live against: {args.channel!r} channel\n{args.text!r}\n")
    result = process_message(client, message, CONFIG)

    print("=" * 60)
    print(f"Category:    {result['extraction']['category']}")
    print(f"Confidence:  {result['confidence']['score']} ({result['confidence']['band']})")
    print(f"Reasoning:   {result['confidence']['reasons']}")
    print(f"Queue:       {result['queue']}  (loop in: {result['loop_in']})")
    if result.get("investigation_summary"):
        print(f"\nInvestigation:\n{result['investigation_summary']}")
    print(f"\nDraft reply:\n{result.get('draft')}")
    print("=" * 60)
    print("\nFull result JSON:")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
