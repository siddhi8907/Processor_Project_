; 10_stack_protection.asm - stack memory protection (run with: protect trap)
;   BUG 1 writes below SP (unallocated stack), BUG 2 writes the stack through a data pointer.
;   protect off : both stores silently corrupt the stack
;   protect warn: both are reported, the program continues
;   protect trap: the first one raises a precise exception (cause 2), handler = halt
; CONFIG stack_protect=trap
; EXPECT cause = 2
; EXPECT epc = 0x0C
; EXPECT mem[0x1FF8] = 0
        mov   r13, 0x1FF0       ; a data pointer that has run into the stack region
        mov   r1, 0x1234
        push  r1
        st    r1, -4[sp]        ; BUG 1: below SP
        st    r1, 8[r13]        ; BUG 2: stack written through r13
        halt
