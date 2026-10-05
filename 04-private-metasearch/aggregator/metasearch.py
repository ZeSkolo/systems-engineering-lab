#!/usr/bin/env python3
"""Educational meta-search: parallel engines, tracker stripping, re-rank.

This is the aggregation layer SearXNG performs at scale. Offline engines always
work; Wikipedia OpenSearch is used when the network is available.
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from html import unescape
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

TRACKING_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "referrer",
}


@dataclass
class Result:
    title: str
    url: str
    snippet: str
    engine: str
    score: float


def strip_tracking(url: str) -> str:
    parts = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_KEYS]
    return urlunparse(parts._replace(query=urlencode(q), fragment=""))


def mock_docs() -> list[Result]:
    corpus = [
        ("Redis protocol RESP", "https://redis.io/docs/latest/develop/reference/protocol-spec/?utm_source=ad", "RESP arrays, bulk strings, and integers.", "catalog"),
        ("SQLite file format", "https://www.sqlite.org/fileformat.html?fbclid=abc", "Pages, B-trees, and the database header.", "catalog"),
        ("SearXNG documentation", "https://docs.searxng.org/", "Self-hosted metasearch engine.", "catalog"),
        ("x86-64 System V ABI", "https://wiki.osdev.org/System_V_ABI", "Calling conventions, registers, stack frames.", "catalog"),
        ("ReAct prompting", "https://arxiv.org/abs/2210.03629", "Reason + Act loops for language agents.", "catalog"),
        ("B+ tree indexes", "https://en.wikipedia.org/wiki/B%2B_tree", "Leaf-linked balanced trees for range scans.", "catalog"),
    ]
    return [Result(t, strip_tracking(u), s, e, 1.0) for t, u, s, e in corpus]


def engine_catalog(query: str) -> list[Result]:
    q = query.lower().split()
    out = []
    for r in mock_docs():
        hay = (r.title + " " + r.snippet).lower()
        hits = sum(1 for w in q if w in hay)
        if hits:
            out.append(Result(r.title, r.url, r.snippet, "catalog", 1.0 + hits))
    return out


def engine_wikipedia(query: str) -> list[Result]:
    url = "https://en.wikipedia.org/w/api.php?action=opensearch&limit=5&namespace=0&format=json&search=" + query.replace(" ", "%20")
    req = Request(url, headers={"User-Agent": "PrivateMetaSearch/1.0 (educational)"})
    with urlopen(req, timeout=4) as resp:
        title, titles, descs, links = json.loads(resp.read().decode())
        _ = title
    out = []
    for t, d, link in zip(titles, descs, links):
        out.append(Result(t, strip_tracking(link), d or "", "wikipedia", 1.2))
    return out


ENGINES: dict[str, Callable[[str], list[Result]]] = {
    "catalog": engine_catalog,
    "wikipedia": engine_wikipedia,
}


def rerank(results: list[Result]) -> list[Result]:
    seen = set()
    merged: list[Result] = []
    for r in results:
        key = re.sub(r"^www\\.", "", urlparse(r.url).netloc + urlparse(r.url).path)
        if key in seen:
            continue
        seen.add(key)
        bonus = 0.15 if r.engine == "wikipedia" else 0.0
        diversity = 0.1 if urlparse(r.url).netloc not in {urlparse(x.url).netloc for x in merged} else 0.0
        merged.append(Result(r.title, r.url, r.snippet, r.engine, r.score + bonus + diversity))
    merged.sort(key=lambda r: r.score, reverse=True)
    return merged


def search(query: str, engines: list[str] | None = None) -> list[Result]:
    names = engines or list(ENGINES)
    bag: list[Result] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(ENGINES[name], query): name for name in names if name in ENGINES}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                bag.extend(fut.result())
            except Exception as exc:
                bag.append(Result(f"{name} failed", "", str(exc), name, 0.0))
    return rerank(bag)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("query")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    engines = ["catalog"] if args.offline else None
    rows = search(args.query, engines)
    if args.json:
        print(json.dumps([asdict(r) for r in rows], indent=2))
        return
    for i, r in enumerate(rows, 1):
        print(f"{i}. [{r.engine} {r.score:.2f}] {unescape(r.title)}")
        print(f"   {r.url}")
        if r.snippet:
            print(f"   {r.snippet}")


if __name__ == "__main__":
    main()
