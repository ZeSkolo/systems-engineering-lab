"""ReAct loop: Thought -> Action -> Observation, then extract durable facts."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extractor import apply_facts, extract_llm, extract_rules
from .llm import LLM, make_llm
from .memory import MemorySystem
from .tools import make_tools

REACT_PROMPT = """You are a ReAct agent with long-term memory.
Use this exact format:
Thought: <reason>
Action: <tool name>
Action Input: <input>
OR
Thought: <reason>
Final Answer: <answer to the user>

Tools: {tool_names}

Long-term semantic memory:
{memory}

Working memory (recent turns):
{working}

Current user message:
{user}
"""

ACTION_RE = re.compile(r"Action:\s*(\w+)\s*\nAction Input:\s*(.*)", re.I | re.S)
FINAL_RE = re.compile(r"Final Answer:\s*(.*)", re.I | re.S)


@dataclass
class TraceStep:
    thought: str
    action: str | None = None
    action_input: str | None = None
    observation: str | None = None
    final: str | None = None


@dataclass
class AgentResult:
    answer: str
    trace: list[TraceStep] = field(default_factory=list)
    extracted: list[str] = field(default_factory=list)


class Agent:
    def __init__(self, data_dir: str = "data", llm: LLM | None = None, max_steps: int = 6) -> None:
        self.memory = MemorySystem(data_dir)
        self.llm = llm or make_llm()
        self.tools = make_tools(self.memory)
        self.max_steps = max_steps

    def chat(self, user: str) -> AgentResult:
        self.memory.observe("user", user)
        trace: list[TraceStep] = []
        scratch = ""
        answer = ""
        for _ in range(self.max_steps):
            prompt = (
                REACT_PROMPT.format(
                    tool_names=", ".join(self.tools),
                    memory=self.memory.recall(user),
                    working=self.memory.working.prompt_block(),
                    user=user,
                )
                + scratch
            )
            raw = self.llm.complete(prompt)
            final_m = FINAL_RE.search(raw)
            act_m = ACTION_RE.search(raw)
            thought = raw.split("Thought:")[-1].split("\n")[0].strip() if "Thought:" in raw else ""
            if act_m and not (final_m and final_m.start() < act_m.start()):
                name, arg = act_m.group(1).strip(), act_m.group(2).strip().split("\n")[0]
                fn = self.tools.get(name)
                obs = fn(arg) if fn else f"unknown tool {name}"
                step = TraceStep(thought, name, arg, obs)
                trace.append(step)
                scratch += f"\nThought: {thought}\nAction: {name}\nAction Input: {arg}\nObservation: {obs}\n"
                continue
            if final_m:
                answer = final_m.group(1).strip()
                trace.append(TraceStep(thought, final=answer))
                break
            answer = raw.strip()
            trace.append(TraceStep(thought, final=answer))
            break
        if not answer:
            answer = "I could not finish reasoning in the step budget."
        self.memory.observe("assistant", answer)
        facts = extract_rules(user)
        facts += extract_llm(self.llm, user, answer)
        extracted = apply_facts(self.memory, facts)
        return AgentResult(answer, trace, extracted)
