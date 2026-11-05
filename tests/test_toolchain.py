#!/usr/bin/env python3
"""
test_toolchain.py - tests for the software suite around the simulator

  1. assembler <-> disassembler round trip (random words)
  2. preprocessor: stack macros, user macros, \\@ labels, .org, error line mapping
  3. static stack safety checker
  4. runtime stack protection (off / warn / trap) in every processor model
  5. microcode ROMs: validation, widths, illegal microinstructions raise MicrocodeError
  6. ASCII stack visualizer and frame tracker
  7. CLI debugger commands (batch, output captured)
  8. every program in programs/ meets its EXPECT lines in all configurations

Run:  python3 tests/test_toolchain.py
"""
import io
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import assembler                                   # noqa: E402
import disassembler                                # noqa: E402
import horizontal_microprogramming as HMP          # noqa: E402
import preprocessor                                # noqa: E402
import vertical_microprogramming as VMP            # noqa: E402
from cli import RiscShell                          # noqa: E402
from simulator import Config, Simulator            # noqa: E402
from stack_safety import check_stack               # noqa: E402
from stack_visualizer import FrameTracker, render_stack   # noqa: E402

FAILS = []
COUNT = [0]


def check(cond, msg):
    COUNT[0] += 1
    if not cond:
        FAILS.append(msg)


def expect_error(fn, text, msg):
    try:
        fn()
    except Exception as exc:                       # noqa: BLE001
        check(text in str(exc), f"{msg}: wrong error: {exc}")
        return
    check(False, f"{msg}: no error raised")


def assemble(src):
    return preprocessor.preprocess(src).assemble()


def labels_of(res):
    return {s.value: s.name for s in res.symbols.values() if s.kind == "label"}


# ---------------------------------------------------------------------------------------------
def t_roundtrip():
    rnd = random.Random(1)
    words = [rnd.getrandbits(32) for _ in range(20000)]
    bad = [w for w in words if assembler.assemble_source(disassembler.disassemble_word(w)).words != [w]]
    check(not bad, f"round trip failed for {[hex(w) for w in bad[:3]]}")


def t_preprocessor():
    pre = preprocessor.preprocess("PUSHM {r1, r4-r5}\nPOPM {r1, r4-r5}\nENTER 8\nLEAVE 8\nALLOC 4\nFREE 4\n")
    lines = [l.text.strip() for l in pre.lines]
    check(lines == ["push r5", "push r4", "push r1", "pop r1", "pop r4", "pop r5",
                    "push ra", "sub sp, sp, 8", "add sp, sp, 8", "pop ra", "sub sp, sp, 4", "add sp, sp, 4"],
          f"stack macro expansion wrong: {lines}")
    src = ".macro L r\nx\\@: sub \\r, \\r, 1\n bgt x\\@\n.endm\n L r1\n L r2\n halt\n"
    res = assemble(src)
    names = sorted(labels_of(res).values())
    check(names == ["x_1", "x_2"], f"unique macro labels wrong: {names}")
    res = assemble(" nop\n.org 0x20\nh: halt\n")
    check(res.symbols["h"].value == 0x20 and len(res.words) == 9, ".org padding wrong")
    expect_error(lambda: preprocessor.preprocess(" nop\n nop\n.org 4\n"), "behind", ".org backwards")
    expect_error(lambda: preprocessor.preprocess(" PUSHM {r1, sp}\n"), "sp cannot", "PUSHM with sp")
    expect_error(lambda: preprocessor.preprocess(".macro M\n nop\n"), "no matching .endm", "missing .endm")
    expect_error(lambda: preprocessor.preprocess(".macro M a\n nop\n.endm\n M\n"), "missing argument", "missing arg")
    expect_error(lambda: preprocessor.preprocess(".macro M\n M\n.endm\n M\n"), "deeper than", "recursive macro")
    try:
        assemble("; c\n.macro BAD\n add r1, r2, r99\n.endm\n nop\n BAD\n")
        check(False, "bad macro body accepted")
    except assembler.AssemblyFailed as exc:
        d = exc.diagnostics[0]
        check(d.line == 6 and "macro bad" in d.message, f"error not mapped to call line: {d.line} {d.message}")
    plain = "start: mov r1, 3\n add r2, r1, r1 ; c\n halt\n"
    check(preprocessor.preprocess(plain).text == plain, "plain source changed by preprocessor")


