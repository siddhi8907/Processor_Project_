"""
vertical_microprogramming.py - Vertical Microcode ROM and Decoder for RISC201
"""
import isa


class MicrocodeError(Exception):
    """Raised for an illegal microinstruction (unknown field value) or an empty ROM address."""

# Opcode -> mnemonic, taken from the shared ISA table (isa.py) so there is only one opcode list.
# Reserved opcodes 23 / 24 are not in it, so they dispatch to the nop microinstruction.
OPCODE_MAP = {op.opcode: op.name for op in isa.OPS_BY_OPCODE.values()}

ALU3_OPS = {'add', 'sub', 'mul', 'div', 'mod', 'and', 'or', 'lsl', 'lsr', 'asr', 'min', 'max'}
UN2_OPS = {'not', 'mov', 'abs', 'neg'}
# instructions that have a floating point version (F = 1). For every other instruction the
# F bit is ignored: the dispatch table sends F = 1 to the same (integer) microinstruction.
FLOAT_ALU3_OPS = {'add', 'sub', 'mul', 'div', 'min', 'max'}
FLOAT_UN2_OPS = {'mov', 'abs', 'neg'}

# Vertical Microinstruction Fields: (ALU_OP, MEM_STACK, BRANCH_SYS, REG_WRITE, SRC_MUX)
#
# ALU_OP is the function the EX stage sends to the ALU:
#   'opc'  -> integer unit, function = the instruction's own opcode (add, sub, ..., not, mov, abs, neg)
#   'fopc' -> floating point unit, function = the opcode (fadd, fsub, fmul, fdiv, fmin, fmax, fmov, ...)
#   'cmp'  -> integer compare (sets flags E / GT)
#   'fcmp' -> floating point compare
#   'i2f'  -> converter, int -> float   (fcvt)
#   'f2i'  -> converter, float -> int   (fcvti)
#   'add'  -> fixed add  (ld/st address = rs1 + imm, pop SP + 4, call link PC + 4)
#   'sub'  -> fixed sub  (push SP - 4)
#   'none' -> ALU not used
# So isFloat comes from the microinstruction, not from decoding the F bit separately.
VERTICAL_ROM = {
    0x10: ('opc',  'none', 'none', 'wb1',  'r_r'),  # Reg-Reg ALU
    0x11: ('opc',  'none', 'none', 'wb1',  'r_i'),  # Reg-Imm ALU
    0x12: ('opc',  'none', 'none', 'wb1',  'n_r'),  # Unary Reg  (not, mov, abs, neg)
    0x13: ('opc',  'none', 'none', 'wb1',  'n_i'),  # Unary Imm
    0x14: ('i2f',  'none', 'none', 'wb1',  'n_r'),  # fcvt rd, rs2
    0x15: ('i2f',  'none', 'none', 'wb1',  'n_i'),  # fcvt with I=1 (not valid assembly; converts 0)
    0x16: ('f2i',  'none', 'none', 'wb1',  'n_r'),  # fcvti rd, rs2
    0x17: ('f2i',  'none', 'none', 'wb1',  'n_i'),  # fcvti with I=1
    0x18: ('fopc', 'none', 'none', 'wb1',  'r_r'),  # Reg-Reg FPU  (fadd, fsub, fmul, fdiv, fmin, fmax)
    0x19: ('fopc', 'none', 'none', 'wb1',  'r_i'),  # Reg-Imm FPU
    0x1A: ('fopc', 'none', 'none', 'wb1',  'n_r'),  # Unary Reg FPU (fmov, fabs, fneg)
    0x1B: ('fopc', 'none', 'none', 'wb1',  'n_i'),  # Unary Imm FPU (fmov rd, 1.5)
    0x20: ('add',  'ld',   'none', 'wb1',  'r_i'),  # ld (src1 + imm)
    0x21: ('add',  'st',   'none', 'none', 'r_rd'), # st (src1 + imm, read rd as src2)
    0x22: ('sub',  'push', 'none', 'wb2',  'sp_r'), # push (SP - 4, read rd as src2)
    0x23: ('add',  'pop',  'none', 'both', 'sp_n'), # pop (SP + 4, write rd and SP)
    0x30: ('cmp',  'none', 'none', 'none', 'r_r'),  # cmp Reg
    0x31: ('cmp',  'none', 'none', 'none', 'r_i'),  # cmp Imm
    0x3A: ('fcmp', 'none', 'none', 'none', 'r_r'),  # fcmp Reg
    0x3B: ('fcmp', 'none', 'none', 'none', 'r_i'),  # fcmp Imm
    0x32: ('none', 'none', 'ubr',  'none', 'n_n'),  # b
    0x33: ('none', 'none', 'beq',  'none', 'n_n'),  # beq
    0x34: ('none', 'none', 'bgt',  'none', 'n_n'),  # bgt
    0x35: ('add',  'none', 'call', 'wb1',  'n_n'),  # call (RA <- PC + 4)
    0x36: ('none', 'none', 'ret',  'none', 'ra_n'), # ret (read RA)
    0x37: ('none', 'none', 'rte',  'none', 'n_n'),  # rte
    0x38: ('none', 'none', 'halt', 'none', 'n_n'),  # halt
    0x00: ('none', 'none', 'none', 'none', 'n_n')   # nop / default
}

