; 07_macros.asm - preprocessor: user macros with parameters, default values and unique labels
; EXPECT r1 = 22
; EXPECT r2 = 11
; EXPECT r3 = 100
; EXPECT r4 = 0
; EXPECT r5 = 55
; EXPECT r6 = 10
; EXPECT r7 = 22
; EXPECT r8 = 11
; EXPECT r9 = 100
.macro SWAP a, b
        push  \a
        mov   \a, \b
        pop   \b
.endm
.macro CLAMP reg, lo, hi
        max   \reg, \reg, \lo
        min   \reg, \reg, \hi
.endm
.macro SUMTO dst, n=10          ; dst = 1 + 2 + ... + n
        mov   \dst, 0
        mov   r12, \n
loop\@: add   \dst, \dst, r12
        sub   r12, r12, 1
        cmp   r12, 0
        bgt   loop\@
.endm
        mov   r1, 11
        mov   r2, 22
        SWAP  r1, r2            ; r1 = 22, r2 = 11
        mov   r3, 500
        CLAMP r3, 0, 100        ; 100
        mov   r4, -5
        CLAMP r4, 0, 100        ; 0
        SUMTO r5                ; 55 (default n = 10)
        SUMTO r6, 4             ; 10
        PUSHM {r1-r3}           ; full-descending: push r3, r2, r1
        POPM  {r7-r9}           ; pop r7, r8, r9 -> copies of r1, r2, r3
        halt
