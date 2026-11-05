#!/usr/bin/env python3
"""
run_all.py - run every test suite and the evaluation.

    python tests/run_all.py          (about 2 minutes)
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SUITES = [
    ("ALU vs reference arithmetic", [os.path.join(HERE, "test_alu.py")]),
    ("software toolchain + CLI", [os.path.join(HERE, "test_toolchain.py")]),
    ("simulator vs pipeline_sim (both control units)", [os.path.join(HERE, "test_simulator.py")]),
    ("evaluation report", [os.path.join(ROOT, "evaluate.py")]),
]


def main():
    ok = True
    for name, cmd in SUITES:
        t = time.time()
        print(f"=== {name}")
        r = subprocess.run([sys.executable] + cmd, cwd=ROOT)
        print(f"    -> {'passed' if r.returncode == 0 else 'FAILED'} in {time.time() - t:.0f}s\n")
        ok &= r.returncode == 0
    print("ALL TEST SUITES PASSED" if ok else "SOME TEST SUITES FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