def t_stack_checker():
    def run(src):
        res = assemble(src)
        return check_stack(res.words, labels_of(res), 256, 0x400)
    r = run(" call f\n halt\nf: ENTER 8\n PUSHM {r1-r3}\n POPM {r1-r3}\n LEAVE 8\n ret\n")
    check(not r.findings and r.worst_case == 24, f"balanced program flagged: {r.format()}")
    r = run(" call f\n halt\nf: push r1\n cmp r1, 0\n beq s\n push r2\ns: pop r1\n ret\n")
    check(any("unbalanced" in f.message for f in r.errors), "branch imbalance not found")
    r = run(" call f\n halt\nf: push r1\n ret\n")
    check(any("still pushed" in f.message for f in r.errors), "ret with pushed data not found")
    r = run(" pop r1\n halt\n")
    check(any("underflow" in f.message for f in r.errors), "main underflow not found")
    r = run(" call f\n halt\nf: push ra\n call f\n pop ra\n ret\n")
    check(r.worst_case is None and any("recursive" in f.message for f in r.findings), "recursion not found")
    r = run(" call f\n halt\nf: ALLOC 300\n FREE 300\n ret\n")
    check(any("exceeds the stack size" in f.message for f in r.findings), "static overflow not found")
    r = run("l: push r1\n b l\n")
    check(any("loop" in f.message for f in r.errors), "push inside loop not found")


def t_protection():
    bad = " mov r13, 0x1FF0\n mov r1, 7\n push r1\n st r1, -4[sp]\n st r1, 8[r13]\n ld r2, 0[sp]\n halt\n"
    for mode in ("functional", "4stage", "6stage"):
        states = {}
        for prot in ("off", "warn", "trap"):
            s = Simulator(mode, Config(stack_protect=prot))
            s.load_source(bad)
            s.run()
            states[prot] = s
        off, warn, trap = states["off"], states["warn"], states["trap"]
        check(off.core.cause == 0 and off.read_mem(0x1FF8) == 7, f"{mode}: protect off changed behaviour")
        check(warn.core.cause == 0 and warn.stats().get("stack_warnings") == 2,
              f"{mode}: warn should report 2 violations, got {warn.stats().get('stack_warnings')}")
        check(warn.state() == off.state(), f"{mode}: warn must not change the result")
        check(trap.core.cause == 2 and trap.core.epc == 0x0C and trap.read_mem(0x1FF8) == 0,
              f"{mode}: trap should stop at 0x0C with cause 2 and no write")
        check(trap.read_reg(2) == 0, f"{mode}: instructions after the fault must not execute")
    s = Simulator("6stage", Config(stack_protect="trap"))
    s.load_source(" mov sp, 0x100\n halt\n")
    s.run()
    check(s.core.cause == 2 and s.read_reg(14) == 0x2000, "SP moved outside the stack must trap")
    s = Simulator("6stage", Config(stack_protect="trap"))
    s.load_source(" mov r1, 4\n push r1\n ld r2, 0[sp]\n st r2, 0[sp]\n pop r3\n halt\n")
    s.run()
    check(s.core.cause == 0 and s.read_reg(3) == 4, "legal sp-relative access trapped")


def t_microcode():
    check(VMP.WIDTH == 16 and HMP.WIDTH == 25, "microinstruction widths changed")
    for (op, I, F), a in VMP.DISPATCH_TABLE.items():                  # F=1 on an integer-only op
        name = VMP.OPCODE_MAP[op]                                    # must reach an integer word
        if F and name in ("mod", "and", "or", "lsl", "lsr", "asr", "not"):
            check(not VMP.expand_micro_fields(op, I, F, 1, 2, 3)["isFloat"], f"{name} with F=1 used the FPU")
    check(set(VMP.VERTICAL_ROM) == set(HMP.CONTROL_MEMORY), "control memories use different addresses")
    expect_error(lambda: VMP.validate(("opc", "none", "jump", "wb1", "r_r")), "illegal value", "vertical bad field")
    expect_error(lambda: HMP.validate(HMP.word("ALU_ADD", "ALU_SUB")), "more than one ALU", "horizontal 2 ALU ops")
    expect_error(lambda: HMP.validate(HMP.word("SRC1_SP")), "USE_SRC1", "horizontal src1 without use")
    saved = VMP.VERTICAL_ROM[0x10]
    try:
        VMP.VERTICAL_ROM[0x10] = ("opc", "none", "none", "wb9", "r_r")
        expect_error(lambda: VMP.expand_micro_fields(0, 0, 0, 1, 2, 3), "illegal value", "corrupt ROM at run time")
    finally:
        VMP.VERTICAL_ROM[0x10] = saved
    for op in range(32):
        for I in (0, 1):
            for F in (0, 1):
                a = VMP.expand_micro_fields(op, I, F, 1, 2, 3)
                b = HMP.expand_micro_fields(op, I, F, 1, 2, 3)
                a.pop("uInstr"), b.pop("uInstr")
                check(a == b, f"vertical and horizontal disagree for opcode {op} I={I} F={F}")


