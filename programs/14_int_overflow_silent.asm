; 14_int_overflow_silent.asm - integer overflow does NOT raise an exception in RISC201: it wraps.
;   Only stack overflow/underflow (cause 1) and protection (cause 2) trap. So arithmetic overflow
;   must be found by software, which this program does with cmp + bgt, then saturates the result.
;   Debug it:  break check / run / regs  (r3 shows the wrapped value, cause stays 0)
; EXPECT cause = 0
; EXPECT r3 = 0x80000000        ; INT_MAX + 1 wrapped to INT_MIN, no exception
; EXPECT r6 = 1                 ; software detected the overflow
; EXPECT r7 = 0x7FFFFFFF        ; saturated result
; EXPECT r8 = 0                 ; 5 + 1 did not overflow
; EXPECT r9 = 0xFFFFFFFF        ; 10 div 0
; EXPECT r10 = 10               ; 10 mod 0 = dividend
; EXPECT r11 = 0x80000000       ; INT_MIN div -1 stays INT_MIN
        mov   r1, 1
        lsl   r1, r1, 31
        sub   r1, r1, 1         ; r1 = INT_MAX = 0x7FFFFFFF
        mov   r2, 1
        add   r3, r1, r2        ; wraps to 0x80000000, silently
check:  cmp   r3, r1            ; adding a positive number must give a larger result
        bgt   ok1
        mov   r6, 1             ; sum <= operand -> overflow happened
        mov   r7, r1            ; saturate: clamp to INT_MAX
        b     second
ok1:    mov   r6, 0
        mov   r7, r3
second: mov   r4, 5             ; a case that does not overflow
        add   r5, r4, r2
        cmp   r5, r4
        bgt   ok2
        mov   r8, 1
        b     divs
ok2:    mov   r8, 0
divs:   mov   r1, 10
        mov   r2, 0
        div   r9, r1, r2        ; divide by zero: all ones, no exception
        mod   r10, r1, r2       ; remainder = dividend
        mov   r1, 1
        lsl   r1, r1, 31        ; INT_MIN
        mov   r2, -1
        div   r11, r1, r2       ; INT_MIN / -1 overflow case: stays INT_MIN
        halt
