"""
RISC-201 Processor Simulation Framework
Module: ALU & FPU Unit (CSC-201 SimpleRISC Extended)
"""

import struct

MASK32 = 0xFFFFFFFF
SIGN32 = 0x80000000


def to_signed(val: int) -> int:
    val &= MASK32
    return val - 0x100000000 if val & SIGN32 else val


def to_unsigned(val: int) -> int:
    return val & MASK32


# -------------------------------------------------------------------------
# 1. ADDER IMPLEMENTATIONS
# -------------------------------------------------------------------------
def ripple_carry_adder_32(a: int, b: int, cin: int = 0):
    a &= MASK32
    b &= MASK32
    s = 0
    c = cin
    for i in range(32):
        ai = (a >> i) & 1
        bi = (b >> i) & 1
        sum_bit = ai ^ bi ^ c
        c = (ai & bi) | (bi & c) | (ai & c)
        s |= (sum_bit << i)
    return s & MASK32, c


def kogge_stone_adder_32(a: int, b: int, cin: int = 0):
    """
    32-bit Kogge-Stone logarithmic prefix network (5 levels).
    Returns (sum_32, cout, overflow_flag).
    """
    a &= MASK32
    b &= MASK32

    # Level 0: Bitwise generate and propagate
    g = [((a >> i) & 1) & ((b >> i) & 1) for i in range(32)]
    p = [((a >> i) & 1) ^ ((b >> i) & 1) for i in range(32)]

    # Incorporate Cin into bit 0
    g[0] = g[0] | (p[0] & cin)
    p[0] = p[0] & (~cin & 1)

    # Prefix stages: offsets 1, 2, 4, 8, 16
    for step in [1, 2, 4, 8, 16]:
        next_g = list(g)
        next_p = list(p)
        for i in range(step, 32):
            next_g[i] = g[i] | (p[i] & g[i - step])
            next_p[i] = p[i] & p[i - step]
        g, p = next_g, next_p

    # Compute carries
    c = [cin] + [g[i] for i in range(31)]
    cout = g[31]

    # Final sum
    raw_p = [((a >> i) & 1) ^ ((b >> i) & 1) for i in range(32)]
    s = 0
    for i in range(32):
        s |= ((raw_p[i] ^ c[i]) << i)

    # V = Overflow flag for signed addition
    c31 = c[31]
    v = cout ^ c31
    return s & MASK32, cout, v


# -------------------------------------------------------------------------
# 2. MULTIPLIER (Booth Radix-4)
# -------------------------------------------------------------------------
def _count(steps, key, n=1):
    if steps is not None:
        steps[key] = steps.get(key, 0) + n


def booth_radix4_mul_32(a: int, b: int, steps=None):
    """
    Radix-4 Booth's Multiplier (16 steps).
    Computes low 32 bits (identical for signed & unsigned).
    steps (optional dict) counts the partial products that needed an add/subtract.
    """
    m = to_signed(a)
    r = to_signed(b) & MASK32
    # Extend with phantom bit b_{-1} = 0
    padded_r = (r << 1)
    acc = 0

    for i in range(16):
        triplet = (padded_r >> (2 * i)) & 0b111
        if triplet in (0b001, 0b010):
            d = 1
        elif triplet == 0b011:
            d = 2
        elif triplet == 0b100:
            d = -2
        elif triplet in (0b101, 0b110):
            d = -1
        else:
            d = 0

        if d:
            _count(steps, "mul_addsub")
        acc += (d * m) << (2 * i)

    return acc & MASK32


def shift_add_mul_32(a: int, b: int, steps=None):
    """
    Plain shift-and-add multiplier (32 steps, one per multiplier bit), for comparison with Booth.
    Low 32 bits are the same for signed and unsigned operands, so no sign handling is needed.
    """
    a &= MASK32
    b &= MASK32
    acc = 0
    for i in range(32):
        if (b >> i) & 1:
            _count(steps, "mul_addsub")
            acc = (acc + (a << i)) & MASK32
    return acc


