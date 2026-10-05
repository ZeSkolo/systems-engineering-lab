# 3. Build Self-Learning AI Agents

Inspired by *Dave Ebbelaar* (autonomous agents with Mem0). Goes beyond one-shot RAG: memory persists, facts evolve, and the model *acts* before it answers.

## Architecture

```
user message
    |-> working memory (last N turns, in-process)
    |-> episodic log (JSON, every turn)
    |-> semantic facts (hashed embeddings + cosine search)
    v
ReAct loop (Thought / Action / Observation)*
    |-> calculator, now, memory_search, memory_save, note
    v
Final Answer
    v
extractor (regex + JSON LLM)  --upsert by subject-->  vector store
```

Contradiction handling: facts keyed by `subject` (`name`, `likes`, `location`, …) are upserted, so “my name is Ada” replaces “my name is Alan”.

## Run

```bash
python3 demo.py
python3 -m agent.cli --data ./data
# optional live models
LLM_BACKEND=openai OPENAI_API_KEY=sk-... python3 -m agent.cli
LLM_BACKEND=ollama python3 -m agent.cli
```

The default `MockLLM` is a heuristic ReAct brain so the pipeline runs offline. Swap the backend without changing memory, tools, or the loop.

## What you learn

LLM amnesia is an architecture problem. Keep the context window small (working memory), park history on disk (episodic), and retrieve only relevant facts (semantic). ReAct stops the model from guessing when a tool would be cheaper and more correct.
