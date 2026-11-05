; 15_float_overflow_underflow.asm - FPU overflow / underflow / invalid results (all silent, no exception)
;   float overflow  -> +Inf        float underflow -> +0 (subnormals are flushed to zero)
;   0.0 / 0.0       -> canonical quiet NaN         x / 0.0 -> +Inf
;   float->int of a huge value saturates to INT_MAX
;   Debug it:  run / regs  (the float column shows inf / nan / 0), cause stays 0
; EXPECT cause = 0
; EXPECT r2 = 0x7F800000        ; big * big = +Inf
; EXPECT r4 = 0                 ; tiny * tiny = 0
; EXPECT r6 = 0x7FC00000        ; 0 / 0 = NaN
; EXPECT r8 = 0x7F800000        ; 1.0 / 0.0 = +Inf
; EXPECT r10 = 0x7FFFFFFF       ; float->int of +Inf saturates
        mov.h r1, 0x7F00        ; 0x7F000000 = about 1.7e38
        fmul  r2, r1, r1        ; overflows binary32
        mov.h r3, 0x0080        ; 0x00800000 = smallest normal, about 1.2e-38
        fmul  r4, r3, r3        ; far below the smallest normal
        mov   r5, 0             ; 0.0
        fdiv  r6, r5, r5        ; invalid operation
        fmov  r7, 1.0
        fdiv  r8, r7, r5        ; divide by zero
        fcvti r10, r2           ; +Inf -> integer
        halt
