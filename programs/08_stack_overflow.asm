; 08_stack_overflow.asm - runaway recursion -> stack overflow exception -> handler at EXEC_VECTOR
;   Each level pushes 4 bytes; the 256-byte stack allows 64 levels, the 65th push faults.
; EXPECT cause = 1
; EXPECT epc = 0x0C
; EXPECT r1 = 64
; EXPECT r12 = 238
; EXPECT sp = 0x2000
        mov   r1, 0
        call  deep
        halt                    ; never reached
deep:   push  ra
        add   r1, r1, 1         ; recursion depth
        call  deep
        pop   ra
        ret

.org 0x400                      ; EXEC_VECTOR
handler:
        mov   r12, 0xEE         ; mark: handler ran
        mov   sp, 0x2000        ; repair the stack
        halt
