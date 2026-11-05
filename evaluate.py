#!/usr/bin/env python3
"""
evaluate.py - evaluation of the RISC201 processor, control unit and ALU (assignment part 5)

Runs every program in programs/ on
    3 processor models   functional | 4-stage | 6-stage
    2 control units      vertical | horizontal microprogrammed
    2 ALU adders         Kogge-Stone | ripple-carry
checks the "; EXPECT ..." lines of every program in all 12 configurations, and writes
docs/evaluation_report.md with:
    1. correctness matrix
    2. 4-stage vs 6-stage behaviour (cycles, CPI, stall and flush breakdown)
    3. vertical vs horizontal control memory
    4. ALU algorithms: ripple-carry vs Kogge-Stone adder, Booth radix-4 vs shift-add multiplier,
       non-restoring vs restoring divider (all 8 combinations run on every program)

Run:  python evaluate.py            (exit code 0 = every expectation met)
"""
from __future__ import annotations

import glob
import os
import random
import re
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

import alu as alu_mod                                       # noqa: E402
import preprocessor                                         # noqa: E402
import horizontal_microprogramming as HMP                   # noqa: E402
import vertical_microprogramming as VMP                     # noqa: E402
from simulator import Config, Simulator                     # noqa: E402

MODES = ("functional", "4stage", "6stage")
CONTROLS = ("vertical", "horizontal")
ADDERS = ("kogge", "ripple")
MULS = ("booth", "shiftadd")
DIVS = ("nonrestoring", "restoring")
PROGRAMS = sorted(glob.glob(os.path.join(ROOT, "programs", "*.asm")))

_EXPECT = re.compile(r";\s*EXPECT\s+(\S+)\s*=\s*(\S+)", re.I)


def parse_program(path):
    text = open(path, encoding="utf-8").read()
    expects = [(k.lower(), int(v, 0)) for k, v in _EXPECT.findall(text)]
    config = preprocessor.read_config(text)
    return text, expects, config


def run_program(path, mode="6stage", control="vertical", adder="kogge", **overrides):
    text, expects, config = parse_program(path)
    cfg = Config(control=control, adder=adder, **{**config, **overrides})
    sim = Simulator(mode, cfg, trace_limit=1)
    sim.load_source(text, filename=os.path.basename(path))
    result = sim.run(max_cycles=2_000_000)
    return sim, result, expects


def check_expectations(sim, result, expects):
    """-> list of failure strings (empty = all good)"""
    fails = []
    if result.reason != "halted":
        fails.append(f"did not halt ({result.reason})")
    import isa
    for key, want in expects:
        m = re.fullmatch(r"mem\[(.+)\]", key)
        if m:
            got = sim.read_mem(int(m.group(1), 0))
        elif key in ("cause", "epc", "pc"):
            got = {"cause": sim.core.cause, "epc": sim.core.epc, "pc": sim.pc}[key]
        else:
            r = isa.parse_register(key)
            if r is None:
                fails.append(f"bad EXPECT key '{key}'")
                continue
            got = sim.read_reg(r)
        if got != (want & 0xFFFFFFFF):
            fails.append(f"{key} = {got:#x}, expected {want & 0xFFFFFFFF:#x}")
    return fails


def check_all(verbose=True):
    """Correctness of every program in every configuration. -> (results, failures)"""
    results, failures = {}, []
    for path in PROGRAMS:
        name = os.path.basename(path)
        states = set()
        for mode in MODES:
            for control in CONTROLS:
                for adder in ADDERS:
                    sim, res, expects = run_program(path, mode, control, adder)
                    fails = check_expectations(sim, res, expects)
                    results[(name, mode, control, adder)] = (sim.stats(), fails)
                    states.add(sim.state())
                    for f in fails:
                        failures.append(f"{name} [{mode}/{control}/{adder}]: {f}")
        if len(states) != 1:
            failures.append(f"{name}: configurations ended in different architectural states")
        if verbose:
            ok = not any(f.startswith(name) for f in failures)
            print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return results, failures