def generate_dispatch_table():
    """Maps (opcode_val, I, F) -> ROM Address"""
    dt = {}
    for op_val, op_name in OPCODE_MAP.items():
        for I in (0, 1):
            for F in (0, 1):
                addr = 0x00
                if op_name in ALU3_OPS:
                    if F and op_name in FLOAT_ALU3_OPS: addr = 0x19 if I else 0x18
                    else: addr = 0x11 if I else 0x10
                elif op_name == 'cmp':
                    if F: addr = 0x3B if I else 0x3A
                    else: addr = 0x31 if I else 0x30
                elif op_name in UN2_OPS:
                    if F and op_name in FLOAT_UN2_OPS: addr = 0x1B if I else 0x1A
                    else: addr = 0x13 if I else 0x12
                elif op_name == 'fcvt':
                    if F: addr = 0x17 if I else 0x16
                    else: addr = 0x15 if I else 0x14
                elif op_name == 'ld': addr = 0x20
                elif op_name == 'st': addr = 0x21
                elif op_name == 'push': addr = 0x22
                elif op_name == 'pop': addr = 0x23
                elif op_name == 'b': addr = 0x32
                elif op_name == 'beq': addr = 0x33
                elif op_name == 'bgt': addr = 0x34
                elif op_name == 'call': addr = 0x35
                elif op_name == 'ret': addr = 0x36
                elif op_name == 'rte': addr = 0x37
                elif op_name == 'halt': addr = 0x38
                dt[(op_val, I, F)] = addr
    return dt

DISPATCH_TABLE = generate_dispatch_table()

# Legal values of each encoded field -> field width = ceil(log2(number of values)) bits
FIELD_VALUES = {
    'ALU_OP':     ('none', 'opc', 'fopc', 'add', 'sub', 'cmp', 'fcmp', 'i2f', 'f2i'),
    'MEM_STACK':  ('none', 'ld', 'st', 'push', 'pop'),
    'BRANCH_SYS': ('none', 'ubr', 'beq', 'bgt', 'call', 'ret', 'rte', 'halt'),
    'REG_WRITE':  ('none', 'wb1', 'wb2', 'both'),
    'SRC_MUX':    ('n_n', 'r_r', 'r_i', 'r_rd', 'n_r', 'n_i', 'sp_r', 'sp_n', 'ra_n'),
}
FIELD_NAMES = tuple(FIELD_VALUES)
FIELD_BITS = {f: max(1, (len(v) - 1).bit_length()) for f, v in FIELD_VALUES.items()}
WIDTH = sum(FIELD_BITS.values())                       # 4 + 3 + 3 + 2 + 4 = 16-bit microinstruction

# ALU_OP value -> (ALU function class used by the execute stage, uses the FPU / float input)
ALU_CLASS = {'none': ('none', False), 'opc': ('opc', False), 'fopc': ('opc', True),
             'add': ('add', False), 'sub': ('sub', False), 'cmp': ('cmp', False),
             'fcmp': ('cmp', True), 'i2f': ('cvt', False), 'f2i': ('cvt', True)}


