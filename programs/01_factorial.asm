; 01_factorial.asm - 10! with the 2-cycle Booth multiplier in a loop
; EXPECT r2 = 3628800
; EXPECT r1 = 1
        mov   r1, 10            ; n
        mov   r2, 1             ; acc
loop:   mul   r2, r2, r1        ; acc *= n   (multi-cycle EX: exBusy stall)
        sub   r1, r1, 1
        cmp   r1, 1
        bgt   loop              ; taken branch: 2 bubbles (4-stage) / 3 bubbles (6-stage)
        halt
