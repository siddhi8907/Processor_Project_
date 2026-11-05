#!/usr/bin/env python3
"""
cli.py - interactive command-line simulator and debugger for RISC201

    python src/cli.py                               # start empty, then: load programs/fibonacci.asm
    python src/cli.py programs/fibonacci.asm -m 6stage -c horizontal
    python src/cli.py programs/gcd.asm -x "run; regs; stats"          # batch mode (CI / reports)
    python src/cli.py programs/gcd.asm -b script.txt                  # commands from a file

Type "help" inside the CLI for all commands, "help <command>" for one.

Program        load <file>  reload  source  list [addr] [n]  disasm  symbols  check
Machine        mode [functional|4stage|6stage]  control|cu [vertical|horizontal]
               adder [kogge|ripple]  alu [adder|mul|div] [algorithm]  protect [off|warn|trap]  reset
Execution      step [n] (cycles)  stepi [n] (instructions)  next  finish
               run [max_cycles]  continue  until <addr|label>
Breakpoints    break <addr|label>  delete [n|all|addr|label]  breaks|breakpoints  clear-breaks
Watchpoints    watch <reg> | watch mem <addr>  unwatch [n|all]
State          regs  reg <r> [value]  mem [addr] [count]  setmem <addr> <value>  exc  stats
               print-regs  print-stack [words]  print-mem <addr> [words]  print-pipeline
Pipeline       pipe  timeline [first] [last]  trace [on|off]  micro [on|off]
Microcode      ucode [addr|label]  rom  upc <addr>  micro-stats
Stack          stack  frames
Evaluation     compare

Commands with a hyphen can also be typed with an underscore (print_regs).
"""
from __future__ import annotations

import argparse
import cmd
import io
import os
import shlex
import sys
from typing import Dict, List, Optional

import assembler
import disassembler
import horizontal_microprogramming as HMP
import isa
import vertical_microprogramming as VMP
import preprocessor
from simulator import CAUSE_NAMES, Config, Simulator, SimulatorError
from stack_safety import check_stack
from stack_visualizer import FrameTracker, render_stack

M32 = 0xFFFFFFFF
DEFAULT_MAX_CYCLES = 1_000_000
IMM_UPCS = (0x11, 0x13, 0x15, 0x17, 0x19, 0x1B, 0x31, 0x3B)     # microinstructions for I = 1 forms
FLOAT_UPCS = (0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B, 0x3A, 0x3B)   # microinstructions for F = 1 forms


