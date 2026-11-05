; 05_float_newton_sqrt.asm - sqrt(2) by Newton's method on the FPU, then int<->float conversion
;   x(k+1) = 0.5 * (x(k) + a / x(k))
; EXPECT r8 = 1414
; EXPECT r13 = 441
        fmov  r1, 2.0           ; a
        fmov  r2, 1.0           ; x
        fmov  r3, 0.5
        mov   r4, 5             ; iterations
loop:   fdiv  r5, r1, r2        ; 10-cycle FPU divide
        fadd  r5, r5, r2        ; 3-cycle FPU add
        fmul  r2, r5, r3        ; 3-cycle FPU multiply
        sub   r4, r4, 1
        cmp   r4, 0
        bgt   loop
        fmov  r6, 1000.0
        fmul  r7, r2, r6
        fcvti r8, r7            ; float -> int (truncate): 1414
        mov   r10, 3
        fcvt  r11, r10          ; int -> float: 3.0
        fadd  r12, r11, r2      ; 4.4142...
        fmov  r6, 100.0
        fmul  r12, r12, r6
        fcvti r13, r12          ; 441
        halt
