#!/usr/bin/env python3
"""
simulator.py - RISC201 processor simulator (CSC-201, SimpleRISC Extended ISA)

This file ties the team's modules together into one machine.
It has been modified to use vertical_microprogramming.py as the MCU decoder.

Also used: horizontal_microprogramming.py (Config.control = "horizontal"),
preprocessor.py (macros / .org, applied by load_source) and the stack protection
checks in ExecuteUnit (Config.stack_protect = "off" | "warn" | "trap").
"""
from __future__ import annotations

import os
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from typing import Deque, Dict, Iterable, List, Optional, Set, Tuple

import isa
import alu as alu_mod
import assembler
import disassembler
import vertical_microprogramming
import horizontal_microprogramming
import preprocessor

CONTROL_UNITS = {"vertical": vertical_microprogramming, "horizontal": horizontal_microprogramming}

# exception cause codes (cause register)
CAUSE_STACK_BOUNDS = 1        # push below STACK_LIMIT / pop above STACK_BASE (ISA doc)
CAUSE_STACK_PROTECT = 2       # stack memory protection violation (only when stack_protect == "trap")
CAUSE_NAMES = {0: "none", CAUSE_STACK_BOUNDS: "stack overflow/underflow",
               CAUSE_STACK_PROTECT: "stack protection violation"}

M32 = 0xFFFFFFFF
SP = 14                     # r14 = stack pointer
RA = 15                     # r15 = return address

OPCODE = {op.name: op.opcode for op in isa.OPS_BY_OPCODE.values()}
NOP_WORD = isa.pack_word(OPCODE["nop"])
HALT_WORD = isa.pack_word(OPCODE["halt"])


# =============================================================================================
# Configuration
# =============================================================================================
@dataclass
class Config:
    stack_base: int = 0x2000
    stack_limit: int = 0x1F00
    exec_vector: int = 0x400
    initial_sp: Optional[int] = None
    initial_regs: Dict[int, int] = field(default_factory=dict)
    initial_memory: Dict[int, int] = field(default_factory=dict)
    default_handler: Optional[str] = "halt"
    adder: str = "kogge"               # ALU adder: "kogge" (Kogge-Stone) | "ripple"
    mul: str = "booth"                 # ALU multiplier: "booth" (radix-4) | "shiftadd"
    div: str = "nonrestoring"          # ALU divider: "nonrestoring" | "restoring"
    control: str = "vertical"         # microprogrammed control unit: "vertical" | "horizontal"
    # Runtime stack memory protection (extension, off by default so the docs' behaviour is unchanged):
    #   ld/st into the stack region must use sp as base and stay inside [SP, STACK_BASE);
    #   an ALU write to sp must keep it inside [STACK_LIMIT, STACK_BASE].
    #   "warn" logs the violation, "trap" raises a precise exception with cause = 2.
    stack_protect: str = "off"


# =============================================================================================
# Decoder / Micro-Control Unit
# =============================================================================================
@dataclass
class MicroOp:
    pc: int
    word: int
    name: str
    text: str
    seq: int = 0
    I: int = 0
    F: int = 0
    rd: int = 0
    rs1: int = 0
    rs2: int = 0
    isFloat: bool = False
    isfcvt: bool = False
    isLd: bool = False
    isSt: bool = False
    isPush: bool = False
    isPop: bool = False
    isCall: bool = False
    isRet: bool = False
    isBeq: bool = False
    isBgt: bool = False
    isUBranch: bool = False
    isCmp: bool = False
    isRTE: bool = False
    isHalt: bool = False
    isWb: bool = False
    isWb2: bool = False
    useSrc1: bool = False
    useSrc2: bool = False
    src1Addr: int = 0
    src2Addr: int = 0
    writeAddr: int = 0
    imm: int = 0
    branchTarget: int = 0
    op1: int = 0
    op2: int = 0
    ex: Optional["ExResult"] = None
    ex_cycle: int = 0
    ldResult: int = 0
    uPC: int = 0                        # Micro-Program Counter state
    opcode: int = 0                     # 5-bit opcode (ALU function when the ROM says 'opc')
    aluOp: str = "none"                 # ALU_OP field of the microinstruction
    uInstr: str = ""                    # the microinstruction itself (fields or control-word bits)