# -------------------------------------------------------------------------
# 3. DIVIDER (Non-Restoring Division)
# -------------------------------------------------------------------------
def non_restoring_div_mod_32(dividend: int, divisor: int, steps=None):
    """
    32-step Non-Restoring Divider returning (quotient, remainder).
    steps (optional dict) counts the add/subtract operations.
    """
    if divisor == 0:
        return MASK32, dividend & MASK32  # Spec: div -> 0xFFFFFFFF, mod -> dividend

    # Handle INT_MIN / -1 overflow edge case
    if dividend == 0x80000000 and divisor == MASK32:
        return 0x80000000, 0

    sign_d = -1 if (dividend & SIGN32) else 1
    sign_v = -1 if (divisor & SIGN32) else 1

    d_mag = abs(to_signed(dividend))
    v_mag = abs(to_signed(divisor))

    # 32-bit register [A, Q]
    A = 0
    Q = d_mag

    for _ in range(32):
        # Shift [A, Q] left by 1
        A = ((A << 1) | ((Q >> 31) & 1)) & 0x1FFFFFFFF
        Q = (Q << 1) & MASK32

        if A & 0x100000000:  # A < 0
            A = (A + v_mag) & 0x1FFFFFFFF
        else:  # A >= 0
            A = (A - v_mag) & 0x1FFFFFFFF
        _count(steps, "div_addsub")

        if not (A & 0x100000000):  # A >= 0
            Q |= 1

    # Final correction step
    if A & 0x100000000:
        A = (A + v_mag) & 0x1FFFFFFFF
        _count(steps, "div_addsub")

    quotient = Q
    remainder = A & MASK32

    # Fix signs
    if sign_d != sign_v:
        quotient = -quotient
    if sign_d < 0:
        remainder = -remainder

    return quotient & MASK32, remainder & MASK32


def restoring_div_mod_32(dividend: int, divisor: int, steps=None):
    """
    32-step Restoring Divider, for comparison with the non-restoring one.
    Every step subtracts the divisor; when the result goes negative the divisor is added back
    ("restored"), so a step can need two operations instead of one.
    """
    if divisor == 0:
        return MASK32, dividend & MASK32  # same rules as the non-restoring divider
    if dividend == 0x80000000 and divisor == MASK32:
        return 0x80000000, 0

    sign_d = -1 if (dividend & SIGN32) else 1
    sign_v = -1 if (divisor & SIGN32) else 1
    d_mag = abs(to_signed(dividend))
    v_mag = abs(to_signed(divisor))

    R = 0
    Q = d_mag
    for _ in range(32):
        R = (R << 1) | ((Q >> 31) & 1)
        Q = (Q << 1) & MASK32
        R -= v_mag
        _count(steps, "div_addsub")
        if R < 0:
            R += v_mag                   # restore
            _count(steps, "div_addsub")
        else:
            Q |= 1

    quotient, remainder = Q, R
    if sign_d != sign_v:
        quotient = -quotient
    if sign_d < 0:
        remainder = -remainder
    return quotient & MASK32, remainder & MASK32


MULTIPLIERS = {"booth": booth_radix4_mul_32, "shiftadd": shift_add_mul_32}
DIVIDERS = {"nonrestoring": non_restoring_div_mod_32, "restoring": restoring_div_mod_32}


# -------------------------------------------------------------------------
# 4. BARREL SHIFTER
# -------------------------------------------------------------------------
def barrel_shift_32(val: int, amount: int, mode: str = "lsl") -> int:
    val &= MASK32
    shamt = amount & 0x1F  # Only low 5 bits used
    if mode == "lsl":
        return (val << shamt) & MASK32
    elif mode == "lsr":
        return (val >> shamt) & MASK32
    elif mode == "asr":
        signed_val = to_signed(val)
        return (signed_val >> shamt) & MASK32
    return val


