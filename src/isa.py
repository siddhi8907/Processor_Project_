#!/usr/bin/env python3
"""
isa.py - shared RISC201 (SimpleRISC Extended) ISA definitions.

assembler.py and disassembler.py both import this module, so the opcode table,
field layout and naming rules live in exactly one place.

32-bit instruction formats (bit 31 = MSB), from SimpleRISC_Extended_ISA.docx:

    Register (I=0)       opcode[31:27] I[26] F[25] rd[24:21] rs1[20:17] rs2[16:13]  [12:0]=0
    Immediate, F=0       opcode[31:27] 1     0     rd        rs1        mod[16:15] imm15[14:0]
    Immediate, F=1       opcode[31:27] 1     1     rd        rs1        imm17[16:0] (top 17 bits of a binary32)
    Branch/call          opcode[31:27] offset[26:0] (signed word offset)
    No operand           opcode[31:27] all other bits 0
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Tuple

# ---------------------------------------------------------------- word geometry
WORD_BITS = 32
WORD_MASK = (1 << WORD_BITS) - 1
INSTR_BYTES = 4

# ---------------------------------------------------------------- registers
NUM_REGS = 16
REG_SP = 14
REG_RA = 15

# ---------------------------------------------------------------- immediate modifiers (I=1, F=0)
MOD_NONE = 0b00      # sign-extend imm15
MOD_U = 0b01         # zero-extend imm15
MOD_H = 0b10         # imm15 << 16
MOD_SUFFIX = {MOD_NONE: "", MOD_U: ".u", MOD_H: ".h"}

# ---------------------------------------------------------------- operand "kinds" (assembly syntax classes)
K_ALU3 = "alu3"      # op rd, rs1, rs2/imm
K_CMP = "cmp"        # cmp rs1, rs2/imm
K_MOV2 = "mov2"      # op rd, rs2/imm         (mov, not)
K_UN2 = "un2"        # op rd, rs2             (abs, neg, fcvt: register only)
K_LDST = "ldst"      # op rd, imm[rs1]
K_BRANCH = "branch"  # op label|offset
K_STACK = "stack"    # op rd                  (push, pop)
K_NONE = "none"      # op                     (nop, ret, halt, rte)

SYNTAX = {
    K_ALU3: "rd, rs1, rs2/imm",
    K_CMP: "rs1, rs2/imm",
    K_MOV2: "rd, rs2/imm",
    K_UN2: "rd, rs2",
    K_LDST: "rd, imm[rs1]",
    K_BRANCH: "label|offset",
    K_STACK: "rd",
    K_NONE: "",
}


@dataclass(frozen=True)
class Op:
    name: str
    opcode: int
    kind: str
    float_ok: bool = False      # True if F=1 gives a float version (fadd, fcmp, ...)


OPS: List[Op] = [
    Op("add", 0b00000, K_ALU3, True),
    Op("sub", 0b00001, K_ALU3, True),
    Op("mul", 0b00010, K_ALU3, True),
    Op("div", 0b00011, K_ALU3, True),
    Op("mod", 0b00100, K_ALU3),
    Op("cmp", 0b00101, K_CMP, True),
    Op("and", 0b00110, K_ALU3),
    Op("or", 0b00111, K_ALU3),
    Op("not", 0b01000, K_MOV2),
    Op("mov", 0b01001, K_MOV2, True),
    Op("lsl", 0b01010, K_ALU3),
    Op("lsr", 0b01011, K_ALU3),
    Op("asr", 0b01100, K_ALU3),
    Op("nop", 0b01101, K_NONE),
    Op("ld", 0b01110, K_LDST),
    Op("st", 0b01111, K_LDST),
    Op("beq", 0b10000, K_BRANCH),
    Op("bgt", 0b10001, K_BRANCH),
    Op("b", 0b10010, K_BRANCH),
    Op("call", 0b10011, K_BRANCH),
    Op("ret", 0b10100, K_NONE),
    Op("push", 0b10101, K_STACK),
    Op("pop", 0b10110, K_STACK),
    # 0b10111 and 0b11000 are free (old lsbase / lslim) -> execute as nop
    Op("halt", 0b11001, K_NONE),
    Op("rte", 0b11010, K_NONE),
    Op("min", 0b11011, K_ALU3, True),
    Op("max", 0b11100, K_ALU3, True),
    Op("abs", 0b11101, K_UN2, True),
    Op("neg", 0b11110, K_UN2, True),
    Op("fcvt", 0b11111, K_UN2),          # F is the direction bit, not "float version"
]

OPS_BY_NAME: Dict[str, Op] = {op.name: op for op in OPS}
OPS_BY_OPCODE: Dict[int, Op] = {op.opcode: op for op in OPS}
RESERVED_OPCODES = (0b10111, 0b11000)

# assembler names for the F=1 versions
FLOAT_NAME_OF: Dict[str, str] = {op.name: "f" + op.name for op in OPS if op.float_ok}
FCVT_TO_INT_NAME = "fcvti"           # fcvt with F=1 (float -> int); plain "fcvt" is int -> float

# ---------------------------------------------------------------- mnemonic table
# mnemonic -> (Op, F, modifier)
_MNEMONICS: Dict[str, Tuple[Op, int, int]] = {}
for _op in OPS:
    _MNEMONICS[_op.name] = (_op, 0, MOD_NONE)
    if _op.float_ok:
        _MNEMONICS[FLOAT_NAME_OF[_op.name]] = (_op, 1, MOD_NONE)
    if _op.kind in (K_ALU3, K_CMP, K_MOV2):
        _MNEMONICS[_op.name + ".u"] = (_op, 0, MOD_U)
        _MNEMONICS[_op.name + ".h"] = (_op, 0, MOD_H)
_MNEMONICS[FCVT_TO_INT_NAME] = (OPS_BY_NAME["fcvt"], 1, MOD_NONE)
_MNEMONICS["movu"] = (OPS_BY_NAME["mov"], 0, MOD_U)      # classic SimpleRISC spellings
_MNEMONICS["movh"] = (OPS_BY_NAME["mov"], 0, MOD_H)

ALL_MNEMONICS: List[str] = sorted(_MNEMONICS)


def resolve_mnemonic(name: str) -> Optional[Tuple[Op, int, int]]:
    """'fadd' -> (Op(add), F=1, MOD_NONE); 'mov.u' -> (Op(mov), 0, MOD_U); unknown -> None."""
    return _MNEMONICS.get(name.strip().lower())


# ---------------------------------------------------------------- registers
_REG_RE = re.compile(r"^r(\d+)$")


def parse_register(tok: str) -> Optional[int]:
    """'r3' -> 3, 'sp' -> 14, 'ra' -> 15. Anything else (including r16+) -> None."""
    t = tok.strip().lower()
    if t == "sp":
        return REG_SP
    if t == "ra":
        return REG_RA
    m = _REG_RE.match(t)
    if m:
        n = int(m.group(1))
        return n if n < NUM_REGS else None
    return None


def reg_name(n: int) -> str:
    return "sp" if n == REG_SP else "ra" if n == REG_RA else f"r{n}"


# ---------------------------------------------------------------- bit helpers
def sext(value: int, bits: int) -> int:
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def float_to_bits32(value: float) -> int:
    """Python float -> IEEE-754 binary32 bit pattern (round to nearest even, overflow -> +/-Inf)."""
    try:
        return struct.unpack(">I", struct.pack(">f", value))[0]
    except OverflowError:
        return 0xFF800000 if value < 0 else 0x7F800000


def bits32_to_float(bits: int) -> float:
    return struct.unpack(">f", struct.pack(">I", bits & WORD_MASK))[0]


# ---------------------------------------------------------------- pack / unpack
class Fields(NamedTuple):
    opcode: int
    i: int
    f: int
    rd: int
    rs1: int
    rs2: int
    mod: int
    imm15: int
    imm17: int
    offset27: int


def unpack_word(word: int) -> Fields:
    w = word & WORD_MASK
    return Fields(
        opcode=(w >> 27) & 0x1F,
        i=(w >> 26) & 1,
        f=(w >> 25) & 1,
        rd=(w >> 21) & 0xF,
        rs1=(w >> 17) & 0xF,
        rs2=(w >> 13) & 0xF,
        mod=(w >> 15) & 0x3,
        imm15=w & 0x7FFF,
        imm17=w & 0x1FFFF,
        offset27=w & 0x7FFFFFF,
    )


def pack_word(opcode: int, i: int = 0, f: int = 0, rd: int = 0, rs1: int = 0, rest: int = 0) -> int:
    """rest = the low 17 bits: rs2<<13, (mod<<15)|imm15, or imm17."""
    return (((opcode & 0x1F) << 27) | ((i & 1) << 26) | ((f & 1) << 25) | ((rd & 0xF) << 21)
            | ((rs1 & 0xF) << 17) | (rest & 0x1FFFF))


def pack_branch(opcode: int, offset: int) -> int:
    return ((opcode & 0x1F) << 27) | (offset & 0x7FFFFFF)


# ---------------------------------------------------------------- text formats for images
def format_hex(word: int) -> str:
    return f"{word & WORD_MASK:08X}"


def format_bin(word: int) -> str:
    return f"{word & WORD_MASK:032b}"


_COMMENT_RE = re.compile(r";|//|#")


def parse_words(text: str) -> List[int]:
    """Parse an image: 8-digit hex, 0xHEX, 0bBIN or 32-char binary words, any number per line.
    Comments start with ; // or #."""
    words: List[int] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = _COMMENT_RE.split(line, maxsplit=1)[0]
        for tok in re.split(r"[\s,]+", line.strip()):
            if not tok:
                continue
            t = tok.replace("_", "")
            try:
                if t[:2].lower() == "0x":
                    val = int(t[2:], 16)
                elif t[:2].lower() == "0b":
                    val = int(t[2:], 2)
                elif len(t) == 32 and set(t) <= {"0", "1"}:
                    val = int(t, 2)
                elif len(t) == 8:
                    val = int(t, 16)
                else:
                    raise ValueError
            except ValueError:
                raise ValueError(f"line {lineno}: '{tok}' is not a 32-bit word "
                                 f"(use 8 hex digits, 0xHEX, 0bBIN or 32 binary digits)") from None
            if not 0 <= val <= WORD_MASK:
                raise ValueError(f"line {lineno}: '{tok}' does not fit in 32 bits")
            words.append(val)
    return words