def t_visualizer():
    path = os.path.join(ROOT, "programs", "02_fibonacci_recursive.asm")
    s = Simulator("6stage")
    s.load_file(path)
    t = FrameTracker(s)
    for _ in range(80):
        s.step()
        t.update()
    pic = render_stack(s, t)
    check("<- SP" in pic and "fib" in pic and "STACK_LIMIT" in pic, "stack picture incomplete")
    check(len(t.frames) >= 2 and "#0 fib" in t.backtrace(), "frame tracker lost the calls")
    s.run()
    t.update()
    check(not t.frames and "stack empty" in render_stack(s, t), "frames not closed at the end")


def t_cli():
    progs = sorted(os.listdir(os.path.join(ROOT, "programs")))
    for p in progs:
        out = io.StringIO()
        sh = RiscShell(Simulator("6stage"), stdout=out)
        path = os.path.join(ROOT, "programs", p)
        for c in [f"load {path}", "step", "step 5", "stepi 3", "pipe", "ucode", "ucode 0", "regs",
                  "break 0x8", "watch r1", "run", "breaks", "delete all", "unwatch all", "next", "run",
                  "stack", "frames", "mem", "timeline 1 10", "stats", "exc", "check", "list 0 4",
                  "control horizontal", "ucode 0", "rom", "mode 4stage", "adder ripple", "protect warn",
                  "run", "compare", "reset", "source", "symbols",
                  "print-regs", "print-stack 4", "print-mem 0x0 4", "print-pipeline", "breakpoints",
                  "break 0x4", "delete 0x4", "break 0x4", "clear-breaks", "disasm", "cu horizontal",
                  "upc 0x11", "micro-stats", "micro on", "step", "micro off", "cu vertical", "help",
                  "help print-regs"]:
            sh.onecmd(c)
        text = out.getvalue()
        check("error:" not in text and "Traceback" not in text, f"CLI error on {p}:\n" +
              "\n".join(l for l in text.splitlines() if "error" in l)[:400])
        check("halted" in text, f"CLI never reached halt on {p}")
    # the extra debugger commands (print-*, micro, upc, micro-stats, clear-breaks) give sensible output
    out = io.StringIO()
    sh = RiscShell(Simulator("6stage"), stdout=out)
    path = os.path.join(ROOT, "programs", "01_factorial.asm")
    for c in [f"load {path}", "micro on", "step 2", "run", "micro-stats", "upc 0x11", "break loop",
              "breakpoints", "clear-breaks", "breakpoints", "print_regs"]:
        sh.onecmd(c)
    text = out.getvalue()
    for want in ("micro trace on", "microinstructions (vertical", "uPC 0x11", "horizontal  (25 bits)",
                 "all breakpoints cleared", "no breakpoints or watchpoints", "r2 = 0x00375F00"):
        check(want in text, f"CLI extra commands: missing '{want}' in\n{text[-600:]}")
    check("error:" not in text, "CLI extra commands raised an error")
    out = io.StringIO()
    sh = RiscShell(Simulator(), stdout=out)
    for c in ["run", "load nothing.asm", "break nowhere", "reg r99", "mode 5stage", "blah"]:
        sh.onecmd(c)
    text = out.getvalue()
    check(text.count("error:") == 5 and "unknown command" in text, f"CLI error handling: {text}")


