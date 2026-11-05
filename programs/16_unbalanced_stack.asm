; 16_unbalanced_stack.asm - a function pushes but never pops (a leak). Good for the static checker.
;   Each call to 'leaky' leaves 4 bytes on the stack, so SP drifts down on every call.
;   Type 'check' after loading to see the static checker's error, then 'run' and 'stack'
;   to see SP drift. The loop is bounded (5 calls), so the program still halts cleanly.
;   Debug it:  check / break after / run / stack / frames
; EXPECT r1 = 5
; EXPECT sp = 0x1FEC             ; 5 leaked words = 20 bytes below STACK_BASE (0x2000)
        mov   r1, 0
loop:   call  leaky
        cmp   r1, 4             ; stop once r1 reaches 5
        bgt   after
        b     loop
after:  halt

leaky:  add   r1, r1, 1
        push  r1                ; BUG: pushed, never popped
        ret
