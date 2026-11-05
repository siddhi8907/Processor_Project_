; 09_exception_rte.asm - precise exception, handler fixes SP, rte re-executes the faulting push
; EXPECT cause = 1
; EXPECT epc = 0x08
; EXPECT r2 = 5
; EXPECT r3 = 1
; EXPECT sp = 0x1F80
        mov   sp, 0x1F00        ; stack full: SP at STACK_LIMIT
        mov   r1, 5
        push  r1                ; SP-4 < limit -> exception, epc = 0x08
        pop   r2                ; runs after the handler's rte retried the push
        mov   r3, 1
        halt

.org 0x400
handler:
        mov   sp, 0x1F80        ; give the program room on the stack
        rte                     ; PC <- epc (the push)