def t_fixes():
    import subprocess
    import tempfile
    # standalone assembler expands macros / .org and matches the simulator's image
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("02_fibonacci_recursive.asm", "08_stack_overflow.asm", "17_exception_suite.asm"):
            src = os.path.join(ROOT, "programs", name)
            out = os.path.join(tmp, "p.hex")
            r = subprocess.run([sys.executable, os.path.join(ROOT, "src", "assembler.py"), src, "-o", out, "-q"],
                               capture_output=True, text=True)
            check(r.returncode == 0, f"assembler.py failed on {name}: {r.stderr[:200]}")
            if r.returncode == 0:
                s = Simulator()
                s.load_file(src)
                check(disassembler.load_words(out) == s.program, f"assembler.py hex differs from simulator for {name}")
    # ; CONFIG lines: applied on load, undone on the next load, explicit settings win
    out = io.StringIO()
    sh = RiscShell(Simulator(), stdout=out)
    p17 = os.path.join(ROOT, "programs", "17_exception_suite.asm")
    sh.onecmd(f"load {p17}")
    check(sh.sim.config.stack_protect == "trap", "; CONFIG stack_protect=trap not applied")
    sh.onecmd("run")
    check(sh.sim.read_mem(0x240) == 1 and sh.sim.read_mem(0x244) == 4, "exception suite wrong with CONFIG trap")
    sh.onecmd(f"load {os.path.join(ROOT, 'programs', '04_gcd.asm')}")
    check(sh.sim.config.stack_protect == "off", "CONFIG setting not undone for the next program")
    sh.onecmd("protect off")
    sh.onecmd(f"load {p17}")
    check(sh.sim.config.stack_protect == "off" and "keeping your setting" in out.getvalue(),
          "explicit protect off did not win over ; CONFIG")
    # a corrupt microinstruction is reported, the CLI keeps running
    out = io.StringIO()
    sh = RiscShell(Simulator(), stdout=out)
    sh.onecmd(f"load {os.path.join(ROOT, 'programs', '04_gcd.asm')}")
    saved = VMP.VERTICAL_ROM[0x13]
    try:
        VMP.VERTICAL_ROM[0x13] = ("opc", "none", "none", "wbX", "n_i")
        sh.onecmd("run")
    finally:
        VMP.VERTICAL_ROM[0x13] = saved
    sh.onecmd("regs")
    text = out.getvalue()
    check("microcode exception" in text and "PC=" in text, "CLI did not survive a microcode exception")
    # alu command and algorithm choice
    out = io.StringIO()
    sh = RiscShell(Simulator(), stdout=out)
    sh.onecmd(f"load {os.path.join(ROOT, 'programs', '04_gcd.asm')}")
    for c in ("alu mul shiftadd", "alu div restoring", "alu adder ripple", "run", "alu", "alu div slow"):
        sh.onecmd(c)
    text = out.getvalue()
    check("adder ripple, multiplier shiftadd, divider restoring" in text and sh.sim.read_reg(10) == 21,
          f"alu command: {text[-300:]}")
    check("div_addsub" in text and text.count("error:") == 1, "alu steps / bad algorithm handling")
    # F = 1 on an integer-only instruction must not use the FPU (dispatch, not hardwired logic)
    import isa
    word = isa.pack_word(isa.OPS_BY_NAME["and"].opcode, 0, 1, 1, 2, 3 << 13)
    from simulator import decode
    for control in ("vertical", "horizontal"):
        u = decode(word, 0, control=control)
        check(not u.isFloat and u.aluOp == "opc", f"{control}: and with F=1 used the FPU")
        u = decode(isa.pack_word(isa.OPS_BY_NAME["add"].opcode, 0, 1, 1, 2, 3 << 13), 0, control=control)
        check(u.isFloat and u.uPC == 0x18, f"{control}: fadd did not dispatch to the FPU microinstruction")


def t_programs():
    import evaluate
    _, failures = evaluate.check_all(verbose=False)
    check(not failures, "program expectations failed: " + "; ".join(failures[:5]))


def main():
    for name, fn in [("assembler/disassembler round trip", t_roundtrip), ("preprocessor", t_preprocessor),
                     ("static stack checker", t_stack_checker), ("runtime stack protection", t_protection),
                     ("microcode ROMs", t_microcode), ("stack visualizer", t_visualizer),
                     ("CLI debugger", t_cli), ("assembler, CONFIG, microcode errors, ALU choice", t_fixes),
                     ("programs/*.asm expectations", t_programs)]:
        before = len(FAILS)
        fn()
        print(f"  {'ok  ' if len(FAILS) == before else 'FAIL'}  {name}")
    for f in FAILS:
        print("   -", f)
    print(f"{COUNT[0]} checks, {len(FAILS)} failure(s)")
    print("TOOLCHAIN CHECKS PASSED" if not FAILS else "TOOLCHAIN CHECKS FAILED")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
