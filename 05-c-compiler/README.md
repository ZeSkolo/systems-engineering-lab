# 5. Build a C Compiler in C

Inspired by *lolzdev* / the classic self-hosting milestone. `minic` is a real compiler, not an interpreter: it lexes C, builds an AST, assigns stack offsets, and emits x86-64 AT&T assembly that `gcc` assembles and links.

## Architecture

```
source.c  ->  lexer (tokens)
          ->  recursive-descent parser (AST)
          ->  semantic pass (types, pointer scale, local offsets)
          ->  codegen (System V AMD64: rbp frames, rdi..r9 args, rax return)
          ->  file.s  ->  gcc  ->  executable
```

| Stage | What it does |
| --- | --- |
| Lexer | Keywords, identifiers, numbers, `== != <= >= && \|\|`, `//` and `/* */` |
| Parser | Operator precedence, `if/else`, `while`, `for`, blocks, calls, decls |
| Symbols | Per-function locals + params; offsets from `%rbp` |
| Codegen | `push %rbp` / `mov %rsp,%rbp` / `sub $N,%rsp`, `call`, conditional jumps |

## Subset

`int`, pointers, arrays, functions (≤6 args), arithmetic, comparisons, `&& || !`, `sizeof`, `if/else`, `while`, `for`. No preprocessor, no structs yet — those are the remaining steps toward self-hosting (`minic` itself uses structs, so it cannot compile `minic.c` until struct layout lands).

## Run

```bash
make
make test
./minic examples/05_fact.c > /tmp/fact.s
gcc -o /tmp/fact /tmp/fact.s
/tmp/fact; echo $?   # 120
```

## What you learn

`if` is `cmp` + `je`. A local `int` is `-8(%rbp)`. `fact(n-1)` is “evaluate arg, `mov` into `%rdi`, `call fact`”. Pointer arithmetic is scaled by `sizeof(pointee)` in the semantic pass, not by the CPU.
