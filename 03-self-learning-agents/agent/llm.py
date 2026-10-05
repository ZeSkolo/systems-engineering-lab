"""Pluggable LLM: OpenAI, Ollama, or a heuristic mock that still drives ReAct."""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Protocol


class LLM(Protocol):
    def complete(self, prompt: str, json_mode: bool = False) -> str: ...


class OpenAILLM:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.key = os.environ.get("OPENAI_API_KEY", "")

    def complete(self, prompt: str, json_mode: bool = False) -> str:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"]["content"]


class OllamaLLM:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("OLLAMA_MODEL", "llama3.1")
        self.host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

    def complete(self, prompt: str, json_mode: bool = False) -> str:
        body = {"model": self.model, "prompt": prompt, "stream": False}
        if json_mode:
            body["format"] = "json"
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
        return data.get("response", "")


class MockLLM:
    """Offline ReAct brain so the architecture runs without API keys."""

    def complete(self, prompt: str, json_mode: bool = False) -> str:
        if json_mode or "Return JSON only" in prompt:
            user = _after(prompt, "User:")
            facts = []
            m = re.search(r"my name is ([A-Za-z .'-]+)", user, re.I)
            if m:
                facts.append({"subject": "name", "text": f"User name: {m.group(1).strip()}", "obsolete": False})
            m = re.search(r"i (?:like|love|enjoy) ([^.!?]+)", user, re.I)
            if m:
                facts.append({"subject": "likes", "text": f"User likes: {m.group(1).strip()}", "obsolete": False})
            return json.dumps({"facts": facts})

        user = _last_user(prompt)
        if "Observation:" in prompt:
            obs = prompt.rsplit("Observation:", 1)[-1].strip().split("\n")[0]
            return f"Thought: The tool returned a usable result.\nFinal Answer: {obs}"

        math = re.search(r"([-+]?\d+(?:\.\d+)?)\s*([+\-*/x	imes])\s*([-+]?\d+(?:\.\d+)?)", user)
        if math:
            expr = f"{math.group(1)} {math.group(2).replace('x', '*').replace('	imes', '*')} {math.group(3)}"
            return f"Thought: This is arithmetic; use the calculator.\nAction: calculator\nAction Input: {expr}"

        if re.search(r"\b(what(?:'s| is) my name|who am i|what do i like|remember)\b", user, re.I):
            return (
                "Thought: I should consult long-term memory.\n"
                f"Action: memory_search\nAction Input: {user}"
            )

        if re.search(r"\b(my name is|i like|i live)\b", user, re.I):
            return (
                "Thought: Durable personal fact — store it.\n"
                f"Action: memory_save\nAction Input: {user}"
            )

        if re.search(r"\btime|date\b", user, re.I):
            return "Thought: Need the current time.\nAction: now\nAction Input: "

        return (
            "Thought: No tool required; answer from working memory.\n"
            f"Final Answer: I heard you say: {user}"
        )


def _after(text: str, marker: str) -> str:
    if marker not in text:
        return text
    return text.split(marker, 1)[1].strip()


def _last_user(prompt: str) -> str:
    if "Current user message:" in prompt:
        return prompt.rsplit("Current user message:", 1)[-1].strip().split("\n")[0]
    return prompt.strip().split("\n")[-1]


def make_llm() -> LLM:
    kind = os.environ.get("LLM_BACKEND", "mock").lower()
    if kind == "openai" and os.environ.get("OPENAI_API_KEY"):
        return OpenAILLM()
    if kind == "ollama":
        return OllamaLLM()
    return MockLLM()