def decode(word: int, pc: int, labels: Optional[Dict[int, str]] = None,
           control: str = "vertical") -> MicroOp:
    """Micro-programmed decode stage: look up uPC, fetch from ROM, expand fields.
    Every control signal (including isFloat and "is this a branch") comes from the control
    memory; the opcode name is only used for the disassembly text."""
    word &= M32
    f = isa.unpack_word(word)
    op = isa.OPS_BY_OPCODE.get(f.opcode)
    name = op.name if op else "nop"
    text = disassembler.disassemble_word(word, pc, labels)
    u = MicroOp(pc=pc, word=word, name=name, text=text, opcode=f.opcode)
    u.I, u.rd, u.rs1, u.rs2 = f.i, f.rd, f.rs1, f.rs2

    # --- MICRO-SEQUENCER HOOK: dispatch on (opcode, I, F) and read the control memory ---
    signals = CONTROL_UNITS[control].expand_micro_fields(f.opcode, f.i, f.f, f.rd, f.rs1, f.rs2)
    for key, val in signals.items():
        setattr(u, key, val)
    u.F = int(u.isFloat)                # F as the datapath sees it (ignored for integer-only ops)

    # Imm unit & Branch Target unit (datapath), steered by the control signals
    if u.isUBranch or u.isBeq or u.isBgt or u.isCall:
        u.I = u.rd = u.rs1 = u.rs2 = 0                 # those bits belong to the 27-bit offset
        off = isa.sext(f.offset27, 27)
        u.branchTarget = (pc + off * isa.INSTR_BYTES) & M32
    elif u.I:
        if u.isLd or u.isSt:
            u.imm = isa.sext(f.imm15, 15) & M32
        elif u.isFloat:
            u.imm = (f.imm17 << 15) & M32
        elif f.mod == 0b01:
            u.imm = f.imm15
        elif f.mod == 0b10:
            u.imm = (f.imm15 << 16) & M32
        else:
            u.imm = isa.sext(f.imm15, 15) & M32

    return u


# =============================================================================================
# Execute unit: everything in EX goes through RISC201_ALU
# =============================================================================================
@dataclass(frozen=True)
class ExResult:
    result: int = 0
    flags: Optional[Tuple[int, int]] = None
    taken: bool = False
    target: int = 0
    mem_addr: Optional[int] = None
    store_data: Optional[int] = None
    exception: bool = False
    latency: int = 1
    unit: str = "-"
    exc_cause: int = 0
    exc_msg: str = ""
    warning: str = ""


class ExecuteUnit:
    def __init__(self, alu: "alu_mod.RISC201_ALU", config: Config):
        self.alu = alu
        self.cfg = config

    def _alu(self, u: MicroOp, a: int, b: int):
        """Drive the ALU with the function chosen by the microinstruction's ALU_OP field.
        'opc'/'cmp'/'cvt' send the instruction's opcode (FPU if isFloat, converter if isfcvt);
        'add'/'sub' are the fixed integer adds used for addresses, SP and the call link."""
        if u.aluOp in ("opc", "cmp", "cvt"):
            return self.alu.execute(u.opcode, a, b, is_float=u.isFloat, is_fcvt=u.isfcvt)
        return self.alu.execute(OPCODE[u.aluOp], a, b)

    def run(self, u: MicroOp, a: int, b: int, flags: Tuple[int, int]) -> ExResult:
        if u.isUBranch or u.isCall or u.isBeq or u.isBgt:
            taken = u.isUBranch or u.isCall or (u.isBeq and bool(flags[0])) or (u.isBgt and bool(flags[1]))
            link = self._alu(u, u.pc, 4)[0] if u.aluOp != "none" else 0     # call: PC + 4
            return ExResult(result=link, taken=taken, target=u.branchTarget, unit="BRANCH")
        if u.isRet:
            return ExResult(taken=True, target=a & M32, unit="BRANCH")
        if u.aluOp == "none":                                              # nop, halt, rte, reserved
            return ExResult()
        if u.isPush:
            new_sp = self._alu(u, a, 4)[0]                                 # ROM: sub -> SP - 4
            return ExResult(result=new_sp, mem_addr=new_sp, store_data=b,
                            exception=new_sp < self.cfg.stack_limit, unit="AGU",
                            exc_cause=CAUSE_STACK_BOUNDS,
                            exc_msg=f"stack overflow: push would move SP to 0x{new_sp:X} "
                                    f"(below STACK_LIMIT 0x{self.cfg.stack_limit:X})")
        if u.isPop:
            new_sp = self._alu(u, a, 4)[0]                                 # ROM: add -> SP + 4
            return ExResult(result=new_sp, mem_addr=a & M32,
                            exception=new_sp > self.cfg.stack_base, unit="AGU",
                            exc_cause=CAUSE_STACK_BOUNDS,
                            exc_msg=f"stack underflow: pop would move SP to 0x{new_sp:X} "
                                    f"(above STACK_BASE 0x{self.cfg.stack_base:X})")
        if u.isLd or u.isSt:
            addr = self._alu(u, a, u.imm)[0]                               # ROM: add -> rs1 + imm
            viol = self._mem_violation(u, a, addr)
            if viol and self.cfg.stack_protect == "trap":
                return ExResult(result=addr, mem_addr=addr, exception=True, unit="AGU",
                                exc_cause=CAUSE_STACK_PROTECT, exc_msg=viol)
            return ExResult(result=addr, mem_addr=addr, store_data=b if u.isSt else None, unit="AGU",
                            warning=viol)
        operand2 = b if (u.isfcvt or not u.I) else u.imm
        res, e, gt, latency = self._alu(u, a, operand2)
        unit = "FCVT" if u.isfcvt else "FPU" if u.isFloat else "ALU"
        viol = ""
        if self.cfg.stack_protect != "off" and u.isWb and u.writeAddr == SP \
                and not (self.cfg.stack_limit <= (res & M32) <= self.cfg.stack_base):
            viol = (f"{u.text}: SP set to 0x{res & M32:X}, outside the stack "
                    f"[0x{self.cfg.stack_limit:X}, 0x{self.cfg.stack_base:X}]")
            if self.cfg.stack_protect == "trap":
                return ExResult(result=res & M32, exception=True, latency=max(1, latency), unit=unit,
                                exc_cause=CAUSE_STACK_PROTECT, exc_msg=viol)
        return ExResult(result=res & M32, flags=(e, gt) if u.isCmp else None,
                        latency=max(1, latency), unit=unit, warning=viol)

    def _mem_violation(self, u: MicroOp, base: int, addr: int) -> str:
        """Stack memory protection rule for ld / st (empty string = access is fine)."""
        if self.cfg.stack_protect == "off":
            return ""
        lo, hi = self.cfg.stack_limit, self.cfg.stack_base
        kind = "load from" if u.isLd else "store to"
        if u.rs1 == SP:
            if addr < (base & M32):
                return f"{u.text}: {kind} 0x{addr:X} below SP=0x{base & M32:X} (unallocated stack)"
            if addr >= hi:
                return f"{u.text}: {kind} 0x{addr:X} at/above STACK_BASE 0x{hi:X}"
            return ""
        if lo <= addr < hi:
            return (f"{u.text}: {kind} stack region 0x{addr:X} through {isa.reg_name(u.rs1)} "
                    f"(stack memory may only be accessed sp-relative)")
        return ""


