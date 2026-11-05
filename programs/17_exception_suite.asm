; 17_exception_suite.asm - ALL exception / overflow / underflow demonstrations in ONE program.
;
; Seven tests run one after another. Every result is written to data memory at 0x200 so one
; command shows them all:   mem 0x200 18
;
;   Test A  stack UNDERFLOW   pop on empty stack  -> cause 1 -> handler pushes a value -> rte retries the pop
;   Test B  stack OVERFLOW    runaway recursion   -> cause 1 -> handler resets SP, branches to recovered_B
;   Test C  precise exception push at STACK_LIMIT -> cause 1 -> handler makes room -> rte retries the push
;   Test D  INTEGER overflow  wraps silently (no exception), detected and saturated in software, div by zero
;   Test E  FLOAT overflow / underflow / invalid  -> Inf / 0 / NaN, no exception
;   Test F  stack LEAK        push without pop    -> the static checker ('check') reports it; SP drifts
;   Test G  stack PROTECTION  stores that corrupt the stack -> cause 2 (needs protect trap, set below)
;
; The shared handler at EXEC_VECTOR (0x400) uses r13 (= id of the running test) to decide what to do.
; r12 counts how many exceptions were taken. r10 is the base of the results table.
;
; CONFIG stack_protect=trap
; EXPECT cause = 2
; EXPECT sp = 0x2000
; EXPECT r12 = 4
; EXPECT mem[0x200] = 99
; EXPECT mem[0x204] = 64
; EXPECT mem[0x208] = 5
; EXPECT mem[0x20C] = 0x80000000
; EXPECT mem[0x210] = 1
; EXPECT mem[0x214] = 0x7FFFFFFF
; EXPECT mem[0x218] = 0xFFFFFFFF
; EXPECT mem[0x21C] = 10
; EXPECT mem[0x220] = 0x80000000
; EXPECT mem[0x224] = 0x7F800000
; EXPECT mem[0x228] = 0
; EXPECT mem[0x22C] = 0x7FC00000
; EXPECT mem[0x230] = 0x7F800000
; EXPECT mem[0x234] = 0x7FFFFFFF
; EXPECT mem[0x238] = 5
; EXPECT mem[0x23C] = 0x1FEC
; EXPECT mem[0x240] = 1
; EXPECT mem[0x244] = 4

        mov   r10, 0x200        ; results table base
        mov   r12, 0            ; exception counter

; ---------------------------------------------------------------- Test A: stack underflow
        mov   r13, 1
        pop   r2                ; stack is empty -> underflow (cause 1); handler fixes it, rte retries
        st    r2, 0[r10]        ; 0x200 = 99 (the value the handler pushed)

; ---------------------------------------------------------------- Test B: stack overflow, recovered
        mov   r13, 2
        mov   r1, 0
        call  deep              ; 64 levels fit; the 65th push faults (cause 1)
        halt                    ; never reached
recovered_B:
        st    r1, 4[r10]        ; 0x204 = 64 (depth reached when the stack filled up)

; ---------------------------------------------------------------- Test C: precise exception + rte
        mov   r13, 3
        mov   sp, 0x1F00        ; pretend the stack is full
        mov   r1, 5
        push  r1                ; faults (cause 1); handler makes room; rte retries this push
        pop   r3                ; reads back the 5
        st    r3, 8[r10]        ; 0x208 = 5
        mov   sp, 0x2000

; ---------------------------------------------------------------- Test D: integer overflow (silent)
        mov   r13, 0            ; no exception expected in D, E, F
        mov   r1, 1
        lsl   r1, r1, 31
        sub   r1, r1, 1         ; r1 = INT_MAX = 0x7FFFFFFF
        mov   r2, 1
        add   r3, r1, r2        ; wraps to 0x80000000, silently
        st    r3, 12[r10]       ; 0x20C = 0x80000000
        cmp   r3, r1            ; for a positive addend the sum must be bigger
        bgt   d_ok
        mov   r6, 1             ; sum <= operand -> overflow happened
        mov   r7, r1            ; saturate to INT_MAX
        b     d_store
d_ok:   mov   r6, 0
        mov   r7, r3
