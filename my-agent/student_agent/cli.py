import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

from .agent import run_agent
from .model import OllamaModel
from .runtime import Runtime

PACKAGE_DIR = Path(__file__).resolve().parents[1]

DEFAULT_FIXTURE = PACKAGE_DIR.parent / "fixtures" / "decimal-offline.txt"

MODEL_MAX_TURNS = 15
MODEL_NUM_CTX = 16384

def build_parser():
    parser = argparse.ArgumentParser(description="Run the POC coding harness.")
    parser.add_argument("--root", required=True, help="target workspace")
    parser.add_argument("--task", required=True)
    parser.add_argument("--max-turns", type=int, default=MODEL_MAX_TURNS)
    parser.add_argument("--mode", type=str, choices=["read-only", "edit"], default="read-only")
    parser.add_argument("--tools", type=str, default="all")
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"))
    parser.add_argument("--offline", nargs="?",
                        const=str(DEFAULT_FIXTURE), metavar="FIXTURE",)
    return parser

def ask_approval(command, cwd):
    """Human approval for one bash command; only exact 'yes' approves."""
    print(f"\n[bash request]\n  command: {command}\n  cwd:     {cwd}", file=sys.stderr)
    try:
        answer = input("Approve this command only? Type yes: ")
    except EOFError:
        answer = ""
    return answer == "yes"

def main():
    parser = build_parser()
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not root.is_dir():
        parser.error(f"--root is not a directory: {root}")
    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    trace = (PACKAGE_DIR / "traces" / f"{run_id}.jsonl").resolve()
    if trace.is_relative_to(root):
        parser.error("trace files must stay outside --root; select the target-service directory")
    trace.parent.mkdir(parents=True, exist_ok=True)
    enabled_tools = None
    if not args.tools == "all":
        enabled_tools = args.tools.split(",")


    with trace.open("a", encoding="utf-8") as log:

        def emit(event):
            payload = json.dumps(event, default=str)

            log.write(
                json.dumps(
                    {"time": time.time(), **event},
                    default=str,
                ) + "\n"
            )
            log.flush()

            print(
                f"[{event.get('type')}] {payload[:400]}",
                file=sys.stderr,
            )

        def approve(command, cwd):
            approved = ask_approval(command, cwd)

            emit({
                "type": "approval",
                "command": command,
                "cwd": cwd,
                "approved": approved,
            })

            return approved

        model_name = args.model

        ollama = OllamaModel(
            model_name,
            num_ctx=MODEL_NUM_CTX,
        )

        def model(messages):
            reply = ollama(messages)

            emit({
                "type": "usage",
                **ollama.last_usage,
            })

            return reply

        with Runtime(root, approve=approve, enabled=enabled_tools,
                mode=args.mode, offline=args.offline) as runtime:

            emit({
                "type": "start",
                "model": model_name,
                "root": str(root),
                "mode": args.mode,
                "tools": [t["name"] for t in runtime.describe_tools()],
                "offline": args.offline,
                "task": args.task
            })

            outcome = run_agent(
                model, runtime, args.task,
                max_turns=args.max_turns, emit=emit,
            )

    termination = outcome["termination"]

    print(f"\ntermination: {termination}")

    if "final" in outcome:
        # The claim is the model's own text; nothing here has checked it.
        print(f"Model claims (unverified): {outcome['final']}")
    else:
        print(outcome.get("reason", ""))

    print(f"trace: {trace}")

    return 0 if termination == "final" else 1

if __name__ == "__main__":
    sys.exit(main())