# =============================================================================================
# Per-cycle record
# =============================================================================================
@dataclass
class Slot:
    pc: int
    text: str
    seq: Optional[int] = None
    note: str = ""
    uPC: Optional[int] = None           # microinstruction address (None in IF: not decoded yet)


@dataclass
class CycleRecord:
    cycle: int
    stages: Dict[str, Optional[Slot]]
    events: List[str] = field(default_factory=list)


@dataclass
class RunResult:
    reason: str
    cycles: int
    retired: int
    pc: int
    breakpoint: Optional[int] = None

    def __str__(self) -> str:
        where = f" at 0x{self.breakpoint:X}" if self.breakpoint is not None else ""
        return f"stopped: {self.reason}{where} after {self.cycles} cycles, {self.retired} instructions retired"


# =============================================================================================
# Cores
# =============================================================================================
class _Core:
    STAGES: Tuple[str, ...] = ()

    def __init__(self, imem: List[int], config: Config, alu, labels: Dict[int, str],
                 trace_limit: int):
        self.cfg = config
        self.imem = imem
        self.labels = labels
        self.exu = ExecuteUnit(alu, config)
        self.regs = [0] * 16
        self.regs[SP] = config.stack_base if config.initial_sp is None else config.initial_sp & M32
        for r, v in config.initial_regs.items():
            self.regs[r] = v & M32
        self.dmem: Dict[int, int] = {a & M32: v & M32 for a, v in config.initial_memory.items()}
        self.flags: Tuple[int, int] = (0, 0)
        self.pc = 0
        self.epc = 0
        self.cause = 0
        self.halted = False
        self.cycles = 0
        self.retired: List[int] = []
        self.stats: Counter = Counter()
        self.history: Deque[CycleRecord] = deque(maxlen=max(1, trace_limit))
        self._ev: List[str] = []
        self._seq = 0

    def fetch(self, pc: int) -> int:
        i = (pc & M32) // isa.INSTR_BYTES
        return self.imem[i] if 0 <= i < len(self.imem) else NOP_WORD

    def decode(self, word: int, pc: int) -> MicroOp:
        return decode(word, pc, self.labels, self.cfg.control)

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def write_back(self, W1, W2):
        for w in (W2, W1):
            if w is not None:
                self.regs[w[0]] = w[1] & M32

    def retire(self, u: MicroOp, W1, W2):
        self.retired.append(u.pc)
        self.stats["retired"] += 1
        writes = []
        if W1 is not None:
            writes.append(f"{isa.reg_name(W1[0])}=0x{W1[1]:08X}")
        if W2 is not None and not (W1 is not None and W1[0] == SP):
            writes.append(f"sp=0x{W2[1]:08X}")
        self._ev.append(f"retire 0x{u.pc:04X} {u.text}" + (f"  [{', '.join(writes)}]" if writes else ""))

    def mem_write(self, addr: int, value: int):
        self.dmem[addr & M32] = value & M32
        self._ev.append(f"mem[0x{addr & M32:X}] <- 0x{value & M32:08X}")

    def take_exception(self, u: MicroOp, x: "ExResult"):
        self.epc, self.cause = u.pc, (x.exc_cause or CAUSE_STACK_BOUNDS)
        self.stats["exceptions"] += 1
        self._ev.append(f"EXCEPTION cause={self.cause} ({CAUSE_NAMES.get(self.cause, '?')}) at 0x{u.pc:04X}: "
                        f"{x.exc_msg}; epc=0x{u.pc:X}, PC <- 0x{self.cfg.exec_vector:X}")

    def note_warning(self, x: "ExResult"):
        if x.warning:
            self.stats["stack_warnings"] += 1
            self._ev.append(f"STACK WARNING: {x.warning}")

    def slot(self, u: Optional[MicroOp], note: str = "") -> Optional[Slot]:
        return None if u is None else Slot(u.pc, u.text, u.seq, note, u.uPC)

    def step(self) -> Optional[CycleRecord]:
        if self.halted:
            return None
        self.cycles += 1
        self._ev = []
        rec = self._cycle()
        self.history.append(rec)
        return rec

    def _cycle(self) -> CycleRecord:
        raise NotImplementedError

    def in_flight(self) -> List[MicroOp]:
        return []