d_store:
        st    r6, 16[r10]       ; 0x210 = 1  (overflow detected)
        st    r7, 20[r10]       ; 0x214 = 0x7FFFFFFF
        mov   r1, 10
        mov   r2, 0
        div   r9, r1, r2        ; divide by zero: all ones, no exception
        st    r9, 24[r10]       ; 0x218 = 0xFFFFFFFF
        mod   r9, r1, r2        ; remainder = dividend
        st    r9, 28[r10]       ; 0x21C = 10
        mov   r1, 1
        lsl   r1, r1, 31        ; INT_MIN
        mov   r2, -1
        div   r9, r1, r2        ; INT_MIN / -1 stays INT_MIN
        st    r9, 32[r10]       ; 0x220 = 0x80000000

; ---------------------------------------------------------------- Test E: float overflow / underflow
        mov.h r1, 0x7F00        ; about 1.7e38
        fmul  r2, r1, r1        ; overflow -> +Inf
        st    r2, 36[r10]       ; 0x224 = 0x7F800000
        mov.h r3, 0x0080        ; smallest normal float
        fmul  r4, r3, r3        ; underflow -> 0 (flush to zero)
        st    r4, 40[r10]       ; 0x228 = 0
        mov   r5, 0
        fdiv  r6, r5, r5        ; 0/0 -> NaN
        st    r6, 44[r10]       ; 0x22C = 0x7FC00000
        fmov  r7, 1.0
        fdiv  r8, r7, r5        ; 1/0 -> +Inf
        st    r8, 48[r10]       ; 0x230 = 0x7F800000
        fcvti r9, r2            ; +Inf -> integer saturates
        st    r9, 52[r10]       ; 0x234 = 0x7FFFFFFF

; ---------------------------------------------------------------- Test F: stack leak (see 'check')
        mov   r1, 0
f_loop: call  leaky
        cmp   r1, 4             ; stop once r1 reaches 5
        bgt   f_done
        b     f_loop
f_done: st    r1, 56[r10]       ; 0x238 = 5 calls
        st    sp, 60[r10]       ; 0x23C = 0x1FEC : SP drifted 20 bytes below STACK_BASE
        mov   sp, 0x2000        ; repair the leak

; ---------------------------------------------------------------- Test G: stack protection (cause 2)
        mov   r13, 4
        mov   r9, 0x1FF0        ; a data pointer that has run into the stack region
        mov   r1, 0x1234
        push  r1
        st    r1, -4[sp]        ; BUG 1: store below SP  -> trap (protect trap)
        st    r1, 8[r9]         ; BUG 2: stack written through a data pointer
        mov   r7, 0             ; reached only when protection is OFF (the bugs went unnoticed)
g_done: mov   sp, 0x2000
        st    r7, 64[r10]       ; 0x240 = 1 if the trap fired, 0 if the corruption was silent

finish: st    r12, 68[r10]      ; 0x244 = number of exceptions taken (4 with protect trap)
        halt

; ---------------------------------------------------------------- subroutines
deep:   push  ra
        add   r1, r1, 1         ; recursion depth
        call  deep
        pop   ra
        ret

leaky:  add   r1, r1, 1
        push  r1                ; BUG: pushed, never popped
        ret

; ---------------------------------------------------------------- shared exception handler
.org 0x400                      ; EXEC_VECTOR
handler:
        add   r12, r12, 1       ; count the exception
        cmp   r13, 1
        beq   h_underflow
        cmp   r13, 2
        beq   h_overflow
        cmp   r13, 3
        beq   h_retry
        cmp   r13, 4
        beq   h_protect
        halt                    ; unexpected exception: stop

h_underflow:
        mov   r4, 99
        push  r4                ; stack is no longer empty
        rte                     ; the pop runs again and succeeds

h_overflow:
        mov   sp, 0x2000        ; discard the runaway stack
        b     recovered_B       ; (rte would just fault again)

h_retry:
        mov   sp, 0x1F80        ; make room on the stack
        rte                     ; the push runs again and succeeds

h_protect:
        mov   r7, 1             ; record that the protection trap fired
        b     g_done            ; skip the corrupting store
