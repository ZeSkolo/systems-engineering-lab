#!/usr/bin/env python3
from __future__ import annotations

import argparse
from .react import Agent


def main() -> None:
    p = argparse.ArgumentParser(description="Self-learning ReAct agent")
    p.add_argument("--data", default="data")
    p.add_argument("-c", "--chat", help="single message")
    p.add_argument("--trace", action="store_true")
    args = p.parse_args()
    agent = Agent(data_dir=args.data)
    if args.chat:
        result = agent.chat(args.chat)
        print(result.answer)
        if args.trace:
            for step in result.trace:
                print(f"  thought: {step.thought}")
                if step.action:
                    print(f"  {step.action}({step.action_input}) -> {step.observation}")
            if result.extracted:
                print("extracted:", "; ".join(result.extracted))
        return
    print("Self-learning agent. Memory persists in", args.data)
    print("Type quit to exit.")
    while True:
        try:
            line = input("you> ").strip()
        except EOFError:
            print()
            break
        if line in {"quit", "exit"}:
            break
        if not line:
            continue
        result = agent.chat(line)
        print("agent>", result.answer)
        if result.extracted:
            print("  memory+", result.extracted)


if __name__ == "__main__":
    main()
