# Technical Roadmap Comparison

These five projects cut through different black boxes. They do not share a stack on purpose: each one forces a different primitive into the open.

## Layer map

```
Applied AI          03  memory pipeline + ReAct
App protocols       01  RESP / in-memory KV
Query languages     02  SQL → pages → B+ tree
Edge / privacy      04  compose + reverse proxy + sanitization
Language toolchain  05  C → AST → x86-64
```

## Comparison

| | 1 Redis (Java) | 2 Database | 3 Self-learning agent | 4 Private meta-search | 5 C compiler |
| --- | --- | --- | --- | --- | --- |
| Origin | CodeCrafters / Reinventing the Wheel | Code With Sep | Dave Ebbelaar / Mem0 | NetworkChuck / SearXNG | lolzdev / self-hosting compilers |
| Black box opened | “Redis is just a cache” | “SQL is just a query” | “Chatbots remember” | “Search is a website” | “gcc is magic” |
| Hidden primitive | Binary protocol + O(1) RAM map | Paging, WAL-like flush, indexes | Layered memory, not a bigger prompt | Aggregation + IP hygiene | Formal grammar → machine code |
| Language | Java NIO (`Redis.java`) | Python | Python | Compose + Python aggregator | C |
| I/O model | Non-blocking TCP, byte buffers | 4 KB pages, LRU pool, `fsync` | JSON + embeddings | Containers, Redis limiter, TLS | Files in, assembly out |
| Hot path | RESP parse → ConcurrentHashMap | B+ tree leaf walk | cosine k-NN + tool call | parallel engine fan-out | AST walk → `call` / `je` |
| Concurrency | Selector + expiry daemon | Single-writer + buffer pool | ReAct steps, background extract | Docker network + rate limit | Single compilation unit |
| Persistence | Optional (RAM + TTL) | Dirty pages + superblock | episodic.json + semantic.json | none (live upstream) | none (emits `.s`) |
| Failure mode you feel | Protocol desync, expired keys | Full pages, eviction, lost fsync | context bloat, stale facts | fingerprinting, open proxy | wrong offset, smashed stack |
| What frameworks hide | Jedis, Netty, HTTP JSON APIs | SQLite/Postgres, ORMs | LangChain memory wrappers | Google’s frontend | gcc / LLVM |
| Difficulty | M | L | M | M (ops) | L |
| Next boss fight | RDB / AOF, replication | WAL, MVCC, joins | tool graphs, eval harness | multi-region, Captcha/bot | structs, preprocessor, self-host |

## Suggested build order

1. **Redis** — smallest closed loop (bytes in, bytes out). Teaches buffers and protocols.
2. **Compiler** — same idea one layer down (chars in, instructions out).
3. **Database** — compiler ideas (trees, offsets) plus Redis ideas (eviction) on disk.
4. **Meta-search** — leave the process and operate a *system* (DNS, TLS, compose).
5. **Agent** — uses all of the above metaphors: working set (Redis), long-term store (DB), tools (syscalls), and a loop (the processor).

Skip around if you already own one layer. The comparison is about *which primitive you still treat as magic*.

## Honest scope

These are educational systems that actually run, not production replacements.

- Redis speaks real RESP and TTL; it is not Redis Cluster.
- MiniDB is SQLite-shaped (pages, B+, SQL subset), not Postgres WAL/MVCC.
- The agent’s default brain is a MockLLM so the *architecture* runs offline; point `LLM_BACKEND=openai` or `ollama` at a real model.
- SearXNG is a deployable Compose stack; the Python aggregator is the fan-out/re-rank lesson without Docker.
- minic compiles a C subset to x86-64. Self-hosting needs structs + preprocessor — the remaining compiler theory homework.
