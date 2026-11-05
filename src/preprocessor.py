#!/usr/bin/env python3
"""
preprocessor.py - RISC201 assembly preprocessor (runs before assembler.py)

Adds macro expansion, with built-in macros for a FULL-DESCENDING stack
(SP points at the last item pushed; the stack grows towards lower addresses):

  PUSHM {r1, r4-r6}   push several registers. Highest register is pushed first, so the
                      lowest register ends up at the lowest address (same layout as ARM STMFD).
  POPM  {r1, r4-r6}   pop them back in the opposite order (lowest register first).
  ENTER n             function prologue: push ra, then reserve n bytes of locals (sub sp, sp, n)
  LEAVE n             function epilogue: release n bytes (add sp, sp, n), then pop ra
  ALLOC n / FREE n    reserve / release n bytes of stack (n must be a multiple of 4)

User macros:

  .macro NAME p1, p2=default        body uses \\p1, \\p2; \\@ becomes a unique number per expansion
      ...                           (use it in labels: loop\\@:)
  .endm

Placement:

  .org ADDR            pad with nop up to byte address ADDR (e.g. put a handler at 0x400)

Every output line remembers its original source line, so assembler errors point at the line
the student wrote (and say which macro it came from).

Library use:
    from preprocessor import preprocess
    pre = preprocess(text, "prog.asm")
    result = pre.assemble()        # AssemblyResult, errors re-mapped to original lines
    print(pre.text)                # expanded source

Command line:
    python preprocessor.py prog.asm            # print the expanded source
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import assembler
import isa

MAX_DEPTH = 32
_CONFIG_RE = re.compile(r"^\s*;\s*CONFIG\s+(\w+)\s*=\s*(\S+)", re.I | re.M)
_LABEL_RE = re.compile(r"^\s*([A-Za-z_.][A-Za-z0-9_.$]*)\s*:")
_IDENT_RE = re.compile(r"^[A-Za-z_.][A-Za-z0-9_.$]*$")
BUILTINS = ("pushm", "popm", "enter", "leave", "alloc", "free")


@dataclass
class Macro:
    name: str
    params: List[str]
    defaults: Dict[str, str]
    body: List[Tuple[int, str]]          # (original line, raw text)
    line: int


@dataclass
class OutLine:
    text: str
    line: int                            # original source line
    origin: str = ""                     # "macro X" chain, empty for ordinary lines


@dataclass
class PreResult:
    lines: List[OutLine]
    macros: Dict[str, Macro]
    filename: str
    source: List[str]

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines) + "\n"

    def map_line(self, n: int) -> Tuple[int, str]:
        if 1 <= n <= len(self.lines):
            ol = self.lines[n - 1]
            return ol.line, ol.origin
        return n, ""

    def _remap(self, d: "assembler.Diagnostic") -> "assembler.Diagnostic":
        line, origin = self.map_line(d.line)
        msg = d.message + (f" (in expansion of {origin})" if origin else "")
        src = self.source[line - 1] if 1 <= line <= len(self.source) else d.source
        return assembler.Diagnostic(d.level, line, msg, src)

    def assemble(self, strict: bool = False, base: int = 0) -> "assembler.AssemblyResult":
        try:
            res = assembler.assemble_source(self.text, base=base, strict=strict, filename=self.filename)
        except assembler.AssemblyFailed as exc:
            raise assembler.AssemblyFailed([self._remap(d) for d in exc.diagnostics], self.filename) from None
        res.warnings = [self._remap(d) for d in res.warnings]
        for it in res.items:
            it.line = self.map_line(it.line)[0]
        return res


class _Errors:
    def __init__(self, filename: str, source: List[str]):
        self.filename, self.source, self.diags = filename, source, []

    def add(self, line: int, msg: str):
        src = self.source[line - 1] if 1 <= line <= len(self.source) else ""
        self.diags.append(assembler.Diagnostic("error", line, msg, src))


# ---------------------------------------------------------------------------------------------
def _split_head(text: str):
    """-> (labels, head, args) of a source line, ignoring comments."""
    rest = assembler.strip_comment(text)
    labels = []
    while True:
        m = _LABEL_RE.match(rest)
        if not m:
            break
        labels.append(m.group(1))
        rest = rest[m.end():]
    rest = rest.strip()
    if not rest:
        return labels, "", ""
    parts = rest.split(None, 1)
    return labels, parts[0], parts[1] if len(parts) > 1 else ""


def parse_reglist(args: str) -> List[int]:
    """'{r1, r4-r6, ra}' or 'r1, r2' -> sorted unique register numbers."""
    a = args.strip()
    if a.startswith("{"):
        if not a.endswith("}"):
            raise ValueError("register list must end with '}'")
        a = a[1:-1]
    regs = set()
    for part in [p.strip() for p in a.split(",") if p.strip()]:
        if "-" in part:
            lo_s, hi_s = [x.strip() for x in part.split("-", 1)]
            lo, hi = isa.parse_register(lo_s), isa.parse_register(hi_s)
            if lo is None or hi is None or lo > hi:
                raise ValueError(f"bad register range '{part}'")
            regs.update(range(lo, hi + 1))
        else:
            r = isa.parse_register(part)
            if r is None:
                raise ValueError(f"'{part}' is not a register")
            regs.add(r)
    if not regs:
        raise ValueError("empty register list")
    if isa.REG_SP in regs:
        raise ValueError("sp cannot be in a PUSHM/POPM list")
    return sorted(regs)


def _const(args: str, symbols: Dict[str, int], what: str) -> int:
    try:
        v = assembler.eval_expr(args, symbols)
    except assembler.ExprError as exc:
        raise ValueError(f"{what}: {exc}") from None
    return v


def _builtin(name: str, args: str, symbols: Dict[str, int]) -> List[str]:
    R = isa.reg_name
    if name == "pushm":
        return [f"    push {R(r)}" for r in reversed(parse_reglist(args))]
    if name == "popm":
        return [f"    pop {R(r)}" for r in parse_reglist(args)]
    n = _const(args, symbols, name.upper()) if args.strip() else 0
    if n < 0 or n % 4 or n > 16383:
        raise ValueError(f"{name.upper()} size must be a multiple of 4 in [0, 16380] (got {n})")
    if name == "enter":
        return ["    push ra"] + ([f"    sub sp, sp, {n}"] if n else [])
    if name == "leave":
        return ([f"    add sp, sp, {n}"] if n else []) + ["    pop ra"]
    if name == "alloc":
        return [f"    sub sp, sp, {n}"] if n else []
    if name == "free":
        return [f"    add sp, sp, {n}"] if n else []
    raise AssertionError(name)


# ---------------------------------------------------------------------------------------------
def preprocess(text: str, filename: str = "<input>", base: int = 0) -> PreResult:
    source = text.splitlines()
    err = _Errors(filename, source)
    macros: Dict[str, Macro] = {}
    body_lines: List[Tuple[int, str]] = []

    # ---- pass A: collect .macro ... .endm definitions
    cur: Optional[Macro] = None
    for n, raw in enumerate(source, 1):
        labels, head, args = _split_head(raw)
        h = head.lower()
        if cur is not None:
            if h == ".endm":
                macros[cur.name] = cur
                cur = None
            elif h == ".macro":
                err.add(n, "nested .macro definitions are not allowed")
            else:
                cur.body.append((n, raw))
            continue
        if h == ".macro":
            parts = [p.strip() for p in re.split(r"[\s,]+", args.strip()) if p.strip()]
            if not parts or not _IDENT_RE.match(parts[0]):
                err.add(n, "usage: .macro NAME [param[=default], ...]")
                cur = Macro("?", [], {}, [], n)
                continue
            name = parts[0].lower()
            if name in BUILTINS or isa.resolve_mnemonic(name):
                err.add(n, f"macro name '{parts[0]}' clashes with an instruction or built-in macro")
            if name in macros:
                err.add(n, f"macro '{parts[0]}' already defined on line {macros[name].line}")
            params, defaults = [], {}
            for p in parts[1:]:
                pname, _, dflt = p.partition("=")
                params.append(pname)
                if _:
                    defaults[pname] = dflt
            cur = Macro(name, params, defaults, [], n)
            if labels:
                err.add(n, "a label cannot be put on a .macro line")
        elif h == ".endm":
            err.add(n, ".endm without .macro")
        else:
            body_lines.append((n, raw))
    if cur is not None:
        err.add(cur.line, f".macro {cur.name} has no matching .endm")

    # ---- pass B: expand macros
    out: List[OutLine] = []
    counter = [0]
    symbols: Dict[str, int] = {}

    def emit_line(raw: str, line: int, origin: str, depth: int):
        labels, head, args = _split_head(raw)
        h = head.lower()
        if h in (".equ", ".set"):
            nm, _, expr = args.partition(",")
            try:
                symbols[nm.strip()] = assembler.eval_expr(expr, symbols)
            except Exception:
                pass
        if h in macros or h in BUILTINS:
            if depth >= MAX_DEPTH:
                err.add(line, f"macro expansion deeper than {MAX_DEPTH} levels (recursive macro?)")
                return
            for lb in labels:
                out.append(OutLine(f"{lb}:", line, origin))
            try:
                if h in BUILTINS:
                    expansion = [(line, t) for t in _builtin(h, args, symbols)]
                    where = f"{head.upper()}"
                else:
                    expansion = _expand_user(macros[h], args)
                    where = f"macro {macros[h].name}"
            except ValueError as exc:
                err.add(line, str(exc))
                return
            chain = where if not origin else f"{origin} -> {where}"
            for bl, t in expansion:
                emit_line(t, line, chain, depth + 1)
            return
        out.append(OutLine(raw, line, origin))

    def _expand_user(m: Macro, args: str) -> List[Tuple[int, str]]:
        try:
            values = assembler.split_operands(args) if args.strip() else []
        except assembler.AsmError as exc:
            raise ValueError(str(exc)) from None
        if len(values) > len(m.params):
            raise ValueError(f"macro {m.name} takes {len(m.params)} argument(s), got {len(values)}")
        env = {}
        for i, p in enumerate(m.params):
            if i < len(values):
                env[p] = values[i]
            elif p in m.defaults:
                env[p] = m.defaults[p]
            else:
                raise ValueError(f"macro {m.name}: missing argument '{p}'")
        counter[0] += 1
        uniq = f"_{counter[0]}"
        result = []
        for bl, t in m.body:
            t = t.replace("\\@", uniq)
            for p in sorted(env, key=len, reverse=True):
                t = t.replace("\\" + p, env[p])
            result.append((bl, t))
        return result

    for n, raw in body_lines:
        emit_line(raw, n, "", 0)

    # ---- pass C: .org (needs addresses, so it runs after expansion)
    final: List[OutLine] = []
    addr = base
    syms: Dict[str, int] = {}
    for ol in out:
        labels, head, args = _split_head(ol.text)
        h = head.lower()
        if h in (".equ", ".set"):
            nm, _, expr = args.partition(",")
            try:
                syms[nm.strip()] = assembler.eval_expr(expr, syms)
            except Exception:
                pass
        if h == ".org":
            try:
                target = assembler.eval_expr(args, syms)
            except Exception as exc:
                err.add(ol.line, f".org: {exc}")
                continue
            if target % isa.INSTR_BYTES:
                err.add(ol.line, f".org 0x{target:X} is not word aligned")
                continue
            if target < addr:
                err.add(ol.line, f".org 0x{target:X} is behind the current address 0x{addr:X}")
                continue
            pad = (target - addr) // isa.INSTR_BYTES
            final.extend(OutLine("    nop", ol.line, ".org padding") for _ in range(pad))
            addr = target
            for lb in labels:
                final.append(OutLine(f"{lb}:", ol.line, ol.origin))
            continue
        final.append(ol)
        if not head:
            continue
        if h == ".word":
            try:
                addr += isa.INSTR_BYTES * len(assembler.split_operands(args))
            except assembler.AsmError:
                pass
        elif not h.startswith("."):
            addr += isa.INSTR_BYTES

    if err.diags:
        raise assembler.AssemblyFailed(err.diags, filename)
    return PreResult(final, macros, filename, source)


def read_config(text: str) -> Dict[str, object]:
    """'; CONFIG stack_protect=trap' lines -> {'stack_protect': 'trap'} (numbers become ints).
    They are settings the program needs, e.g. a stack protection demo that must run with 'trap'."""
    out: Dict[str, object] = {}
    for key, value in _CONFIG_RE.findall(text):
        try:
            out[key] = int(value, 0)
        except ValueError:
            out[key] = value.lower()
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python preprocessor.py prog.asm", file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        try:
            pre = preprocess(fh.read(), argv[0])
        except assembler.AssemblyFailed as exc:
            print(exc, file=sys.stderr)
            return 1
    print(pre.text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
