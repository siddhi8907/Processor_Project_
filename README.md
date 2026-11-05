# RISC201 Processor Project (CSC-201)

A small 32-bit processor, built in software. It comes with everything needed to write and run programs for it:

* two processor designs: a **4-stage** and a **6-stage** pipeline
* two control units: **vertical** and **horizontal** microprogrammed
* an ALU with selectable adder, multiplier and divider algorithms, and a bit-level IEEE-754 float unit
* an assembler (assembly text to machine code) and a disassembler (machine code back to text)
* an interactive debugger where you can step through a program and look at registers, memory and the stack

You only need **Python 3.8 or newer**. No extra packages.

**Full documentation:** `docs/RISC201_Project_Documentation.pdf` (Word version next to it).
The ALU and float unit are explained in detail in `docs/RISC201_ALU_Documentation.pdf`.

## 1. Run your first program

Open a terminal in this folder and run:

```
python src/cli.py programs/01_factorial.asm
```

On Mac or Linux use `python3` instead of `python`. A prompt `(risc201)` appears. Type:

```
run
regs
quit
```

You should see `r2 = 3628800`, which is 10 factorial.

To run a few commands without the prompt:

```
python src/cli.py programs/01_factorial.asm -x "run; regs; stats"
```

## 2. The commands you need most

| Command | What it does |
|---|---|
| `run` | run until the program stops or hits a breakpoint |
| `step` / `stepi` | one clock cycle / one instruction |
| `break loop`, `break 0x20` | stop at a label or address |
| `regs`, `reg r2` | show all registers / one register |
| `mem 0x100 8` | show 8 memory words starting at 0x100 |
| `stack` | picture of the stack |
| `pipe` | what is in each pipeline stage |
| `stats` | cycles, CPI and stalls |
| `help`, `help <command>` | list every command / explain one |

Every command and command-line option is listed in section 11 of the documentation.

## 3. Writing your own program

Create `programs/mytest.asm`:

```
        mov   r1, 5          // r1 = 5
        mov   r2, 7          // r2 = 7
        add   r3, r1, r2     // r3 = r1 + r2
        halt                 // always end with halt
```

Then `python src/cli.py programs/mytest.asm`, `run`, `reg r3`. The instruction set and assembly syntax are in section 4 of the documentation.

There are 17 example programs in `programs/`, from factorial to the exception test suite. Each one has `; EXPECT` lines at the top that say what the registers or memory should hold at the end. They are listed in section 12 of the documentation.

## 4. Tests and evaluation

```
python tests/run_all.py      # every test suite and the evaluation, about 2 minutes
python evaluate.py           # only the evaluation; writes docs/evaluation_report.md
```

## 5. Where each assignment item is

| Assignment item | File or command |
|---|---|
| 1a 4-stage and 6-stage designs, compared | `docs/Pipeline_4Stage.docx`, `docs/Pipeline_6Stage.docx`, `src/simulator.py`, command `compare`, `docs/evaluation_report.md` |
| 1b microprogrammed control unit | `src/vertical_microprogramming.py`, `src/horizontal_microprogramming.py` |
| 1c ALU | `src/alu.py`: Kogge-Stone / ripple adder, Booth / shift-add multiplier, non-restoring / restoring divider, barrel shifter, float unit built on the same adder, multiplier and divider |
| 2a assembler | `src/assembler.py` |
| 2b disassembler | `src/disassembler.py` |
| 2c, 2d CLI and step-by-step debugger | `src/cli.py` |
| 2e stack macros | `src/preprocessor.py` (`PUSHM`, `POPM`, `ENTER`, `LEAVE`, `ALLOC`, `FREE`) |
| 2f stack safety checks and runtime exceptions | `src/stack_safety.py` (command `check`), exceptions in `src/simulator.py` |
| 2g stack picture | `src/stack_visualizer.py` (command `stack`) |
| 3 microprogrammed control inside the simulator, microPC shown | commands `pipe`, `micro on`, `ucode`, `rom`, `upc`, `micro-stats` |
| 4 ALU drives all arithmetic, shift and logic | every execute-stage operation goes through `RISC201_ALU` in `src/alu.py` |
| 5 evaluation | `python evaluate.py`: 4- vs 6-stage, vertical vs horizontal, all 8 ALU algorithm combinations |

Section 1A of the documentation has the same table and points to the section that explains each item.

## 6. Folder map

```
RISC201/
  README.md        this file
  evaluate.py      runs every program in every design and writes the report
  src/             the simulator and tools (assembler, CLI, ALU, ...)
  programs/        example assembly programs
  scripts/         debugger scripts
  tests/           automatic tests
  docs/            design documents, assignment, evaluation report, full documentation, ALU documentation
```