class FunctionalCore(_Core):
    STAGES = ("EXEC",)

    def _cycle(self) -> CycleRecord:
        pc = self.pc
        u = self.decode(self.fetch(pc), pc)
        u.seq = self.next_seq()
        a = self.regs[u.src1Addr] if u.useSrc1 else 0
        b = self.regs[u.src2Addr] if u.useSrc2 else 0
        x = self.exu.run(u, a, b, self.flags)
        self.stats["ex_cycles"] += x.latency
        self.note_warning(x)
        note = ""
        if x.exception:
            self.take_exception(u, x)
            self.pc = self.cfg.exec_vector
            note = "fault"
        else:
            if x.flags is not None:
                self.flags = x.flags
            if u.isSt or u.isPush:
                self.mem_write(x.mem_addr, x.store_data)
            W1 = W2 = None
            if u.isWb2:
                W2 = (SP, x.result)
            if u.isWb:
                data = self.dmem.get(x.mem_addr, 0) if (u.isLd or u.isPop) else x.result
                W1 = (u.writeAddr, data & M32)
            self.write_back(W1, W2)
            self.retire(u, W1, W2)
            if u.isHalt:
                self.halted = True
            if x.taken:
                self.stats["branches_taken"] += 1
            self.pc = (x.target if x.taken else self.epc if u.isRTE else pc + 4) & M32
        return CycleRecord(self.cycles, {"EXEC": Slot(pc, u.text, u.seq, note, u.uPC)}, self._ev)


class _PipelineCore(_Core):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.halt_pending = False

    def bypass_read(self, u: MicroOp, W1, W2) -> MicroOp:
        u = replace(u)
        for k in (1, 2):
            if not getattr(u, f"useSrc{k}"):
                setattr(u, f"op{k}", 0)
                continue
            addr = getattr(u, f"src{k}Addr")
            value = self.regs[addr]
            for w in (W2, W1):
                if w is not None and w[0] == addr:
                    value = w[1]
            setattr(u, f"op{k}", value & M32)
        return u

    @staticmethod
    def rw_forward(addr: int, W1, W2):
        port2_ok = W2 is not None and not (W1 is not None and W1[0] == SP)
        if W1 is not None and W1[0] == addr:
            return "RW.W1", W1[1]
        if port2_ok and W2[0] == addr:
            return "RW.W2", W2[1]
        return None

    def forward_operands(self, u: MicroOp, W1, W2, ma: Optional[MicroOp] = None):
        vals, srcs = [], []
        for k in (1, 2):
            if not getattr(u, f"useSrc{k}"):
                vals.append(0)
                continue
            addr = getattr(u, f"src{k}Addr")
            hit = None
            if ma is not None and ma.ex is not None:
                if ma.isWb and not (ma.isLd or ma.isPop) and ma.writeAddr == addr:
                    hit = ("EX_MA", ma.ex.result)
                elif ma.isWb2 and addr == SP and not (ma.isWb and ma.writeAddr == SP):
                    hit = ("EX_MA", ma.ex.result)
            if hit is None:
                hit = self.rw_forward(addr, W1, W2)
            if hit is None:
                vals.append(getattr(u, f"op{k}"))
            else:
                vals.append(hit[1] & M32)
                srcs.append(f"{isa.reg_name(addr)} from {hit[0]}")
        if srcs:
            self._ev.append(f"forward to EX ({u.text}): " + ", ".join(srcs))
        return vals[0], vals[1]

    def start_ex(self, u: MicroOp, W1, W2, ma: Optional[MicroOp] = None) -> MicroOp:
        if u.ex is not None:
            return u
        a, b = self.forward_operands(u, W1, W2, ma)
        x = self.exu.run(u, a, b, self.flags)
        self.note_warning(x)
        if x.latency > 1:
            self._ev.append(f"{x.unit} {u.name}: {x.latency}-cycle operation, exBusy")
        return replace(u, op1=a, op2=b, ex=x)

    def front_end(self, ofu: Optional[MicroOp], W1, W2):
        rte = ofu is not None and ofu.isRTE
        if ofu is not None and ofu.isHalt and not self.halt_pending:
            self.halt_pending = True
            self._ev.append("halt decoded: fetch frozen")
        if rte:
            self._ev.append(f"rte: PC <- epc 0x{self.epc:X} (fetch killed)")
            self.stats["rte"] += 1
            return None, self.epc, Slot(self.pc, "", None, "killed")
        if self.halt_pending:
            return None, self.pc, None
        seq = self.next_seq()
        word = self.fetch(self.pc)
        latch = (word, self.pc, seq)
        return latch, self.pc + 4, Slot(self.pc, disassembler.disassemble_word(word, self.pc, self.labels), seq)


