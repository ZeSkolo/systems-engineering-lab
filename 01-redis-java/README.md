# 1. Build Your Own Redis in Java

Inspired by CodeCrafters and *Reinventing the Wheel*.

A high-throughput in-memory key-value store that speaks the official Redis Serialization Protocol (RESP) over raw TCP — no HTTP, no Netty, no Jedis protocol library.

## Architecture

```
client  --TCP-->  NIO Selector / thread-per-conn
                     |
                     v
              RESP byte parser
                     |
                     v
           CommandProcessor (PING/SET/GET/...)
                     |
                     v
        ConcurrentHashMap + TTL metadata
           |                      |
     passive expire          active daemon
      (on access)           (random sampling)
```

| Layer | What it does |
| --- | --- |
| Networking | Non-blocking `java.nio` `Selector` + `ServerSocketChannel` (Java) or thread-per-connection sockets (Python twin) |
| RESP codec | Parses `+ - : $ *` frames directly from socket bytes; also accepts telnet/inline commands |
| Storage | Concurrent hash table of string keys |
| Eviction | Passive TTL on read/write + background sampler every 100ms |

## Commands

`PING` `ECHO` `SET` `GET` `DEL` `EXISTS` `EXPIRE` `PEXPIRE` `TTL` `PTTL` `KEYS` `FLUSHDB` `INCR` `DECR` `INCRBY` `MGET` `MSET` `TYPE` `DBSIZE` `QUIT`

`SET` options: `EX seconds`, `PX milliseconds`, `NX`, `XX`.

## Run (JDK 17+)

```bash
java Redis.java --port 6379
redis-cli PING
redis-cli SET session alice EX 10
redis-cli GET session
```

## Run without a JDK

This sandbox / some machines only ship a JRE. A protocol-compatible twin lives in `python-runtime/` so you can still exercise RESP, TTL, and concurrency:

```bash
python3 python-runtime/mini_redis.py --port 6379
python3 tests/test_resp.py
```

## What you learn

Caching servers hit microsecond latencies by skipping HTTP/REST: length-prefixed binary frames, zero deserialization into JSON objects, and an in-memory hash table with O(1) average lookups.
