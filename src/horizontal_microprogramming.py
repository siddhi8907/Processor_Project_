"""
horizontal_microprogramming.py - Horizontal Microcode ROM and Decoder for RISC201

Horizontal = one bit per control signal. The control word read from the ROM *is* the set of
control signals; there are no encoded fields and no field decoders (compare with
vertical_microprogramming.py, whose 5 encoded fields must be decoded into signals).

Same dispatch scheme and the same uPC addresses as the vertical unit, so the two can be compared
one-to-one, and the same function signature (expand_micro_fields) so the simulator can use either.
"""
import isa


class MicrocodeError(Exception):
    """Raised for an illegal control word (e.g. two ALU functions or two src1 sources at once)."""


# ---------------------------------------------------------------- control word layout (MSB first)
SIGNAL_BITS = [
    # ALU function, one-hot (all zero = ALU not used)
    "ALU_OPC",    # use the instruction's own opcode (add, sub, ..., min, max, not, mov, abs, neg)
    "ALU_ADD",    # fixed add: ld/st address, pop SP+4, call link PC+4
    "ALU_SUB",    # fixed sub: push SP-4
    "ALU_CMP",    # compare, sets flags E / GT
    "ALU_CVT",    # int <-> float converter (isfcvt)
    "FLOAT",      # the ALU function works on floats: FPU for OPC/CMP, float -> int for CVT
    # operand select
    "USE_SRC1",   # read src1
    "SRC1_SP",    # src1 = sp (else rs1)
    "SRC1_RA",    # src1 = ra (else rs1)
    "USE_SRC2",   # read src2
    "SRC2_RD",    # src2 = rd (store data) (else rs2)
    # register write
    "WB1",        # write port 1 enable
    "WB1_RA",     # port 1 address = ra (call) (else rd)
    "WB2",        # write port 2 (SP) enable
    # memory / stack
    "LD", "ST", "PUSH", "POP",
    # control flow / system
    "UBR", "BEQ", "BGT", "CALL", "RET", "RTE", "HALT",
]
WIDTH = len(SIGNAL_BITS)                                  # 25-bit control word
BIT = {name: WIDTH - 1 - i for i, name in enumerate(SIGNAL_BITS)}


def word(*names: str) -> int:
    w = 0
    for n in names:
        w |= 1 << BIT[n]
    return w


# ---------------------------------------------------------------- control memory (same addresses as vertical)
CONTROL_MEMORY = {
    0x10: word("ALU_OPC", "USE_SRC1", "USE_SRC2", "WB1"),                         # Reg-Reg ALU
    0x11: word("ALU_OPC", "USE_SRC1", "WB1"),                                     # Reg-Imm ALU
    0x12: word("ALU_OPC", "USE_SRC2", "WB1"),                                     # Unary Reg
    0x13: word("ALU_OPC", "WB1"),                                                 # Unary Imm
    0x14: word("ALU_CVT", "USE_SRC2", "WB1"),                                     # fcvt rd, rs2
    0x15: word("ALU_CVT", "WB1"),                                                 # fcvt with I=1
    0x16: word("ALU_CVT", "FLOAT", "USE_SRC2", "WB1"),                            # fcvti rd, rs2
    0x17: word("ALU_CVT", "FLOAT", "WB1"),                                        # fcvti with I=1
    0x18: word("ALU_OPC", "FLOAT", "USE_SRC1", "USE_SRC2", "WB1"),                # Reg-Reg FPU
    0x19: word("ALU_OPC", "FLOAT", "USE_SRC1", "WB1"),                            # Reg-Imm FPU
    0x1A: word("ALU_OPC", "FLOAT", "USE_SRC2", "WB1"),                            # Unary Reg FPU
    0x1B: word("ALU_OPC", "FLOAT", "WB1"),                                        # Unary Imm FPU
    0x20: word("ALU_ADD", "USE_SRC1", "WB1", "LD"),                               # ld
    0x21: word("ALU_ADD", "USE_SRC1", "USE_SRC2", "SRC2_RD", "ST"),               # st
    0x22: word("ALU_SUB", "USE_SRC1", "SRC1_SP", "USE_SRC2", "SRC2_RD", "WB2", "PUSH"),  # push
    0x23: word("ALU_ADD", "USE_SRC1", "SRC1_SP", "WB1", "WB2", "POP"),           # pop
    0x30: word("ALU_CMP", "USE_SRC1", "USE_SRC2"),                                # cmp reg
    0x31: word("ALU_CMP", "USE_SRC1"),                                            # cmp imm
    0x3A: word("ALU_CMP", "FLOAT", "USE_SRC1", "USE_SRC2"),                       # fcmp reg
    0x3B: word("ALU_CMP", "FLOAT", "USE_SRC1"),                                   # fcmp imm
    0x32: word("UBR"),                                                            # b
    0x33: word("BEQ"),                                                            # beq
    0x34: word("BGT"),                                                            # bgt
    0x35: word("ALU_ADD", "WB1", "WB1_RA", "CALL"),                               # call
    0x36: word("USE_SRC1", "SRC1_RA", "RET"),                                     # ret
    0x37: word("RTE"),                                                            # rte
    0x38: word("HALT"),                                                           # halt
    0x00: 0,                                                                      # nop / reserved
}