class Pipeline4(_PipelineCore):
    STAGES = ("IF", "OF", "EX_MA", "RW")

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.IF_OF: Optional[Tuple[int, int, int]] = None
        self.OF_EX: Optional[MicroOp] = None
        self.EX_RW: Optional[MicroOp] = None

    def in_flight(self):
        out = []
        if self.IF_OF:
            u = self.decode(self.IF_OF[0], self.IF_OF[1])
            u.seq = self.IF_OF[2]
            out.append(u)
        return out + [u for u in (self.OF_EX, self.EX_RW) if u]

    def _cycle(self) -> CycleRecord:
        of_u = None
        if self.IF_OF:
            of_u = self.decode(self.IF_OF[0], self.IF_OF[1])
            of_u.seq = self.IF_OF[2]
        stages = {"IF": None, "OF": self.slot(of_u), "EX_MA": self.slot(self.OF_EX),
                  "RW": self.slot(self.EX_RW)}

        W1 = W2 = None
        halting = False
        r = self.EX_RW
        if r:
            if r.isWb:
                W1 = (r.writeAddr, (r.ldResult if (r.isLd or r.isPop) else r.ex.result) & M32)
            if r.isWb2:
                W2 = (SP, r.ex.result)
            self.retire(r, W1, W2)
            halting = r.isHalt

        busy, redirect, new_EX_RW, hold, new_flags = False, None, None, None, None
        u = self.OF_EX
        if u:
            u = self.start_ex(u, W1, W2)
            x = u.ex
            if u.ex_cycle < x.latency - 1:
                busy = True
                hold = replace(u, ex_cycle=u.ex_cycle + 1)
                self.stats["stall_ex_busy"] += 1
                stages["EX_MA"].note = "busy"
            elif x.exception:
                self.take_exception(u, x)
                redirect = self.cfg.exec_vector
                stages["EX_MA"].note = "fault"
            else:
                if x.flags is not None:
                    new_flags = x.flags
                if u.isSt or u.isPush:
                    self.mem_write(x.mem_addr, x.store_data)
                ld = self.dmem.get(x.mem_addr, 0) if (u.isLd or u.isPop) else 0
                if x.taken:
                    redirect = x.target
                    self.stats["branches_taken"] += 1
                    self._ev.append(f"branch taken: {u.text} -> 0x{x.target:X} (flush IF, OF)")
                new_EX_RW = replace(u, ldResult=ld & M32)

        if busy:
            nIF_OF, nOF_EX, npc = self.IF_OF, hold, self.pc
            if stages["OF"]:
                stages["OF"].note = "stall"
            stages["IF"] = None if self.halt_pending else Slot(self.pc, "", None, "stall")
        elif redirect is not None:
            nIF_OF, nOF_EX, npc = None, None, redirect
            self.halt_pending = False
            if stages["OF"]:
                stages["OF"].note = "killed"
            stages["IF"] = Slot(self.pc, "", None, "killed")
            self.stats["flush_bubbles"] += 2
        else:
            nOF_EX = self.bypass_read(of_u, W1, W2) if of_u else None
            nIF_OF, npc, stages["IF"] = self.front_end(of_u, W1, W2)

        self.write_back(W1, W2)
        if new_flags is not None:
            self.flags = new_flags
        self.IF_OF, self.OF_EX, self.EX_RW, self.pc = nIF_OF, nOF_EX, new_EX_RW, npc & M32
        if halting:
            self.halted = True
            self._ev.append("halt reached RW: processor halted")
        return CycleRecord(self.cycles, stages, self._ev)


