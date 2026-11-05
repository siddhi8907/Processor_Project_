#!/usr/bin/env python3
"""
test_simulator.py - checks simulator.py against the verified models in pipeline_sim.py.

Every check runs with BOTH microprogrammed control units (vertical and horizontal).
For every program, the three simulator modes must end in exactly the same architectural
state (registers, memory, flags, epc, cause, retire order) as pipeline_sim.Ref, and the
4-stage / 6-stage modes must take exactly as many cycles as pipeline_sim.Pipe4 / Pipe6.

Programs used:
  1. every directed program in pipeline_sim.directed() (captured, not copied)
  2. every program of hazard_check.py's directed matrix and hand-written cases (if present)
  3. random programs from pipeline_sim.gen_program and hazard_check.gen_dense
  4. the assembly demo, through assembler -> simulator -> disassembler

Run:  python3 test_simulator.py          (needs pipeline_sim.py next to it)
"""
import io
import os
import random
import sys
from contextlib import redirect_stdout
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import pipeline_sim as ps

import assembler
import disassembler
from simulator import Config, DEMO, Simulator

MODES = ("functional", "4stage", "6stage")
CFG = Config(stack_base=ps.STACK_BASE, stack_limit=ps.STACK_LIMIT, exec_vector=ps.EXEC_VECTOR,
             initial_regs={ps.DBASE: 0x100}, default_handler=None)   # pipeline_sim's start state


def check(words, label, fails, max_cycles=200000):
    ref = ps.Ref(words).run()
    want = {"4stage": ps.Pipe4(words).run().cycles, "6stage": ps.Pipe6(words).run().cycles}
    for control in ("vertical", "horizontal"):
        for mode in MODES:
            sim = Simulator(mode=mode, config=replace(CFG, control=control), trace_limit=1)
            sim.load_words(words)
            sim.run(max_cycles=max_cycles)
            if sim.state() != ref.state():
                fails.append(f"{label}: {mode}/{control} state differs from reference")
            elif mode in want and sim.cycles != want[mode]:
                fails.append(f"{label}: {mode}/{control} took {sim.cycles} cycles, pipeline_sim took {want[mode]}")


def captured_directed():
    """Run pipeline_sim.directed() and capture each program it builds."""
    progs = []
    orig = ps.compare_run

    def spy(words, label, **opt):
        progs.append((label, list(words)))
        return orig(words, label, **opt)
    ps.compare_run = spy
    try:
        with redirect_stdout(io.StringIO()):
            ps.directed()
    finally:
        ps.compare_run = orig
    return progs


def captured_hazard_cases():
    try:
        import hazard_check as hc
    except Exception:
        return [], None
    progs, seen = [], set()
    orig = hc.check_program

    def spy(words, cls, spec=None, keep=3):
        key = tuple(words)
        if key not in seen:
            seen.add(key)
            progs.append((f"hazard#{len(progs)}", list(words)))
        return orig(words, cls, spec, keep)
    hc.check_program = spy
    try:
        hc.directed_matrix()
        hc.directed_extra()
    finally:
        hc.check_program = orig
    return progs, hc


def main():
    ok = True
    fails = []

    progs = captured_directed()
    for label, words in progs:
        check(words, label, fails)
    print(f"1. pipeline_sim directed programs: {len(progs)} x 3 modes x 2 control units, failures: {len(fails)}")
    ok &= not fails
    for f in fails[:5]:
        print("   FAIL", f)

    fails = []
    hprogs, hc = captured_hazard_cases()
    for label, words in hprogs:
        check(words, label, fails)
    print(f"2. hazard_check matrix + hand cases: {len(hprogs)} programs x 3 modes x 2 control units, failures: {len(fails)}"
          + ("" if hc else "  (hazard_check.py not found, skipped)"))
    ok &= not fails
    for f in fails[:5]:
        print("   FAIL", f)

    fails = []
    rnd = random.Random(2024)
    n1 = 1000
    for t in range(n1):
        main_, subs = ps.gen_program(rnd)
        check(ps.assemble(main_, subs), f"random#{t}", fails)
    n2 = 0
    if hc is not None:
        rnd2 = random.Random(11)
        n2 = 600
        for t in range(n2):
            main_, subs = hc.gen_dense(rnd2)
            check(ps.assemble(main_, subs), f"dense#{t}", fails)
    print(f"3. random programs: {n1} general + {n2} hazard-dense x 3 modes x 2 control units, failures: {len(fails)}")
    ok &= not fails
    for f in fails[:5]:
        print("   FAIL", f)

    # 4. assembly path: assemble, run in all modes, disassemble round trip
    fails = []
    states = {}
    for mode in MODES:
        sim = Simulator(mode=mode)
        res = sim.load_source(DEMO, "demo.asm")
        r = sim.run()
        states[mode] = sim.state()
        if r.reason != "halted":
            fails.append(f"demo did not halt in {mode}")
    if len(set(states.values())) != 1:
        fails.append("demo: modes disagree")
    back = assembler.assemble_source(disassembler.disassemble_words(res.words)).words
    if back != res.words:
        fails.append("demo: disassemble -> assemble round trip changed the program")
    sim = Simulator(mode="6stage")
    sim.load_source(DEMO, "demo.asm")
    if sim.read_reg(9) != 0 or sim.run(breakpoints={0x24}).reason != "breakpoint":
        fails.append("breakpoint did not trigger")
    print(f"4. assembler -> simulator -> disassembler demo: failures: {len(fails)}")
    ok &= not fails
    for f in fails:
        print("   FAIL", f)

    print("ALL SIMULATOR CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