def validate(entry, where=""):
    if len(entry) != len(FIELD_NAMES):
        raise MicrocodeError(f"microinstruction{where} has {len(entry)} fields, expected {len(FIELD_NAMES)}")
    for name, value in zip(FIELD_NAMES, entry):
        if value not in FIELD_VALUES[name]:
            raise MicrocodeError(f"illegal value '{value}' in field {name}{where}")


def encode(entry) -> int:
    """Pack the 5 fields into the 16-bit binary microinstruction stored in the ROM."""
    w = 0
    for name, value in zip(FIELD_NAMES, entry):
        w = (w << FIELD_BITS[name]) | FIELD_VALUES[name].index(value)
    return w


def format_entry(entry) -> str:
    return "|".join(entry) + f"  (0b{encode(entry):0{WIDTH}b})"


for _addr, _entry in VERTICAL_ROM.items():          # check the whole ROM when the module is loaded
    validate(_entry, f" at uPC 0x{_addr:02X}")

def expand_micro_fields(opcode_val: int, I: int, F: int, rd: int, rs1: int, rs2: int):
    """
    Decodes the macro-instruction signature using the Dispatch ROM, fetches
    the vertical fields, and expands them into flat boolean pipeline signals.
    """
    uPC = DISPATCH_TABLE.get((opcode_val, I, F), 0x00)
    if uPC not in VERTICAL_ROM:
        raise MicrocodeError(f"dispatch to empty ROM address 0x{uPC:02X}")
    entry = VERTICAL_ROM[uPC]
    validate(entry, f" at uPC 0x{uPC:02X}")
    alu_f, mem_f, br_f, wb_f, mux_f = entry

    signals = {
        'uPC': uPC,
        'uInstr': format_entry(entry),
        'aluOp': ALU_CLASS[alu_f][0],
        'isFloat': ALU_CLASS[alu_f][1],
        'isfcvt': alu_f in ('i2f', 'f2i'),
        'isLd': (mem_f == 'ld'),
        'isSt': (mem_f == 'st'),
        'isPush': (mem_f == 'push'),
        'isPop': (mem_f == 'pop'),
        'isCall': (br_f == 'call'),
        'isRet': (br_f == 'ret'),
        'isBeq': (br_f == 'beq'),
        'isBgt': (br_f == 'bgt'),
        'isUBranch': (br_f == 'ubr'),          # b only; call is signalled by isCall
        'isCmp': alu_f in ('cmp', 'fcmp'),
        'isRTE': (br_f == 'rte'),
        'isHalt': (br_f == 'halt'),
        'isWb': False,
        'isWb2': False,
        'useSrc1': False,
        'useSrc2': False,
        'src1Addr': 0,
        'src2Addr': 0,
        'writeAddr': 0
    }

    # Expand SRC_MUX field
    if mux_f == 'r_r':
        signals['useSrc1'], signals['src1Addr'] = True, rs1
        signals['useSrc2'], signals['src2Addr'] = True, rs2
    elif mux_f == 'r_i':
        signals['useSrc1'], signals['src1Addr'] = True, rs1
    elif mux_f == 'r_rd':
        signals['useSrc1'], signals['src1Addr'] = True, rs1
        signals['useSrc2'], signals['src2Addr'] = True, rd
    elif mux_f == 'n_r':
        signals['useSrc2'], signals['src2Addr'] = True, rs2
    elif mux_f == 'n_i' or mux_f == 'n_n':
        pass  # No source registers used
    elif mux_f == 'sp_r':
        signals['useSrc1'], signals['src1Addr'] = True, 14  # SP
        signals['useSrc2'], signals['src2Addr'] = True, rd  # Store data from rd
    elif mux_f == 'sp_n':
        signals['useSrc1'], signals['src1Addr'] = True, 14  # SP
    elif mux_f == 'ra_n':
        signals['useSrc1'], signals['src1Addr'] = True, 15  # RA

    # Expand REG_WRITE field
    if wb_f in ('wb1', 'both'):
        signals['isWb'] = True
        signals['writeAddr'] = 15 if br_f == 'call' else rd
    if wb_f in ('wb2', 'both'):
        signals['isWb2'] = True

    return signals
