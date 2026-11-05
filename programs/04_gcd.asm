; 04_gcd.asm - Euclid's algorithm with mod (8-cycle non-restoring divider)
; EXPECT r10 = 21
        mov   r1, 1071
        mov   r2, 462
loop:   cmp   r2, 0
        beq   done
        mod   r3, r1, r2
        mov   r1, r2
        mov   r2, r3
        b     loop
done:   mov   r10, r1
        halt
