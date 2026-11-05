; 02_fibonacci_recursive.asm - recursive fib(10) using the stack macros (full-descending stack)
;   ENTER/LEAVE save and restore ra, PUSHM/POPM save the callee's working registers.
; EXPECT r5 = 55
; EXPECT sp = 0x2000
        mov   r1, 10
        call  fib               ; r2 = fib(r1)
        mov   r5, r2
        halt

fib:    ENTER 0                 ; push ra
        cmp   r1, 1
        bgt   recurse
        mov   r2, r1            ; fib(0) = 0, fib(1) = 1
        LEAVE 0                 ; pop ra
        ret
recurse:
        PUSHM {r1, r3}          ; push r3, push r1
        sub   r1, r1, 1
        call  fib               ; fib(n-1)
        mov   r3, r2
        sub   r1, r1, 1
        call  fib               ; fib(n-2)
        add   r2, r2, r3
        POPM  {r1, r3}          ; pop r1, pop r3
        LEAVE 0
        ret