# -------------------------------------------------------------------------
# 5. FLOATING POINT UNIT (IEEE-754 Single Precision / binary32)
# -------------------------------------------------------------------------
# The FPU works on the bit fields of the operands, step by step as the hardware would:
#     unpack  -> sign, 8-bit exponent, 24-bit significand (hidden 1 made explicit)
#     operate -> align + add/subtract, multiply, or divide the significands
#     normalize, then round to nearest even with guard / round / sticky bits
#     pack    -> sign | exponent | 23-bit fraction
# The significand arithmetic uses the ALU's own units: every significand add/subtract goes
# through the selected adder (Kogge-Stone or ripple-carry), the 24 x 24 significand product is
# built by the selected multiplier algorithm (Booth radix-4 or shift-and-add) and the quotient
# by the selected divider algorithm (non-restoring or restoring). So --adder, --mul and --div
# change how a float result is produced; the result itself is the same for all of them.
# Exponents are small 8/10-bit numbers and are handled with plain integer arithmetic.
#
# Rules from the spec: subnormal inputs and results are flushed to a signed zero, every NaN
# result is the canonical quiet NaN 0x7FC00000, overflow gives +/-Inf, x/0 gives Inf with
# sign = sign(x) XOR sign(divisor).

CANONICAL_NAN = 0x7FC00000
POS_INF, NEG_INF = 0x7F800000, 0xFF800000
MASK64 = (1 << 64) - 1
EXP_BIAS = 127
HIDDEN = 1 << 23            # the implicit leading 1 of a normal number
FRAC_MASK = HIDDEN - 1
QUOTIENT_BITS = 27          # 24 significand bits + guard + round + one spare for ma >= mb


def _is_subnormal(bits: int) -> bool:
    return ((bits >> 23) & 0xFF) == 0 and (bits & 0x7FFFFF) != 0


# Conversions between the 32-bit pattern and a Python float. The FPU datapath below does NOT
# use these; they are only for printing values (debugger, tests).
def bits_to_float(b: int) -> float:
    b &= MASK32
    if _is_subnormal(b):            # flush-to-zero, as the FPU does
        b &= SIGN32
    return struct.unpack('>f', struct.pack('>I', b))[0]


def float_to_bits(f: float) -> int:
    if f != f:
        return CANONICAL_NAN
    try:
        bits = struct.unpack('>I', struct.pack('>f', f))[0]
    except OverflowError:
        return NEG_INF if f < 0 else POS_INF
    if _is_subnormal(bits):
        bits &= SIGN32
    return bits


def unpack_float(bits: int):
    """Split a binary32 pattern into (sign, exponent field, significand).
    Normal numbers get the hidden 1 (significand 1.f as a 24-bit integer). Exponent 0 is zero:
    a subnormal is flushed, so its fraction is ignored. Exponent 255 is Inf (fraction 0) or NaN."""
    bits &= MASK32
    sign = bits >> 31
    exp = (bits >> 23) & 0xFF
    frac = bits & FRAC_MASK
    if exp == 0:
        return sign, 0, 0
    if exp == 0xFF:
        return sign, 0xFF, frac
    return sign, exp, HIDDEN | frac


def _shr_sticky(m: int, n: int) -> int:
    """Shift right by n; if any 1 is shifted out, set bit 0 (the sticky bit)."""
    if n <= 0:
        return m
    lost = m & ((1 << n) - 1)
    return (m >> n) | (1 if lost else 0)


