#!/usr/bin/env python3
"""
stack_safety.py - static stack boundary verification for RISC201 programs

Before the program runs, every function (entry at 0, every call target, and the exception
handler) is walked along all of its control-flow paths while tracking the stack depth
(bytes pushed since the function was entered). The checker reports:

  error    pop / add sp below the function's entry depth (would corrupt the caller's frame,
           or underflow the stack in main)
  error    ret with bytes still pushed (unbalanced push/pop -> wrong return / leaked stack)
  error    two paths reach the same instruction with different depths (unbalanced branches,
           or a loop that keeps pushing)
  warning  worst-case depth through the call graph exceeds the stack size (STACK_BASE-STACK_LIMIT)
  warning  recursion (depth cannot be bounded statically; the runtime push/pop check guards it)
  note     sp written in a way the checker cannot follow (mov sp / ld sp / pop sp / add sp, reg)

The runtime half of stack safety is in simulator.py: push/pop bounds exceptions (cause 1) and
optional stack memory protection (cause 2).

Library use:
    from stack_safety import check_stack
    report = check_stack(words, labels, stack_size=256, exec_vector=0x400)
    print(report.format())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import disassembler
import isa

SP, RA = isa.REG_SP, isa.REG_RA
UNKNOWN = None


@dataclass
class Finding:
    level: str          # "error" | "warning" | "note"
    pc: int
    message: str


@dataclass
class FunctionInfo:
    entry: int
    name: str
    max_depth: int = 0
    calls: List[Tuple[int, int, int]] = field(default_factory=list)   # (call pc, target, depth at call)
    dynamic_sp: bool = False


@dataclass
class StackReport:
    findings: List[Finding]
    functions: Dict[int, FunctionInfo]
    worst_case: Optional[int]          # bytes, None if unbounded (recursion)
    stack_size: int

    @property
    def errors(self):
        return [f for f in self.findings if f.level == "error"]

    def format(self, labels: Optional[Dict[int, str]] = None) -> str:
        out = ["stack safety check"]
        for fn in sorted(self.functions.values(), key=lambda f: f.entry):
            out.append(f"  function {fn.name:<16} entry 0x{fn.entry:04X}  own max depth {fn.max_depth:>4} B"
                       + ("  (sp changed dynamically)" if fn.dynamic_sp else ""))
        wc = "unbounded (recursion)" if self.worst_case is None else f"{self.worst_case} B"
        out.append(f"  worst-case stack use from main: {wc} of {self.stack_size} B available")
        if not self.findings:
            out.append("  OK: no stack problems found")
        for f in sorted(self.findings, key=lambda f: (f.pc, f.level)):
            where = f"0x{f.pc:04X}"
            if labels and f.pc in labels:
                where += f" <{labels[f.pc]}>"
            out.append(f"  {f.level.upper():<7} {where}: {f.message}")
        return "\n".join(out)


def _decode(word: int):
    f = isa.unpack_word(word)
    op = isa.OPS_BY_OPCODE.get(f.opcode)
    return (op.name if op else "nop"), f


def check_stack(words: List[int], labels: Optional[Dict[int, str]] = None, stack_size: int = 256,
                exec_vector: Optional[int] = 0x400) -> StackReport:
    labels = labels or {}
    n = len(words)
    end = n * isa.INSTR_BYTES
    findings: List[Finding] = []
    seen_msgs: Set[Tuple[int, str]] = set()

    def add(level, pc, msg):
        if (pc, msg) not in seen_msgs:
            seen_msgs.add((pc, msg))
            findings.append(Finding(level, pc, msg))

    def name_of(addr):
        return labels.get(addr, "main" if addr == 0 else "handler" if addr == exec_vector else f"f_{addr:04X}")

    entries = [0]
    if exec_vector is not None and exec_vector < end:
        entries.append(exec_vector)
    functions: Dict[int, FunctionInfo] = {}
    queue = list(entries)

    while queue:
        entry = queue.pop()
        if entry in functions or not (0 <= entry < end):
            continue
        fn = FunctionInfo(entry, name_of(entry))
        functions[entry] = fn
        depth_at: Dict[int, Optional[int]] = {}
        work = [(entry, 0)]
        while work:
            pc, d = work.pop()
            if not (0 <= pc < end) or pc % 4:
                continue
            if d is not UNKNOWN:
                fn.max_depth = max(fn.max_depth, d)
            if pc in depth_at:
                old = depth_at[pc]
                if old is not UNKNOWN and d is not UNKNOWN and old != d:
                    add("error", pc, f"reached with {old} B and with {d} B on the stack "
                                     f"(push/pop unbalanced between paths or inside a loop)")
                continue
            depth_at[pc] = d
            name, f = _decode(words[pc // 4])
            nxt = [pc + 4]
            nd = d
            text = disassembler.disassemble_word(words[pc // 4], pc, labels)

            if name in ("b", "beq", "bgt", "call"):
                target = (pc + isa.sext(f.offset27, 27) * 4) & isa.WORD_MASK
                if name == "b":
                    nxt = [target]
                elif name in ("beq", "bgt"):
                    nxt = [pc + 4, target]
                else:
                    fn.calls.append((pc, target, d if d is not UNKNOWN else 0))
                    queue.append(target)
            elif name == "ret":
                if d not in (UNKNOWN, 0):
                    add("error", pc, f"ret with {d} B still pushed (unbalanced push/pop in {fn.name})")
                if entry == 0:
                    add("warning", pc, "ret in main (main was not called)")
                nxt = []
            elif name in ("halt", "rte"):
                nxt = []
            elif name == "push":
                nd = None if d is UNKNOWN else d + 4
            elif name == "pop":
                if f.rd == SP:
                    add("note", pc, f"{text}: sp loaded from memory, depth no longer tracked")
                    fn.dynamic_sp = True
                    nd = UNKNOWN
                elif d is not UNKNOWN:
                    nd = d - 4
                    if nd < 0:
                        what = "stack underflow (pop on an empty stack)" if entry == 0 else \
                            f"pops {-nd} B of the caller's frame"
                        add("error", pc, f"{text}: {what}")
            else:
                op = isa.OPS_BY_OPCODE.get(f.opcode)
                writes_sp = op is not None and op.kind in (isa.K_ALU3, isa.K_MOV2, isa.K_UN2, isa.K_LDST) \
                    and name != "st" and f.rd == SP
                if writes_sp:
                    if name in ("add", "sub") and f.i == 1 and f.f == 0 and f.rs1 == SP and f.mod == 0:
                        imm = isa.sext(f.imm15, 15)
                        if d is not UNKNOWN:
                            nd = d - imm if name == "add" else d + imm
                            if nd < 0:
                                add("error", pc, f"{text}: releases {-nd} B more than this function reserved")
                    else:
                        if entry != 0 or pc != 0:
                            add("note", pc, f"{text}: sp set dynamically, depth no longer tracked")
                        fn.dynamic_sp = True
                        nd = UNKNOWN
            for t in nxt:
                work.append((t, nd))

    # ---- worst case through the call graph
    stack_size = max(0, stack_size)
    memo: Dict[int, Optional[int]] = {}
    recursive: Set[int] = set()

    def worst(e, path):
        if e in path:
            recursive.add(e)
            return None
        if e in memo:
            return memo[e]
        fn = functions.get(e)
        if fn is None:
            return 0
        best = fn.max_depth
        for (cpc, tgt, d) in fn.calls:
            w = worst(tgt, path | {e})
            if w is None:
                memo[e] = None
                return None
            best = max(best, d + w)
        memo[e] = best
        return best

    wc = worst(0, frozenset())
    for e in sorted(recursive):
        add("warning", e, f"function {name_of(e)} is recursive: stack depth cannot be bounded statically "
                          f"(the runtime push/pop check raises an exception if it overflows)")
    if wc is not None and wc > stack_size:
        add("warning", 0, f"worst-case stack use {wc} B exceeds the stack size {stack_size} B")
    return StackReport(findings, functions, wc, stack_size)
