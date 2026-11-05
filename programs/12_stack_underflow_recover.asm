; 12_stack_underflow_recover.asm - POP on an empty stack -> underflow exception -> handler FIXES it -> rte
;   The stack is empty at start (SP = STACK_BASE = 0x2000), so "pop r2" would move SP above
;   STACK_BASE. The hardware raises a precise exception (cause 1, epc = address of the pop) and
;   jumps to the handler at 0x400. The handler pushes a replacement value so the stack is no longer
;   empty, then rte re-executes the same pop, which now succeeds.
;   Debug it:  break handler / run / exc / stack / regs / continue
; EXPECT cause = 1
; EXPECT epc = 0x04
; EXPECT r2 = 99
; EXPECT r3 = 1
; EXPECT sp = 0x2000
        mov   r1, 7
        pop   r2                ; epc = 0x04 : underflow, nothing to pop
        mov   r3, 1             ; reached only after the handler's rte retried the pop
        halt

.org 0x400
handler:
        mov   r4, 99
        push  r4                ; stack now holds one word, so the pop can succeed
        rte                     ; PC <- epc (the pop is executed again)