class FloatUnit:
    """IEEE-754 binary32 unit. It borrows the adder, multiplier and divider choice of the ALU
    it belongs to; FloatUnit() on its own uses the default algorithms."""

    def __init__(self, alu=None):
        self.alu = alu if alu is not None else RISC201_ALU()

    @staticmethod
    def is_nan(bits: int) -> bool:
        exp = (bits >> 23) & 0xFF
        frac = bits & 0x7FFFFF
        return exp == 0xFF and frac != 0

    # ---------------------------------------------------------------- building blocks
    def _add64(self, a: int, b: int, cin: int = 0) -> int:
        """64-bit add made of two passes through the 32-bit adder, carry chained."""
        lo, c, _ = self.alu._add(a & MASK32, b & MASK32, cin)
        hi, _, _ = self.alu._add((a >> 32) & MASK32, (b >> 32) & MASK32, c)
        return (hi << 32) | lo

    def _round_pack(self, sign: int, m: int, scale: int) -> int:
        """Pack the value  (-1)^sign * m * 2^scale  as binary32 (m is an unsigned integer that
        may carry extra low bits, bit 0 possibly a sticky bit)."""
        if m == 0:
            return sign << 31
        # Normalize: leading-one detector, then shift so the leading 1 is at bit 26.
        # Bits 26..3 are then the 24-bit significand, bit 2 = guard, bit 1 = round, bit 0 = sticky.
        lead = m.bit_length() - 1
        exp = scale + lead + EXP_BIAS          # biased exponent of the result
        if lead > 26:
            m = _shr_sticky(m, lead - 26)
        else:
            m <<= 26 - lead
        if exp <= 0:
            # Below the normal range: line the bits up as a subnormal would be (exponent 1,
            # no hidden 1), so the rounding below happens at the subnormal precision.
            m = _shr_sticky(m, 1 - exp)
            exp = 0
        guard, rnd, sticky = (m >> 2) & 1, (m >> 1) & 1, m & 1
        sig = m >> 3
        if guard and (rnd or sticky or (sig & 1)):      # round to nearest, ties to even
            sig, _, _ = self.alu._add(sig, 1)
        if exp == 0:
            # Rounded up to 2^-126 -> the smallest normal number; anything else is a subnormal
            # result, flushed to a signed zero.
            return (sign << 31) | HIDDEN if sig >= HIDDEN else sign << 31
        if sig == 1 << 24:                     # rounding carried out: 10.000... -> 1.000... x 2
            sig >>= 1
            exp += 1
        if exp >= 0xFF:
            return NEG_INF if sign else POS_INF
        return (sign << 31) | (exp << 23) | (sig & FRAC_MASK)

    def _sig_mul(self, ma: int, mb: int) -> int:
        """48-bit product of two 24-bit significands with the ALU's multiplier algorithm."""
        steps = self.alu.steps
        acc = 0
        if self.alu.mul_mode == "booth":
            # Radix-4 Booth: mb is unsigned, so it is read as a 26-bit signed number (top bits 0),
            # giving 13 digits in {-2..2}. Negative partial products are added as ~pp + 1.
            padded = mb << 1                   # phantom bit b(-1) = 0
            for i in range(13):
                triplet = (padded >> (2 * i)) & 0b111
                d = (0, 1, 1, 2, -2, -1, -1, 0)[triplet]
                if d == 0:
                    continue
                pp = (ma << 1 if abs(d) == 2 else ma) << (2 * i)
                if d > 0:
                    acc = self._add64(acc, pp)
                else:
                    acc = self._add64(acc, (~pp) & MASK64, 1)
                _count(steps, "fmul_addsub")
        else:
            # Shift-and-add: one partial product per 1 bit of the multiplier.
            for i in range(24):
                if (mb >> i) & 1:
                    acc = self._add64(acc, ma << i)
                    _count(steps, "fmul_addsub")
        return acc & ((1 << 48) - 1)

    def _sig_div(self, ma: int, mb: int):
        """Quotient bits of ma / mb: returns (Q, sticky) with Q = floor(ma * 2^26 / mb) and
        sticky = 1 when the remainder is not zero. Uses the ALU's divider algorithm."""
        steps = self.alu.steps
        q = 0
        if self.alu.div_mode == "restoring":
            r = ma
            for i in range(QUOTIENT_BITS):
                t, _, _ = self.alu._sub(r, mb)
                _count(steps, "fdiv_addsub")
                if t & SIGN32:                 # negative: keep the old remainder (restore)
                    q <<= 1
                    _count(steps, "fdiv_addsub")
                else:
                    q = (q << 1) | 1
                    r = t
                if i != QUOTIENT_BITS - 1:
                    r <<= 1
            rem = r
        else:
            # Non-restoring: the partial remainder may go negative; the next step then adds the
            # divisor instead of subtracting it, so every step is a single add or subtract.
            p, _, _ = self.alu._sub(ma, mb)
            _count(steps, "fdiv_addsub")
            for i in range(QUOTIENT_BITS):
                bit = 0 if p & SIGN32 else 1
                q = (q << 1) | bit
                if i == QUOTIENT_BITS - 1:
                    break
                p2 = (p << 1) & MASK32
                p, _, _ = self.alu._sub(p2, mb) if bit else self.alu._add(p2, mb)
                _count(steps, "fdiv_addsub")
            if p & SIGN32:                     # final correction of the remainder
                p, _, _ = self.alu._add(p, mb)
                _count(steps, "fdiv_addsub")
            rem = p
        return q, (1 if rem else 0)

    # ---------------------------------------------------------------- arithmetic
    def fadd(self, a_bits: int, b_bits: int) -> int:
        sa, ea, ma = unpack_float(a_bits)
        sb, eb, mb = unpack_float(b_bits)
        if (ea == 0xFF and ma) or (eb == 0xFF and mb):
            return CANONICAL_NAN
        if ea == 0xFF or eb == 0xFF:
            if ea == 0xFF and eb == 0xFF and sa != sb:
                return CANONICAL_NAN           # Inf - Inf
            return (NEG_INF if sa else POS_INF) if ea == 0xFF else (NEG_INF if sb else POS_INF)
        if ma == 0 and mb == 0:
            return (sa & sb) << 31             # +0 + -0 = +0, -0 + -0 = -0
        if mb == 0:
            return (sa << 31) | (a_bits & 0x7FFFFFFF)
        if ma == 0:
            return (sb << 31) | (b_bits & 0x7FFFFFFF)

        # Put the operand with the larger magnitude first (exponent, then significand).
        if (ea, ma) < (eb, mb):
            sa, ea, ma, sb, eb, mb = sb, eb, mb, sa, ea, ma
        # Three extra low bits (guard, round, sticky); align the smaller one to the larger.
        ma3 = ma << 3
        mb3 = _shr_sticky(mb << 3, ea - eb)
        if sa == sb:
            s, _, _ = self.alu._add(ma3, mb3)
        else:
            s, _, _ = self.alu._sub(ma3, mb3)  # never negative: |a| >= |b|
        _count(self.alu.steps, "fadd_addsub")
        if s == 0:
            return 0                           # exact cancellation gives +0
        # ma3 is ma * 2^3, and a = ma * 2^(ea - 127 - 23), so s is in units of 2^(ea - 153).
        return self._round_pack(sa, s, ea - 153)

    def fsub(self, a_bits: int, b_bits: int) -> int:
        return self.fadd(a_bits, (b_bits ^ SIGN32) & MASK32)

    def fmul(self, a_bits: int, b_bits: int) -> int:
        sa, ea, ma = unpack_float(a_bits)
        sb, eb, mb = unpack_float(b_bits)
        sign = sa ^ sb
        if (ea == 0xFF and ma) or (eb == 0xFF and mb):
            return CANONICAL_NAN
        if ea == 0xFF or eb == 0xFF:
            if ea == 0 or eb == 0:
                return CANONICAL_NAN           # Inf x 0
            return NEG_INF if sign else POS_INF
        if ea == 0 or eb == 0:
            return sign << 31
        p = self._sig_mul(ma, mb)
        # Each significand is in units of 2^(e - 150), so the product is in units of 2^(ea + eb - 300).
        return self._round_pack(sign, p, ea + eb - 300)

    def fdiv(self, a_bits: int, b_bits: int) -> int:
        sa, ea, ma = unpack_float(a_bits)
        sb, eb, mb = unpack_float(b_bits)
        sign = sa ^ sb
        if (ea == 0xFF and ma) or (eb == 0xFF and mb):
            return CANONICAL_NAN
        a_inf, b_inf = ea == 0xFF, eb == 0xFF
        a_zero, b_zero = ea == 0, eb == 0
        if a_inf:
            return CANONICAL_NAN if b_inf else (NEG_INF if sign else POS_INF)
        if b_inf:
            return sign << 31                  # x / Inf = 0
        if b_zero:
            return CANONICAL_NAN if a_zero else (NEG_INF if sign else POS_INF)
        if a_zero:
            return sign << 31
        q, sticky = self._sig_div(ma, mb)
        # q = floor(ma / mb * 2^26), so (q << 1 | sticky) is ma / mb in units of 2^-27.
        return self._round_pack(sign, (q << 1) | sticky, ea - eb - QUOTIENT_BITS)

    # ---------------------------------------------------------------- compare / min / max
    @staticmethod
    def _order_key(bits: int) -> int:
        """Integer with the same order as the float value (a sign-magnitude comparator).
        A subnormal counts as zero, so +0, -0 and subnormals are all equal."""
        mag = bits & 0x7FFFFFFF
        if (mag >> 23) == 0:
            mag = 0
        return -mag if bits & SIGN32 else mag

    def fcmp(self, a_bits: int, b_bits: int):
        if self.is_nan(a_bits) or self.is_nan(b_bits):
            return 0, 0                        # unordered: E = 0, GT = 0
        ka, kb = self._order_key(a_bits), self._order_key(b_bits)
        return (1 if ka == kb else 0), (1 if ka > kb else 0)

    def fmin(self, a_bits: int, b_bits: int) -> int:
        if self.is_nan(a_bits): return b_bits
        if self.is_nan(b_bits): return a_bits
        return a_bits if self._order_key(a_bits) < self._order_key(b_bits) else b_bits

    def fmax(self, a_bits: int, b_bits: int) -> int:
        if self.is_nan(a_bits): return b_bits
        if self.is_nan(b_bits): return a_bits
        return a_bits if self._order_key(a_bits) > self._order_key(b_bits) else b_bits

    @staticmethod
    def fabs(bits: int) -> int:
        return bits & 0x7FFFFFFF               # clear the sign bit

    @staticmethod
    def fneg(bits: int) -> int:
        return (bits ^ SIGN32) & MASK32        # flip the sign bit

    # ---------------------------------------------------------------- conversions (fcvt)
    def int_to_float(self, value: int) -> int:
        """Signed 32-bit integer -> binary32, round to nearest even."""
        value &= MASK32
        sign = value >> 31
        mag = self.alu._sub(0, value)[0] if sign else value     # |INT_MIN| = 2^31 still fits
        return self._round_pack(sign, mag, 0)

    def float_to_int(self, bits: int) -> int:
        """binary32 -> signed 32-bit integer, truncating toward zero. NaN gives 0; values
        outside the int range (and +/-Inf) saturate to INT_MAX / INT_MIN."""
        sign, exp, sig = unpack_float(bits)
        if exp == 0xFF and sig:
            return 0
        if exp == 0:
            return 0
        e = exp - EXP_BIAS                     # value = 1.f x 2^e
        if exp == 0xFF or e >= 31:
            return 0x80000000 if sign else 0x7FFFFFFF
        if e < 0:
            return 0                           # |value| < 1
        if e <= 23:
            mag = barrel_shift_32(sig, 23 - e, "lsr")    # drop the fraction bits
        else:
            mag = barrel_shift_32(sig, e - 23, "lsl")
        return self.alu._sub(0, mag)[0] if sign else mag


