# RISC201 evaluation report

This file is created automatically by `python evaluate.py`. Every program in `programs/` is run 12 different ways (3 processor models x 2 control units x 2 adders) and the result is compared with the answer written in the program's `; EXPECT` lines.

It answers four questions:

1. Do all programs give the right answer?
2. Which is faster, the 4-stage or the 6-stage pipeline?
3. Does the vertical or horizontal control unit change anything?
4. Which algorithms does the ALU use?

**Words used below**

| word | meaning |
|---|---|
| cycle | one tick of the processor clock |
| CPI | cycles per instruction. Lower is better, 1.0 is the best a pipeline can do |
| stall | the pipeline waits one or more cycles, for example for a slow multiply or divide |
| wasted slot | an empty pipeline slot after a taken branch, because the instructions fetched behind it were wrong |
| load-use wait | an instruction needs a value that a load (`ld` / `pop`) has not delivered yet |
| vertical / horizontal | the two control-unit designs. Vertical stores short encoded control words, horizontal stores one bit per signal |

## 1. Do all programs give the right answer?

Each cell is `pass` when the program ended with exactly the values its `; EXPECT` lines promise. A cell covers both adders.

| program | functional + vertical | functional + horizontal | 4stage + vertical | 4stage + horizontal | 6stage + vertical | 6stage + horizontal |
|---|---|---|---|---|---|---|
| 01_factorial.asm | pass | pass | pass | pass | pass | pass |
| 02_fibonacci_recursive.asm | pass | pass | pass | pass | pass | pass |
| 03_bubble_sort.asm | pass | pass | pass | pass | pass | pass |
| 04_gcd.asm | pass | pass | pass | pass | pass | pass |
| 05_float_newton_sqrt.asm | pass | pass | pass | pass | pass | pass |
| 06_hazards.asm | pass | pass | pass | pass | pass | pass |
| 07_macros.asm | pass | pass | pass | pass | pass | pass |
| 08_stack_overflow.asm | pass | pass | pass | pass | pass | pass |
| 09_exception_rte.asm | pass | pass | pass | pass | pass | pass |
| 10_stack_protection.asm | pass | pass | pass | pass | pass | pass |
| 11_all_units_demo.asm | pass | pass | pass | pass | pass | pass |
| 12_stack_underflow_recover.asm | pass | pass | pass | pass | pass | pass |
| 13_overflow_survive.asm | pass | pass | pass | pass | pass | pass |
| 14_int_overflow_silent.asm | pass | pass | pass | pass | pass | pass |
| 15_float_overflow_underflow.asm | pass | pass | pass | pass | pass | pass |
| 16_unbalanced_stack.asm | pass | pass | pass | pass | pass | pass |
| 17_exception_suite.asm | pass | pass | pass | pass | pass | pass |

Number of failures: **0**. All 12 ways of running a program also ended in exactly the same final state (registers, memory, flags, exception registers and order of instructions), so the pipeline and control unit choices never change the answer, only the speed.

## 2. Which is faster, the 4-stage or the 6-stage pipeline?

Both were run with the vertical control unit and the Kogge-Stone adder. The number of instructions is the same for both.

