#!/usr/bin/env python3
"""
stack_visualizer.py - active ASCII stack memory frame renderer for the RISC201 simulator

FrameTracker watches instructions as they retire (call opens a frame, ret closes it) so the
renderer knows where each function's frame starts. render_stack() draws the full-descending
stack from STACK_BASE down to SP:

    STACK  full-descending   base 0x2000   limit 0x1F00   SP 0x1FEC
    used 20 / 256 B  [###.........................]  7%
              +------------+-------------+
    0x2000    |============ STACK_BASE ===========|
              +==== main ====================
    0x1FFC    | 0x00000078 |         120 |
              +==== fib  (call @0x0008 -> ret 0x000C)
    0x1FF8    | 0x0000000C |          12 |  saved return address (fib)
    0x1FF4    | 0x00000009 |           9 |  <- SP
              |    free    |             |
    ...

Used by cli.py ("stack" / "frames"); also callable directly from Python.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import isa

SP = isa.REG_SP


@dataclass
class Frame:
    name: str
    call_pc: int
    entry: int
    ret_addr: int
    sp_at_call: int


class FrameTracker:
    """Call this after every simulator step (update()); it follows call / ret as they retire."""

    def __init__(self, sim):
        self.sim = sim
        self.core = sim.core
        self.frames: List[Frame] = []
        self._seen = 0

    def reset(self):
        self.core = self.sim.core
        self.frames = []
        self._seen = 0

    def update(self):
        if self.core is not self.sim.core:          # simulator was reset or mode changed
            self.reset()
        retired = self.core.retired
        for pc in retired[self._seen:]:
            f = isa.unpack_word(self.core.fetch(pc))
            op = isa.OPS_BY_OPCODE.get(f.opcode)
            name = op.name if op else "nop"
            if name == "call":
                target = (pc + isa.sext(f.offset27, 27) * 4) & isa.WORD_MASK
                label = self.sim.labels.get(target, f"f_{target:04X}")
                self.frames.append(Frame(label, pc, target, pc + 4, self.core.regs[SP]))
            elif name == "ret" and self.frames:
                self.frames.pop()
        # a frame that starts below the current SP was abandoned (exception handler or code that
        # reset sp), so it no longer exists
        sp = self.core.regs[SP]
        while self.frames and self.frames[-1].sp_at_call < sp:
            self.frames.pop()
        self._seen = len(retired)

    def backtrace(self) -> str:
        if not self.frames:
            return "  #0 main"
        out = []
        for i, fr in enumerate(reversed(self.frames)):
            out.append(f"  #{i} {fr.name:<14} entry 0x{fr.entry:04X}  called from 0x{fr.call_pc:04X}  "
                       f"returns to 0x{fr.ret_addr:04X}  SP at call 0x{fr.sp_at_call:X}")
        out.append(f"  #{len(self.frames)} main")
        return "\n".join(out)


def _signed(v: int) -> int:
    return v - (1 << 32) if v >> 31 else v


def render_stack(sim, tracker: Optional[FrameTracker] = None, max_words: int = 24, free_rows: int = 2) -> str:
    cfg = sim.config
    lo, hi = cfg.stack_limit, cfg.stack_base
    sp = sim.read_reg(SP)
    size = hi - lo
    frames = tracker.frames if tracker else []
    out = []
    in_bounds = lo <= sp <= hi
    used = hi - sp if in_bounds else 0
    pct = 100 * used / size if size else 0
    bar_w = 30
    fill = round(bar_w * used / size) if size else 0
    out.append(f"STACK  full-descending   base 0x{hi:X}   limit 0x{lo:X}   SP 0x{sp:X}")
    out.append(f"used {used} / {size} B  [{'#' * fill}{'.' * (bar_w - fill)}]  {pct:.0f}%"
               f"   frames: {len(frames)} (+main)")
    if not in_bounds:
        out.append(f"!! SP 0x{sp:X} is OUTSIDE the stack [0x{lo:X}, 0x{hi:X}] - stack is corrupt or not set up")
        return "\n".join(out)

    ret_addrs = {fr.ret_addr: fr.name for fr in frames}
    # frame i occupies [next frame's sp_at_call (or SP), frames[i].sp_at_call); main starts at the base
    starts = []   # (address of first word below boundary, title)
    starts.append((hi, "main"))
    for fr in frames:
        starts.append((fr.sp_at_call,
                       f"{fr.name}  (call @0x{fr.call_pc:04X} -> returns to 0x{fr.ret_addr:04X})"))

    border = "          +------------+-------------+"
    out.append(border)
    out.append(f"0x{hi:04X}    |=========== STACK_BASE ===========|")

    addrs = list(range(hi - 4, sp - 4, -4))                 # live words, top (base) to SP
    if len(addrs) > max_words:
        keep_top = max_words // 3
        shown = addrs[:keep_top] + [None] + addrs[-(max_words - keep_top):]
    else:
        shown = addrs
    pending = list(starts)
    for a in shown:
        if a is None:
            out.append(f"   ...    |    ...     |     ...     |  ({len(addrs) - max_words} more words)")
            continue
        while pending and pending[0][0] >= a + 4:
            title = pending.pop(0)[1]
            out.append(f"          +==== {title}")
        v = sim.read_mem(a)
        notes = []
        if v in ret_addrs:
            notes.append(f"saved return address? ({ret_addrs[v]})")
        if a == sp:
            notes.append("<- SP")
        out.append(f"0x{a:04X}    | 0x{v:08X} | {_signed(v):>11} |  " + "  ".join(notes))
    # frames opened at the current SP (callee has not pushed anything yet)
    for start, title in pending:
        out.append(f"          +==== {title}  (nothing pushed yet)")
    if sp == hi:
        out.append("          |  (stack empty)          |  <- SP = STACK_BASE")
    for i in range(1, free_rows + 1):
        a = sp - 4 * i
        if a < lo:
            break
        out.append(f"0x{a:04X}    |    free    |             |")
    if sp - lo > 4 * free_rows:
        out.append(f"          |   ... {sp - lo - 4 * free_rows} B free ...")
    out.append(f"0x{lo:04X}    |=========== STACK_LIMIT ==========|")
    out.append(border)
    return "\n".join(out)
