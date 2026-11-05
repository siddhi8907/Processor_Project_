; 03_bubble_sort.asm - sort 8 words in data memory (ld/st, nested loops, load-use hazards)
; EXPECT mem[0x100] = 1
; EXPECT mem[0x104] = 2
; EXPECT mem[0x108] = 3
; EXPECT mem[0x10C] = 4
; EXPECT mem[0x110] = 5
; EXPECT mem[0x114] = 7
; EXPECT mem[0x118] = 8
; EXPECT mem[0x11C] = 9
; EXPECT r7 = 1
; EXPECT r8 = 9
.equ N, 8
.equ BASE, 0x100
        mov   r13, BASE
        mov   r1, 5
        st    r1, 0[r13]
        mov   r1, 3
        st    r1, 4[r13]
        mov   r1, 8
        st    r1, 8[r13]
        mov   r1, 1
        st    r1, 12[r13]
        mov   r1, 9
        st    r1, 16[r13]
        mov   r1, 2
        st    r1, 20[r13]
        mov   r1, 7
        st    r1, 24[r13]
        mov   r1, 4
        st    r1, 28[r13]

        mov   r1, N-1           ; comparisons in this pass
outer:  mov   r2, 0             ; byte offset of a[j]
        mov   r3, 0             ; j
inner:  add   r4, r13, r2
        ld    r5, 0[r4]
        ld    r6, 4[r4]
        cmp   r5, r6            ; uses r6 right after the load: 1 stall in the 6-stage pipeline
        bgt   swap
        b     next
swap:   st    r6, 0[r4]
        st    r5, 4[r4]
next:   add   r2, r2, 4
        add   r3, r3, 1
        cmp   r1, r3
        bgt   inner
        sub   r1, r1, 1
        cmp   r1, 0
        bgt   outer
        ld    r7, 0[r13]        ; smallest
        ld    r8, 28[r13]       ; largest
        halt