| program | instructions | 4-stage cycles | 4-stage CPI | 6-stage cycles | 6-stage CPI | stall cycles, slow units (4 / 6) | load-use waits (6-stage) | taken branches | wasted slots after branches (4 / 6) |
|---|---|---|---|---|---|---|---|---|---|
| 01_factorial.asm | 39 | 67 | 1.718 | 77 | 1.974 | 9 / 9 | 0 | 8 | 16 / 24 |
| 02_fibonacci_recursive.asm | 1858 | 2745 | 1.477 | 3366 | 1.812 | 0 / 0 | 177 | 442 | 884 / 1326 |
| 03_bubble_sort.asm | 350 | 463 | 1.323 | 548 | 1.566 | 0 / 0 | 28 | 55 | 110 / 165 |
| 04_gcd.asm | 24 | 56 | 2.333 | 62 | 2.583 | 21 / 21 | 0 | 4 | 8 / 12 |
| 05_float_newton_sqrt.asm | 44 | 129 | 2.932 | 135 | 3.068 | 74 / 74 | 0 | 4 | 8 / 12 |
| 06_hazards.asm | 16 | 34 | 2.125 | 38 | 2.375 | 15 / 15 | 2 | 0 | 0 / 0 |
| 07_macros.asm | 78 | 105 | 1.346 | 119 | 1.526 | 0 / 0 | 0 | 12 | 24 / 36 |
| 08_stack_overflow.asm | 197 | 333 | 1.69 | 401 | 2.036 | 0 / 0 | 0 | 65 | 132 / 198 |
| 09_exception_rte.asm | 8 | 15 | 1.875 | 18 | 2.25 | 0 / 0 | 0 | 0 | 2 / 3 |
| 10_stack_protection.asm | 4 | 10 | 2.5 | 13 | 3.25 | 0 / 0 | 0 | 0 | 2 / 3 |
| 11_all_units_demo.asm | 32 | 60 | 1.875 | 68 | 2.125 | 15 / 15 | 1 | 5 | 10 / 15 |
| 12_stack_underflow_recover.asm | 7 | 14 | 2.0 | 17 | 2.429 | 0 / 0 | 0 | 0 | 2 / 3 |
| 13_overflow_survive.asm | 200 | 338 | 1.69 | 407 | 2.035 | 0 / 0 | 0 | 66 | 134 / 201 |
| 14_int_overflow_silent.asm | 24 | 52 | 2.167 | 56 | 2.333 | 21 / 21 | 0 | 2 | 4 / 6 |
| 15_float_overflow_underflow.asm | 10 | 36 | 3.6 | 38 | 3.8 | 23 / 23 | 0 | 0 | 0 / 0 |
| 16_unbalanced_stack.asm | 36 | 69 | 1.917 | 86 | 2.389 | 0 / 0 | 0 | 15 | 30 / 45 |
| 17_exception_suite.asm | 326 | 561 | 1.721 | 656 | 2.012 | 44 / 44 | 2 | 87 | 182 / 273 |
| **all programs** | 3253 | 5087 | 1.564 | 6105 | 1.877 | | | | |

**What this shows**

* The 6-stage pipeline needs **20.0% more cycles** than the 4-stage one (CPI 1.877 compared with 1.564).
* Reason 1: a taken branch, call or return wastes 3 slots in the 6-stage pipeline but only 2 in the 4-stage one, because the 6-stage pipeline finds out where to jump one stage later.
* Reason 2: in the 6-stage pipeline a value read by `ld` or `pop` is not ready for the very next instruction, so it waits 1 cycle. The 4-stage pipeline reads memory inside its execute stage and never waits.
* Slow units (multiply 2 cycles, divide 8, float add/multiply 3, float divide 10, convert 2) stall both pipelines by the same amount.
* The 6-stage pipeline also needs 2 extra cycles at the start and end to fill and empty.
* Cycles are not the whole story. Each 6-stage stage does less work, so its clock can run faster. If the 6-stage clock is at least 17% faster, it finishes these programs sooner (time = cycles x clock period).

## 3. Does the vertical or horizontal control unit change anything?

| | vertical | horizontal |
|---|---|---|
| number of control words | 28 | 28 |
| width of one control word | 16 bits, split into 5 encoded fields (ALU_OP 4 bits, MEM_STACK 3 bits, BRANCH_SYS 3 bits, REG_WRITE 2 bits, SRC_MUX 4 bits) | 25 bits, one bit per control signal |
| total size of control memory | 448 bits | 700 bits |
| extra hardware after the memory | 5 decoders (16 bits become 35 signal lines) | none, the bits drive the datapath directly |
| how the next control word is found | opcode, I and F select it | same |
| cycles and results | identical (see section 1) | identical |