OPCODE_MAP = {op.opcode: op.name for op in isa.OPS_BY_OPCODE.values()}
ALU3_OPS = {'add', 'sub', 'mul', 'div', 'mod', 'and', 'or', 'lsl', 'lsr', 'asr', 'min', 'max'}
UN2_OPS = {'not', 'mov', 'abs', 'neg'}
FLOAT_ALU3_OPS = {'add', 'sub', 'mul', 'div', 'min', 'max'}     # F = 1 selects the FPU version
FLOAT_UN2_OPS = {'mov', 'abs', 'neg'}                             # (F is ignored everywhere else)
FIXED = {'ld': 0x20, 'st': 0x21, 'push': 0x22, 'pop': 0x23, 'b': 0x32, 'beq': 0x33, 'bgt': 0x34,
         'call': 0x35, 'ret': 0x36, 'rte': 0x37, 'halt': 0x38}


def generate_dispatch_table():
    """Maps (opcode_val, I, F) -> control memory address"""
    dt = {}
    for op_val, name in OPCODE_MAP.items():
        for I in (0, 1):
            for F in (0, 1):
                if name in ALU3_OPS:
                    fl = F and name in FLOAT_ALU3_OPS
                    addr = (0x19 if I else 0x18) if fl else (0x11 if I else 0x10)
                elif name == 'cmp':
                    addr = (0x3B if I else 0x3A) if F else (0x31 if I else 0x30)
                elif name in UN2_OPS:
                    fl = F and name in FLOAT_UN2_OPS
                    addr = (0x1B if I else 0x1A) if fl else (0x13 if I else 0x12)
                elif name == 'fcvt':
                    addr = (0x17 if I else 0x16) if F else (0x15 if I else 0x14)
                else:
                    addr = FIXED.get(name, 0x00)
                dt[(op_val, I, F)] = addr
    return dt


DISPATCH_TABLE = generate_dispatch_table()


def bits(cw: int, *names: str):
    return [bool(cw >> BIT[n] & 1) for n in names]


def validate(cw: int, where: str = "") -> None:
    """Hardware sanity rules for a control word; raises MicrocodeError if violated."""
    def count(*names):
        return sum(bits(cw, *names))
    problems = []
    if count("ALU_OPC", "ALU_ADD", "ALU_SUB", "ALU_CMP", "ALU_CVT") > 1:
        problems.append("more than one ALU function selected")
    if count("SRC1_SP", "SRC1_RA") > 1:
        problems.append("src1 mux has two sources")
    if count("LD", "ST", "PUSH", "POP") > 1:
        problems.append("more than one memory operation")
    if count("UBR", "BEQ", "BGT", "CALL", "RET", "RTE", "HALT") > 1:
        problems.append("more than one control-flow operation")
    if (bits(cw, "SRC1_SP")[0] or bits(cw, "SRC1_RA")[0]) and not bits(cw, "USE_SRC1")[0]:
        problems.append("src1 selected but USE_SRC1 = 0")
    if bits(cw, "FLOAT")[0] and not count("ALU_OPC", "ALU_CMP", "ALU_CVT"):
        problems.append("FLOAT set without an ALU function that can use it")
    if bits(cw, "WB1_RA")[0] and not bits(cw, "WB1")[0]:
        problems.append("WB1_RA set without WB1")
    if cw >> WIDTH:
        problems.append(f"control word wider than {WIDTH} bits")
    if problems:
        raise MicrocodeError(f"illegal control word {cw:0{WIDTH}b}{where}: " + "; ".join(problems))


for _addr, _cw in CONTROL_MEMORY.items():          # check the whole ROM when the module is loaded
    validate(_cw, f" at uPC 0x{_addr:02X}")


def format_word(cw: int) -> str:
    """'0b1000_0100_1000_0000_0000_0000' style, grouped by 4."""
    s = f"{cw:0{WIDTH}b}"
    return "0b" + "_".join(s[i:i + 4] for i in range(0, WIDTH, 4))


def active_signals(cw: int):
    return [n for n in SIGNAL_BITS if cw >> BIT[n] & 1]


def expand_micro_fields(opcode_val: int, I: int, F: int, rd: int, rs1: int, rs2: int):
    """Dispatch -> read the control word -> each bit IS a control signal (no field decoding)."""
    uPC = DISPATCH_TABLE.get((opcode_val, I, F), 0x00)
    if uPC not in CONTROL_MEMORY:
        raise MicrocodeError(f"dispatch to empty control memory address 0x{uPC:02X}")
    cw = CONTROL_MEMORY[uPC]
    validate(cw, f" at uPC 0x{uPC:02X}")
    b = lambda n: bool(cw >> BIT[n] & 1)

    alu_op = ("opc" if b("ALU_OPC") else "add" if b("ALU_ADD") else "sub" if b("ALU_SUB")
              else "cmp" if b("ALU_CMP") else "cvt" if b("ALU_CVT") else "none")
    use1, use2 = b("USE_SRC1"), b("USE_SRC2")
    return {
        'uPC': uPC,
        'uInstr': format_word(cw),
        'aluOp': alu_op,
        'isFloat': b("FLOAT"),
        'isfcvt': b("ALU_CVT"),
        'isCmp': b("ALU_CMP"),
        'isLd': b("LD"), 'isSt': b("ST"), 'isPush': b("PUSH"), 'isPop': b("POP"),
        'isUBranch': b("UBR"), 'isBeq': b("BEQ"), 'isBgt': b("BGT"),
        'isCall': b("CALL"), 'isRet': b("RET"), 'isRTE': b("RTE"), 'isHalt': b("HALT"),
        'useSrc1': use1,
        'src1Addr': (14 if b("SRC1_SP") else 15 if b("SRC1_RA") else rs1) if use1 else 0,
        'useSrc2': use2,
        'src2Addr': (rd if b("SRC2_RD") else rs2) if use2 else 0,
        'isWb': b("WB1"),
        'writeAddr': (15 if b("WB1_RA") else rd) if b("WB1") else 0,
        'isWb2': b("WB2"),
    }
