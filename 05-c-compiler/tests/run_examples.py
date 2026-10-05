#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MINIC = ROOT / "minic"
EX = ROOT / "examples"

CASES = [
    ("01_return.c", 42),
    ("02_arith.c", 22),
    ("03_if.c", 7),
    ("04_while.c", 10),
    ("05_fact.c", 120),
    ("06_ptr.c", 4),
    ("07_add.c", 42),
]


def run(src: str, expected: int) -> None:
    asm = Path(f"/tmp/minic-{src}.s")
    exe = Path(f"/tmp/minic-{src}.out")
    asm.write_bytes(subprocess.check_output([str(MINIC), str(EX / src)]))
    subprocess.check_call(["gcc", "-o", str(exe), str(asm)])
    p = subprocess.run([str(exe)])
    code = p.returncode
    if code != expected:
        raise SystemExit(f"FAIL {src}: got {code} expected {expected}\n{asm.read_text()}")
    print(f"  {src}: exit {code} OK")


def main() -> None:
    if not MINIC.exists():
        subprocess.check_call(["make", "-C", str(ROOT)])
    print("minic examples:")
    for src, expected in CASES:
        run(src, expected)
    print("c compiler tests: PASS")


if __name__ == "__main__":
    main()