class Pipeline6(_PipelineCore):
    STAGES = ("IF", "ID", "OF", "EX", "MA", "RW")

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.IF_ID: Optional[Tuple[int, int, int]] = None
        self.ID_OF: Optional[MicroOp] = None
        self.OF_EX: Optional[MicroOp] = None
        self.EX_MA: Optional[MicroOp] = None
        self.MA_RW: Optional[MicroOp] = None

    def in_flight(self):
        out = []
        if self.IF_ID:
            u = self.decode(self.IF_ID[0], self.IF_ID[1])
            u.seq = self.IF_ID[2]
            out.append(u)
        return out + [u for u in (self.ID_OF, self.OF_EX, self.EX_MA, self.MA_RW) if u]

    def _cycle(self) -> CycleRecord:
        id_u = None
        if self.IF_ID:
            id_u = self.decode(self.IF_ID[0], self.IF_ID[1])
            id_u.seq = self.IF_ID[2]
        stages = {"IF": None, "ID": self.slot(id_u), "OF": self.slot(self.ID_OF),
                  "EX": self.slot(self.OF_EX), "MA": self.slot(self.EX_MA), "RW": self.slot(self.MA_RW)}

        W1 = W2 = None
        halting = False
        r = self.MA_RW
        if r:
            if r.isWb:
                W1 = (r.writeAddr, (r.ldResult if (r.isLd or r.isPop) else r.ex.result) & M32)
            if r.isWb2:
                W2 = (SP, r.ex.result)
            self.retire(r, W1, W2)
            halting = r.isHalt

        nMA_RW = None
        m = self.EX_MA
        if m:
            m = replace(m)
            if m.isLd or m.isPop:
                m.ldResult = self.dmem.get(m.ex.mem_addr, 0)
            if m.isSt or m.isPush:
                self.mem_write(m.ex.mem_addr, m.ex.store_data)
            nMA_RW = m

        busy, redirect, nEX_MA, hold, new_flags = False, None, None, None, None
        u = self.OF_EX
        if u:
            u = self.start_ex(u, W1, W2, ma=self.EX_MA)
            x = u.ex
            if u.ex_cycle < x.latency - 1:
                busy = True
                hold = replace(u, ex_cycle=u.ex_cycle + 1)
                self.stats["stall_ex_busy"] += 1
                stages["EX"].note = "busy"
            elif x.exception:
                self.take_exception(u, x)
                redirect = self.cfg.exec_vector
                stages["EX"].note = "fault"
            else:
                if x.flags is not None:
                    new_flags = x.flags
                if x.taken:
                    redirect = x.target
                    self.stats["branches_taken"] += 1
                    self._ev.append(f"branch taken: {u.text} -> 0x{x.target:X} (flush IF, ID, OF)")
                nEX_MA = u

        stall_lu = False
        d = self.ID_OF
        if d and u and not busy and u.isWb and (u.isLd or u.isPop):
            stall_lu = ((d.useSrc1 and d.src1Addr == u.writeAddr)
                        or (d.useSrc2 and d.src2Addr == u.writeAddr))

        if busy:
            nIF_ID, nID_OF, nOF_EX, npc = self.IF_ID, self.ID_OF, hold, self.pc
            for s in ("ID", "OF"):
                if stages[s]:
                    stages[s].note = "stall"
            stages["IF"] = None if self.halt_pending else Slot(self.pc, "", None, "stall")
        elif redirect is not None:
            nIF_ID = nID_OF = nOF_EX = None
            npc = redirect
            self.halt_pending = False
            for s in ("ID", "OF"):
                if stages[s]:
                    stages[s].note = "killed"
            stages["IF"] = Slot(self.pc, "", None, "killed")
            self.stats["flush_bubbles"] += 3
        elif stall_lu:
            nIF_ID, nID_OF, nOF_EX, npc = self.IF_ID, self.ID_OF, None, self.pc
            self.stats["stall_load_use"] += 1
            self._ev.append(f"load-use stall: {d.text} waits for {u.text}")
            for s in ("ID", "OF"):
                if stages[s]:
                    stages[s].note = "stall"
            stages["IF"] = None if self.halt_pending else Slot(self.pc, "", None, "stall")
        else:
            nOF_EX = self.bypass_read(self.ID_OF, W1, W2) if self.ID_OF else None
            nID_OF = id_u
            nIF_ID, npc, stages["IF"] = self.front_end(id_u, W1, W2)

        self.write_back(W1, W2)
        if new_flags is not None:
            self.flags = new_flags
        self.IF_ID, self.ID_OF, self.OF_EX, self.EX_MA, self.MA_RW = nIF_ID, nID_OF, nOF_EX, nEX_MA, nMA_RW
        self.pc = npc & M32
        if halting:
            self.halted = True
            self._ev.append("halt reached RW: processor halted")
        return CycleRecord(self.cycles, stages, self._ev)


# =============================================================================================
# Simulator facade
# =============================================================================================
MODES = {
    "functional": FunctionalCore, "f": FunctionalCore, "ref": FunctionalCore, "single": FunctionalCore,
    "4stage": Pipeline4, "4": Pipeline4, "pipe4": Pipeline4,
    "6stage": Pipeline6, "6": Pipeline6, "pipe6": Pipeline6,
}
CANONICAL_MODE = {FunctionalCore: "functional", Pipeline4: "4stage", Pipeline6: "6stage"}


class SimulatorError(Exception):
    pass


