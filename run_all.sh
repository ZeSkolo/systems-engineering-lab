#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo "== 1 Redis RESP =="
python3 01-redis-java/tests/test_resp.py
echo "== 2 MiniDB =="
python3 02-database-engine/tests/test_minidb.py
echo "== 3 Agent =="
python3 03-self-learning-agents/tests/test_agent.py
echo "== 4 Meta-search =="
python3 04-private-metasearch/tests/test_aggregator.py
echo "== 5 C compiler =="
make -C 05-c-compiler test
echo
echo "ALL PROJECTS: PASS"