def alu_benchmark(n=20000, seed=7):
    rnd = random.Random(seed)
    pairs = [(rnd.getrandbits(32), rnd.getrandbits(32)) for _ in range(n)]
    out = {}
    for adder in ADDERS:
        a = alu_mod.RISC201_ALU(adder_mode=adder)
        wrong = 0
        t0 = time.perf_counter()
        for x, y in pairs:
            if a.execute(0, x, y)[0] != (x + y) & 0xFFFFFFFF:
                wrong += 1
            if a.execute(1, x, y)[0] != (x - y) & 0xFFFFFFFF:
                wrong += 1
        out[adder] = (wrong, (time.perf_counter() - t0) / (2 * n) * 1e6)
    return out


def alu_algorithm_sweep():
    """Every program with every multiplier x divider: same results? how many add/sub steps?"""
    steps = {(m, d): {} for m in MULS for d in DIVS}
    failures = []
    for path in PROGRAMS:
        name = os.path.basename(path)
        states, cycles = set(), set()
        for m in MULS:
            for d in DIVS:
                for adder in ADDERS:
                    sim, res, expects = run_program(path, "6stage", "vertical", adder, mul=m, div=d)
                    for f in check_expectations(sim, res, expects):
                        failures.append(f"{name} [mul {m}, div {d}, adder {adder}]: {f}")
                    states.add(sim.state())
                    cycles.add(sim.cycles)
                    if adder == "kogge":
                        for k, v in sim.alu.steps.items():
                            steps[(m, d)][k] = steps[(m, d)].get(k, 0) + v
        if len(states) != 1 or len(cycles) != 1:
            failures.append(f"{name}: the ALU algorithms changed the result or the cycle count")
    return steps, failures


def mul_div_benchmark(n=4000, seed=11):
    """Average add/sub steps per operation on random operands (and the worst case seen)."""
    rnd = random.Random(seed)
    pairs = [(rnd.getrandbits(32), rnd.getrandbits(32) or 1) for _ in range(n)]
    small = [(rnd.getrandbits(32), rnd.getrandbits(8) or 1) for _ in range(n)]
    out = {}
    for name, fn, key in [("booth", alu_mod.booth_radix4_mul_32, "mul_addsub"),
                          ("shiftadd", alu_mod.shift_add_mul_32, "mul_addsub"),
                          ("nonrestoring", alu_mod.non_restoring_div_mod_32, "div_addsub"),
                          ("restoring", alu_mod.restoring_div_mod_32, "div_addsub")]:
        per, t0 = [], time.perf_counter()
        for x, y in (pairs if key == "mul_addsub" else pairs + small):
            st = {}
            fn(x, y, st)
            per.append(st.get(key, 0))
        out[name] = (sum(per) / len(per), max(per), (time.perf_counter() - t0) / len(per) * 1e6)
    return out


