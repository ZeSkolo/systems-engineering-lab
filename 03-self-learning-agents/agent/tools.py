"""ReAct tools with JSON-serializable results."""
from __future__ import annotations

import ast
import operator
import time
from typing import Callable

from .memory import MemorySystem

OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}


def _eval_expr(node):
    if isinstance(node, ast.Expression):
        return _eval_expr(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_eval_expr(node.left), _eval_expr(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_eval_expr(node.operand))
    raise ValueError("unsupported expression")


def make_tools(memory: MemorySystem) -> dict[str, Callable[[str], str]]:
    notes: list[str] = []

    def calculator(expr: str) -> str:
        tree = ast.parse(expr.replace("	imes", "*").replace("x", "*"), mode="eval")
        return str(_eval_expr(tree))

    def now(_: str) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S %Z")

    def memory_search(query: str) -> str:
        return memory.recall(query)

    def memory_save(text: str) -> str:
        memory.semantic.remember(text, kind="note", subject="manual")
        return f"saved: {text}"

    def note(text: str) -> str:
        notes.append(text)
        return f"noted ({len(notes)}): {text}"

    return {
        "calculator": calculator,
        "now": now,
        "memory_search": memory_search,
        "memory_save": memory_save,
        "note": note,
    }