The vertical unit needs 36% fewer bits of memory, but it needs decoders after the memory, which adds delay. The horizontal unit uses more memory, but its signals are ready as soon as the word is read, and adding a new signal combination needs no new decoder. In both designs each instruction is one control word: the pipeline needs fresh control signals every cycle, so the control unit looks up one word per instruction and then returns to address 0x00.

## 4. Which algorithms does the ALU use?

| unit | algorithm | how much work for a 32-bit number | used for |
|---|---|---|---|
| adder | ripple-carry | 32 full adders one after another (about 64 gate delays) | add, sub, cmp, addresses |
| adder | Kogge-Stone (parallel prefix) | 7 levels in total (about 14 gate delays), 129 small cells | add, sub, cmp, addresses |
| multiplier | Booth radix-4 (default) | 16 partial products, each 0, +-1x or +-2x the multiplicand | mul (2 cycles) |
| multiplier | shift-and-add | 32 steps, one add for every 1 bit of the multiplier | mul (2 cycles) |
| divider | non-restoring (default) | 32 add/subtract steps (+1 final correction) | div, mod (8 cycles) |
| divider | restoring | 32 subtract steps, plus an add-back every time a step goes negative | div, mod (8 cycles) |
| shifter | barrel shifter | 5 multiplexer levels | lsl, lsr, asr |
| float unit | IEEE-754 single precision, round to nearest even, tiny values flushed to zero | | fadd/fsub/fmul (3 cycles), fdiv (10), fcmp, fmin/fmax/fabs/fneg, fcvt (2) |

**Check of the two adders.** Random 32-bit additions and subtractions were compared with exact arithmetic:

| adder | wrong results | Python time per operation |
|---|---|---|
| kogge | 0 | 14.5 microseconds |
| ripple | 0 | 4.5 microseconds |

Both adders give identical results in every program (section 1). Kogge-Stone is faster as hardware, but this simulator does not turn that into extra cycles: each operation has a fixed number of cycles. The Python times above only show how slow each *software model* is, not how fast the real circuit would be.

**Multiplier and divider algorithms compared.** The number of add/subtract operations is what an iterative hardware unit spends its time on, so fewer is better. Measured on 4,000 random 32-bit operand pairs (the divider also on 4,000 pairs with a small divisor):

| algorithm | average add/sub per operation | worst case seen | Python time per operation |
|---|---|---|---|
| booth | 12.0 | 16 | 4 microseconds |
| shiftadd | 16.1 | 26 | 3 microseconds |
| nonrestoring | 32.6 | 33 | 8 microseconds |
| restoring | 57.6 | 64 | 7 microseconds |

Inside the test programs (6-stage, all programs together):

| multiplier + divider | add/sub steps in multiplies | add/sub steps in divides |
|---|---|---|
| booth + nonrestoring | 28 | 196 |
| booth + restoring | 28 | 368 |
| shiftadd + nonrestoring | 25 | 196 |
| shiftadd + restoring | 25 | 368 |

* Booth radix-4 needs about 25% fewer add/subtract steps than shift-and-add, and at most 16 (one per pair of bits) where shift-and-add can need 32. That is why it is the default.
* In the test programs shift-and-add did slightly fewer steps (25 vs 28): those programs multiply by small numbers such as 2..10, which have only a few 1 bits. Booth's advantage is on large operands and its fixed worst case of 16 steps.
* The non-restoring divider needs about 43% fewer steps than the restoring one: it never adds the divisor back, it just adds instead of subtracting in the next step.
* All 8 combinations (2 adders x 2 multipliers x 2 dividers) gave exactly the same results and the same cycle counts on every program, because the number of cycles per operation is fixed by the design (section 8 of the pipeline documents). The algorithm decides how much hardware work fits in those cycles.