def write_report(results, failures, bench, sweep_steps, md_bench):
    """Write docs/evaluation_report.md in plain language from the measured results."""
    L = []
    w = L.append
    n_cfg = len(MODES) * len(CONTROLS) * len(ADDERS)

    w("# RISC201 evaluation report")
    w("")
    w("This file is created automatically by `python evaluate.py`. Every program in `programs/` is run "
      f"{n_cfg} different ways (3 processor models x 2 control units x 2 adders) and the result is compared "
      "with the answer written in the program's `; EXPECT` lines.")
    w("")
    w("It answers four questions:")
    w("")
    w("1. Do all programs give the right answer?")
    w("2. Which is faster, the 4-stage or the 6-stage pipeline?")
    w("3. Does the vertical or horizontal control unit change anything?")
    w("4. Which algorithms does the ALU use?")
    w("")
    w("**Words used below**")
    w("")
    w("| word | meaning |")
    w("|---|---|")
    w("| cycle | one tick of the processor clock |")
    w("| CPI | cycles per instruction. Lower is better, 1.0 is the best a pipeline can do |")
    w("| stall | the pipeline waits one or more cycles, for example for a slow multiply or divide |")
    w("| wasted slot | an empty pipeline slot after a taken branch, because the instructions fetched behind it were wrong |")
    w("| load-use wait | an instruction needs a value that a load (`ld` / `pop`) has not delivered yet |")
    w("| vertical / horizontal | the two control-unit designs. Vertical stores short encoded control words, horizontal stores one bit per signal |")
    w("")

    # ---------------------------------------------------------------- 1. correctness
    w("## 1. Do all programs give the right answer?")
    w("")
    w("Each cell is `pass` when the program ended with exactly the values its `; EXPECT` lines promise. "
      "A cell covers both adders.")
    w("")
    w("| program | " + " | ".join(f"{m} + {c}" for m in MODES for c in CONTROLS) + " |")
    w("|---|" + "---|" * (len(MODES) * len(CONTROLS)))
    for path in PROGRAMS:
        name = os.path.basename(path)
        cells = []
        for m in MODES:
            for c in CONTROLS:
                ok = all(not results[(name, m, c, a)][1] for a in ADDERS)
                cells.append("pass" if ok else "**FAIL**")
        w(f"| {name} | " + " | ".join(cells) + " |")
    w("")
    w(f"Number of failures: **{len(failures)}**. All {n_cfg} ways of running a program also ended in exactly the "
      "same final state (registers, memory, flags, exception registers and order of instructions), "
      "so the pipeline and control unit choices never change the answer, only the speed.")
    w("")

    # ---------------------------------------------------------------- 2. pipelines
    w("## 2. Which is faster, the 4-stage or the 6-stage pipeline?")
    w("")
    w("Both were run with the vertical control unit and the Kogge-Stone adder. "
      "The number of instructions is the same for both.")
    w("")
    w("| program | instructions | 4-stage cycles | 4-stage CPI | 6-stage cycles | 6-stage CPI "
      "| stall cycles, slow units (4 / 6) | load-use waits (6-stage) | taken branches "
      "| wasted slots after branches (4 / 6) |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    tot = {"i": 0, "c4": 0, "c6": 0}
    for path in PROGRAMS:
        name = os.path.basename(path)
        s4 = results[(name, "4stage", "vertical", "kogge")][0]
        s6 = results[(name, "6stage", "vertical", "kogge")][0]
        tot["i"] += s4["retired"]
        tot["c4"] += s4["cycles"]
        tot["c6"] += s6["cycles"]
        w(f"| {name} | {s4['retired']} | {s4['cycles']} | {s4['cpi']} | {s6['cycles']} | {s6['cpi']} | "
          f"{s4.get('stall_ex_busy', 0)} / {s6.get('stall_ex_busy', 0)} | {s6.get('stall_load_use', 0)} | "
          f"{s4.get('branches_taken', 0)} | {s4.get('flush_bubbles', 0)} / {s6.get('flush_bubbles', 0)} |")
    cpi4, cpi6 = tot["c4"] / tot["i"], tot["c6"] / tot["i"]
    w(f"| **all programs** | {tot['i']} | {tot['c4']} | {cpi4:.3f} | {tot['c6']} | {cpi6:.3f} | | | | |")
    w("")
    w("**What this shows**")
    w("")
    w(f"* The 6-stage pipeline needs **{100 * (tot['c6'] / tot['c4'] - 1):.1f}% more cycles** than the 4-stage one "
      f"(CPI {cpi6:.3f} compared with {cpi4:.3f}).")
    w("* Reason 1: a taken branch, call or return wastes 3 slots in the 6-stage pipeline but only 2 in the "
      "4-stage one, because the 6-stage pipeline finds out where to jump one stage later.")
    w("* Reason 2: in the 6-stage pipeline a value read by `ld` or `pop` is not ready for the very next "
      "instruction, so it waits 1 cycle. The 4-stage pipeline reads memory inside its execute stage and never waits.")
    w("* Slow units (multiply 2 cycles, divide 8, float add/multiply 3, float divide 10, convert 2) stall both "
      "pipelines by the same amount.")
    w("* The 6-stage pipeline also needs 2 extra cycles at the start and end to fill and empty.")
    w("* Cycles are not the whole story. Each 6-stage stage does less work, so its clock can run faster. "
      f"If the 6-stage clock is at least {100 * (1 - tot['c4'] / tot['c6']):.0f}% faster, "
      "it finishes these programs sooner (time = cycles x clock period).")
    w("")

    # ---------------------------------------------------------------- 3. control units
    w("## 3. Does the vertical or horizontal control unit change anything?")
    w("")
    vbits = len(VMP.VERTICAL_ROM) * VMP.WIDTH
    hbits = len(HMP.CONTROL_MEMORY) * HMP.WIDTH
    nlines = sum(len(v) for v in VMP.FIELD_VALUES.values())
    w("| | vertical | horizontal |")
    w("|---|---|---|")
    w(f"| number of control words | {len(VMP.VERTICAL_ROM)} | {len(HMP.CONTROL_MEMORY)} |")
    w(f"| width of one control word | {VMP.WIDTH} bits, split into 5 encoded fields ("
      + ", ".join(f"{n} {VMP.FIELD_BITS[n]} bits" for n in VMP.FIELD_NAMES)
      + f") | {HMP.WIDTH} bits, one bit per control signal |")
    w(f"| total size of control memory | {vbits} bits | {hbits} bits |")
    w(f"| extra hardware after the memory | 5 decoders ({VMP.WIDTH} bits become {nlines} signal lines) "
      "| none, the bits drive the datapath directly |")
    w("| how the next control word is found | opcode, I and F select it | same |")
    w("| cycles and results | identical (see section 1) | identical |")
    w("")
    w(f"The vertical unit needs {100 * (1 - vbits / hbits):.0f}% fewer bits of memory, but it needs decoders "
      "after the memory, which adds delay. The horizontal unit uses more memory, but its signals are ready as "
      "soon as the word is read, and adding a new signal combination needs no new decoder. "
      "In both designs each instruction is one control word: the pipeline needs fresh control signals every "
      "cycle, so the control unit looks up one word per instruction and then returns to address 0x00.")
    w("")

    # ---------------------------------------------------------------- 4. ALU
    w("## 4. Which algorithms does the ALU use?")
    w("")
    w("| unit | algorithm | how much work for a 32-bit number | used for |")
    w("|---|---|---|---|")
    w("| adder | ripple-carry | 32 full adders one after another (about 64 gate delays) | add, sub, cmp, addresses |")
    w("| adder | Kogge-Stone (parallel prefix) | 7 levels in total (about 14 gate delays), 129 small cells | add, sub, cmp, addresses |")
    w("| multiplier | Booth radix-4 (default) | 16 partial products, each 0, +-1x or +-2x the multiplicand | mul (2 cycles) |")
    w("| multiplier | shift-and-add | 32 steps, one add for every 1 bit of the multiplier | mul (2 cycles) |")
    w("| divider | non-restoring (default) | 32 add/subtract steps (+1 final correction) | div, mod (8 cycles) |")
    w("| divider | restoring | 32 subtract steps, plus an add-back every time a step goes negative | div, mod (8 cycles) |")
    w("| shifter | barrel shifter | 5 multiplexer levels | lsl, lsr, asr |")
    w("| float unit | IEEE-754 single precision, round to nearest even, tiny values flushed to zero "
      "| | fadd/fsub/fmul (3 cycles), fdiv (10), fcmp, fmin/fmax/fabs/fneg, fcvt (2) |")
    w("")
    w("**Check of the two adders.** Random 32-bit additions and subtractions were compared with exact arithmetic:")
    w("")
    w("| adder | wrong results | Python time per operation |")
    w("|---|---|---|")
    for a in ADDERS:
        wrong, us = bench[a]
        w(f"| {a} | {wrong} | {us:.1f} microseconds |")
    w("")
    w("Both adders give identical results in every program (section 1). Kogge-Stone is faster as hardware, "
      "but this simulator does not turn that into extra cycles: each operation has a fixed number of cycles. "
      "The Python times above only show how slow each *software model* is, not how fast the real circuit would be.")
    w("")
    w("**Multiplier and divider algorithms compared.** The number of add/subtract operations is what an "
      "iterative hardware unit spends its time on, so fewer is better. Measured on 4,000 random 32-bit "
      "operand pairs (the divider also on 4,000 pairs with a small divisor):")
    w("")
    w("| algorithm | average add/sub per operation | worst case seen | Python time per operation |")
    w("|---|---|---|---|")
    for a in ("booth", "shiftadd", "nonrestoring", "restoring"):
        avg, worst, us = md_bench[a]
        w(f"| {a} | {avg:.1f} | {worst} | {us:.0f} microseconds |")
    w("")
    w("Inside the test programs (6-stage, all programs together):")
    w("")
    w("| multiplier + divider | add/sub steps in multiplies | add/sub steps in divides |")
    w("|---|---|---|")
    for (m, d), st in sweep_steps.items():
        w(f"| {m} + {d} | {st.get('mul_addsub', 0)} | {st.get('div_addsub', 0)} |")
    b, sa = md_bench["booth"][0], md_bench["shiftadd"][0]
    nr, rs = md_bench["nonrestoring"][0], md_bench["restoring"][0]
    w("")
    w(f"* Booth radix-4 needs about {100 * (1 - b / sa):.0f}% fewer add/subtract steps than shift-and-add, and at "
      "most 16 (one per pair of bits) where shift-and-add can need 32. That is why it is the default.")
    pm_b = sweep_steps[("booth", "nonrestoring")].get("mul_addsub", 0)
    pm_s = sweep_steps[("shiftadd", "nonrestoring")].get("mul_addsub", 0)
    if pm_s < pm_b:
        w(f"* In the test programs shift-and-add did slightly fewer steps ({pm_s} vs {pm_b}): those programs "
          "multiply by small numbers such as 2..10, which have only a few 1 bits. Booth's advantage is on large "
          "operands and its fixed worst case of 16 steps.")
    w(f"* The non-restoring divider needs about {100 * (1 - nr / rs):.0f}% fewer steps than the restoring one: it "
      "never adds the divisor back, it just adds instead of subtracting in the next step.")
    w("* All 8 combinations (2 adders x 2 multipliers x 2 dividers) gave exactly the same results and the same "
      "cycle counts on every program, because the number of cycles per operation is fixed by the design "
      "(section 8 of the pipeline documents). The algorithm decides how much hardware work fits in those cycles.")
    w("")
    if failures:
        w("## Failures")
        w("")
        for f in failures:
            w(f"* {f}")
        w("")
    path = os.path.join(ROOT, "docs", "evaluation_report.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    return path


def main():
    print(f"checking {len(PROGRAMS)} programs x {len(MODES) * len(CONTROLS) * len(ADDERS)} configurations")
    results, failures = check_all()
    bench = alu_benchmark()
    sweep_steps, sweep_fail = alu_algorithm_sweep()
    failures += sweep_fail
    print(f"  ALU algorithm sweep: {len(PROGRAMS)} programs x {len(MULS) * len(DIVS) * len(ADDERS)} ALU combinations, "
          f"{len(sweep_fail)} failure(s)")
    path = write_report(results, failures, bench, sweep_steps, mul_div_benchmark())
    for f in failures:
        print("  FAIL", f)
    print(f"report written to {os.path.relpath(path, ROOT)}")
    print("ALL EXPECTATIONS MET" if not failures else f"{len(failures)} FAILURE(S)")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