# -------------------------------------------------------------------------
# 6. MAIN RISC-201 ALU CLASS
# -------------------------------------------------------------------------
class RISC201_ALU:
    def __init__(self, adder_mode="kogge", mul_mode="booth", div_mode="nonrestoring"):
        if adder_mode not in ("kogge", "ripple"):
            raise ValueError(f"adder_mode must be kogge or ripple, not {adder_mode!r}")
        if mul_mode not in MULTIPLIERS:
            raise ValueError(f"mul_mode must be one of {sorted(MULTIPLIERS)}, not {mul_mode!r}")
        if div_mode not in DIVIDERS:
            raise ValueError(f"div_mode must be one of {sorted(DIVIDERS)}, not {div_mode!r}")
        self.adder_mode = adder_mode
        self.mul_mode = mul_mode
        self.div_mode = div_mode
        self._mul = MULTIPLIERS[mul_mode]
        self._div = DIVIDERS[div_mode]
        self.steps = {}          # add/sub operations done inside the multiplier, divider and FPU
        self.fpu = FloatUnit(self)

    def _add(self, a: int, b: int, cin: int = 0):
        if self.adder_mode == "kogge":
            return kogge_stone_adder_32(a, b, cin)
        s, cout = ripple_carry_adder_32(a, b, cin)
        v = (((a ^ s) & (b ^ s)) >> 31) & 1
        return s, cout, v

    def _sub(self, a: int, b: int):
        # A - B = A + (~B) + 1
        return self._add(a, (~b) & MASK32, 1)

    def execute(self, opcode: int, op1: int, op2: int, is_float: bool = False, is_fcvt: bool = False):
        """
        Executes an ALU operation.
        Returns: (result_32bit, flag_E, flag_GT, latency_cycles)
        """
        op1 &= MASK32
        op2 &= MASK32
        res = 0
        flag_e = 0
        flag_gt = 0
        latency = 1

        # 1. Type Conversion instruction (fcvt, opcode 11111)
        if is_fcvt or opcode == 0b11111:
            latency = 2
            if not is_float:
                res = self.fpu.int_to_float(op2)     # F = 0: int -> float, round to nearest even
            else:
                res = self.fpu.float_to_int(op2)     # F = 1: float -> int, truncate, saturate
            return res, 0, 0, latency

        # 2. Floating-Point Operations (is_float == True)
        if is_float:
            if opcode == 0b00000:    # fadd
                return self.fpu.fadd(op1, op2), 0, 0, 3
            elif opcode == 0b00001:  # fsub
                return self.fpu.fsub(op1, op2), 0, 0, 3
            elif opcode == 0b00010:  # fmul
                return self.fpu.fmul(op1, op2), 0, 0, 3
            elif opcode == 0b00011:  # fdiv
                return self.fpu.fdiv(op1, op2), 0, 0, 10
            elif opcode == 0b00101:  # fcmp
                fe, fgt = self.fpu.fcmp(op1, op2)
                return 0, fe, fgt, 1
            elif opcode == 0b01001:  # mov (float immediate / copy)
                return op2, 0, 0, 1
            elif opcode == 0b11011:  # fmin
                return self.fpu.fmin(op1, op2), 0, 0, 1
            elif opcode == 0b11100:  # fmax
                return self.fpu.fmax(op1, op2), 0, 0, 1
            elif opcode == 0b11101:  # fabs
                return self.fpu.fabs(op2), 0, 0, 1
            elif opcode == 0b11110:  # fneg
                return self.fpu.fneg(op2), 0, 0, 1

        # 3. Integer Operations (is_float == False)
        if opcode == 0b00000:    # add / ld / st / push / pop address calc
            res, _, _ = self._add(op1, op2)
            latency = 1

        elif opcode == 0b00001:  # sub
            res, _, _ = self._sub(op1, op2)
            latency = 1

        elif opcode == 0b00010:  # mul
            res = self._mul(op1, op2, self.steps)
            latency = 2

        elif opcode == 0b00011:  # div
            res, _ = self._div(op1, op2, self.steps)
            latency = 8

        elif opcode == 0b00100:  # mod
            _, res = self._div(op1, op2, self.steps)
            latency = 8

        elif opcode == 0b00101:  # cmp
            diff, _, v = self._sub(op1, op2)
            n = (diff >> 31) & 1
            is_less = n ^ v
            flag_e = 1 if diff == 0 else 0
            flag_gt = 1 if (diff != 0 and not is_less) else 0
            res = 0
            latency = 1

        elif opcode == 0b00110:  # and
            res = op1 & op2
        elif opcode == 0b00111:  # or
            res = op1 | op2
        elif opcode == 0b01000:  # not
            res = (~op2) & MASK32
        elif opcode == 0b01001:  # mov
            res = op2
        elif opcode == 0b01010:  # lsl
            res = barrel_shift_32(op1, op2, "lsl")
        elif opcode == 0b01011:  # lsr
            res = barrel_shift_32(op1, op2, "lsr")
        elif opcode == 0b01100:  # asr
            res = barrel_shift_32(op1, op2, "asr")

        elif opcode == 0b11011:  # min (signed)
            s1, s2 = to_signed(op1), to_signed(op2)
            res = op1 if s1 < s2 else op2

        elif opcode == 0b11100:  # max (signed)
            s1, s2 = to_signed(op1), to_signed(op2)
            res = op1 if s1 > s2 else op2

        elif opcode == 0b11101:  # abs
            res = 0x80000000 if op2 == 0x80000000 else (abs(to_signed(op2)) & MASK32)

        elif opcode == 0b11110:  # neg
            res, _, _ = self._sub(0, op2)

        return res & MASK32, flag_e, flag_gt, latency