; 11_all_units_demo.asm - every functional unit: mul, call/div, stack, int<->float, FPU, memory
; EXPECT r2 = 120
; EXPECT r3 = 60
; EXPECT r5 = 180
; EXPECT r9 = 45
; EXPECT r10 = 45
; EXPECT mem[0x100] = 45
        mov   r13, 0x100
        mov   r1, 5
        mov   r2, 1
loop:   mul   r2, r2, r1
        sub   r1, r1, 1
        cmp   r1, 1
        bgt   loop
        push  r2
        call  half
        pop   r4
        add   r5, r4, r3
        fcvt  r6, r5
        fmov  r7, 0.25
        fmul  r8, r6, r7
        fcvti r9, r8
        st    r9, 0[r13]
        ld    r10, 0[r13]
        halt
half:   div   r3, r2, 2
        ret
