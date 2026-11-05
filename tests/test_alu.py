#!/usr/bin/env python3
"""
test_alu.py - RISC201_ALU against the reference arithmetic in pipeline_sim.py, for every
combination of ALU algorithms (2 adders x 2 multipliers x 2 dividers).

80,000 random integer and float operations per combination, including the special values
(0, -1, INT_MIN, +/-Inf, NaN, subnormals, float32 max), plus every latency against the spec.

Run:  python3 tests/test_alu.py
"""
import collections
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import alu                      # noqa: E402
import pipeline_sim as ps       # noqa: E402

INT = ['add', 'sub', 'mul', 'div', 'mod', 'and', 'or', 'not', 'mov', 'lsl', 'lsr', 'asr', 'min', 'max', 'abs', 'neg']
FLT = ['add', 'sub', 'mul', 'div', 'mov', 'min', 'max', 'abs', 'neg']
SPECIAL = [0, 1, 2, 3, 0xFFFFFFFF, 0x80000000, 0x7FFFFFFF, 0xFFFFFFFE, 7, 100, 0x7F7FFFFF, 0xFF7FFFFF,
           0x7F800000, 0xFF800000, 0x7FC00000, 0xFFC00000, 0x7F800001, 0x00000001, 0x80000001,
           0x007FFFFF, 0x3F800000, 0xBF800000, 0x00800000]


def fuzz(adder, mul, div, n=80000, seed=5):
    A = alu.RISC201_ALU(adder, mul, div)
    rnd = random.Random(seed)

    def val():
        k = rnd.random()
        if k < .3:
            return rnd.choice(SPECIAL)
        if k < .5:
            return ps.f2b(rnd.uniform(-1e6, 1e6))
        if k < .6:
            return ps.f2b(rnd.choice([1e38, -1e38, 3e38, 1e-38, -1e-39, 1e-45]))
        return rnd.getrandbits(32)
    bad, example = collections.Counter(), {}
    for _ in range(n):
        a, b = val(), val()
        F = rnd.random() < .5
        op = rnd.choice((FLT if F else INT) + ['cmp', 'fcvt'])
        try:
            if op == 'cmp':
                got = A.execute(ps.OPC['cmp'], a, b, is_float=F)[1:3]
                exp = ps.compare(F, a, b)
            elif op == 'fcvt':
                got = A.execute(31, 0, b, is_float=F, is_fcvt=True)[0]
                exp = ps.fcvt(F, b)
            else:
                got = A.execute(ps.OPC[op], a, b, is_float=F)[0]
                exp = ps.alu(op, F, a, b)
        except Exception as exc:                      # a crash counts as a failure
            got, exp = repr(exc), "no exception"
        if tuple(got) != tuple(exp) if isinstance(exp, tuple) else got != exp:
            key = ('f' if F else 'i') + op
            bad[key] += 1
            example.setdefault(key, (hex(a), hex(b), got, exp))
    lat_bad = []
    for op in INT + ['cmp', 'fcvt']:
        for F in (0, 1):
            if F and op not in FLT + ['cmp', 'fcvt']:
                continue
            u = ps.make_uop(ps.encode(ps.A(op, 1, 2, 3, F=F)), 0)
            got = A.execute(ps.OPC[op], 1, 1, is_float=bool(F), is_fcvt=(op == 'fcvt'))[3]
            if got != ps.latency(u):
                lat_bad.append((op, F, got, ps.latency(u)))
    return bad, example, lat_bad


def main():
    ok = True
    for adder in ("kogge", "ripple"):
        for mul in ("booth", "shiftadd"):
            for div in ("nonrestoring", "restoring"):
                bad, example, lat_bad = fuzz(adder, mul, div)
                n = sum(bad.values())
                print(f"  {adder:<7}{mul:<9}{div:<13} 80000 int/float operations: {n} mismatches; "
                      f"latency mismatches: {len(lat_bad)}")
                for k, v in example.items():
                    print("     e.g.", k, v)
                ok &= not n and not lat_bad
    print("ALU CHECKS PASSED" if ok else "ALU CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
