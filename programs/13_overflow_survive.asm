; 13_overflow_survive.asm - runaway recursion overflows the stack; the handler RECOVERS and the
;   program carries on to a normal halt (compare 08, where the handler just halts).
;   Each level pushes 4 bytes, so the 256-byte stack holds 64 levels and the 65th push faults.
;   Handler: count the fault (r12), reset SP, and branch to 'recovered' (not rte: retrying the
;   same push would only fault again). r1 keeps the depth reached when the fault happened.
;   Debug it:  watch r1 / break handler / run / exc / stack / regs
; EXPECT cause = 1
; EXPECT r1 = 64
; EXPECT r12 = 1
; EXPECT r5 = 777
; EXPECT sp = 0x2000
        mov   r1, 0
        mov   r12, 0
        call  deep
        halt                    ; never reached: deep never returns normally
deep:   push  ra
        add   r1, r1, 1         ; recursion depth
        call  deep
        pop   ra
        ret

recovered:
        mov   r5, 777           ; proof that execution continued after the fault
        halt

.org 0x400                      ; EXEC_VECTOR
handler:
        add   r12, r12, 1       ; count faults
        mov   sp, 0x2000        ; discard the whole runaway stack
        b     recovered
