; 06_hazards.asm - forwarding, load-use and multi-cycle EX hazards (compare 4- vs 6-stage)
; EXPECT r4 = 35
; EXPECT r6 = 70
; EXPECT r7 = 490
; EXPECT r9 = 70
; EXPECT r10 = 1
; EXPECT r11 = 71
        mov   r13, 0x100
        mov   r1, 7
        add   r2, r1, r1        ; RAW distance 1 -> forwarded
        add   r3, r2, r1        ; distance 1 and 2
        add   r4, r3, r2
        st    r4, 0[r13]
        ld    r5, 0[r13]
        add   r6, r5, r5        ; load-use: 6-stage stalls 1 cycle, 4-stage forwards from RW
        mul   r7, r6, r1        ; 2-cycle multiplier
        add   r8, r7, 1         ; waits for the multiplier
        div   r9, r8, r1        ; 8-cycle divider
        mod   r10, r8, r1       ; 8-cycle divider again
        push  r10
        pop   r11               ; load-use through pop
        add   r11, r11, r9
        halt