class RiscShell(cmd.Cmd):
    intro = ("RISC201 simulator / debugger.  Type 'help' for commands, 'load <file.asm>' to start.\n")
    prompt = "(risc201) "

    def __init__(self, sim: Optional[Simulator] = None, stdout=None):
        super().__init__(stdout=stdout)
        self.sim = sim or Simulator()
        self.tracker = FrameTracker(self.sim)
        self.path: Optional[str] = None
        self.breakpoints: List[int] = []
        self.watches: List[Dict] = []
        self.trace = False
        self.micro = False                            # 'micro on': show each stage's microinstruction
        self.user_settings: Dict[str, object] = {}    # settings chosen by the user (flags / commands)
        self.program_keys = set()                     # settings that came from a program's ; CONFIG
        self.batch = False

    # ------------------------------------------------------------------ helpers
    def p(self, *args):
        print(*args, file=self.stdout)

    def emptyline(self):
        return False                                  # do not repeat the last command

    def default(self, line):
        self.p(f"unknown command '{line.split()[0]}' (type 'help')")

    # commands that are typed with a hyphen (a Python method name cannot contain one)
    HYPHEN_COMMANDS = ("clear_breaks", "print_regs", "print_stack", "print_mem",
                       "print_pipeline", "micro_stats")

    def _unhyphen(self, word: str) -> str:
        """'print-regs' -> 'print_regs' when that is a real command; anything else is left alone."""
        fixed = word.replace("-", "_")
        return fixed if fixed != word and hasattr(self, "do_" + fixed) else word

    def onecmd(self, line):
        word, sep, rest = line.strip().partition(" ")
        line = self._unhyphen(word) + sep + rest
        try:
            return super().onecmd(line)
        except (SimulatorError, ValueError, IndexError) as exc:
            self.p(f"error: {exc}")
        except assembler.AssemblyFailed as exc:
            self.p(str(exc))
            self.p(f"assembly failed: {len(exc.errors)} error(s)")
        except (VMP.MicrocodeError, HMP.MicrocodeError) as exc:
            self.p(f"microcode exception in cycle {self.sim.cycles}: {exc}")
            self.p("  the control memory holds an illegal microinstruction; execution stopped "
                   f"(PC = {self.where(self.sim.pc)})")
        except KeyboardInterrupt:
            self.p("\ninterrupted")
        except Exception as exc:                      # keep the debugger alive whatever happens
            self.p(f"internal error: {type(exc).__name__}: {exc}")
        return False

    def set_user(self, **settings):
        """A setting the user chose (command-line flag or command): it wins over ; CONFIG lines."""
        self.user_settings.update(settings)
        self.sim.configure(**settings)
        self._reset_debug_state()

    def _apply_program_config(self, text: str):
        wanted = preprocessor.read_config(text)
        defaults = Config()
        changes = {}
        for k in self.program_keys - set(wanted):       # undo the previous program's settings
            if k not in self.user_settings:
                changes[k] = getattr(defaults, k)
        for k, v in wanted.items():
            if not hasattr(defaults, k):
                raise ValueError(f"; CONFIG {k}: unknown setting")
            if k in self.user_settings and self.user_settings[k] != v:
                self.p(f"note: the program asks for {k}={v}; keeping your setting {k}={self.user_settings[k]}")
            elif k not in self.user_settings:
                changes[k] = v
        self.program_keys = set(wanted)
        if changes:
            self.sim.configure(**changes)
            shown = {k: v for k, v in changes.items() if k in wanted}
            if shown:
                self.p("program settings from ; CONFIG: " + ", ".join(f"{k}={v}" for k, v in shown.items()))

    def _micro_lines(self, rec) -> List[str]:
        """One line per pipeline stage: the microPC and microinstruction used in cycle `rec`."""
        if rec is None:
            return []
        sim = self.sim
        stages = [st for st in sim.core.STAGES if st in rec.stages] or list(rec.stages)
        lines = []
        for st in stages:
            s = rec.stages[st]
            if s is None or s.uPC is None:
                lines.append(f"  {st:<6} {'-' if s is None else '(not decoded yet)'}")
                continue
            u = sim.core.decode(sim.core.fetch(s.pc), s.pc)
            lines.append(f"  {st:<6} uPC 0x{u.uPC:02X}  {u.uInstr:<44} {s.text}")
        return lines

    def _pipeline_text(self, rec=None) -> str:
        """The pipeline picture; with 'micro on' it is followed by each stage's microinstruction."""
        text = self.sim.format_pipeline(rec) if rec is not None else self.sim.format_pipeline()
        if self.micro:
            extra = self._micro_lines(rec or self.sim.pipeline_view())
            if extra:
                text += f"\n  microinstructions ({self.sim.config.control} control unit):\n" + "\n".join(extra)
        return text

    def _labels_by_name(self) -> Dict[str, int]:
        return {name: addr for addr, name in self.sim.labels.items()}

    def addr(self, tok: str) -> int:
        tok = tok.strip()
        names = self._labels_by_name()
        if tok in names:
            return names[tok]
        try:
            return int(tok, 0) & M32
        except ValueError:
            raise ValueError(f"'{tok}' is not an address or label") from None

    def reg(self, tok: str) -> int:
        r = isa.parse_register(tok)
        if r is None:
            raise ValueError(f"'{tok}' is not a register (r0-r15, sp, ra)")
        return r

    def where(self, a: int) -> str:
        lab = self.sim.labels.get(a)
        return f"0x{a:04X}" + (f" <{lab}>" if lab else "")

    def need_program(self):
        if not self.sim.program:
            raise ValueError("no program loaded (use: load <file>)")

    def _reset_debug_state(self):
        self.tracker.reset()
        for w in self.watches:
            w["old"] = self._watch_value(w)

    # ------------------------------------------------------------------ program
    def do_load(self, arg):
        """load <file>      assemble (.asm/.s, with macros) or read a .hex/.bin image, then reset"""
        args = shlex.split(arg)
        if not args:
            raise ValueError("usage: load <file>")
        path = args[0]
        if not os.path.exists(path):
            raise ValueError(f"cannot open '{path}'")
        if os.path.splitext(path)[1].lower() in (".asm", ".s"):
            with open(path, encoding="utf-8") as fh:
                self._apply_program_config(fh.read())
        res = self.sim.load_file(path)
        self.path = path
        self._reset_debug_state()
        n = len(self.sim.program)
        self.p(f"loaded {path}: {n} instruction word(s), {len(self.sim.labels)} label(s)  "
               f"[mode {self.sim.mode}, control {self.sim.config.control}, "
               f"protect {self.sim.config.stack_protect}]")
        if res is not None:
            for w in res.warnings:
                self.p(w.format(path))
        rep = self._stack_report()
        errs = [f for f in rep.findings if f.level == "error"]
        warns = [f for f in rep.findings if f.level == "warning"]
        if errs or warns:
            self.p(f"stack check: {len(errs)} error(s), {len(warns)} warning(s) - type 'check' for details")

    def do_reload(self, arg):
        """reload           load the same file again (after editing it)"""
        if not self.path:
            raise ValueError("nothing loaded yet")
        self.do_load(shlex.quote(self.path))

    def do_source(self, arg):
        """source           show the source after macro / .org expansion"""
        self.need_program()
        pre = self.sim.pre_result
        if pre is None:
            self.p("(program was not loaded from assembly source)")
            return
        for i, ol in enumerate(pre.lines, 1):
            tag = f"  ; line {ol.line}" + (f", {ol.origin}" if ol.origin else "")
            if ol.origin == ".org padding" and i > 1 and pre.lines[i - 2].origin == ".org padding":
                continue
            self.p(f"{ol.text:<40}{tag}")

    def do_list(self, arg):
        """list [addr|label] [count]   disassemble the program (default: around the PC)"""
        self.need_program()
        args = arg.split()
        start = self.addr(args[0]) if args else max(0, self.sim.pc - 8)
        count = int(args[1], 0) if len(args) > 1 else 12
        start &= ~3
        for a in range(start, start + 4 * count, 4):
            i = a // 4
            if i >= len(self.sim.core.imem):
                break
            w = self.sim.core.imem[i]
            text = disassembler.disassemble_word(w, a, self.sim.labels)
            lab = self.sim.labels.get(a)
            if lab:
                self.p(f"{lab}:")
            mark = "B" if a in self.breakpoints else " "
            cur = "=>" if a == self.sim.pc else "  "
            self.p(f" {mark}{cur} 0x{a:04X}  {w:08X}  {text}")

    def do_disasm(self, arg):
        """disasm           disassemble the whole program (long runs of padding nops are collapsed)"""
        self.need_program()
        words = self.sim.program
        i = 0
        while i < len(words):
            a = i * 4
            text = disassembler.disassemble_word(words[i], a, self.sim.labels)
            run = 1
            while text == "nop" and i + run < len(words) and words[i + run] == words[i]:
                run += 1
            lab = self.sim.labels.get(a)
            if lab:
                self.p(f"{lab}:")
            mark = "B" if a in self.breakpoints else " "
            cur = "=>" if a == self.sim.pc else "  "
            self.p(f" {mark}{cur} 0x{a:04X}  {words[i]:08X}  {text}")
            if run > 3:
                self.p(f"        ... {run - 1} more nop word(s), up to 0x{(i + run - 1) * 4:04X}")
                i += run
            else:
                i += 1

    def do_symbols(self, arg):
        """symbols          labels and .equ constants"""
        res = self.sim.asm_result
        if res is None:
            self.p("(no symbol table: program not assembled from source)")
            return
        self.p(res.symbol_table().rstrip())

    def _stack_report(self):
        cfg = self.sim.config
        return check_stack(self.sim.program, self.sim.labels, cfg.stack_base - cfg.stack_limit,
                           cfg.exec_vector)

    def do_check(self, arg):
        """check            static stack safety verification (push/pop balance, depth, recursion)"""
        self.need_program()
        self.p(self._stack_report().format(self.sim.labels))

    # ------------------------------------------------------------------ machine configuration
    def do_mode(self, arg):
        """mode [functional|4stage|6stage]   show or change the processor model (resets)"""
        if arg.strip():
            self.sim.set_mode(arg.strip())
            self._reset_debug_state()
        self.p(f"mode: {self.sim.mode}")

    def do_control(self, arg):
        """control [vertical|horizontal]     show or change the microprogrammed control unit (resets)"""
        if arg.strip():
            self.set_user(control=arg.strip().lower())
        c = self.sim.config.control
        m = VMP if c == "vertical" else HMP
        self.p(f"control unit: {c} microprogrammed, {m.WIDTH}-bit microinstructions")

    do_cu = do_control

    def do_adder(self, arg):
        """adder [kogge|ripple]   adder used by the ALU: Kogge-Stone prefix or ripple-carry (resets)"""
        if arg.strip():
            if arg.strip() not in ("kogge", "ripple"):
                raise ValueError("adder must be kogge or ripple")
            self.set_user(adder=arg.strip())
        self.p(f"adder: {self.sim.config.adder}")

    def do_alu(self, arg):
        """alu [adder|mul|div] [algorithm]   show or choose the ALU algorithms (resets)
        adder: kogge | ripple    mul: booth | shiftadd    div: nonrestoring | restoring"""
        args = arg.split()
        keys = {"adder": "adder", "mul": "mul", "div": "div"}
        if args:
            if len(args) != 2 or args[0] not in keys:
                raise ValueError("usage: alu [adder|mul|div] <algorithm>")
            self.set_user(**{keys[args[0]]: args[1].lower()})
        c = self.sim.config
        self.p(f"ALU: adder {c.adder}, multiplier {c.mul}, divider {c.div}")
        steps = self.sim.alu.steps
        if steps:
            self.p("  add/subtract steps so far: " + ", ".join(f"{k} {v}" for k, v in sorted(steps.items())))

    def do_protect(self, arg):
        """protect [off|warn|trap]   runtime stack memory protection (trap = exception, cause 2) (resets)"""
        if arg.strip():
            self.set_user(stack_protect=arg.strip().lower())
        self.p(f"stack protection: {self.sim.config.stack_protect}")

    def do_reset(self, arg):
        """reset            restart the program from PC = 0 (breakpoints and watches are kept)"""
        self.sim.reset()
        self._reset_debug_state()
        self.p("reset: PC = 0x0000, cycle 0")

    # ------------------------------------------------------------------ execution core
    def _watch_value(self, w) -> int:
        return self.sim.read_reg(w["key"]) if w["kind"] == "reg" else self.sim.read_mem(w["key"])

    def _advance(self, max_cycles: int, retire_target: Optional[int] = None,
                 stop_at: Optional[int] = None, quiet_steps: bool = True) -> str:
        """Clock the machine; returns why it stopped. Stops on breakpoints, watchpoints,
        exceptions are reported (not stopped on), halt, retire_target instructions, stop_at address."""
        sim = self.sim
        if sim.halted:
            return "halted"
        start_cycles = sim.cycles
        start_retired = len(sim.core.retired)
        reason = "max_cycles"
        while sim.cycles - start_cycles < max_cycles:
            before = len(sim.core.retired)
            recs = sim.step()
            if not recs:
                reason = "halted"
                break
            rec = recs[0]
            self.tracker.update()
            if self.trace or not quiet_steps:
                self.p(self._pipeline_text(rec))
            else:
                for e in rec.events:
                    if e.startswith("EXCEPTION") or e.startswith("STACK WARNING"):
                        self.p(f"  ! cycle {rec.cycle}: {e}")
            new = sim.core.retired[before:]
            hit = [pc for pc in new if pc in self.breakpoints]
            if hit:
                reason = f"breakpoint at {self.where(hit[0])}"
                break
            if stop_at is not None and stop_at in new:
                reason = f"reached {self.where(stop_at)}"
                break
            changed = []
            for i, w in enumerate(self.watches):
                v = self._watch_value(w)
                if v != w["old"]:
                    changed.append(f"watch #{i + 1} {w['name']}: 0x{w['old']:08X} -> 0x{v:08X}")
                    w["old"] = v
            if changed:
                reason = "; ".join(changed)
                break
            if sim.halted:
                reason = "halted"
                break
            if retire_target is not None and len(sim.core.retired) - start_retired >= retire_target:
                reason = "stepped"
                break
        return reason

    def _report_stop(self, reason: str):
        sim = self.sim
        self.p(f"[{reason}]  cycle {sim.cycles}, {len(sim.core.retired)} retired, PC = {self.where(sim.pc)}"
               + (f", CPI {sim.stats()['cpi']}" if sim.core.retired else ""))
        if sim.core.cause:
            self.p(f"  exception state: cause = {sim.core.cause} ({CAUSE_NAMES.get(sim.core.cause)}), "
                   f"epc = {self.where(sim.core.epc)}")

    def do_step(self, arg):
        """step [n]         advance n clock cycles (default 1) and show the pipeline"""
        self.need_program()
        n = int(arg, 0) if arg.strip() else 1
        if n == 1 and not self.sim.halted:
            reason = self._advance(1, quiet_steps=False)
        else:
            reason = self._advance(n)
            if self.sim.pipeline_view():
                self.p(self._pipeline_text())
        self._report_stop(reason if reason != "max_cycles" else f"{n} cycle(s)")

    do_s = do_step

    def do_stepi(self, arg):
        """stepi [n]        run until n more instructions have retired (default 1)"""
        self.need_program()
        n = int(arg, 0) if arg.strip() else 1
        reason = self._advance(DEFAULT_MAX_CYCLES, retire_target=n)
        if self.sim.pipeline_view():
            self.p(self._pipeline_text())
        self._report_stop(reason)

    do_si = do_stepi

    def do_next(self, arg):
        """next             like stepi, but runs a whole call (stops when the call returns)"""
        self.need_program()
        reason = self._advance(DEFAULT_MAX_CYCLES, retire_target=1)
        if reason == "stepped" and self.sim.core.retired:
            pc = self.sim.core.retired[-1]
            f = isa.unpack_word(self.sim.core.fetch(pc))
            if isa.OPS_BY_OPCODE.get(f.opcode) and isa.OPS_BY_OPCODE[f.opcode].name == "call":
                reason = self._advance(DEFAULT_MAX_CYCLES, stop_at=pc + 4)
        self._report_stop(reason)

    do_n = do_next

    def do_finish(self, arg):
        """finish           run until the current function returns to its caller"""
        self.need_program()
        if not self.tracker.frames:
            raise ValueError("not inside a called function (use run)")
        ret = self.tracker.frames[-1].ret_addr
        reason = self._advance(DEFAULT_MAX_CYCLES, stop_at=ret)
        self._report_stop(reason)

    def do_run(self, arg):
        """run [max_cycles] run until halt / breakpoint / watchpoint (default limit 1,000,000 cycles)"""
        self.need_program()
        n = int(arg, 0) if arg.strip() else DEFAULT_MAX_CYCLES
        reason = self._advance(n)
        self._report_stop(reason)

    def do_continue(self, arg):
        """continue         same as run"""
        self.do_run(arg)

    do_c = do_continue

    def do_until(self, arg):
        """until <addr|label>   run until the instruction at that address retires"""
        self.need_program()
        a = self.addr(arg)
        reason = self._advance(DEFAULT_MAX_CYCLES, stop_at=a)
        self._report_stop(reason)

    # ------------------------------------------------------------------ breakpoints / watchpoints
    def do_break(self, arg):
        """break <addr|label>   stop after the instruction at that address retires"""
        if not arg.strip():
            return self.do_breaks("")
        a = self.addr(arg)
        if a % 4:
            raise ValueError("breakpoint address must be a multiple of 4")
        if a not in self.breakpoints:
            self.breakpoints.append(a)
        self.p(f"breakpoint #{self.breakpoints.index(a) + 1} at {self.where(a)}")

    do_b = do_break

    def do_breaks(self, arg):
        """breaks           list breakpoints and watchpoints"""
        if not self.breakpoints and not self.watches:
            self.p("no breakpoints or watchpoints")
        for i, a in enumerate(self.breakpoints, 1):
            text = disassembler.disassemble_word(self.sim.core.fetch(a), a, self.sim.labels)
            self.p(f"  break #{i}  {self.where(a)}  {text}")
        for i, w in enumerate(self.watches, 1):
            self.p(f"  watch #{i}  {w['name']} = 0x{self._watch_value(w):08X}")

    def do_delete(self, arg):
        """delete [n|all|addr|label]   remove breakpoint number n (see 'breaks'), one by address, or all"""
        tok = arg.strip()
        if tok in ("", "all"):
            self.breakpoints.clear()
            self.p("all breakpoints deleted")
        elif tok.lower().startswith("0x") or tok in self._labels_by_name():
            a = self.addr(tok)                      # an address or label (a plain number is an index)
            if a not in self.breakpoints:
                raise ValueError(f"no breakpoint at {self.where(a)}")
            self.breakpoints.remove(a)
            self.p(f"deleted breakpoint at {self.where(a)}")
        else:
            a = self.breakpoints.pop(int(tok) - 1)
            self.p(f"deleted breakpoint at {self.where(a)}")

    do_breakpoints = do_breaks

    def do_clear_breaks(self, arg):
        """clear-breaks     remove all breakpoints (watchpoints are kept)"""
        self.breakpoints.clear()
        self.p("all breakpoints cleared")

    def do_watch(self, arg):
        """watch <reg> | watch mem <addr>   stop when the value changes"""
        args = arg.split()
        if not args:
            raise ValueError("usage: watch <reg> | watch mem <addr>")
        if args[0] == "mem":
            a = self.addr(args[1])
            w = {"kind": "mem", "key": a, "name": f"mem[0x{a:X}]"}
        else:
            r = self.reg(args[0])
            w = {"kind": "reg", "key": r, "name": isa.reg_name(r)}
        w["old"] = self._watch_value(w)
        self.watches.append(w)
        self.p(f"watch #{len(self.watches)}: {w['name']} (now 0x{w['old']:08X})")

    def do_unwatch(self, arg):
        """unwatch [n|all]  remove watchpoints"""
        if arg.strip() in ("", "all"):
            self.watches.clear()
            self.p("all watchpoints deleted")
        else:
            w = self.watches.pop(int(arg) - 1)
            self.p(f"deleted watch on {w['name']}")

    # ------------------------------------------------------------------ state
    def do_regs(self, arg):
        """regs             register file (hex, signed, as float), PC, flags, epc, cause"""
        self.p(self.sim.format_registers())

    def do_reg(self, arg):
        """reg <r> [value]  show or set one register"""
        args = arg.split()
        if not args:
            raise ValueError("usage: reg <r> [value]")
        r = self.reg(args[0])
        if len(args) > 1:
            self.sim.write_reg(r, int(args[1], 0))
        v = self.sim.read_reg(r)
        self.p(f"{isa.reg_name(r)} = 0x{v:08X}  {v - (1 << 32) if v >> 31 else v}  ({isa.bits32_to_float(v):.7g})")

    def do_mem(self, arg):
        """mem [addr] [count]   data memory words (default: all non-zero words)"""
        args = arg.split()
        if not args:
            self.p(self.sim.format_memory(count=64))
            return
        a = self.addr(args[0]) & ~3
        n = int(args[1], 0) if len(args) > 1 else 8
        for i in range(n):
            v = self.sim.read_mem(a + 4 * i)
            self.p(f"  [0x{a + 4 * i:08X}] = 0x{v:08X}  {v - (1 << 32) if v >> 31 else v}")

    def do_setmem(self, arg):
        """setmem <addr> <value>   write a data memory word"""
        a, v = arg.split()
        self.sim.write_mem(self.addr(a), int(v, 0))
        self.p(f"[0x{self.addr(a):08X}] = 0x{int(v, 0) & M32:08X}")

    def do_exc(self, arg):
        """exc              exception state (cause, epc) and the exception handler address"""
        c = self.sim.core
        self.p(f"cause = {c.cause} ({CAUSE_NAMES.get(c.cause, '?')}), epc = {self.where(c.epc)}, "
               f"EXEC_VECTOR = 0x{self.sim.config.exec_vector:X}, stack protection: {self.sim.config.stack_protect}")
        self.p("cause codes: " + ", ".join(f"{k} = {v}" for k, v in CAUSE_NAMES.items()))

    def do_print_regs(self, arg):
        """print-regs       same as regs"""
        self.do_regs(arg)

    def do_print_stack(self, arg):
        """print-stack [words]   same as stack (default 16 words)"""
        self.do_stack(arg if arg.strip() else "16")

    def do_print_mem(self, arg):
        """print-mem <addr> [words]   data memory from an address (default 32 words)"""
        args = arg.split()
        if not args:
            raise ValueError("usage: print-mem <address> [words]")
        self.do_mem(f"{args[0]} {args[1] if len(args) > 1 else 32}")

    def do_stats(self, arg):
        """stats            cycles, CPI, stalls, flushes, exceptions"""
        self.p(self.sim.format_stats())

    # ------------------------------------------------------------------ pipeline
    def do_pipe(self, arg):
        """pipe             what was in each pipeline stage in the last cycle (with uPC)"""
        self.p(self._pipeline_text())

    def do_print_pipeline(self, arg):
        """print-pipeline   same as pipe"""
        self.do_pipe(arg)

    def do_timeline(self, arg):
        """timeline [first] [last]   instruction x cycle diagram (:s stall, :b busy, :x killed, :! fault)"""
        args = [int(a) for a in arg.split()]
        first = args[0] if args else max(1, self.sim.cycles - 24)
        last = args[1] if len(args) > 1 else first + 29
        self.p(self.sim.format_timeline(first, last))

    def do_trace(self, arg):
        """trace [on|off]   print the pipeline every cycle while running"""
        if arg.strip():
            self.trace = arg.strip().lower() == "on"
        self.p(f"trace {'on' if self.trace else 'off'}")

    # ------------------------------------------------------------------ microcode
    def do_micro(self, arg):
        """micro [on|off]   also show each stage's microinstruction in step / pipe output (no argument = toggle)"""
        word = arg.strip().lower()
        if word not in ("", "on", "off"):
            raise ValueError("usage: micro [on|off]")
        self.micro = (word == "on") if word else not self.micro
        self.p(f"micro trace {'on' if self.micro else 'off'}")

    def do_upc(self, arg):
        """upc <addr>       one microinstruction as stored in BOTH control memories (e.g. upc 0x11)"""
        tok = arg.strip()
        if not tok:
            raise ValueError("usage: upc <address>")
        try:
            u = int(tok, 0)
        except ValueError:
            u = int(tok, 16)                        # bare hex such as 'a2'
        if u not in VMP.VERTICAL_ROM and u not in HMP.CONTROL_MEMORY:
            raise ValueError(f"no microinstruction at uPC 0x{u:02X}")
        self.p(f"uPC 0x{u:02X}")
        if u in VMP.VERTICAL_ROM:
            self.p(f"  vertical    ({VMP.WIDTH} bits)  {VMP.format_entry(VMP.VERTICAL_ROM[u])}")
        if u in HMP.CONTROL_MEMORY:
            cw = HMP.CONTROL_MEMORY[u]
            self.p(f"  horizontal  ({HMP.WIDTH} bits)  {HMP.format_word(cw)}  {' '.join(HMP.active_signals(cw)) or '-'}")

    def do_micro_stats(self, arg):
        """micro-stats      how many retired instructions used each microinstruction (uPC histogram)"""
        sim = self.sim
        counts: Dict[int, int] = {}
        text: Dict[int, str] = {}
        for pc in sim.core.retired:
            u = sim.core.decode(sim.core.fetch(pc), pc)
            counts[u.uPC] = counts.get(u.uPC, 0) + 1
            text.setdefault(u.uPC, u.uInstr)
        if not counts:
            self.p("  (no instructions retired yet)")
            return
        total = sum(counts.values())
        self.p(f"retired instructions per microinstruction ({sim.config.control} control unit):")
        for upc, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            self.p(f"  uPC 0x{upc:02X}  {n:5d}  {100 * n / total:5.1f}%  {text[upc]}")

    def do_ucode(self, arg):
        """ucode [addr|label]   microPC of every stage, or the microsequencer steps for one instruction"""
        sim = self.sim
        control = sim.config.control
        if not arg.strip():
            rec = sim.pipeline_view()
            if rec is None:
                self.p("(no cycle executed yet)")
                return
            self.p(f"cycle {rec.cycle}   control unit: {control}")
            for line in self._micro_lines(rec):
                self.p(line)
            return
        a = self.addr(arg)
        word = sim.core.fetch(a)
        u = sim.core.decode(word, a)
        f = isa.unpack_word(word)
        m = VMP if control == "vertical" else HMP
        key = (f.opcode, f.i, f.f)                     # raw I and F bits of the word
        self.p(f"instruction {self.where(a)}: {u.text}   word 0x{word:08X}")
        self.p(f"control unit: {control} ({m.WIDTH}-bit microinstructions)")
        self.p(f"  step 1  IR  <- 0x{word:08X}         opcode={f.opcode:05b} ({u.name})  I={f.i}  F={f.f}")
        self.p(f"  step 2  uPC <- DISPATCH[{f.opcode}, {f.i}, {f.f}] = 0x{m.DISPATCH_TABLE.get(key, 0):02X}"
               + ("   (reserved opcode -> nop microinstruction)" if f.opcode in isa.RESERVED_OPCODES else ""))
        self.p(f"  step 3  uIR <- ROM[0x{u.uPC:02X}] = {u.uInstr}")
        if control == "vertical":
            fields = VMP.VERTICAL_ROM[u.uPC]
            self.p("  step 4  decode the encoded fields: " +
                   ", ".join(f"{n}={v}" for n, v in zip(VMP.FIELD_NAMES, fields)))
        else:
            cw = HMP.CONTROL_MEMORY[u.uPC]
            self.p("  step 4  no field decoding - each set bit is a control signal: " +
                   (" ".join(HMP.active_signals(cw)) or "(none)"))
        sig = [n for n in ("isLd", "isSt", "isPush", "isPop", "isCall", "isRet", "isBeq", "isBgt",
                           "isUBranch", "isCmp", "isRTE", "isHalt", "isWb", "isWb2", "isFloat", "isfcvt")
               if getattr(u, n)]
        srcs = []
        if u.useSrc1:
            srcs.append(f"src1={isa.reg_name(u.src1Addr)}")
        if u.useSrc2:
            srcs.append(f"src2={isa.reg_name(u.src2Addr)}")
        if u.isWb:
            srcs.append(f"write port1={isa.reg_name(u.writeAddr)}")
        if u.isWb2:
            srcs.append("write port2=sp")
        alu = {"opc": f"opcode ({u.name}{', FPU' if u.isFloat else ''})", "none": "not used"}.get(u.aluOp, u.aluOp)
        self.p(f"          -> signals: {' '.join(sig) or '(none)'}")
        self.p(f"          -> ALU function: {alu};  {'  '.join(srcs) or 'no register access'}")
        self.p("  step 5  uPC <- 0x00 (end of microroutine; dispatch again for the next instruction)")

    def do_rom(self, arg):
        """rom              dump the control memory of the current control unit"""
        control = self.sim.config.control
        users: Dict[int, List[str]] = {}
        m = VMP if control == "vertical" else HMP
        for (op, I, F), a in sorted(m.DISPATCH_TABLE.items()):
            name = m.OPCODE_MAP.get(op, "?")
            if F and a in FLOAT_UPCS:
                name = "fcvti" if name == "fcvt" else "f" + name
            tag = name + ("(imm)" if I and a in IMM_UPCS else "")
            users.setdefault(a, [])
            if tag not in users[a]:
                users[a].append(tag)
        if control == "vertical":
            rom = VMP.VERTICAL_ROM
            self.p(f"vertical control memory: {len(rom)} words x {VMP.WIDTH} bits = {len(rom) * VMP.WIDTH} bits")
            self.p("  fields: " + ", ".join(f"{n}({VMP.FIELD_BITS[n]}b)" for n in VMP.FIELD_NAMES))
            for a in sorted(rom):
                self.p(f"  0x{a:02X}  {VMP.format_entry(rom[a]):<52} {' '.join(users.get(a, [])) or 'nop/reserved'}")
        else:
            rom = HMP.CONTROL_MEMORY
            self.p(f"horizontal control memory: {len(rom)} words x {HMP.WIDTH} bits = {len(rom) * HMP.WIDTH} bits")
            self.p("  bit order (MSB first): " + " ".join(HMP.SIGNAL_BITS))
            for a in sorted(rom):
                self.p(f"  0x{a:02X}  {HMP.format_word(rom[a])}  {' '.join(HMP.active_signals(rom[a])) or '-':<44} "
                       f"{' '.join(users.get(a, [])) or 'nop/reserved'}")

    # ------------------------------------------------------------------ stack
    def do_stack(self, arg):
        """stack [words]    ASCII picture of the stack with frames, saved return addresses and SP"""
        n = int(arg, 0) if arg.strip() else 24
        self.tracker.update()
        self.p(render_stack(self.sim, self.tracker, max_words=n))

    def do_frames(self, arg):
        """frames           call stack (backtrace)"""
        self.tracker.update()
        self.p(self.tracker.backtrace())

    do_bt = do_frames

    # ------------------------------------------------------------------ evaluation
    def do_compare(self, arg):
        """compare          run the program in every mode x control unit and compare cycles / CPI"""
        self.need_program()
        base_cfg = self.sim.config
        rows, ref_state = [], None
        for control in ("vertical", "horizontal"):
            for mode in ("functional", "4stage", "6stage"):
                cfg = Config(**{**base_cfg.__dict__, "control": control,
                                "initial_regs": dict(base_cfg.initial_regs),
                                "initial_memory": dict(base_cfg.initial_memory)})
                s = Simulator(mode, cfg, trace_limit=1)
                s.load_words(self.sim.program, self.sim.labels)
                r = s.run(max_cycles=DEFAULT_MAX_CYCLES)
                st = s.stats()
                state = s.state()
                ref_state = ref_state or state
                rows.append((control, mode, r.reason, st["cycles"], st["retired"], st["cpi"],
                             st.get("stall_ex_busy", 0), st.get("stall_load_use", 0),
                             st.get("branches_taken", 0), st.get("flush_bubbles", 0),
                             "yes" if state == ref_state else "NO"))
        self.p(f"{'control':<11}{'mode':<11}{'stop':<9}{'cycles':>8}{'instr':>7}{'CPI':>7}"
               f"{'busy':>6}{'ld-use':>7}{'taken':>7}{'flush':>7}  same result")
        for r in rows:
            self.p(f"{r[0]:<11}{r[1]:<11}{r[2]:<9}{r[3]:>8}{r[4]:>7}{r[5]:>7}{r[6]:>6}{r[7]:>7}{r[8]:>7}{r[9]:>7}  {r[10]}")

    # ------------------------------------------------------------------ misc
    def do_help(self, arg):
        """help [command]   list all commands, or describe one"""
        topic = self._unhyphen(arg.strip())
        if topic:
            return super().do_help(topic)
        real, self.stdout = self.stdout, io.StringIO()
        try:
            super().do_help("")
            text = self.stdout.getvalue()
        finally:
            self.stdout = real
        for name in self.HYPHEN_COMMANDS:             # show the hyphenated spelling in the list
            text = text.replace(name, name.replace("_", "-"))
        self.stdout.write(text)

    def do_quit(self, arg):
        """quit             leave the simulator"""
        return True

    do_exit = do_quit
    do_q = do_quit

    def do_EOF(self, arg):
        self.p("")
        return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="RISC201 interactive simulator and debugger")
    ap.add_argument("program", nargs="?", help=".asm / .s source or .hex / .bin image")
    ap.add_argument("-m", "--mode", default="6stage", help="functional | 4stage | 6stage (default 6stage)")
    ap.add_argument("-c", "--control", help="vertical | horizontal (default vertical)")
    ap.add_argument("--adder", help="kogge | ripple (default kogge)")
    ap.add_argument("--mul", help="booth | shiftadd (default booth)")
    ap.add_argument("--div", help="nonrestoring | restoring (default nonrestoring)")
    ap.add_argument("--protect", help="stack protection: off | warn | trap (default off, "
                                      "or what the program's ; CONFIG line asks for)")
    ap.add_argument("-x", "--execute", help="commands to run, separated by ';' (batch mode, then exit)")
    ap.add_argument("-b", "--batch", help="file with one command per line (batch mode, then exit)")
    args = ap.parse_args(argv)

    chosen = {k: v for k, v in (("control", args.control), ("adder", args.adder), ("mul", args.mul),
                                ("div", args.div), ("stack_protect", args.protect)) if v is not None}
    try:
        sim = Simulator(args.mode, Config())
        sim.configure(**chosen)
    except SimulatorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    sh = RiscShell(sim)
    sh.user_settings.update(chosen)
    if args.program:
        sh.onecmd(f"load {shlex.quote(args.program)}")
    cmds = []
    if args.execute:
        cmds += [c.strip() for c in args.execute.split(";") if c.strip()]
    if args.batch:
        with open(args.batch, encoding="utf-8") as fh:
            cmds += [l.strip() for l in fh if l.strip() and not l.strip().startswith("#")]
    if cmds:
        sh.batch = True
        for c in cmds:
            print(f"{sh.prompt}{c}")
            if sh.onecmd(c):
                break
        return 0
    try:
        sh.cmdloop()
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