class Simulator:
    def __init__(self, mode: str = "6stage", config: Optional[Config] = None,
                 trace_limit: int = 10000):
        self.config = config or Config()
        self.alu = alu_mod.RISC201_ALU(self.config.adder, self.config.mul, self.config.div)
        self.trace_limit = trace_limit
        self.program: List[int] = []
        self.labels: Dict[int, str] = {}
        self.asm_result: Optional["assembler.AssemblyResult"] = None
        self.source_name = "<none>"
        self.pre_result = None
        self._mode_cls = self._resolve_mode(mode)
        self.core: _Core = self._new_core()

    @staticmethod
    def _resolve_mode(mode: str):
        cls = MODES.get(str(mode).lower().replace("-", "").replace("_", ""))
        if cls is None:
            raise SimulatorError(f"unknown mode '{mode}' (use functional, 4stage or 6stage)")
        return cls

    def _image(self) -> List[int]:
        image = list(self.program)
        vec = self.config.exec_vector // isa.INSTR_BYTES
        if self.config.default_handler == "halt" and len(image) <= vec:
            image += [NOP_WORD] * (vec - len(image)) + [HALT_WORD]
        return image

    def _new_core(self) -> _Core:
        return self._mode_cls(self._image(), self.config, self.alu, self.labels, self.trace_limit)

    def load_words(self, words: Iterable[int], labels: Optional[Dict[int, str]] = None,
                   name: str = "<words>") -> None:
        words = [w & M32 for w in words]
        self.program = words
        self.labels = dict(labels or {})
        self.source_name = name
        self.asm_result = None
        self.reset()

    def load_source(self, text: str, filename: str = "<input>", strict: bool = False,
                    preprocess: bool = True):
        """Preprocess (macros, PUSHM/POPM, ENTER/LEAVE, .org), assemble and load.
        Errors raise assembler.AssemblyFailed with line numbers of the ORIGINAL source."""
        if preprocess:
            pre = preprocessor.preprocess(text, filename)
            result = pre.assemble(strict=strict)
        else:
            pre = None
            result = assembler.assemble_source(text, base=0, strict=strict, filename=filename)
        self.pre_result = pre
        labels = {s.value & M32: s.name for s in result.symbols.values() if s.kind == "label"}
        self.load_words(result.words, labels, filename)
        self.asm_result = result
        return result

    def load_file(self, path: str, strict: bool = False):
        ext = os.path.splitext(path)[1].lower()
        if ext in (".asm", ".s"):
            with open(path, "r", encoding="utf-8") as fh:
                return self.load_source(fh.read(), filename=path, strict=strict)
        self.load_words(disassembler.load_words(path), name=path)
        return None

    def reset(self) -> None:
        self.core = self._new_core()

    def configure(self, **changes) -> None:
        """Change Config fields (control, adder, stack_protect, stack_base, ...) and reset."""
        for k, v in changes.items():
            if not hasattr(self.config, k):
                raise SimulatorError(f"unknown setting '{k}'")
            setattr(self.config, k, v)
        if self.config.control not in CONTROL_UNITS:
            raise SimulatorError(f"unknown control unit '{self.config.control}' (vertical, horizontal)")
        if self.config.stack_protect not in ("off", "warn", "trap"):
            raise SimulatorError("stack_protect must be off, warn or trap")
        try:
            self.alu = alu_mod.RISC201_ALU(self.config.adder, self.config.mul, self.config.div)
        except ValueError as exc:
            raise SimulatorError(str(exc)) from None
        self.reset()

    def set_mode(self, mode: str) -> None:
        self._mode_cls = self._resolve_mode(mode)
        self.reset()

    def step(self, n: int = 1) -> List[CycleRecord]:
        out = []
        for _ in range(n):
            rec = self.core.step()
            if rec is None:
                break
            out.append(rec)
        return out

    def run(self, max_cycles: int = 1_000_000, breakpoints: Optional[Set[int]] = None) -> RunResult:
        bps = {b & M32 for b in (breakpoints or ())}
        start = self.core.cycles
        while not self.core.halted and self.core.cycles - start < max_cycles:
            before = len(self.core.retired)
            self.core.step()
            if bps:
                for pc in self.core.retired[before:]:
                    if pc in bps:
                        return RunResult("breakpoint", self.core.cycles, len(self.core.retired),
                                         self.core.pc, pc)
        reason = "halted" if self.core.halted else "max_cycles"
        return RunResult(reason, self.core.cycles, len(self.core.retired), self.core.pc)

    @property
    def mode(self) -> str:
        return CANONICAL_MODE[self._mode_cls]

    @property
    def regs(self) -> List[int]:
        return self.core.regs

    @property
    def pc(self) -> int:
        return self.core.pc

    @property
    def flags(self) -> Tuple[int, int]:
        return self.core.flags

    @property
    def halted(self) -> bool:
        return self.core.halted

    @property
    def cycles(self) -> int:
        return self.core.cycles

    @property
    def memory(self) -> Dict[int, int]:
        return self.core.dmem

    @property
    def history(self) -> List[CycleRecord]:
        return list(self.core.history)

    def read_reg(self, r: int) -> int:
        return self.core.regs[r]

    def write_reg(self, r: int, value: int) -> None:
        self.core.regs[r] = value & M32

    def read_mem(self, addr: int) -> int:
        return self.core.dmem.get(addr & M32, 0)

    def write_mem(self, addr: int, value: int) -> None:
        self.core.dmem[addr & M32] = value & M32

    def state(self) -> tuple:
        c = self.core
        return (tuple(c.regs), tuple(sorted(c.dmem.items())), c.flags, c.epc, c.cause,
                tuple(c.retired), c.halted)

    def stats(self) -> Dict[str, float]:
        c = self.core
        n = len(c.retired)
        s = dict(c.stats)
        s.update(mode=self.mode, control=self.config.control, cycles=c.cycles, retired=n,
                 cpi=round(c.cycles / n, 3) if n else 0.0, epc=c.epc, cause=c.cause)
        return s

    def pipeline_view(self) -> Optional[CycleRecord]:
        return self.core.history[-1] if self.core.history else None

    def disassemble_program(self, annotate: bool = True) -> str:
        return disassembler.disassemble_words(self.program, base=0, annotate=annotate)

    def format_registers(self) -> str:
        c = self.core
        lines = [f"PC=0x{c.pc:08X}  flags: E={c.flags[0]} GT={c.flags[1]}  epc=0x{c.epc:X}  "
                 f"cause={c.cause}  halted={c.halted}  cycles={c.cycles}"]
        for r in range(len(c.regs)):
            v = c.regs[r]
            signed = v - (1 << 32) if v >> 31 else v
            fv = isa.bits32_to_float(v)
            lines.append(f"  {isa.reg_name(r):>3} = 0x{v:08X}  {signed:>12}  {fv:>14.7g}")
        return "\n".join(lines)

    def format_memory(self, start: Optional[int] = None, count: int = 32) -> str:
        items = sorted(self.core.dmem.items())
        if start is not None:
            items = [(a, v) for a, v in items if a >= start]
        items = items[:count]
        if not items:
            return "  (data memory is empty)"
        return "\n".join(f"  [0x{a:08X}] = 0x{v:08X}  {v - (1 << 32) if v >> 31 else v}" for a, v in items)

    def format_pipeline(self, rec: Optional[CycleRecord] = None) -> str:
        rec = rec or self.pipeline_view()
        if rec is None:
            return "  (no cycle executed yet)"
        out = [f"cycle {rec.cycle}"]
        for st in self.core.STAGES:
            s = rec.stages.get(st)
            if s is None:
                out.append(f"  {st:<6} -")
            else:
                text = s.text or "(fetch)"
                note = f"   <{s.note}>" if s.note else ""
                
                # uPC stored in the record when the cycle ran (works for every stage, mode and old records)
                upc_str = f" [uPC: 0x{s.uPC:02X}]" if s.uPC is not None else ""

                out.append(f"  {st:<6} 0x{s.pc:04X}  {text}{note}{upc_str}")
        for e in rec.events:
            out.append(f"  * {e}")
        return "\n".join(out)

    def format_timeline(self, first_cycle: Optional[int] = None, last_cycle: Optional[int] = None,
                        max_rows: int = 40) -> str:
        recs = [r for r in self.core.history
                if (first_cycle is None or r.cycle >= first_cycle) and (last_cycle is None or r.cycle <= last_cycle)]
        if not recs:
            return "  (no cycles recorded)"
        rows: Dict[int, Tuple[int, str]] = {}
        cells: Dict[Tuple[int, int], str] = {}
        mark = {"stall": ":s", "busy": ":b", "killed": ":x", "fault": ":!"}
        for r in recs:
            for st, s in r.stages.items():
                if s is None or s.seq is None:
                    continue
                rows.setdefault(s.seq, (s.pc, s.text))
                cells[(s.seq, r.cycle)] = st + mark.get(s.note, "")
        seqs = sorted(rows)[:max_rows]
        cyc = [r.cycle for r in recs]
        w = max(5, max((len(v) for v in cells.values()), default=2) + 1)
        head = f"{'':>6} {'instruction':<26}" + "".join(f"{c:>{w}}" for c in cyc)
        out = [head]
        for s in seqs:
            pc, text = rows[s]
            line = f"0x{pc:04X} {text[:26]:<26}" + "".join(f"{cells.get((s, c), ''):>{w}}" for c in cyc)
            out.append(line)
        if len(rows) > max_rows:
            out.append(f"  ... {len(rows) - max_rows} more instructions")
        return "\n".join(out)

    def format_stats(self) -> str:
        s = self.stats()
        keys = ["mode", "control", "cycles", "retired", "cpi", "stall_ex_busy", "stall_load_use",
                "branches_taken", "flush_bubbles", "exceptions", "stack_warnings", "rte"]
        return "\n".join(f"  {k:<16} {s.get(k, 0)}" for k in keys)


