# Black-Box Systems Lab

Five working implementations of the primitives that ordinary frameworks hide: a cache server, an RDBMS, a memory-backed agent, a private meta-search stack, and a C compiler.

```
systems-engineering-lab/
  01-redis-java/              raw TCP + RESP + TTL store
  02-database-engine/         slotted pages + LRU + B+ tree + SQL
  03-self-learning-agents/    working/episodic/semantic memory + ReAct
  04-private-metasearch/      SearXNG compose + aggregator + edge TLS
  05-c-compiler/              lexer → AST → x86-64 SysV assembly
```

## Quick test (this repo)

```bash
bash run_all.sh
```

| # | Project | Run |
| --- | --- | --- |
| 1 | Redis | `python3 01-redis-java/tests/test_resp.py` (Java: `java Redis.java --port 6379`) |
| 2 | MiniDB | `python3 02-database-engine/tests/test_minidb.py` |
| 3 | Agent | `python3 03-self-learning-agents/demo.py` |
| 4 | Meta-search | `python3 04-private-metasearch/aggregator/metasearch.py "B+ tree" --offline` |
| 5 | minic | `make -C 05-c-compiler test` |

See [ROADMAP.md](ROADMAP.md) for the technical comparison across layers, difficulty, and what each project demystifies.