# =============================================================================================
# Demo
# =============================================================================================
DEMO = """\
; RISC201 demo: 5! with mul, a call with div, stack, floats, memory
        mov   r13, 0x100       ; data base
        mov   r1, 5            ; n
        mov   r2, 1            ; acc
loop:   mul   r2, r2, r1       ; acc *= n          (2-cycle multiplier)
        sub   r1, r1, 1
        cmp   r1, 1
        bgt   loop
        push  r2               ; save 120
        call  half             ; r3 = r2 / 2        (8-cycle divider)
        pop   r4               ; r4 = 120
        add   r5, r4, r3       ; load-use on r4 in the 6-stage pipeline
        fcvt  r6, r5           ; 180 -> 180.0
        fmov  r7, 0.25
        fmul  r8, r6, r7       ; 45.0               (3-cycle FPU)
        fcvti r9, r8           ; 45
        st    r9, 0[r13]
        ld    r10, 0[r13]
        halt
half:   div   r3, r2, 2
        ret
"""

def _demo() -> None:
    for mode in ("functional", "4stage", "6stage"):
        sim = Simulator(mode=mode)
        sim.load_source(DEMO, filename="demo.asm")
        res = sim.run()
        print(f"=== {mode}: {res}")
        print(f"    r2={sim.read_reg(2)} r3={sim.read_reg(3)} r4={sim.read_reg(4)} r5={sim.read_reg(5)} "
              f"r8={isa.bits32_to_float(sim.read_reg(8))} r9={sim.read_reg(9)} r10={sim.read_reg(10)} "
              f"mem[0x100]={sim.read_mem(0x100)}  CPI={sim.stats()['cpi']}")
    sim = Simulator(mode="6stage")
    sim.load_source(DEMO, filename="demo.asm")
    sim.run()
    print("\n6-stage timeline, first 22 cycles:")
    print(sim.format_timeline(1, 22, max_rows=14))
    print("\n6-stage statistics:")
    print(sim.format_stats())


if __name__ == "__main__":
    _demo()