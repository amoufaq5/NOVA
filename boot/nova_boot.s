# ============================================================================
# Nova Bootstrap — Self-Hosting x86-64 Assembly
# ============================================================================
# The ONLY non-Nova code in the entire system. Once the Nova compiler
# compiles itself, even this becomes optional.
#
# No libc. No external dependencies. Direct Linux syscalls only.
#
# Build:
#   as -o nova_boot.o nova_boot.s
#   ld -o nova_boot nova_boot.o
#
# Usage:
#   ./nova_boot <file.nova>
#
# ============================================================================
# M1: Syscalls & Memory — Foundation layer
# M2: Lexer — Tokenization of Nova source
# ============================================================================

.intel_syntax noprefix

# ======================== CONSTANTS ========================================

# Linux syscall numbers (x86-64)
.equ SYS_READ,    0
.equ SYS_WRITE,   1
.equ SYS_OPEN,    2
.equ SYS_CLOSE,   3
.equ SYS_MMAP,    9
.equ SYS_BRK,     12
.equ SYS_EXIT,    60

# File flags
.equ O_RDONLY,     0

# mmap
.equ PROT_RW,          3       # PROT_READ | PROT_WRITE
.equ MAP_PRIV_ANON,    0x22    # MAP_PRIVATE | MAP_ANONYMOUS

# Sizes
.equ INITIAL_HEAP,     0x100000    # 1 MB
.equ ARENA_SIZE,       0x10000     # 64 KB
.equ ALLOC_ALIGN,      8
.equ BLOCK_HDR_SIZE,   16          # free-list block header: size + next

# File descriptors
.equ STDOUT,   1
.equ STDERR,   2

# Token struct offsets (40 bytes per token)
.equ TOK_TYPE,     0       # i64
.equ TOK_START,    8       # *u8  pointer into source
.equ TOK_LEN,      16      # i64
.equ TOK_LINE,     24      # i64
.equ TOK_COL,      32      # i64
.equ TOK_SIZE,     40

# Token buffer initial capacity
.equ TOKBUF_INIT_CAP,  1024

# --- Token type constants ---

# Special
.equ T_EOF,             0
.equ T_ERROR,           1

# Literals
.equ T_INT_LIT,         10
.equ T_FLOAT_LIT,       11
.equ T_STRING_LIT,      12
.equ T_IDENT,           13

# Single-char operators / delimiters
.equ T_PLUS,            20
.equ T_MINUS,           21
.equ T_STAR,            22
.equ T_SLASH,           23
.equ T_EQ,              24
.equ T_BANG,            25
.equ T_LT,              26
.equ T_GT,              27
.equ T_AT,              28
.equ T_HASH,            29
.equ T_AMP,             30
.equ T_PIPE,            31
.equ T_TILDE,           32
.equ T_DOT,             33

# Two-char operators
.equ T_EQEQ,            40
.equ T_BANGEQ,          41
.equ T_LTEQ,            42
.equ T_GTEQ,            43

# Flow operators
.equ T_FLOW_FWD,        50      # ~>
.equ T_FLOW_BWD,        51      # <~
.equ T_BROADCAST,       52      # =>>
.equ T_ENRICH_OP,       53      # <<~
.equ T_TENTATIVE,       54      # ~~>
.equ T_BIDIR,           55      # <=>
.equ T_FILTERED,        56      # |~>

# Delimiters
.equ T_LPAREN,          60
.equ T_RPAREN,          61
.equ T_LBRACE,          62
.equ T_RBRACE,          63
.equ T_LBRACKET,        64
.equ T_RBRACKET,        65
.equ T_COMMA,           66
.equ T_COLON,           67
.equ T_SEMI,            68

# Keywords — cognitive
.equ T_MOMENT,          80
.equ T_SIGNAL,          81
.equ T_NODE,            82
.equ T_PATH,            83
.equ T_CHANNEL,         84
.equ T_FIELD,           85
.equ T_MIND,            86
.equ T_WHEN,            87
.equ T_ARRIVES,         88
.equ T_RESPOND,         89
.equ T_EMIT,            90
.equ T_ABSORB,          91
.equ T_REFLECT,         92
.equ T_ENRICH_KW,       93
.equ T_DECAY,           94
.equ T_STRENGTHEN,      95
.equ T_CRYSTALLIZE,     96
.equ T_PROMOTE,         97
.equ T_ACADEMIC,        98
.equ T_EXPERIENTIAL,    99
.equ T_FELT,            100
.equ T_CONSEQUENCE,     101
.equ T_ENTITY,          102
.equ T_PERCEIVER,       103
.equ T_KNOWER,          104
.equ T_REMEMBERER,      105
.equ T_REASONER,        106
.equ T_FEELER,          107
.equ T_ACTOR,           108
.equ T_AWARENESS,       109
.equ T_SALIENCE,        110
.equ T_TRACE,           111
.equ T_URGENCY,         112

# Keywords — signal types
.equ T_EVENT_SIGNAL,    120
.equ T_QUESTION_SIGNAL, 121
.equ T_ORDER_SIGNAL,    122
.equ T_COMMAND_SIGNAL,  123
.equ T_REQUEST_SIGNAL,  124

# Keywords — path directions
.equ T_FORWARD,         130
.equ T_BACKWARD,        131
.equ T_REFLEX,          132

# Keywords — reasoning
.equ T_REASON_BY,       140
.equ T_PATTERN_MATCH,   141
.equ T_ANALOGIZE,       142
.equ T_WEIGHT_BY,       143
.equ T_DRIFT_TOWARD,    144
.equ T_SHIFT,           145

# Keywords — standard
.equ T_LET,             150
.equ T_IF,              151
.equ T_ELSE,            152
.equ T_WHILE,           153
.equ T_FOR,             154
.equ T_IN,              155
.equ T_FN,              156
.equ T_RETURN,          157
.equ T_TYPE,            158
.equ T_STRUCT,          159
.equ T_ENUM,            160
.equ T_TRUE,            161
.equ T_FALSE,           162
.equ T_NONE,            163
.equ T_AS,              164
.equ T_WITH,            165
.equ T_AGAINST,         166
.equ T_FROM,            167
.equ T_TO,              168
.equ T_WHERE,           169
.equ T_UNLESS,          170
.equ T_WAS,             171
.equ T_SIMULTANEOUSLY,  172

# Type keywords
.equ T_INT_TYPE,        180
.equ T_FLOAT_TYPE,      181
.equ T_STR_TYPE,        182
.equ T_BOOL_TYPE,       183

# --- AST Node Tags ---
.equ AST_PROGRAM,       1
.equ AST_MOMENT_DECL,   2
.equ AST_NODE_DECL,     3
.equ AST_PATH_DECL,     4
.equ AST_MIND_DECL,     5
.equ AST_FN_DECL,       6
.equ AST_LET_STMT,      7
.equ AST_IF_STMT,       8
.equ AST_WHILE_STMT,    9
.equ AST_FOR_STMT,      10
.equ AST_RETURN_STMT,   11
.equ AST_BLOCK,         12
.equ AST_BIN_OP,        13
.equ AST_UNARY_OP,      14
.equ AST_CALL,          15
.equ AST_DOT_ACCESS,    16
.equ AST_MOMENT_ACCESS, 17
.equ AST_IDENT,         18
.equ AST_INT_LIT,       19
.equ AST_FLOAT_LIT,     20
.equ AST_STRING_LIT,    21
.equ AST_BOOL_LIT,      22
.equ AST_NONE_LIT,      23
.equ AST_LIST_LIT,      24
.equ AST_FLOW_EXPR,     25
.equ AST_EMIT_STMT,     26
.equ AST_WHEN_BLOCK,    27
.equ AST_AWARENESS,     28
.equ AST_STRUCT_DECL,   29
.equ AST_FIELD_DEF,     30
.equ AST_ASSIGN_STMT,   31
.equ AST_RESPOND_STMT,  32
.equ AST_CHANNEL_DECL,  33
.equ AST_INDEX_EXPR,    34
.equ AST_EXPR_STMT,     35

# AST Node layout: 48 bytes each
# [0]  tag    [8]  f1    [16] f2    [24] f3    [32] f4    [40] f5
.equ AST_TAG,   0
.equ AST_F1,    8
.equ AST_F2,    16
.equ AST_F3,    24
.equ AST_F4,    32
.equ AST_F5,    40
.equ AST_NODE_SIZE, 48

# Node list layout: [count:8][capacity:8][items*:8]
.equ NL_COUNT,  0
.equ NL_CAP,    8
.equ NL_ITEMS,  16
.equ NL_HEADER, 24


# ======================== DATA SECTION =====================================
.section .data

# --- Heap state ---
heap_base:      .quad 0
heap_ptr:       .quad 0
heap_limit:     .quad 0

# --- Arena state ---
arena_base:     .quad 0
arena_ptr:      .quad 0
arena_limit:    .quad 0

# --- Free list ---
free_head:      .quad 0

# --- Lexer state ---
lex_source:     .quad 0     # pointer to source text
lex_pos:        .quad 0     # current byte offset
lex_length:     .quad 0     # total source length
lex_line:       .quad 1     # current line (1-based)
lex_col:        .quad 1     # current column (1-based)

# --- Token buffer ---
tok_buf:        .quad 0     # pointer to Token array
tok_count:      .quad 0     # number of tokens stored
tok_cap:        .quad 0     # allocated capacity

# --- Parser state ---
parse_pos:      .quad 0     # current token index
parse_ast:      .quad 0     # root AST node (Program)

# --- Scratch buffer for print_int ---
int_buf:        .space 21
newline_ch:     .byte 10


# ======================== BSS SECTION ======================================
.section .bss
.lcomm file_buf, 0x100000          # 1 MB file read buffer


# ======================== TEXT SECTION ======================================
.section .text
.global _start

# ======================== ENTRY POINT ======================================

_start:
    # Stack layout: [rsp]=argc, [rsp+8]=argv[0], [rsp+16]=argv[1], ...
    call heap_init
    call arena_init

    # Check argc >= 2
    mov rdi, [rsp]
    cmp rdi, 2
    jl .show_usage

    # Read source file (argv[1])
    mov rdi, [rsp + 16]
    call read_file
    test rax, rax
    js .read_err

    # Initialize lexer with file contents
    lea rdi, [rip + file_buf]
    mov rsi, rax
    call lexer_init

    # Tokenize entire source
    call lex_all

    # Parse token stream into AST
    call parse_program
    mov [rip + parse_ast], rax

    # Print AST summary
    mov rdi, rax
    xor esi, esi
    call print_ast

    # Exit 0
    xor edi, edi
    call sys_exit

.show_usage:
    lea rdi, [rip + msg_usage]
    call print_str
    mov edi, 1
    call sys_exit

.read_err:
    lea rdi, [rip + msg_read_err]
    call print_str
    mov edi, 1
    call sys_exit


# ============================================================================
# M1: SYSCALL WRAPPERS
# ============================================================================

# sys_exit(code: edi)
sys_exit:
    mov eax, SYS_EXIT
    syscall

# sys_read(fd: edi, buf: rsi, count: rdx) -> bytes_read
sys_read:
    mov eax, SYS_READ
    syscall
    ret

# sys_write(fd: edi, buf: rsi, count: rdx) -> bytes_written
sys_write:
    mov eax, SYS_WRITE
    syscall
    ret

# sys_open(path: rdi, flags: esi, mode: edx) -> fd
sys_open:
    mov eax, SYS_OPEN
    syscall
    ret

# sys_close(fd: edi) -> status
sys_close:
    mov eax, SYS_CLOSE
    syscall
    ret

# sys_brk(addr: rdi) -> new_brk
sys_brk:
    mov eax, SYS_BRK
    syscall
    ret

# sys_mmap(addr: rdi, len: rsi, prot: edx, flags: r10d, fd: r8d, off: r9) -> ptr
sys_mmap:
    mov eax, SYS_MMAP
    syscall
    ret


# ============================================================================
# M1: HEAP ALLOCATOR — bump allocator with free list fallback
# ============================================================================

# heap_init() — get program break and extend by INITIAL_HEAP
heap_init:
    push rbx

    xor edi, edi
    call sys_brk
    mov [rip + heap_base], rax
    mov [rip + heap_ptr], rax

    lea rdi, [rax + INITIAL_HEAP]
    call sys_brk
    mov [rip + heap_limit], rax

    # Verify we got the memory
    mov rbx, [rip + heap_base]
    add rbx, INITIAL_HEAP
    cmp rax, rbx
    jl .heap_init_fail

    pop rbx
    ret

.heap_init_fail:
    lea rdi, [rip + msg_heap_fail]
    call print_str
    mov edi, 1
    call sys_exit

# heap_alloc(size: rdi) -> ptr in rax
heap_alloc:
    push rbx
    push rcx

    # Try free list first
    mov rbx, rdi
    call free_list_find
    test rax, rax
    jnz .ha_done

    # Align size to 8 bytes
    lea rax, [rbx + ALLOC_ALIGN - 1]
    and rax, -ALLOC_ALIGN
    mov rbx, rax

    # Bump pointer
    mov rax, [rip + heap_ptr]
    lea rcx, [rax + rbx]
    cmp rcx, [rip + heap_limit]
    jg .ha_extend

    mov [rip + heap_ptr], rcx
.ha_done:
    pop rcx
    pop rbx
    ret

.ha_extend:
    # Double heap or fit requested size
    push rbx
    push rax

    mov rdi, [rip + heap_limit]
    mov rcx, rdi
    sub rcx, [rip + heap_base]     # current size
    cmp rbx, rcx
    cmovg rcx, rbx                 # max(current_size, requested)
    add rdi, rcx
    call sys_brk
    mov [rip + heap_limit], rax

    pop rax                         # saved heap_ptr
    pop rbx                         # requested size

    lea rcx, [rax + rbx]
    cmp rcx, [rip + heap_limit]
    jg .ha_oom
    mov [rip + heap_ptr], rcx
    jmp .ha_done

.ha_oom:
    lea rdi, [rip + msg_oom]
    call print_str
    mov edi, 1
    call sys_exit

# heap_free(ptr: rdi) — prepend block to free list
heap_free:
    test rdi, rdi
    jz .hf_ret

    sub rdi, BLOCK_HDR_SIZE
    mov rax, [rip + free_head]
    mov [rdi + 8], rax
    mov [rip + free_head], rdi
.hf_ret:
    ret

# free_list_find(size: rdi) -> ptr or 0
free_list_find:
    push rbx
    push rcx
    push rdx

    lea rbx, [rdi + BLOCK_HDR_SIZE + ALLOC_ALIGN - 1]
    and rbx, -ALLOC_ALIGN

    lea rcx, [rip + free_head]
.fl_loop:
    mov rax, [rcx]
    test rax, rax
    jz .fl_miss

    cmp [rax], rbx                  # block->size >= needed?
    jge .fl_hit

    lea rcx, [rax + 8]
    jmp .fl_loop

.fl_hit:
    mov rdx, [rax + 8]
    mov [rcx], rdx                  # unlink
    lea rax, [rax + BLOCK_HDR_SIZE]
    pop rdx
    pop rcx
    pop rbx
    ret

.fl_miss:
    xor eax, eax
    pop rdx
    pop rcx
    pop rbx
    ret


# ============================================================================
# M1: ARENA ALLOCATOR — per-cycle, zero-cost reset
# ============================================================================

# arena_init()
arena_init:
    mov rdi, ARENA_SIZE
    call heap_alloc
    mov [rip + arena_base], rax
    mov [rip + arena_ptr], rax
    add rax, ARENA_SIZE
    mov [rip + arena_limit], rax
    ret

# arena_alloc(size: rdi) -> ptr
arena_alloc:
    add rdi, ALLOC_ALIGN - 1
    and rdi, -ALLOC_ALIGN

    mov rax, [rip + arena_ptr]
    lea rcx, [rax + rdi]
    cmp rcx, [rip + arena_limit]
    jg .aa_overflow

    mov [rip + arena_ptr], rcx
    ret

.aa_overflow:
    call heap_alloc
    ret

# arena_reset()
arena_reset:
    mov rax, [rip + arena_base]
    mov [rip + arena_ptr], rax
    ret


# ============================================================================
# M1: STRING OPERATIONS
# ============================================================================

# str_len(s: rdi) -> len in rax
str_len:
    xor eax, eax
    test rdi, rdi
    jz .sl_done
.sl_loop:
    cmp byte ptr [rdi + rax], 0
    je .sl_done
    inc rax
    jmp .sl_loop
.sl_done:
    ret

# str_cmp(a: rdi, b: rsi) -> 0 equal, <0 a<b, >0 a>b
str_cmp:
.sc_loop:
    movzx eax, byte ptr [rdi]
    movzx ecx, byte ptr [rsi]
    sub eax, ecx
    jnz .sc_ret
    test cl, cl
    jz .sc_ret
    inc rdi
    inc rsi
    jmp .sc_loop
.sc_ret:
    ret

# str_eq(a: rdi, b: rsi) -> 1 equal, 0 not
str_eq:
    call str_cmp
    test eax, eax
    setz al
    movzx eax, al
    ret

# str_ncmp(a: rdi, b: rsi, n: rdx) -> 0 equal, nonzero otherwise
str_ncmp:
    test rdx, rdx
    jz .sn_eq
.sn_loop:
    movzx eax, byte ptr [rdi]
    movzx ecx, byte ptr [rsi]
    sub eax, ecx
    jnz .sn_ret
    test cl, cl
    jz .sn_eq
    inc rdi
    inc rsi
    dec rdx
    jnz .sn_loop
.sn_eq:
    xor eax, eax
.sn_ret:
    ret

# str_copy(dst: rdi, src: rsi) -> dst
str_copy:
    mov rax, rdi
.scp_loop:
    movzx ecx, byte ptr [rsi]
    mov byte ptr [rdi], cl
    test cl, cl
    jz .scp_done
    inc rdi
    inc rsi
    jmp .scp_loop
.scp_done:
    ret

# str_concat(a: rdi, b: rsi) -> new heap string
str_concat:
    push r12
    push r13
    push r14

    mov r12, rdi
    mov r13, rsi

    call str_len
    mov r14, rax

    mov rdi, r13
    call str_len
    add r14, rax
    inc r14

    mov rdi, r14
    call heap_alloc
    mov r14, rax

    mov rdi, rax
    mov rsi, r12
    call str_copy

    mov rdi, r14
    call str_len
    lea rdi, [r14 + rax]
    mov rsi, r13
    call str_copy

    mov rax, r14

    pop r14
    pop r13
    pop r12
    ret

# mem_copy(dst: rdi, src: rsi, n: rdx)
mem_copy:
    test rdx, rdx
    jz .mc_done
.mc_loop:
    movzx eax, byte ptr [rsi]
    mov byte ptr [rdi], al
    inc rdi
    inc rsi
    dec rdx
    jnz .mc_loop
.mc_done:
    ret

# mem_set(dst: rdi, val: sil, n: rdx)
mem_set:
    test rdx, rdx
    jz .ms_done
.ms_loop:
    mov byte ptr [rdi], sil
    inc rdi
    dec rdx
    jnz .ms_loop
.ms_done:
    ret


# ============================================================================
# M1: FILE I/O
# ============================================================================

# read_file(path: rdi) -> bytes_read in rax, content in file_buf
# Returns -1 on error.
read_file:
    push rbx
    push r12

    xor esi, esi                    # O_RDONLY
    xor edx, edx
    call sys_open
    test rax, rax
    js .rf_err
    mov r12, rax                    # fd

    xor ebx, ebx                   # total bytes
.rf_loop:
    mov edi, r12d
    lea rsi, [rip + file_buf]
    add rsi, rbx
    mov edx, 4096
    call sys_read
    test rax, rax
    js .rf_close_err
    jz .rf_eof
    add rbx, rax
    cmp rbx, 0x100000
    jge .rf_eof
    jmp .rf_loop

.rf_eof:
    # Null-terminate
    lea rax, [rip + file_buf]
    mov byte ptr [rax + rbx], 0

    mov edi, r12d
    call sys_close
    mov rax, rbx

    pop r12
    pop rbx
    ret

.rf_close_err:
    mov edi, r12d
    call sys_close
.rf_err:
    mov rax, -1
    pop r12
    pop rbx
    ret


# ============================================================================
# M1: OUTPUT HELPERS
# ============================================================================

# print_str(s: rdi) — print null-terminated string to stdout
print_str:
    push rbx
    mov rbx, rdi
    call str_len
    mov rdx, rax
    mov rsi, rbx
    mov edi, STDOUT
    call sys_write
    pop rbx
    ret

# print_bytes(buf: rdi, len: rsi)
print_bytes:
    mov rdx, rsi
    mov rsi, rdi
    mov edi, STDOUT
    call sys_write
    ret

# print_char(c: dil)
print_char:
    push rdi
    mov rsi, rsp
    mov edi, STDOUT
    mov edx, 1
    call sys_write
    pop rdi
    ret

# print_newline()
print_newline:
    lea rsi, [rip + newline_ch]
    mov edi, STDOUT
    mov edx, 1
    call sys_write
    ret

# print_int(n: rdi) — print signed 64-bit integer
print_int:
    push rbx

    mov rax, rdi
    test rax, rax
    jns .pi_pos
    neg rax
    push rax
    mov dil, '-'
    call print_char
    pop rax
.pi_pos:
    lea rbx, [rip + int_buf + 20]
    mov byte ptr [rbx], 0

    test rax, rax
    jnz .pi_cvt
    dec rbx
    mov byte ptr [rbx], '0'
    jmp .pi_out
.pi_cvt:
    mov rcx, 10
.pi_cvt_loop:
    test rax, rax
    jz .pi_out
    xor edx, edx
    div rcx
    add dl, '0'
    dec rbx
    mov [rbx], dl
    jmp .pi_cvt_loop
.pi_out:
    mov rdi, rbx
    call print_str
    pop rbx
    ret


# ============================================================================
# M2: LEXER — Character Classification
# ============================================================================

# is_alpha(c: dil) -> 1/0 in eax  (a-z, A-Z, _)
is_alpha:
    movzx eax, dil
    cmp al, '_'
    je .ia_yes
    or al, 0x20                     # lowercase
    sub al, 'a'
    cmp al, 26
    jb .ia_yes
    xor eax, eax
    ret
.ia_yes:
    mov eax, 1
    ret

# is_digit(c: dil) -> 1/0 in eax
is_digit:
    movzx eax, dil
    sub al, '0'
    cmp al, 10
    jb .id_yes
    xor eax, eax
    ret
.id_yes:
    mov eax, 1
    ret

# is_alnum(c: dil) -> 1/0 in eax
is_alnum:
    call is_alpha
    test eax, eax
    jnz .ian_ret
    call is_digit
.ian_ret:
    ret

# is_whitespace(c: dil) -> 1/0 in eax  (space, tab, \r, \n)
is_whitespace:
    movzx eax, dil
    cmp al, ' '
    je .iw_yes
    cmp al, 9          # tab
    je .iw_yes
    cmp al, 13         # \r
    je .iw_yes
    cmp al, 10         # \n
    je .iw_yes
    xor eax, eax
    ret
.iw_yes:
    mov eax, 1
    ret


# ============================================================================
# M2: LEXER — Core
# ============================================================================

# lexer_init(source: rdi, length: rsi)
lexer_init:
    mov [rip + lex_source], rdi
    mov [rip + lex_length], rsi
    mov qword ptr [rip + lex_pos], 0
    mov qword ptr [rip + lex_line], 1
    mov qword ptr [rip + lex_col], 1

    # Allocate token buffer
    mov rdi, TOKBUF_INIT_CAP * TOK_SIZE
    call heap_alloc
    mov [rip + tok_buf], rax
    mov qword ptr [rip + tok_count], 0
    mov qword ptr [rip + tok_cap], TOKBUF_INIT_CAP
    ret

# lex_peek() -> current char in al (0 if at end)
lex_peek:
    mov rax, [rip + lex_pos]
    cmp rax, [rip + lex_length]
    jge .lp_eof
    mov rcx, [rip + lex_source]
    movzx eax, byte ptr [rcx + rax]
    ret
.lp_eof:
    xor eax, eax
    ret

# lex_peek_at(offset: rdi) -> char at pos+offset in al
lex_peek_at:
    mov rax, [rip + lex_pos]
    add rax, rdi
    cmp rax, [rip + lex_length]
    jge .lpa_eof
    mov rcx, [rip + lex_source]
    movzx eax, byte ptr [rcx + rax]
    ret
.lpa_eof:
    xor eax, eax
    ret

# lex_advance() -> consumed char in al, updates pos/line/col
lex_advance:
    mov rax, [rip + lex_pos]
    cmp rax, [rip + lex_length]
    jge .la_eof

    mov rcx, [rip + lex_source]
    movzx eax, byte ptr [rcx + rax]

    inc qword ptr [rip + lex_pos]
    inc qword ptr [rip + lex_col]

    cmp al, 10                      # newline
    jne .la_ret
    inc qword ptr [rip + lex_line]
    mov qword ptr [rip + lex_col], 1
.la_ret:
    ret
.la_eof:
    xor eax, eax
    ret

# lex_skip_whitespace_and_comments()
lex_skip_ws:
    call lex_peek
    test al, al
    jz .lsw_done

    # Whitespace?
    mov dil, al
    call is_whitespace
    test eax, eax
    jz .lsw_check_comment
    call lex_advance
    jmp lex_skip_ws

.lsw_check_comment:
    # Line comment: //
    call lex_peek
    cmp al, '/'
    jne .lsw_done
    mov rdi, 1
    call lex_peek_at
    cmp al, '/'
    jne .lsw_done

    # Skip until newline
.lsw_comment_loop:
    call lex_advance
    test al, al
    jz .lsw_done
    cmp al, 10
    jne .lsw_comment_loop
    jmp lex_skip_ws

.lsw_done:
    ret


# ============================================================================
# M2: LEXER — Token Buffer
# ============================================================================

# tok_buf_push(type: rdi, start: rsi, len: rdx, line: rcx, col: r8)
# Append a token to the buffer.
tok_buf_push:
    push rbx
    push r12
    push r13
    push r14
    push r15

    mov r12, rdi        # type
    mov r13, rsi        # start
    mov r14, rdx        # len
    mov r15, rcx        # line
    mov rbx, r8         # col

    # Grow if needed
    mov rax, [rip + tok_count]
    cmp rax, [rip + tok_cap]
    jl .tbp_write

    # Double capacity
    mov rdi, [rip + tok_cap]
    shl rdi, 1
    mov [rip + tok_cap], rdi
    imul rdi, TOK_SIZE
    call heap_alloc
    mov rsi, [rip + tok_buf]        # old buffer
    mov rdi, rax                    # new buffer
    push rax
    mov rdx, [rip + tok_count]
    imul rdx, TOK_SIZE
    call mem_copy
    pop rax
    mov [rip + tok_buf], rax

.tbp_write:
    mov rax, [rip + tok_count]
    imul rax, TOK_SIZE
    add rax, [rip + tok_buf]

    mov [rax + TOK_TYPE], r12
    mov [rax + TOK_START], r13
    mov [rax + TOK_LEN], r14
    mov [rax + TOK_LINE], r15
    mov [rax + TOK_COL], rbx

    inc qword ptr [rip + tok_count]

    pop r15
    pop r14
    pop r13
    pop r12
    pop rbx
    ret


# ============================================================================
# M2: LEXER — Scanning
# ============================================================================

# scan_identifier() — identifier or keyword, starting at current pos
scan_ident:
    push r12
    push r13

    # Record start position, line, col
    mov r12, [rip + lex_pos]        # start offset
    mov r13, [rip + lex_line]
    push qword ptr [rip + lex_col]

    # Consume [a-zA-Z0-9_]+
.si_loop:
    call lex_peek
    test al, al
    jz .si_emit
    mov dil, al
    call is_alnum
    test eax, eax
    jnz .si_consume
    jmp .si_emit
.si_consume:
    call lex_advance
    jmp .si_loop

.si_emit:
    # Length = current pos - start
    mov rdx, [rip + lex_pos]
    sub rdx, r12

    # Pointer into source
    mov rsi, [rip + lex_source]
    add rsi, r12

    # Check if it's a keyword
    mov rdi, rsi
    push rdx
    mov rsi, rdx
    call keyword_lookup             # rax = token type or T_IDENT
    pop rdx

    # Push token
    mov rdi, rax                    # type
    mov rsi, [rip + lex_source]
    add rsi, r12                    # start ptr
    # rdx already = length
    mov rcx, r13                    # line
    pop r8                          # col

    call tok_buf_push

    pop r13
    pop r12
    ret

# scan_number() — integer or float literal
scan_number:
    push r12
    push r13
    push r14

    mov r12, [rip + lex_pos]
    mov r13, [rip + lex_line]
    mov r14, [rip + lex_col]

    mov rbx, T_INT_LIT              # assume int

    # Consume digits
.num_loop:
    call lex_peek
    test al, al
    jz .num_emit
    mov dil, al
    call is_digit
    test eax, eax
    jz .num_check_dot
    call lex_advance
    jmp .num_loop

.num_check_dot:
    call lex_peek
    cmp al, '.'
    jne .num_emit

    # Check next char is digit (avoid consuming "1.method")
    mov rdi, 1
    call lex_peek_at
    mov dil, al
    call is_digit
    test eax, eax
    jz .num_emit

    # It's a float — consume the dot
    mov rbx, T_FLOAT_LIT
    call lex_advance

    # Consume fractional digits
.num_frac:
    call lex_peek
    test al, al
    jz .num_emit
    mov dil, al
    call is_digit
    test eax, eax
    jz .num_emit
    call lex_advance
    jmp .num_frac

.num_emit:
    mov rdi, rbx                    # type
    mov rsi, [rip + lex_source]
    add rsi, r12                    # start
    mov rdx, [rip + lex_pos]
    sub rdx, r12                    # length
    mov rcx, r13                    # line
    mov r8, r14                     # col
    call tok_buf_push

    pop r14
    pop r13
    pop r12
    ret

# scan_string() — string literal (double-quoted)
scan_string:
    push r12
    push r13
    push r14

    mov r12, [rip + lex_pos]
    mov r13, [rip + lex_line]
    mov r14, [rip + lex_col]

    # Skip opening quote
    call lex_advance

.ss_loop:
    call lex_peek
    test al, al
    jz .ss_unterminated
    cmp al, '"'
    je .ss_close
    cmp al, '\\'
    jne .ss_normal

    # Escape sequence — skip two chars
    call lex_advance
    call lex_advance
    jmp .ss_loop

.ss_normal:
    call lex_advance
    jmp .ss_loop

.ss_close:
    call lex_advance                # skip closing quote

    mov rdi, T_STRING_LIT
    mov rsi, [rip + lex_source]
    add rsi, r12
    mov rdx, [rip + lex_pos]
    sub rdx, r12
    mov rcx, r13
    mov r8, r14
    call tok_buf_push

    pop r14
    pop r13
    pop r12
    ret

.ss_unterminated:
    lea rdi, [rip + msg_unterm_str]
    call print_str
    mov rdi, r13
    call print_int
    call print_newline

    # Emit error token
    mov rdi, T_ERROR
    mov rsi, [rip + lex_source]
    add rsi, r12
    mov rdx, [rip + lex_pos]
    sub rdx, r12
    mov rcx, r13
    mov r8, r14
    call tok_buf_push

    pop r14
    pop r13
    pop r12
    ret


# ============================================================================
# M2: LEXER — Operator Scanning
# ============================================================================

# scan_operator() — single or multi-character operator/delimiter
scan_operator:
    push r12
    push r13
    push r14

    mov r12, [rip + lex_pos]
    mov r13, [rip + lex_line]
    mov r14, [rip + lex_col]

    call lex_peek
    movzx ebx, al

    # --- Multi-char operators first ---

    # ~ : ~> or ~~>
    cmp bl, '~'
    je .so_tilde

    # < : <~ or <<~ or <=> or <= or <
    cmp bl, '<'
    je .so_lt

    # = : =>> or == or =
    cmp bl, '='
    je .so_eq

    # | : |~> or |
    cmp bl, '|'
    je .so_pipe

    # > : >= or >
    cmp bl, '>'
    je .so_gt

    # ! : != or !
    cmp bl, '!'
    je .so_bang

    # --- Single-char ---
    mov edi, T_PLUS
    cmp bl, '+'
    je .so_single
    mov edi, T_MINUS
    cmp bl, '-'
    je .so_single
    mov edi, T_STAR
    cmp bl, '*'
    je .so_single
    mov edi, T_SLASH
    cmp bl, '/'
    je .so_single
    mov edi, T_AT
    cmp bl, '@'
    je .so_single
    mov edi, T_HASH
    cmp bl, '#'
    je .so_single
    mov edi, T_AMP
    cmp bl, '&'
    je .so_single
    mov edi, T_DOT
    cmp bl, '.'
    je .so_single
    mov edi, T_LPAREN
    cmp bl, '('
    je .so_single
    mov edi, T_RPAREN
    cmp bl, ')'
    je .so_single
    mov edi, T_LBRACE
    cmp bl, '{'
    je .so_single
    mov edi, T_RBRACE
    cmp bl, '}'
    je .so_single
    mov edi, T_LBRACKET
    cmp bl, '['
    je .so_single
    mov edi, T_RBRACKET
    cmp bl, ']'
    je .so_single
    mov edi, T_COMMA
    cmp bl, ','
    je .so_single
    mov edi, T_COLON
    cmp bl, ':'
    je .so_single
    mov edi, T_SEMI
    cmp bl, ';'
    je .so_single

    # Unknown character — emit as error
    call lex_advance
    mov rdi, T_ERROR
    mov rsi, [rip + lex_source]
    add rsi, r12
    mov rdx, 1
    mov rcx, r13
    mov r8, r14
    call tok_buf_push
    jmp .so_ret

# --- Single-char emit ---
.so_single:
    call lex_advance
    # rdi already has token type
    mov rsi, [rip + lex_source]
    add rsi, r12
    mov rdx, 1
    mov rcx, r13
    mov r8, r14
    call tok_buf_push
    jmp .so_ret

# --- Tilde: ~> or ~~> ---
.so_tilde:
    call lex_advance
    call lex_peek
    cmp al, '>'
    je .so_flow_fwd
    cmp al, '~'
    je .so_maybe_tentative

    # Bare ~
    mov rdi, T_TILDE
    jmp .so_emit_from_r12

.so_flow_fwd:
    call lex_advance                # consume >
    mov rdi, T_FLOW_FWD
    jmp .so_emit_from_r12

.so_maybe_tentative:
    call lex_advance                # consume second ~
    call lex_peek
    cmp al, '>'
    jne .so_two_tildes
    call lex_advance                # consume >
    mov rdi, T_TENTATIVE
    jmp .so_emit_from_r12

.so_two_tildes:
    # Two tildes but no > — emit first as tilde, rewind for second
    mov rdi, T_TILDE
    mov rsi, [rip + lex_source]
    add rsi, r12
    mov rdx, 1
    mov rcx, r13
    mov r8, r14
    call tok_buf_push
    # The second ~ will be picked up on next scan_operator call
    dec qword ptr [rip + lex_pos]
    dec qword ptr [rip + lex_col]
    jmp .so_ret

# --- Less-than: <~ or <<~ or <=> or <= or < ---
.so_lt:
    call lex_advance
    call lex_peek
    cmp al, '~'
    je .so_flow_bwd
    cmp al, '<'
    je .so_maybe_enrich
    cmp al, '='
    je .so_maybe_bidir_or_lteq

    # Bare <
    mov rdi, T_LT
    jmp .so_emit_from_r12

.so_flow_bwd:
    call lex_advance                # consume ~
    mov rdi, T_FLOW_BWD
    jmp .so_emit_from_r12

.so_maybe_enrich:
    call lex_advance                # consume second <
    call lex_peek
    cmp al, '~'
    jne .so_two_lt
    call lex_advance                # consume ~
    mov rdi, T_ENRICH_OP
    jmp .so_emit_from_r12

.so_two_lt:
    # << but no ~ — emit < and rewind
    mov rdi, T_LT
    mov rsi, [rip + lex_source]
    add rsi, r12
    mov rdx, 1
    mov rcx, r13
    mov r8, r14
    call tok_buf_push
    dec qword ptr [rip + lex_pos]
    dec qword ptr [rip + lex_col]
    jmp .so_ret

.so_maybe_bidir_or_lteq:
    # We have < then =
    mov rdi, 1
    call lex_peek_at                # look at char after =
    cmp al, '>'
    jne .so_lteq

    # <=>
    call lex_advance                # consume =
    call lex_advance                # consume >
    mov rdi, T_BIDIR
    jmp .so_emit_from_r12

.so_lteq:
    call lex_advance                # consume =
    mov rdi, T_LTEQ
    jmp .so_emit_from_r12

# --- Equals: =>> or == or = ---
.so_eq:
    call lex_advance
    call lex_peek
    cmp al, '='
    je .so_eqeq
    cmp al, '>'
    je .so_maybe_broadcast

    # Bare =
    mov rdi, T_EQ
    jmp .so_emit_from_r12

.so_eqeq:
    call lex_advance
    mov rdi, T_EQEQ
    jmp .so_emit_from_r12

.so_maybe_broadcast:
    call lex_advance                # consume >
    call lex_peek
    cmp al, '>'
    jne .so_eq_gt
    call lex_advance                # consume second >
    mov rdi, T_BROADCAST
    jmp .so_emit_from_r12

.so_eq_gt:
    # => but not =>> — emit = and rewind past >
    mov rdi, T_EQ
    mov rsi, [rip + lex_source]
    add rsi, r12
    mov rdx, 1
    mov rcx, r13
    mov r8, r14
    call tok_buf_push
    dec qword ptr [rip + lex_pos]
    dec qword ptr [rip + lex_col]
    jmp .so_ret

# --- Pipe: |~> or | ---
.so_pipe:
    call lex_advance
    call lex_peek
    cmp al, '~'
    jne .so_bare_pipe

    mov rdi, 1
    call lex_peek_at
    cmp al, '>'
    jne .so_bare_pipe

    call lex_advance                # consume ~
    call lex_advance                # consume >
    mov rdi, T_FILTERED
    jmp .so_emit_from_r12

.so_bare_pipe:
    mov rdi, T_PIPE
    jmp .so_emit_from_r12

# --- Greater-than: >= or > ---
.so_gt:
    call lex_advance
    call lex_peek
    cmp al, '='
    jne .so_bare_gt
    call lex_advance
    mov rdi, T_GTEQ
    jmp .so_emit_from_r12

.so_bare_gt:
    mov rdi, T_GT
    jmp .so_emit_from_r12

# --- Bang: != or ! ---
.so_bang:
    call lex_advance
    call lex_peek
    cmp al, '='
    jne .so_bare_bang
    call lex_advance
    mov rdi, T_BANGEQ
    jmp .so_emit_from_r12

.so_bare_bang:
    mov rdi, T_BANG
    jmp .so_emit_from_r12

# --- Common emit helper ---
.so_emit_from_r12:
    mov rsi, [rip + lex_source]
    add rsi, r12
    mov rdx, [rip + lex_pos]
    sub rdx, r12
    mov rcx, r13
    mov r8, r14
    call tok_buf_push

.so_ret:
    pop r14
    pop r13
    pop r12
    ret


# ============================================================================
# M2: LEXER — Main Loop
# ============================================================================

# next_token() — scan one token, push to buffer
next_token:
    call lex_skip_ws

    call lex_peek
    test al, al
    jz .nt_eof

    # Identifier or keyword?
    mov dil, al
    push rax
    call is_alpha
    pop rcx
    test eax, eax
    jnz .nt_ident

    # Number?
    mov dil, cl
    push rcx
    call is_digit
    pop rcx
    test eax, eax
    jnz .nt_number

    # String?
    cmp cl, '"'
    je .nt_string

    # Operator or delimiter
    jmp .nt_operator

.nt_ident:
    call scan_ident
    ret
.nt_number:
    call scan_number
    ret
.nt_string:
    call scan_string
    ret
.nt_operator:
    call scan_operator
    ret

.nt_eof:
    # Push EOF token
    mov rdi, T_EOF
    mov rsi, [rip + lex_source]
    add rsi, [rip + lex_pos]
    xor edx, edx
    mov rcx, [rip + lex_line]
    mov r8, [rip + lex_col]
    call tok_buf_push
    ret

# lex_all() — tokenize entire source
lex_all:
.lxa_loop:
    call lex_peek
    test al, al
    jz .lxa_eof
    call next_token
    jmp .lxa_loop
.lxa_eof:
    call next_token                 # push EOF token
    ret


# ============================================================================
# M2: LEXER — Keyword Lookup
# ============================================================================

# keyword_lookup(text: rdi, len: rsi) -> token type in rax
# Linear scan through keyword table. Returns T_IDENT if no match.
keyword_lookup:
    push rbx
    push r12
    push r13
    push r14

    mov r12, rdi                    # text pointer
    mov r13, rsi                    # text length

    lea r14, [rip + kw_table]

.kl_loop:
    # Load keyword entry: [ptr, len, type]
    mov rdi, [r14]                  # keyword string ptr
    test rdi, rdi
    jz .kl_miss                     # null = end of table

    mov rbx, [r14 + 8]             # keyword length
    cmp rbx, r13
    jne .kl_next                    # length mismatch — skip

    # Compare text
    mov rsi, r12
    mov rdx, rbx
    call str_ncmp
    test eax, eax
    jz .kl_hit

.kl_next:
    add r14, 24                     # next entry (3 quads)
    jmp .kl_loop

.kl_hit:
    mov rax, [r14 + 16]            # token type
    pop r14
    pop r13
    pop r12
    pop rbx
    ret

.kl_miss:
    mov eax, T_IDENT
    pop r14
    pop r13
    pop r12
    pop rbx
    ret


# ============================================================================
# M2: TOKEN PRINTING (debug output)
# ============================================================================

# print_tokens() — print all tokens in buffer
print_tokens:
    push rbx
    push r12

    xor ebx, ebx                   # index

.pt_loop:
    cmp rbx, [rip + tok_count]
    jge .pt_done

    # Compute token address
    mov rax, rbx
    imul rax, TOK_SIZE
    add rax, [rip + tok_buf]
    mov r12, rax

    # Print line number
    mov rdi, [r12 + TOK_LINE]
    call print_int
    mov dil, ':'
    call print_char
    mov rdi, [r12 + TOK_COL]
    call print_int
    mov dil, ' '
    call print_char

    # Print token type name
    mov rdi, [r12 + TOK_TYPE]
    call print_tok_type_name
    mov dil, ' '
    call print_char

    # Print token text
    mov dil, '['
    call print_char
    mov rdi, [r12 + TOK_START]
    mov rsi, [r12 + TOK_LEN]
    call print_bytes
    mov dil, ']'
    call print_char
    call print_newline

    inc rbx
    jmp .pt_loop

.pt_done:
    # Print summary
    lea rdi, [rip + msg_total]
    call print_str
    mov rdi, [rip + tok_count]
    call print_int
    lea rdi, [rip + msg_tokens]
    call print_str
    call print_newline

    pop r12
    pop rbx
    ret

# print_tok_type_name(type: rdi)
print_tok_type_name:
    # Look up in type name table
    push rbx

    lea rbx, [rip + tok_type_names]
.pttn_loop:
    mov rax, [rbx]                  # type value
    cmp rax, -1
    je .pttn_unknown
    cmp rax, rdi
    je .pttn_found
    add rbx, 16
    jmp .pttn_loop

.pttn_found:
    mov rdi, [rbx + 8]
    call print_str
    pop rbx
    ret

.pttn_unknown:
    lea rdi, [rip + msg_unknown_tok]
    call print_str
    pop rbx
    ret


# ============================================================================
# M3: PARSER — AST Node Allocation
# ============================================================================

# ast_new(tag: rdi) -> node ptr in rax
ast_new:
    push rbx
    mov rbx, rdi
    mov rdi, AST_NODE_SIZE
    call arena_alloc
    mov qword ptr [rax + AST_TAG], rbx
    mov qword ptr [rax + AST_F1], 0
    mov qword ptr [rax + AST_F2], 0
    mov qword ptr [rax + AST_F3], 0
    mov qword ptr [rax + AST_F4], 0
    mov qword ptr [rax + AST_F5], 0
    pop rbx
    ret

# node_list_new() -> list ptr in rax
# Creates a growable list of AST node pointers.
node_list_new:
    push rbx
    mov rdi, NL_HEADER
    call arena_alloc
    mov rbx, rax
    mov qword ptr [rbx + NL_COUNT], 0
    mov qword ptr [rbx + NL_CAP], 16
    # Allocate initial items array (16 pointers)
    mov rdi, 128                    # 16 * 8
    call arena_alloc
    mov [rbx + NL_ITEMS], rax
    mov rax, rbx
    pop rbx
    ret

# node_list_push(list: rdi, node: rsi)
node_list_push:
    push rbx
    push r12
    mov rbx, rdi
    mov r12, rsi

    mov rax, [rbx + NL_COUNT]
    cmp rax, [rbx + NL_CAP]
    jl .nlp_write

    # Grow: double capacity
    mov rdi, [rbx + NL_CAP]
    shl rdi, 1
    mov [rbx + NL_CAP], rdi
    shl rdi, 3                      # * 8 bytes per pointer
    call arena_alloc
    # Copy old items
    push rax
    mov rdi, rax
    mov rsi, [rbx + NL_ITEMS]
    mov rdx, [rbx + NL_COUNT]
    shl rdx, 3
    call mem_copy
    pop rax
    mov [rbx + NL_ITEMS], rax

.nlp_write:
    mov rax, [rbx + NL_COUNT]
    mov rcx, [rbx + NL_ITEMS]
    mov [rcx + rax*8], r12
    inc qword ptr [rbx + NL_COUNT]

    pop r12
    pop rbx
    ret


# ============================================================================
# M3: PARSER — Token Stream Access
# ============================================================================

# par_current() -> pointer to current token in rax
par_current:
    mov rax, [rip + parse_pos]
    imul rax, TOK_SIZE
    add rax, [rip + tok_buf]
    ret

# par_peek_type() -> current token type in rax
par_peek_type:
    call par_current
    mov rax, [rax + TOK_TYPE]
    ret

# par_advance() -> pointer to consumed token in rax
par_advance:
    call par_current
    mov rcx, [rip + parse_pos]
    inc rcx
    # Don't advance past end
    cmp rcx, [rip + tok_count]
    jg .padv_ret
    mov [rip + parse_pos], rcx
.padv_ret:
    ret

# par_expect(type: rdi) -> pointer to consumed token in rax (or error)
par_expect:
    push rbx
    mov rbx, rdi
    call par_peek_type
    cmp rax, rbx
    jne .pexp_err
    call par_advance
    pop rbx
    ret

.pexp_err:
    # Print error: "Parse error at line:col: expected X, got Y"
    push rdi
    lea rdi, [rip + msg_parse_err]
    call print_str
    call par_current
    mov rdi, [rax + TOK_LINE]
    call print_int
    mov dil, ':'
    call print_char
    call par_current
    mov rdi, [rax + TOK_COL]
    call print_int
    lea rdi, [rip + msg_expected]
    call print_str
    pop rdi
    call print_int
    lea rdi, [rip + msg_got]
    call print_str
    call par_peek_type
    mov rdi, rax
    call print_int
    call print_newline

    mov edi, 1
    call sys_exit

# par_match(type: rdi) -> 1 if matched (and advanced), 0 if not
par_match:
    push rbx
    mov rbx, rdi
    call par_peek_type
    cmp rax, rbx
    jne .pmatch_no
    call par_advance
    mov eax, 1
    pop rbx
    ret
.pmatch_no:
    xor eax, eax
    pop rbx
    ret

# par_at(type: rdi) -> 1 if current token is type, 0 otherwise
par_at:
    push rbx
    mov rbx, rdi
    call par_peek_type
    cmp rax, rbx
    sete al
    movzx eax, al
    pop rbx
    ret


# ============================================================================
# M3: PARSER — Top Level
# ============================================================================

# parse_program() -> AST_PROGRAM node
parse_program:
    push r12
    push r13

    mov qword ptr [rip + parse_pos], 0

    # Create program node
    mov rdi, AST_PROGRAM
    call ast_new
    mov r12, rax                    # program node

    # Create declarations list
    call node_list_new
    mov r13, rax                    # decls list
    mov [r12 + AST_F1], r13

.pp_loop:
    call par_peek_type
    cmp rax, T_EOF
    je .pp_done

    call parse_declaration
    test rax, rax
    jz .pp_done

    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .pp_loop

.pp_done:
    mov rax, r12
    pop r13
    pop r12
    ret

# parse_declaration() -> AST node or 0
parse_declaration:
    call par_peek_type

    cmp rax, T_MOMENT
    je .pd_moment
    cmp rax, T_NODE
    je .pd_node
    cmp rax, T_PATH
    je .pd_path
    cmp rax, T_MIND
    je .pd_mind
    cmp rax, T_FN
    je .pd_fn
    cmp rax, T_LET
    je .pd_let
    cmp rax, T_STRUCT
    je .pd_struct

    # Try parsing as expression statement
    call parse_expr_stmt
    ret

.pd_moment:
    call parse_moment_decl
    ret
.pd_node:
    call parse_node_decl
    ret
.pd_path:
    call parse_path_decl
    ret
.pd_mind:
    call parse_mind_decl
    ret
.pd_fn:
    call parse_fn_decl
    ret
.pd_let:
    call parse_let_stmt
    ret
.pd_struct:
    call parse_struct_decl
    ret


# ============================================================================
# M3: PARSER — Declarations
# ============================================================================

# parse_moment_decl() -> AST_MOMENT_DECL
# moment Name { field: value, ... }
parse_moment_decl:
    push r12
    push r13

    mov rdi, AST_MOMENT_DECL
    call ast_new
    mov r12, rax

    # consume "moment"
    call par_advance

    # expect identifier (name)
    mov rdi, T_IDENT
    call par_expect
    mov [r12 + AST_F1], rax         # name token

    # expect {
    mov rdi, T_LBRACE
    call par_expect

    # Parse fields until }
    call node_list_new
    mov r13, rax
    mov [r12 + AST_F2], r13

.pmd_field_loop:
    call par_peek_type
    cmp rax, T_RBRACE
    je .pmd_close
    cmp rax, T_EOF
    je .pmd_close

    # Parse field: name: expr
    call parse_field_def
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .pmd_field_loop

.pmd_close:
    mov rdi, T_RBRACE
    call par_expect

    mov rax, r12
    pop r13
    pop r12
    ret

# parse_field_def() -> AST_FIELD_DEF { f1=name_tok, f2=value (expr or list) }
parse_field_def:
    push r12
    push r13

    mov rdi, AST_FIELD_DEF
    call ast_new
    mov r12, rax

    # name (identifier or keyword used as field name)
    call par_advance
    mov [r12 + AST_F1], rax

    # expect :
    mov rdi, T_COLON
    call par_expect

    # Parse first value expression
    call parse_expr
    mov [r12 + AST_F2], rax

    # Check for comma — but only if it's NOT a field separator
    # (field separator: comma followed by ident/kw + colon = new field)
    call .pfd_comma_is_value_sep
    test eax, eax
    jz .pfd_done

    # Consume the comma
    call par_advance

    # Multiple values — wrap in list
    mov r13, [r12 + AST_F2]        # first value

    mov rdi, AST_LIST_LIT
    call ast_new
    mov [r12 + AST_F2], rax
    push rax

    call node_list_new
    pop rcx
    mov [rcx + AST_F1], rax
    mov rcx, rax                    # list ptr

    # Push first value
    mov rdi, rcx
    mov rsi, r13
    push rcx
    call node_list_push
    pop rcx

.pfd_more:
    # Parse next value
    push rcx
    call parse_expr
    pop rcx
    mov rdi, rcx
    mov rsi, rax
    push rcx
    call node_list_push
    pop rcx

    # More commas (with same lookahead check)?
    push rcx
    call .pfd_comma_is_value_sep
    pop rcx
    test eax, eax
    jz .pfd_done
    push rcx
    call par_advance                # consume comma
    pop rcx
    jmp .pfd_more

.pfd_done:
    mov rax, r12
    pop r13
    pop r12
    ret

# .pfd_comma_is_value_sep() -> 1 if comma is a value separator, 0 if field separator
# Checks: current=COMMA, and token at pos+2 is NOT COLON (meaning it's not "ident:")
.pfd_comma_is_value_sep:
    # First check: is current token a comma?
    call par_peek_type
    cmp rax, T_COMMA
    jne .pfd_csv_no

    # Look ahead: token at pos+2 (after comma + one token)
    mov rax, [rip + parse_pos]
    add rax, 2
    cmp rax, [rip + tok_count]
    jge .pfd_csv_yes                # can't lookahead → treat as value sep

    imul rax, TOK_SIZE
    add rax, [rip + tok_buf]
    mov rax, [rax + TOK_TYPE]
    cmp rax, T_COLON
    je .pfd_csv_no                  # ident: pattern → field separator

.pfd_csv_yes:
    mov eax, 1
    ret
.pfd_csv_no:
    xor eax, eax
    ret

# parse_node_decl() -> AST_NODE_DECL { f1=name, f2=type_tok, f3=body_list }
parse_node_decl:
    push r12
    push r13

    mov rdi, AST_NODE_DECL
    call ast_new
    mov r12, rax

    # consume "node"
    call par_advance

    # name
    mov rdi, T_IDENT
    call par_expect
    mov [r12 + AST_F1], rax

    # expect :
    mov rdi, T_COLON
    call par_expect

    # type (perceiver, knower, rememberer, reasoner, feeler, actor)
    call par_advance
    mov [r12 + AST_F2], rax

    # expect {
    mov rdi, T_LBRACE
    call par_expect

    # Parse body statements
    call node_list_new
    mov r13, rax
    mov [r12 + AST_F3], r13

.pnd_body:
    call par_peek_type
    cmp rax, T_RBRACE
    je .pnd_close
    cmp rax, T_EOF
    je .pnd_close

    call parse_stmt
    test rax, rax
    jz .pnd_close
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .pnd_body

.pnd_close:
    mov rdi, T_RBRACE
    call par_expect
    mov rax, r12
    pop r13
    pop r12
    ret

# parse_path_decl() -> AST_PATH_DECL { f1=name, f2=body_list }
parse_path_decl:
    push r12
    push r13

    mov rdi, AST_PATH_DECL
    call ast_new
    mov r12, rax

    # consume "path"
    call par_advance

    # name
    mov rdi, T_IDENT
    call par_expect
    mov [r12 + AST_F1], rax

    # expect {
    mov rdi, T_LBRACE
    call par_expect

    # Parse body (sequence of statements/expressions with flow ops)
    call node_list_new
    mov r13, rax
    mov [r12 + AST_F2], r13

.ppd_body:
    call par_peek_type
    cmp rax, T_RBRACE
    je .ppd_close
    cmp rax, T_EOF
    je .ppd_close

    call parse_stmt
    test rax, rax
    jz .ppd_close
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .ppd_body

.ppd_close:
    mov rdi, T_RBRACE
    call par_expect
    mov rax, r12
    pop r13
    pop r12
    ret

# parse_mind_decl() -> AST_MIND_DECL { f1=name, f2=body_list }
parse_mind_decl:
    push r12
    push r13

    mov rdi, AST_MIND_DECL
    call ast_new
    mov r12, rax

    # consume "mind"
    call par_advance

    # name
    mov rdi, T_IDENT
    call par_expect
    mov [r12 + AST_F1], rax

    # expect {
    mov rdi, T_LBRACE
    call par_expect

    # Parse body
    call node_list_new
    mov r13, rax
    mov [r12 + AST_F2], r13

.pmnd_body:
    call par_peek_type
    cmp rax, T_RBRACE
    je .pmnd_close
    cmp rax, T_EOF
    je .pmnd_close

    call parse_stmt
    test rax, rax
    jz .pmnd_close
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .pmnd_body

.pmnd_close:
    mov rdi, T_RBRACE
    call par_expect
    mov rax, r12
    pop r13
    pop r12
    ret

# parse_fn_decl() -> AST_FN_DECL { f1=name, f2=params_list, f3=body_list }
parse_fn_decl:
    push r12
    push r13
    push r14

    mov rdi, AST_FN_DECL
    call ast_new
    mov r12, rax

    # consume "fn"
    call par_advance

    # name
    mov rdi, T_IDENT
    call par_expect
    mov [r12 + AST_F1], rax

    # expect (
    mov rdi, T_LPAREN
    call par_expect

    # Parse parameters
    call node_list_new
    mov r13, rax
    mov [r12 + AST_F2], r13

    call par_peek_type
    cmp rax, T_RPAREN
    je .pfd_close_params

.pfd_param:
    call par_advance                # param name token
    mov rdi, r13
    mov rsi, rax
    call node_list_push

    # Check for comma
    mov rdi, T_COMMA
    call par_match
    test eax, eax
    jnz .pfd_param

.pfd_close_params:
    mov rdi, T_RPAREN
    call par_expect

    # expect {
    mov rdi, T_LBRACE
    call par_expect

    # Parse body
    call node_list_new
    mov r14, rax
    mov [r12 + AST_F3], r14

.pfd_body:
    call par_peek_type
    cmp rax, T_RBRACE
    je .pfd_close
    cmp rax, T_EOF
    je .pfd_close

    call parse_stmt
    test rax, rax
    jz .pfd_close
    mov rdi, r14
    mov rsi, rax
    call node_list_push
    jmp .pfd_body

.pfd_close:
    mov rdi, T_RBRACE
    call par_expect
    mov rax, r12
    pop r14
    pop r13
    pop r12
    ret

# parse_struct_decl() -> AST_STRUCT_DECL { f1=name, f2=fields_list }
parse_struct_decl:
    push r12
    push r13

    mov rdi, AST_STRUCT_DECL
    call ast_new
    mov r12, rax

    call par_advance                # consume "struct"

    mov rdi, T_IDENT
    call par_expect
    mov [r12 + AST_F1], rax

    mov rdi, T_LBRACE
    call par_expect

    call node_list_new
    mov r13, rax
    mov [r12 + AST_F2], r13

.psd_field:
    call par_peek_type
    cmp rax, T_RBRACE
    je .psd_close
    cmp rax, T_EOF
    je .psd_close

    call parse_field_def
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .psd_field

.psd_close:
    mov rdi, T_RBRACE
    call par_expect
    mov rax, r12
    pop r13
    pop r12
    ret


# ============================================================================
# M3: PARSER — Statements
# ============================================================================

# parse_stmt() -> AST node
parse_stmt:
    call par_peek_type

    cmp rax, T_LET
    je .ps_let
    cmp rax, T_IF
    je .ps_if
    cmp rax, T_WHILE
    je .ps_while
    cmp rax, T_FOR
    je .ps_for
    cmp rax, T_RETURN
    je .ps_return
    cmp rax, T_WHEN
    je .ps_when
    cmp rax, T_EMIT
    je .ps_emit
    cmp rax, T_RESPOND
    je .ps_respond
    cmp rax, T_AWARENESS
    je .ps_awareness

    # Keyword-started block-like declarations within bodies
    cmp rax, T_MOMENT
    je .ps_moment_inner
    cmp rax, T_NODE
    je .ps_node_inner
    cmp rax, T_PATH
    je .ps_path_inner
    cmp rax, T_FN
    je .ps_fn_inner

    # Default: expression statement
    call parse_expr_stmt
    ret

.ps_let:
    call parse_let_stmt
    ret
.ps_if:
    call parse_if_stmt
    ret
.ps_while:
    call parse_while_stmt
    ret
.ps_for:
    call parse_for_stmt
    ret
.ps_return:
    call parse_return_stmt
    ret
.ps_when:
    call parse_when_block
    ret
.ps_emit:
    call parse_emit_stmt
    ret
.ps_respond:
    call parse_respond_stmt
    ret
.ps_awareness:
    call parse_awareness_block
    ret
.ps_moment_inner:
    call parse_moment_decl
    ret
.ps_node_inner:
    call parse_node_decl
    ret
.ps_path_inner:
    call parse_path_decl
    ret
.ps_fn_inner:
    call parse_fn_decl
    ret

# parse_let_stmt() -> AST_LET_STMT { f1=name_tok, f2=value_expr }
parse_let_stmt:
    push r12

    mov rdi, AST_LET_STMT
    call ast_new
    mov r12, rax

    call par_advance                # consume "let"

    # name
    mov rdi, T_IDENT
    call par_expect
    mov [r12 + AST_F1], rax

    # expect = or flow operator (<<~ is common in let)
    call par_peek_type
    cmp rax, T_EQ
    je .pls_eq
    # For flow operators, the expr parser handles them
    jmp .pls_value

.pls_eq:
    call par_advance                # consume =

.pls_value:
    call parse_expr
    mov [r12 + AST_F2], rax

    mov rax, r12
    pop r12
    ret

# parse_if_stmt() -> AST_IF_STMT { f1=cond, f2=then_list, f3=else_list }
parse_if_stmt:
    push r12
    push r13

    mov rdi, AST_IF_STMT
    call ast_new
    mov r12, rax

    call par_advance                # consume "if"

    # condition expression
    call parse_expr
    mov [r12 + AST_F1], rax

    # expect {
    mov rdi, T_LBRACE
    call par_expect

    # then body
    call node_list_new
    mov r13, rax
    mov [r12 + AST_F2], r13

.pif_then:
    call par_peek_type
    cmp rax, T_RBRACE
    je .pif_then_close
    cmp rax, T_EOF
    je .pif_then_close
    call parse_stmt
    test rax, rax
    jz .pif_then_close
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .pif_then

.pif_then_close:
    mov rdi, T_RBRACE
    call par_expect

    # Check for else
    mov rdi, T_ELSE
    call par_match
    test eax, eax
    jz .pif_done

    # else { ... }
    mov rdi, T_LBRACE
    call par_expect

    call node_list_new
    mov r13, rax
    mov [r12 + AST_F3], r13

.pif_else:
    call par_peek_type
    cmp rax, T_RBRACE
    je .pif_else_close
    cmp rax, T_EOF
    je .pif_else_close
    call parse_stmt
    test rax, rax
    jz .pif_else_close
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .pif_else

.pif_else_close:
    mov rdi, T_RBRACE
    call par_expect

.pif_done:
    mov rax, r12
    pop r13
    pop r12
    ret

# parse_while_stmt() -> AST_WHILE_STMT { f1=cond, f2=body_list }
parse_while_stmt:
    push r12
    push r13

    mov rdi, AST_WHILE_STMT
    call ast_new
    mov r12, rax

    call par_advance                # consume "while"

    call parse_expr
    mov [r12 + AST_F1], rax

    mov rdi, T_LBRACE
    call par_expect

    call node_list_new
    mov r13, rax
    mov [r12 + AST_F2], r13

.pwh_body:
    call par_peek_type
    cmp rax, T_RBRACE
    je .pwh_close
    cmp rax, T_EOF
    je .pwh_close
    call parse_stmt
    test rax, rax
    jz .pwh_close
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .pwh_body

.pwh_close:
    mov rdi, T_RBRACE
    call par_expect
    mov rax, r12
    pop r13
    pop r12
    ret

# parse_for_stmt() -> AST_FOR_STMT { f1=var_tok, f2=iter_expr, f3=body_list }
parse_for_stmt:
    push r12
    push r13

    mov rdi, AST_FOR_STMT
    call ast_new
    mov r12, rax

    call par_advance                # consume "for"

    mov rdi, T_IDENT
    call par_expect
    mov [r12 + AST_F1], rax

    mov rdi, T_IN
    call par_expect

    call parse_expr
    mov [r12 + AST_F2], rax

    mov rdi, T_LBRACE
    call par_expect

    call node_list_new
    mov r13, rax
    mov [r12 + AST_F3], r13

.pfor_body:
    call par_peek_type
    cmp rax, T_RBRACE
    je .pfor_close
    cmp rax, T_EOF
    je .pfor_close
    call parse_stmt
    test rax, rax
    jz .pfor_close
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .pfor_body

.pfor_close:
    mov rdi, T_RBRACE
    call par_expect
    mov rax, r12
    pop r13
    pop r12
    ret

# parse_return_stmt() -> AST_RETURN_STMT { f1=expr }
parse_return_stmt:
    push r12

    mov rdi, AST_RETURN_STMT
    call ast_new
    mov r12, rax

    call par_advance                # consume "return"

    # Check if there's an expression to return
    call par_peek_type
    cmp rax, T_RBRACE
    je .pret_done
    cmp rax, T_EOF
    je .pret_done

    call parse_expr
    mov [r12 + AST_F1], rax

.pret_done:
    mov rax, r12
    pop r12
    ret

# parse_when_block() -> AST_WHEN_BLOCK { f1=signal_type_tok, f2=var_tok, f3=body }
parse_when_block:
    push r12
    push r13

    mov rdi, AST_WHEN_BLOCK
    call ast_new
    mov r12, rax

    call par_advance                # consume "when"

    # Signal type (identifier or keyword)
    call par_advance
    mov [r12 + AST_F1], rax

    # "arrives" keyword
    mov rdi, T_ARRIVES
    call par_match

    # "as" var_name (optional)
    mov rdi, T_AS
    call par_match
    test eax, eax
    jz .pwb_body

    call par_advance                # var name
    mov [r12 + AST_F2], rax

.pwb_body:
    mov rdi, T_LBRACE
    call par_expect

    call node_list_new
    mov r13, rax
    mov [r12 + AST_F3], r13

.pwb_stmts:
    call par_peek_type
    cmp rax, T_RBRACE
    je .pwb_close
    cmp rax, T_EOF
    je .pwb_close
    call parse_stmt
    test rax, rax
    jz .pwb_close
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .pwb_stmts

.pwb_close:
    mov rdi, T_RBRACE
    call par_expect
    mov rax, r12
    pop r13
    pop r12
    ret

# parse_emit_stmt() -> AST_EMIT_STMT { f1=signal_expr }
parse_emit_stmt:
    push r12

    mov rdi, AST_EMIT_STMT
    call ast_new
    mov r12, rax

    call par_advance                # consume "emit"

    call parse_expr
    mov [r12 + AST_F1], rax

    mov rax, r12
    pop r12
    ret

# parse_respond_stmt() -> AST_RESPOND_STMT { f1=body_expr }
parse_respond_stmt:
    push r12

    mov rdi, AST_RESPOND_STMT
    call ast_new
    mov r12, rax

    call par_advance                # consume "respond"
    call parse_expr
    mov [r12 + AST_F1], rax

    mov rax, r12
    pop r12
    ret

# parse_awareness_block() -> AST_AWARENESS { f1=body_list }
parse_awareness_block:
    push r12
    push r13

    mov rdi, AST_AWARENESS
    call ast_new
    mov r12, rax

    call par_advance                # consume "awareness"
    mov rdi, T_LBRACE
    call par_expect

    call node_list_new
    mov r13, rax
    mov [r12 + AST_F1], r13

.pab_body:
    call par_peek_type
    cmp rax, T_RBRACE
    je .pab_close
    cmp rax, T_EOF
    je .pab_close
    call parse_field_def
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .pab_body

.pab_close:
    mov rdi, T_RBRACE
    call par_expect
    mov rax, r12
    pop r13
    pop r12
    ret

# parse_expr_stmt() -> AST_EXPR_STMT { f1=expr }
parse_expr_stmt:
    push r12

    mov rdi, AST_EXPR_STMT
    call ast_new
    mov r12, rax

    call parse_expr
    mov [r12 + AST_F1], rax

    mov rax, r12
    pop r12
    ret


# ============================================================================
# M3: PARSER — Expressions (Precedence Climbing)
# ============================================================================

# parse_expr() -> AST node
# Entry point: handles flow operators (lowest precedence)
parse_expr:
    push r12

    call parse_comparison
    mov r12, rax

.pe_flow:
    call par_peek_type

    # Check for flow operators
    cmp rax, T_FLOW_FWD
    je .pe_flow_op
    cmp rax, T_FLOW_BWD
    je .pe_flow_op
    cmp rax, T_BROADCAST
    je .pe_flow_op
    cmp rax, T_ENRICH_OP
    je .pe_flow_op
    cmp rax, T_TENTATIVE
    je .pe_flow_op
    cmp rax, T_BIDIR
    je .pe_flow_op
    cmp rax, T_FILTERED
    je .pe_flow_op

    mov rax, r12
    pop r12
    ret

.pe_flow_op:
    push r13
    call par_advance                # consume flow op
    mov r13, rax                    # save operator token

    mov rdi, AST_FLOW_EXPR
    call ast_new
    mov [rax + AST_F1], r12         # left
    mov [rax + AST_F3], r13         # operator token

    push rax
    call parse_comparison
    pop rcx
    mov [rcx + AST_F2], rax         # right
    mov r12, rcx

    pop r13
    jmp .pe_flow

# parse_comparison() -> handles ==, !=, <, >, <=, >=
parse_comparison:
    push r12

    call parse_addition
    mov r12, rax

.pcmp_loop:
    call par_peek_type
    cmp rax, T_EQEQ
    je .pcmp_op
    cmp rax, T_BANGEQ
    je .pcmp_op
    cmp rax, T_LT
    je .pcmp_op
    cmp rax, T_GT
    je .pcmp_op
    cmp rax, T_LTEQ
    je .pcmp_op
    cmp rax, T_GTEQ
    je .pcmp_op
    # Nova keyword-operators used as binary modifiers
    cmp rax, T_WHEN
    je .pcmp_op
    cmp rax, T_WITH
    je .pcmp_op
    cmp rax, T_AGAINST
    je .pcmp_op
    cmp rax, T_FROM
    je .pcmp_op
    cmp rax, T_IF
    je .pcmp_op
    cmp rax, T_WHERE
    je .pcmp_op

    mov rax, r12
    pop r12
    ret

.pcmp_op:
    push r13
    call par_advance
    mov r13, rax                    # operator token

    mov rdi, AST_BIN_OP
    call ast_new
    mov [rax + AST_F1], r12         # left
    mov [rax + AST_F3], r13         # op

    push rax
    call parse_addition
    pop rcx
    mov [rcx + AST_F2], rax         # right
    mov r12, rcx

    pop r13
    jmp .pcmp_loop

# parse_addition() -> handles + -
parse_addition:
    push r12

    call parse_multiplication
    mov r12, rax

.padd_loop:
    call par_peek_type
    cmp rax, T_PLUS
    je .padd_op
    cmp rax, T_MINUS
    je .padd_op

    mov rax, r12
    pop r12
    ret

.padd_op:
    push r13
    call par_advance
    mov r13, rax

    mov rdi, AST_BIN_OP
    call ast_new
    mov [rax + AST_F1], r12
    mov [rax + AST_F3], r13

    push rax
    call parse_multiplication
    pop rcx
    mov [rcx + AST_F2], rax
    mov r12, rcx

    pop r13
    jmp .padd_loop

# parse_multiplication() -> handles * /
parse_multiplication:
    push r12

    call parse_unary
    mov r12, rax

.pmul_loop:
    call par_peek_type
    cmp rax, T_STAR
    je .pmul_op
    cmp rax, T_SLASH
    je .pmul_op

    mov rax, r12
    pop r12
    ret

.pmul_op:
    push r13
    call par_advance
    mov r13, rax

    mov rdi, AST_BIN_OP
    call ast_new
    mov [rax + AST_F1], r12
    mov [rax + AST_F3], r13

    push rax
    call parse_unary
    pop rcx
    mov [rcx + AST_F2], rax
    mov r12, rcx

    pop r13
    jmp .pmul_loop

# parse_unary() -> handles ! -
parse_unary:
    call par_peek_type
    cmp rax, T_BANG
    je .pu_op
    cmp rax, T_MINUS
    je .pu_op

    call parse_postfix
    ret

.pu_op:
    push r12
    call par_advance
    mov r12, rax                    # operator token

    mov rdi, AST_UNARY_OP
    call ast_new
    mov [rax + AST_F2], r12         # op token
    push rax

    call parse_unary                # recursive
    pop rcx
    mov [rcx + AST_F1], rax         # operand
    mov rax, rcx
    pop r12
    ret

# parse_postfix() -> handles .field, @field, (args), [index]
parse_postfix:
    push r12

    call parse_primary
    mov r12, rax

.ppf_loop:
    call par_peek_type

    cmp rax, T_DOT
    je .ppf_dot
    cmp rax, T_AT
    je .ppf_at
    cmp rax, T_LPAREN
    je .ppf_call
    cmp rax, T_LBRACKET
    je .ppf_index

    # Juxtaposition: only when LHS is a bare identifier
    # (not literals, not already-constructed calls — prevents runaway)
    mov rcx, [r12 + AST_TAG]
    cmp rcx, AST_IDENT
    je .ppf_check_juxt
    jmp .ppf_done

.ppf_check_juxt:
    # Adjacent primary tokens are implicit call arguments
    # e.g. "entity Person { ... }", "warmth 0.7", "expectation "text""
    # But NOT if the adjacent token is followed by ":" (it's a field name then)
    cmp rax, T_IDENT
    je .ppf_juxt_guard
    cmp rax, T_INT_LIT
    je .ppf_juxt
    cmp rax, T_FLOAT_LIT
    je .ppf_juxt
    cmp rax, T_STRING_LIT
    je .ppf_juxt
    cmp rax, T_LBRACE
    je .ppf_juxt_block_only
    # Cognitive keywords as adjacent values (also need colon guard)
    cmp rax, T_MOMENT
    jl .ppf_done
    cmp rax, T_BOOL_TYPE
    jle .ppf_juxt_guard
    jmp .ppf_done

.ppf_juxt_guard:
    # First: exclude keyword-operators from juxtaposition
    # These are binary ops parsed at comparison level, not arguments
    call par_peek_type
    cmp rax, T_WHEN
    je .ppf_done
    cmp rax, T_WITH
    je .ppf_done
    cmp rax, T_AGAINST
    je .ppf_done
    cmp rax, T_FROM
    je .ppf_done
    cmp rax, T_IF
    je .ppf_done
    cmp rax, T_WHERE
    je .ppf_done
    cmp rax, T_UNLESS
    je .ppf_done
    cmp rax, T_AS
    je .ppf_done
    cmp rax, T_IN
    je .ppf_done

    # Don't juxtapose if next token is followed by ':' (field separator)
    mov rax, [rip + parse_pos]
    inc rax
    cmp rax, [rip + tok_count]
    jge .ppf_juxt                   # can't lookahead, allow juxt
    imul rax, TOK_SIZE
    add rax, [rip + tok_buf]
    mov rax, [rax + TOK_TYPE]
    cmp rax, T_COLON
    je .ppf_done                    # followed by : → field name, not argument
    jmp .ppf_juxt                   # not a field name → proceed with juxtaposition

.ppf_done:
    mov rax, r12
    pop r12
    ret

.ppf_juxt_block_only:
    # Just { follows an ident — parse block as single arg
    mov rdi, AST_CALL
    call ast_new
    mov [rax + AST_F1], r12
    push rax

    call node_list_new
    mov rbx, rax
    pop rcx
    push rcx
    mov [rcx + AST_F2], rbx

    push rbx
    call parse_primary              # parse { ... } block
    pop rbx
    mov rdi, rbx
    mov rsi, rax
    call node_list_push

    pop r12
    jmp .ppf_loop

.ppf_juxt:
    # Wrap in implicit CALL: callee=r12, arg=next primary
    mov rdi, AST_CALL
    call ast_new
    mov [rax + AST_F1], r12
    push rax

    call node_list_new
    mov rbx, rax
    pop rcx
    push rcx
    mov [rcx + AST_F2], rbx

    # Parse one argument (primary only, not full expr to avoid greediness)
    call parse_primary
    mov rdi, rbx
    mov rsi, rax
    call node_list_push

    # Check if followed by { or [ — if so, parse as additional arg
    call par_peek_type
    cmp rax, T_LBRACE
    je .ppf_juxt_extra
    cmp rax, T_LBRACKET
    je .ppf_juxt_extra
    jmp .ppf_juxt_done

.ppf_juxt_extra:
    push rbx
    call parse_primary              # parses { ... } block or [ ... ] list
    pop rbx
    mov rdi, rbx
    mov rsi, rax
    call node_list_push

.ppf_juxt_done:
    pop r12                         # the CALL node
    jmp .ppf_loop

.ppf_dot:
    call par_advance                # consume .
    mov rdi, AST_DOT_ACCESS
    call ast_new
    mov [rax + AST_F1], r12         # object

    push rax
    call par_advance                # field name token
    pop rcx
    mov [rcx + AST_F2], rax         # field
    mov r12, rcx
    jmp .ppf_loop

.ppf_at:
    call par_advance                # consume @
    mov rdi, AST_MOMENT_ACCESS
    call ast_new
    mov [rax + AST_F1], r12

    push rax
    call par_advance                # field name
    pop rcx
    mov [rcx + AST_F2], rax
    mov r12, rcx
    jmp .ppf_loop

.ppf_call:
    call par_advance                # consume (
    mov rdi, AST_CALL
    call ast_new
    mov [rax + AST_F1], r12         # callee
    push rax

    # Parse arguments
    call node_list_new
    mov rbx, rax
    pop rcx
    push rcx
    mov [rcx + AST_F2], rbx         # args list

    call par_peek_type
    cmp rax, T_RPAREN
    je .ppf_call_close

.ppf_call_arg:
    call parse_expr
    mov rdi, rbx
    mov rsi, rax
    call node_list_push

    mov rdi, T_COMMA
    call par_match
    test eax, eax
    jnz .ppf_call_arg

.ppf_call_close:
    mov rdi, T_RPAREN
    call par_expect
    pop r12                         # the CALL node
    jmp .ppf_loop

.ppf_index:
    call par_advance                # consume [
    mov rdi, AST_INDEX_EXPR
    call ast_new
    mov [rax + AST_F1], r12
    push rax

    call parse_expr
    pop rcx
    mov [rcx + AST_F2], rax

    mov rdi, T_RBRACKET
    call par_expect
    mov r12, rcx
    jmp .ppf_loop


# ============================================================================
# M3: PARSER — Primary Expressions
# ============================================================================

# parse_primary() -> AST node (literal, ident, parenthesized, list, block)
parse_primary:
    call par_peek_type

    cmp rax, T_INT_LIT
    je .ppr_int
    cmp rax, T_FLOAT_LIT
    je .ppr_float
    cmp rax, T_STRING_LIT
    je .ppr_string
    cmp rax, T_TRUE
    je .ppr_true
    cmp rax, T_FALSE
    je .ppr_false
    cmp rax, T_NONE
    je .ppr_none
    cmp rax, T_IDENT
    je .ppr_ident
    cmp rax, T_LPAREN
    je .ppr_paren
    cmp rax, T_LBRACKET
    je .ppr_list
    cmp rax, T_LBRACE
    je .ppr_block

    # Any cognitive/standard keyword can appear as identifier in expressions
    cmp rax, T_MOMENT
    jl .ppr_error
    cmp rax, T_BOOL_TYPE
    jle .ppr_ident_like

.ppr_error:
    # Unknown primary — emit identifier-like node from current token
    mov rdi, AST_IDENT
    call ast_new
    push rax
    call par_advance
    pop rcx
    mov [rcx + AST_F1], rax
    mov rax, rcx
    ret

.ppr_ident_like:
.ppr_ident:
    mov rdi, AST_IDENT
    call ast_new
    push rax
    call par_advance
    pop rcx
    mov [rcx + AST_F1], rax         # token
    mov rax, rcx
    ret

.ppr_int:
    mov rdi, AST_INT_LIT
    call ast_new
    push rax
    call par_advance
    pop rcx
    mov [rcx + AST_F1], rax
    mov rax, rcx
    ret

.ppr_float:
    mov rdi, AST_FLOAT_LIT
    call ast_new
    push rax
    call par_advance
    pop rcx
    mov [rcx + AST_F1], rax
    mov rax, rcx
    ret

.ppr_string:
    mov rdi, AST_STRING_LIT
    call ast_new
    push rax
    call par_advance
    pop rcx
    mov [rcx + AST_F1], rax
    mov rax, rcx
    ret

.ppr_true:
    mov rdi, AST_BOOL_LIT
    call ast_new
    mov qword ptr [rax + AST_F1], 1
    push rax
    call par_advance
    pop rax
    ret

.ppr_false:
    mov rdi, AST_BOOL_LIT
    call ast_new
    mov qword ptr [rax + AST_F1], 0
    push rax
    call par_advance
    pop rax
    ret

.ppr_none:
    mov rdi, AST_NONE_LIT
    call ast_new
    push rax
    call par_advance
    pop rax
    ret

.ppr_paren:
    call par_advance                # consume (
    call parse_expr
    push rax
    mov rdi, T_RPAREN
    call par_expect
    pop rax
    ret

.ppr_list:
    push r12
    push r13

    call par_advance                # consume [
    mov rdi, AST_LIST_LIT
    call ast_new
    mov r12, rax

    call node_list_new
    mov r13, rax
    mov [r12 + AST_F1], r13

    call par_peek_type
    cmp rax, T_RBRACKET
    je .ppr_list_close

.ppr_list_elem:
    call parse_expr
    mov rdi, r13
    mov rsi, rax
    call node_list_push

    mov rdi, T_COMMA
    call par_match
    test eax, eax
    jnz .ppr_list_elem

.ppr_list_close:
    mov rdi, T_RBRACKET
    call par_expect
    mov rax, r12
    pop r13
    pop r12
    ret

.ppr_block:
    push r12
    push r13

    call par_advance                # consume {

    # Lookahead: if first content is "ident/kw :" → parse as field block
    call par_peek_type
    cmp rax, T_RBRACE
    je .ppr_block_stmt              # empty block

    # Check if token after current could be ':'
    push rax
    mov rdi, 1
    # Peek at next token (pos+1)
    mov rax, [rip + parse_pos]
    inc rax
    cmp rax, [rip + tok_count]
    jge .ppr_block_no_lookahead
    imul rax, TOK_SIZE
    add rax, [rip + tok_buf]
    mov rax, [rax + TOK_TYPE]
    cmp rax, T_COLON
    pop rax
    je .ppr_block_fields
    jmp .ppr_block_stmt

.ppr_block_no_lookahead:
    pop rax

.ppr_block_stmt:
    mov rdi, AST_BLOCK
    call ast_new
    mov r12, rax

    call node_list_new
    mov r13, rax
    mov [r12 + AST_F1], r13

.ppr_block_loop:
    call par_peek_type
    cmp rax, T_RBRACE
    je .ppr_block_close
    cmp rax, T_EOF
    je .ppr_block_close

    call parse_stmt
    test rax, rax
    jz .ppr_block_close
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .ppr_block_loop

.ppr_block_close:
    mov rdi, T_RBRACE
    call par_expect
    mov rax, r12
    pop r13
    pop r12
    ret

# Block with field definitions (ident: value)
.ppr_block_fields:
    mov rdi, AST_BLOCK
    call ast_new
    mov r12, rax

    call node_list_new
    mov r13, rax
    mov [r12 + AST_F1], r13

.ppr_bf_loop:
    # Skip optional comma between fields
    mov rdi, T_COMMA
    call par_match

    call par_peek_type
    cmp rax, T_RBRACE
    je .ppr_bf_close
    cmp rax, T_EOF
    je .ppr_bf_close

    call parse_field_def
    mov rdi, r13
    mov rsi, rax
    call node_list_push
    jmp .ppr_bf_loop

.ppr_bf_close:
    mov rdi, T_RBRACE
    call par_expect
    mov rax, r12
    pop r13
    pop r12
    ret


# ============================================================================
# M3: PARSER — AST Printer (Debug)
# ============================================================================

# print_ast(node: rdi, indent: esi) — recursively print AST tree
print_ast:
    push rbx
    push r12
    push r13
    push r14

    mov r12, rdi                    # node
    mov r13d, esi                   # indent level

    test r12, r12
    jz .pa_null

    # Print indentation
    mov ecx, r13d
.pa_indent:
    test ecx, ecx
    jz .pa_tag
    mov dil, ' '
    push rcx
    call print_char
    call print_char
    pop rcx
    dec ecx
    jmp .pa_indent

.pa_tag:
    # Print node tag name
    mov rdi, [r12 + AST_TAG]
    call print_ast_tag_name

    # Dispatch based on tag for details
    mov rax, [r12 + AST_TAG]

    cmp rax, AST_PROGRAM
    je .pa_program
    cmp rax, AST_MOMENT_DECL
    je .pa_named_block
    cmp rax, AST_NODE_DECL
    je .pa_node_decl
    cmp rax, AST_PATH_DECL
    je .pa_named_block
    cmp rax, AST_MIND_DECL
    je .pa_named_block
    cmp rax, AST_FN_DECL
    je .pa_fn_decl
    cmp rax, AST_LET_STMT
    je .pa_let
    cmp rax, AST_IF_STMT
    je .pa_if
    cmp rax, AST_WHILE_STMT
    je .pa_while_for
    cmp rax, AST_FOR_STMT
    je .pa_while_for
    cmp rax, AST_RETURN_STMT
    je .pa_unary_node
    cmp rax, AST_EMIT_STMT
    je .pa_unary_node
    cmp rax, AST_RESPOND_STMT
    je .pa_unary_node
    cmp rax, AST_EXPR_STMT
    je .pa_unary_node
    cmp rax, AST_WHEN_BLOCK
    je .pa_when
    cmp rax, AST_AWARENESS
    je .pa_list_node
    cmp rax, AST_BLOCK
    je .pa_list_node
    cmp rax, AST_LIST_LIT
    je .pa_list_node
    cmp rax, AST_BIN_OP
    je .pa_binop
    cmp rax, AST_FLOW_EXPR
    je .pa_binop
    cmp rax, AST_UNARY_OP
    je .pa_unary_node
    cmp rax, AST_CALL
    je .pa_call
    cmp rax, AST_DOT_ACCESS
    je .pa_dot
    cmp rax, AST_MOMENT_ACCESS
    je .pa_dot
    cmp rax, AST_INDEX_EXPR
    je .pa_dot
    cmp rax, AST_IDENT
    je .pa_ident
    cmp rax, AST_INT_LIT
    je .pa_token_val
    cmp rax, AST_FLOAT_LIT
    je .pa_token_val
    cmp rax, AST_STRING_LIT
    je .pa_token_val
    cmp rax, AST_BOOL_LIT
    je .pa_bool_val
    cmp rax, AST_FIELD_DEF
    je .pa_field_def
    cmp rax, AST_STRUCT_DECL
    je .pa_named_block

    # Default: just print tag and newline
    call print_newline
    jmp .pa_ret

# --- Program: print all children ---
.pa_program:
    call print_newline
    mov r14, [r12 + AST_F1]         # decls list
    test r14, r14
    jz .pa_ret
    call .pa_print_list
    jmp .pa_ret

# --- Named block (moment, path, mind, struct): print name + children ---
.pa_named_block:
    mov dil, ' '
    call print_char
    mov rax, [r12 + AST_F1]         # name token
    test rax, rax
    jz .pa_nb_nl
    mov rdi, [rax + TOK_START]
    mov rsi, [rax + TOK_LEN]
    call print_bytes
.pa_nb_nl:
    call print_newline
    mov r14, [r12 + AST_F2]
    test r14, r14
    jz .pa_ret
    call .pa_print_list
    jmp .pa_ret

# --- Node decl: name : type ---
.pa_node_decl:
    mov dil, ' '
    call print_char
    mov rax, [r12 + AST_F1]
    mov rdi, [rax + TOK_START]
    mov rsi, [rax + TOK_LEN]
    call print_bytes
    mov dil, ':'
    call print_char
    mov rax, [r12 + AST_F2]
    test rax, rax
    jz .pa_nd_nl
    mov rdi, [rax + TOK_START]
    mov rsi, [rax + TOK_LEN]
    call print_bytes
.pa_nd_nl:
    call print_newline
    mov r14, [r12 + AST_F3]
    test r14, r14
    jz .pa_ret
    call .pa_print_list
    jmp .pa_ret

# --- Fn decl ---
.pa_fn_decl:
    mov dil, ' '
    call print_char
    mov rax, [r12 + AST_F1]
    mov rdi, [rax + TOK_START]
    mov rsi, [rax + TOK_LEN]
    call print_bytes
    call print_newline
    mov r14, [r12 + AST_F3]
    test r14, r14
    jz .pa_ret
    call .pa_print_list
    jmp .pa_ret

# --- Let statement ---
.pa_let:
    mov dil, ' '
    call print_char
    mov rax, [r12 + AST_F1]
    mov rdi, [rax + TOK_START]
    mov rsi, [rax + TOK_LEN]
    call print_bytes
    call print_newline
    # Print value expression
    mov rdi, [r12 + AST_F2]
    lea esi, [r13d + 1]
    call print_ast
    jmp .pa_ret

# --- If statement ---
.pa_if:
    call print_newline
    # condition
    mov rdi, [r12 + AST_F1]
    lea esi, [r13d + 1]
    call print_ast
    # then
    mov r14, [r12 + AST_F2]
    test r14, r14
    jz .pa_if_else
    call .pa_print_list
.pa_if_else:
    mov r14, [r12 + AST_F3]
    test r14, r14
    jz .pa_ret
    call .pa_print_list
    jmp .pa_ret

# --- While/For ---
.pa_while_for:
    call print_newline
    mov rdi, [r12 + AST_F1]
    lea esi, [r13d + 1]
    call print_ast
    mov r14, [r12 + AST_F2]
    test r14, r14
    jz .pa_wf_f3
    call .pa_print_list
.pa_wf_f3:
    mov r14, [r12 + AST_F3]
    test r14, r14
    jz .pa_ret
    call .pa_print_list
    jmp .pa_ret

# --- When block ---
.pa_when:
    mov dil, ' '
    call print_char
    mov rax, [r12 + AST_F1]
    test rax, rax
    jz .pa_when_nl
    mov rdi, [rax + TOK_START]
    mov rsi, [rax + TOK_LEN]
    call print_bytes
.pa_when_nl:
    call print_newline
    mov r14, [r12 + AST_F3]
    test r14, r14
    jz .pa_ret
    call .pa_print_list
    jmp .pa_ret

# --- Unary node (return, emit, respond, expr_stmt, unary_op): one child ---
.pa_unary_node:
    call print_newline
    mov rdi, [r12 + AST_F1]
    test rdi, rdi
    jz .pa_ret
    lea esi, [r13d + 1]
    call print_ast
    jmp .pa_ret

# --- List node (awareness, block, list_lit): f1 is a list ---
.pa_list_node:
    call print_newline
    mov r14, [r12 + AST_F1]
    test r14, r14
    jz .pa_ret
    call .pa_print_list
    jmp .pa_ret

# --- BinOp / FlowExpr ---
.pa_binop:
    mov dil, ' '
    call print_char
    mov rax, [r12 + AST_F3]        # op token
    test rax, rax
    jz .pa_bo_nl
    mov rdi, [rax + TOK_START]
    mov rsi, [rax + TOK_LEN]
    call print_bytes
.pa_bo_nl:
    call print_newline
    mov rdi, [r12 + AST_F1]
    lea esi, [r13d + 1]
    call print_ast
    mov rdi, [r12 + AST_F2]
    lea esi, [r13d + 1]
    call print_ast
    jmp .pa_ret

# --- Call ---
.pa_call:
    call print_newline
    mov rdi, [r12 + AST_F1]         # callee
    lea esi, [r13d + 1]
    call print_ast
    mov r14, [r12 + AST_F2]         # args list
    test r14, r14
    jz .pa_ret
    call .pa_print_list
    jmp .pa_ret

# --- Dot/Moment/Index access ---
.pa_dot:
    mov dil, ' '
    call print_char
    mov rax, [r12 + AST_F2]
    test rax, rax
    jz .pa_dot_nl
    mov rdi, [rax + TOK_START]
    mov rsi, [rax + TOK_LEN]
    call print_bytes
.pa_dot_nl:
    call print_newline
    mov rdi, [r12 + AST_F1]
    lea esi, [r13d + 1]
    call print_ast
    jmp .pa_ret

# --- Ident ---
.pa_ident:
    mov dil, ' '
    call print_char
    mov rax, [r12 + AST_F1]
    test rax, rax
    jz .pa_id_nl
    mov rdi, [rax + TOK_START]
    mov rsi, [rax + TOK_LEN]
    call print_bytes
.pa_id_nl:
    call print_newline
    jmp .pa_ret

# --- Token-valued literal (int, float, string) ---
.pa_token_val:
    mov dil, ' '
    call print_char
    mov rax, [r12 + AST_F1]
    test rax, rax
    jz .pa_tv_nl
    mov rdi, [rax + TOK_START]
    mov rsi, [rax + TOK_LEN]
    call print_bytes
.pa_tv_nl:
    call print_newline
    jmp .pa_ret

# --- Bool literal ---
.pa_bool_val:
    mov dil, ' '
    call print_char
    mov rax, [r12 + AST_F1]
    test rax, rax
    jz .pa_bv_f
    lea rdi, [rip + str_true]
    jmp .pa_bv_print
.pa_bv_f:
    lea rdi, [rip + str_false]
.pa_bv_print:
    call print_str
    call print_newline
    jmp .pa_ret

# --- Field def ---
.pa_field_def:
    mov dil, ' '
    call print_char
    mov rax, [r12 + AST_F1]
    test rax, rax
    jz .pa_fd_nl
    mov rdi, [rax + TOK_START]
    mov rsi, [rax + TOK_LEN]
    call print_bytes
.pa_fd_nl:
    call print_newline
    mov rdi, [r12 + AST_F2]
    test rdi, rdi
    jz .pa_ret
    lea esi, [r13d + 1]
    call print_ast
    jmp .pa_ret

.pa_null:
.pa_ret:
    pop r14
    pop r13
    pop r12
    pop rbx
    ret

# --- Helper: print all nodes in list r14 at indent r13+1 ---
.pa_print_list:
    push r15
    push rbx

    mov rbx, [r14 + NL_ITEMS]
    xor r15d, r15d
.pa_pl_loop:
    cmp r15, [r14 + NL_COUNT]
    jge .pa_pl_done
    mov rdi, [rbx + r15*8]
    lea esi, [r13d + 1]
    call print_ast
    inc r15
    jmp .pa_pl_loop
.pa_pl_done:
    pop rbx
    pop r15
    ret

# print_ast_tag_name(tag: rdi)
print_ast_tag_name:
    push rbx
    lea rbx, [rip + ast_tag_names]
.patn_loop:
    mov rax, [rbx]
    cmp rax, -1
    je .patn_unknown
    cmp rax, rdi
    je .patn_found
    add rbx, 16
    jmp .patn_loop
.patn_found:
    mov rdi, [rbx + 8]
    call print_str
    pop rbx
    ret
.patn_unknown:
    lea rdi, [rip + msg_unknown_ast]
    call print_str
    pop rbx
    ret


# ============================================================================
# READ-ONLY DATA
# ============================================================================
.section .rodata

msg_usage:       .asciz "Usage: nova_boot <file.nova>\n"
msg_heap_fail:   .asciz "Fatal: heap init failed\n"
msg_oom:         .asciz "Fatal: out of memory\n"
msg_read_err:    .asciz "Fatal: cannot read file\n"
msg_unterm_str:  .asciz "Error: unterminated string at line "
msg_total:       .asciz "Total: "
msg_tokens:      .asciz " tokens"
msg_unknown_tok: .asciz "???"
msg_parse_err:   .asciz "Parse error at "
msg_expected:    .asciz " expected type "
msg_got:         .asciz " got "
msg_unknown_ast: .asciz "AST:?"
str_true:        .asciz "true"
str_false:       .asciz "false"

# --- AST tag name table ---
ast_tag_names:
    .quad AST_PROGRAM,       atn_program
    .quad AST_MOMENT_DECL,   atn_moment
    .quad AST_NODE_DECL,     atn_node
    .quad AST_PATH_DECL,     atn_path
    .quad AST_MIND_DECL,     atn_mind
    .quad AST_FN_DECL,       atn_fn
    .quad AST_LET_STMT,      atn_let
    .quad AST_IF_STMT,       atn_if
    .quad AST_WHILE_STMT,    atn_while
    .quad AST_FOR_STMT,      atn_for
    .quad AST_RETURN_STMT,   atn_return
    .quad AST_BLOCK,         atn_block
    .quad AST_BIN_OP,        atn_binop
    .quad AST_UNARY_OP,      atn_unary
    .quad AST_CALL,          atn_call
    .quad AST_DOT_ACCESS,    atn_dot
    .quad AST_MOMENT_ACCESS, atn_moment_acc
    .quad AST_IDENT,         atn_ident
    .quad AST_INT_LIT,       atn_int
    .quad AST_FLOAT_LIT,     atn_float
    .quad AST_STRING_LIT,    atn_string
    .quad AST_BOOL_LIT,      atn_bool
    .quad AST_NONE_LIT,      atn_none
    .quad AST_LIST_LIT,      atn_list
    .quad AST_FLOW_EXPR,     atn_flow
    .quad AST_EMIT_STMT,     atn_emit
    .quad AST_WHEN_BLOCK,    atn_when
    .quad AST_AWARENESS,     atn_awareness
    .quad AST_STRUCT_DECL,   atn_struct
    .quad AST_FIELD_DEF,     atn_field
    .quad AST_ASSIGN_STMT,   atn_assign
    .quad AST_RESPOND_STMT,  atn_respond
    .quad AST_CHANNEL_DECL,  atn_channel
    .quad AST_INDEX_EXPR,    atn_index
    .quad AST_EXPR_STMT,     atn_expr_stmt
    .quad -1, 0

atn_program:     .asciz "Program"
atn_moment:      .asciz "Moment"
atn_node:        .asciz "Node"
atn_path:        .asciz "Path"
atn_mind:        .asciz "Mind"
atn_fn:          .asciz "Fn"
atn_let:         .asciz "Let"
atn_if:          .asciz "If"
atn_while:       .asciz "While"
atn_for:         .asciz "For"
atn_return:      .asciz "Return"
atn_block:       .asciz "Block"
atn_binop:       .asciz "BinOp"
atn_unary:       .asciz "Unary"
atn_call:        .asciz "Call"
atn_dot:         .asciz "Dot"
atn_moment_acc:  .asciz "MomentAcc"
atn_ident:       .asciz "Ident"
atn_int:         .asciz "Int"
atn_float:       .asciz "Float"
atn_string:      .asciz "Str"
atn_bool:        .asciz "Bool"
atn_none:        .asciz "None"
atn_list:        .asciz "List"
atn_flow:        .asciz "Flow"
atn_emit:        .asciz "Emit"
atn_when:        .asciz "When"
atn_awareness:   .asciz "Awareness"
atn_struct:      .asciz "Struct"
atn_field:       .asciz "Field"
atn_assign:      .asciz "Assign"
atn_respond:     .asciz "Respond"
atn_channel:     .asciz "Channel"
atn_index:       .asciz "Index"
atn_expr_stmt:   .asciz "ExprStmt"

# --- Keyword table: [ptr, len, type] triples, terminated by null ---
kw_table:
    .quad kw_moment,       6, T_MOMENT
    .quad kw_signal,       6, T_SIGNAL
    .quad kw_node,         4, T_NODE
    .quad kw_path,         4, T_PATH
    .quad kw_channel,      7, T_CHANNEL
    .quad kw_field,        5, T_FIELD
    .quad kw_mind,         4, T_MIND
    .quad kw_when,         4, T_WHEN
    .quad kw_arrives,      7, T_ARRIVES
    .quad kw_respond,      7, T_RESPOND
    .quad kw_emit,         4, T_EMIT
    .quad kw_absorb,       6, T_ABSORB
    .quad kw_reflect,      7, T_REFLECT
    .quad kw_enrich,       6, T_ENRICH_KW
    .quad kw_decay,        5, T_DECAY
    .quad kw_strengthen,   10, T_STRENGTHEN
    .quad kw_crystallize,  12, T_CRYSTALLIZE
    .quad kw_promote,      7, T_PROMOTE
    .quad kw_academic,     8, T_ACADEMIC
    .quad kw_experiential, 13, T_EXPERIENTIAL
    .quad kw_felt,         4, T_FELT
    .quad kw_consequence,  11, T_CONSEQUENCE
    .quad kw_entity,       6, T_ENTITY
    .quad kw_perceiver,    9, T_PERCEIVER
    .quad kw_knower,       6, T_KNOWER
    .quad kw_rememberer,   10, T_REMEMBERER
    .quad kw_reasoner,     8, T_REASONER
    .quad kw_feeler,       6, T_FEELER
    .quad kw_actor,        5, T_ACTOR
    .quad kw_awareness,    9, T_AWARENESS
    .quad kw_salience,     8, T_SALIENCE
    .quad kw_trace,        5, T_TRACE
    .quad kw_urgency,      7, T_URGENCY
    .quad kw_event_signal,    12, T_EVENT_SIGNAL
    .quad kw_question_signal, 15, T_QUESTION_SIGNAL
    .quad kw_order_signal,    12, T_ORDER_SIGNAL
    .quad kw_command_signal,  14, T_COMMAND_SIGNAL
    .quad kw_request_signal,  14, T_REQUEST_SIGNAL
    .quad kw_forward,      7, T_FORWARD
    .quad kw_backward,     8, T_BACKWARD
    .quad kw_reflex,       6, T_REFLEX
    .quad kw_reason_by,    9, T_REASON_BY
    .quad kw_pattern_match, 13, T_PATTERN_MATCH
    .quad kw_analogize,    9, T_ANALOGIZE
    .quad kw_weight_by,    9, T_WEIGHT_BY
    .quad kw_drift_toward, 12, T_DRIFT_TOWARD
    .quad kw_shift,        5, T_SHIFT
    .quad kw_let,          3, T_LET
    .quad kw_if,           2, T_IF
    .quad kw_else,         4, T_ELSE
    .quad kw_while,        5, T_WHILE
    .quad kw_for,          3, T_FOR
    .quad kw_in,           2, T_IN
    .quad kw_fn,           2, T_FN
    .quad kw_return,       6, T_RETURN
    .quad kw_type,         4, T_TYPE
    .quad kw_struct,       6, T_STRUCT
    .quad kw_enum,         4, T_ENUM
    .quad kw_true,         4, T_TRUE
    .quad kw_false,        5, T_FALSE
    .quad kw_none,         4, T_NONE
    .quad kw_as,           2, T_AS
    .quad kw_with,         4, T_WITH
    .quad kw_against,      7, T_AGAINST
    .quad kw_from,         4, T_FROM
    .quad kw_to,           2, T_TO
    .quad kw_where,        5, T_WHERE
    .quad kw_unless,       6, T_UNLESS
    .quad kw_was,          3, T_WAS
    .quad kw_simultaneously, 14, T_SIMULTANEOUSLY
    .quad kw_int_t,        3, T_INT_TYPE
    .quad kw_float_t,      5, T_FLOAT_TYPE
    .quad kw_str_t,        3, T_STR_TYPE
    .quad kw_bool_t,       4, T_BOOL_TYPE
    .quad 0, 0, 0                   # sentinel

# --- Keyword strings ---
kw_moment:          .asciz "moment"
kw_signal:          .asciz "signal"
kw_node:            .asciz "node"
kw_path:            .asciz "path"
kw_channel:         .asciz "channel"
kw_field:           .asciz "field"
kw_mind:            .asciz "mind"
kw_when:            .asciz "when"
kw_arrives:         .asciz "arrives"
kw_respond:         .asciz "respond"
kw_emit:            .asciz "emit"
kw_absorb:          .asciz "absorb"
kw_reflect:         .asciz "reflect"
kw_enrich:          .asciz "enrich"
kw_decay:           .asciz "decay"
kw_strengthen:      .asciz "strengthen"
kw_crystallize:     .asciz "crystallize"
kw_promote:         .asciz "promote"
kw_academic:        .asciz "academic"
kw_experiential:    .asciz "experiential"
kw_felt:            .asciz "felt"
kw_consequence:     .asciz "consequence"
kw_entity:          .asciz "entity"
kw_perceiver:       .asciz "perceiver"
kw_knower:          .asciz "knower"
kw_rememberer:      .asciz "rememberer"
kw_reasoner:        .asciz "reasoner"
kw_feeler:          .asciz "feeler"
kw_actor:           .asciz "actor"
kw_awareness:       .asciz "awareness"
kw_salience:        .asciz "salience"
kw_trace:           .asciz "trace"
kw_urgency:         .asciz "urgency"
kw_event_signal:    .asciz "event_signal"
kw_question_signal: .asciz "question_signal"
kw_order_signal:    .asciz "order_signal"
kw_command_signal:  .asciz "command_signal"
kw_request_signal:  .asciz "request_signal"
kw_forward:         .asciz "forward"
kw_backward:        .asciz "backward"
kw_reflex:          .asciz "reflex"
kw_reason_by:       .asciz "reason_by"
kw_pattern_match:   .asciz "pattern_match"
kw_analogize:       .asciz "analogize"
kw_weight_by:       .asciz "weight_by"
kw_drift_toward:    .asciz "drift_toward"
kw_shift:           .asciz "shift"
kw_let:             .asciz "let"
kw_if:              .asciz "if"
kw_else:            .asciz "else"
kw_while:           .asciz "while"
kw_for:             .asciz "for"
kw_in:              .asciz "in"
kw_fn:              .asciz "fn"
kw_return:          .asciz "return"
kw_type:            .asciz "type"
kw_struct:          .asciz "struct"
kw_enum:            .asciz "enum"
kw_true:            .asciz "true"
kw_false:           .asciz "false"
kw_none:            .asciz "none"
kw_as:              .asciz "as"
kw_with:            .asciz "with"
kw_against:         .asciz "against"
kw_from:            .asciz "from"
kw_to:              .asciz "to"
kw_where:           .asciz "where"
kw_unless:          .asciz "unless"
kw_was:             .asciz "was"
kw_simultaneously:  .asciz "simultaneously"
kw_int_t:           .asciz "int"
kw_float_t:         .asciz "float"
kw_str_t:           .asciz "str"
kw_bool_t:          .asciz "bool"

# --- Token type name table: [type_value, name_ptr] pairs ---
tok_type_names:
    .quad T_EOF,             tn_eof
    .quad T_ERROR,           tn_error
    .quad T_INT_LIT,         tn_int_lit
    .quad T_FLOAT_LIT,       tn_float_lit
    .quad T_STRING_LIT,      tn_string_lit
    .quad T_IDENT,           tn_ident
    .quad T_PLUS,            tn_plus
    .quad T_MINUS,           tn_minus
    .quad T_STAR,            tn_star
    .quad T_SLASH,           tn_slash
    .quad T_EQ,              tn_eq
    .quad T_BANG,            tn_bang
    .quad T_LT,              tn_lt
    .quad T_GT,              tn_gt
    .quad T_AT,              tn_at
    .quad T_HASH,            tn_hash
    .quad T_AMP,             tn_amp
    .quad T_PIPE,            tn_pipe
    .quad T_TILDE,           tn_tilde
    .quad T_DOT,             tn_dot
    .quad T_EQEQ,           tn_eqeq
    .quad T_BANGEQ,         tn_bangeq
    .quad T_LTEQ,           tn_lteq
    .quad T_GTEQ,           tn_gteq
    .quad T_FLOW_FWD,       tn_flow_fwd
    .quad T_FLOW_BWD,       tn_flow_bwd
    .quad T_BROADCAST,      tn_broadcast
    .quad T_ENRICH_OP,      tn_enrich_op
    .quad T_TENTATIVE,      tn_tentative
    .quad T_BIDIR,          tn_bidir
    .quad T_FILTERED,       tn_filtered
    .quad T_LPAREN,         tn_lparen
    .quad T_RPAREN,         tn_rparen
    .quad T_LBRACE,         tn_lbrace
    .quad T_RBRACE,         tn_rbrace
    .quad T_LBRACKET,       tn_lbracket
    .quad T_RBRACKET,       tn_rbracket
    .quad T_COMMA,          tn_comma
    .quad T_COLON,          tn_colon
    .quad T_SEMI,           tn_semi
    .quad T_MOMENT,         tn_kw_moment
    .quad T_SIGNAL,         tn_kw_signal
    .quad T_NODE,           tn_kw_node
    .quad T_PATH,           tn_kw_path
    .quad T_CHANNEL,        tn_kw_channel
    .quad T_FIELD,          tn_kw_field
    .quad T_MIND,           tn_kw_mind
    .quad T_WHEN,           tn_kw_when
    .quad T_ARRIVES,        tn_kw_arrives
    .quad T_RESPOND,        tn_kw_respond
    .quad T_EMIT,           tn_kw_emit
    .quad T_ABSORB,         tn_kw_absorb
    .quad T_REFLECT,        tn_kw_reflect
    .quad T_ENRICH_KW,      tn_kw_enrich
    .quad T_DECAY,          tn_kw_decay
    .quad T_STRENGTHEN,     tn_kw_strengthen
    .quad T_CRYSTALLIZE,    tn_kw_crystallize
    .quad T_PROMOTE,        tn_kw_promote
    .quad T_ACADEMIC,       tn_kw_academic
    .quad T_EXPERIENTIAL,   tn_kw_experiential
    .quad T_FELT,           tn_kw_felt
    .quad T_CONSEQUENCE,    tn_kw_consequence
    .quad T_ENTITY,         tn_kw_entity
    .quad T_PERCEIVER,      tn_kw_perceiver
    .quad T_KNOWER,         tn_kw_knower
    .quad T_REMEMBERER,     tn_kw_rememberer
    .quad T_REASONER,       tn_kw_reasoner
    .quad T_FEELER,         tn_kw_feeler
    .quad T_ACTOR,          tn_kw_actor
    .quad T_AWARENESS,      tn_kw_awareness
    .quad T_SALIENCE,       tn_kw_salience
    .quad T_TRACE,          tn_kw_trace
    .quad T_URGENCY,        tn_kw_urgency
    .quad T_EVENT_SIGNAL,   tn_kw_event_signal
    .quad T_QUESTION_SIGNAL,tn_kw_question_signal
    .quad T_ORDER_SIGNAL,   tn_kw_order_signal
    .quad T_COMMAND_SIGNAL, tn_kw_command_signal
    .quad T_REQUEST_SIGNAL, tn_kw_request_signal
    .quad T_FORWARD,        tn_kw_forward
    .quad T_BACKWARD,       tn_kw_backward
    .quad T_REFLEX,         tn_kw_reflex
    .quad T_REASON_BY,      tn_kw_reason_by
    .quad T_PATTERN_MATCH,  tn_kw_pattern_match
    .quad T_ANALOGIZE,      tn_kw_analogize
    .quad T_WEIGHT_BY,      tn_kw_weight_by
    .quad T_DRIFT_TOWARD,   tn_kw_drift_toward
    .quad T_SHIFT,          tn_kw_shift
    .quad T_LET,            tn_kw_let
    .quad T_IF,             tn_kw_if
    .quad T_ELSE,           tn_kw_else
    .quad T_WHILE,          tn_kw_while
    .quad T_FOR,            tn_kw_for
    .quad T_IN,             tn_kw_in
    .quad T_FN,             tn_kw_fn
    .quad T_RETURN,         tn_kw_return
    .quad T_TYPE,           tn_kw_type
    .quad T_STRUCT,         tn_kw_struct
    .quad T_ENUM,           tn_kw_enum
    .quad T_TRUE,           tn_kw_true
    .quad T_FALSE,          tn_kw_false
    .quad T_NONE,           tn_kw_none
    .quad T_AS,             tn_kw_as
    .quad T_WITH,           tn_kw_with
    .quad T_AGAINST,        tn_kw_against
    .quad T_FROM,           tn_kw_from
    .quad T_TO,             tn_kw_to
    .quad T_WHERE,          tn_kw_where
    .quad T_UNLESS,         tn_kw_unless
    .quad T_WAS,            tn_kw_was
    .quad T_SIMULTANEOUSLY, tn_kw_simultaneously
    .quad T_INT_TYPE,       tn_kw_int_type
    .quad T_FLOAT_TYPE,     tn_kw_float_type
    .quad T_STR_TYPE,       tn_kw_str_type
    .quad T_BOOL_TYPE,      tn_kw_bool_type
    .quad -1, 0                     # sentinel

# --- Token type display names ---
tn_eof:               .asciz "EOF"
tn_error:             .asciz "ERROR"
tn_int_lit:           .asciz "INT"
tn_float_lit:         .asciz "FLOAT"
tn_string_lit:        .asciz "STRING"
tn_ident:             .asciz "IDENT"
tn_plus:              .asciz "+"
tn_minus:             .asciz "-"
tn_star:              .asciz "*"
tn_slash:             .asciz "/"
tn_eq:                .asciz "="
tn_bang:              .asciz "!"
tn_lt:                .asciz "<"
tn_gt:                .asciz ">"
tn_at:                .asciz "@"
tn_hash:              .asciz "#"
tn_amp:               .asciz "&"
tn_pipe:              .asciz "|"
tn_tilde:             .asciz "~"
tn_dot:               .asciz "."
tn_eqeq:             .asciz "=="
tn_bangeq:           .asciz "!="
tn_lteq:             .asciz "<="
tn_gteq:             .asciz ">="
tn_flow_fwd:         .asciz "~>"
tn_flow_bwd:         .asciz "<~"
tn_broadcast:        .asciz "=>>"
tn_enrich_op:        .asciz "<<~"
tn_tentative:        .asciz "~~>"
tn_bidir:            .asciz "<=>"
tn_filtered:         .asciz "|~>"
tn_lparen:           .asciz "("
tn_rparen:           .asciz ")"
tn_lbrace:           .asciz "{"
tn_rbrace:           .asciz "}"
tn_lbracket:         .asciz "["
tn_rbracket:         .asciz "]"
tn_comma:            .asciz ","
tn_colon:            .asciz ":"
tn_semi:             .asciz ";"
tn_kw_moment:        .asciz "KW:moment"
tn_kw_signal:        .asciz "KW:signal"
tn_kw_node:          .asciz "KW:node"
tn_kw_path:          .asciz "KW:path"
tn_kw_channel:       .asciz "KW:channel"
tn_kw_field:         .asciz "KW:field"
tn_kw_mind:          .asciz "KW:mind"
tn_kw_when:          .asciz "KW:when"
tn_kw_arrives:       .asciz "KW:arrives"
tn_kw_respond:       .asciz "KW:respond"
tn_kw_emit:          .asciz "KW:emit"
tn_kw_absorb:        .asciz "KW:absorb"
tn_kw_reflect:       .asciz "KW:reflect"
tn_kw_enrich:        .asciz "KW:enrich"
tn_kw_decay:         .asciz "KW:decay"
tn_kw_strengthen:    .asciz "KW:strengthen"
tn_kw_crystallize:   .asciz "KW:crystallize"
tn_kw_promote:       .asciz "KW:promote"
tn_kw_academic:      .asciz "KW:academic"
tn_kw_experiential:  .asciz "KW:experiential"
tn_kw_felt:          .asciz "KW:felt"
tn_kw_consequence:   .asciz "KW:consequence"
tn_kw_entity:        .asciz "KW:entity"
tn_kw_perceiver:     .asciz "KW:perceiver"
tn_kw_knower:        .asciz "KW:knower"
tn_kw_rememberer:    .asciz "KW:rememberer"
tn_kw_reasoner:      .asciz "KW:reasoner"
tn_kw_feeler:        .asciz "KW:feeler"
tn_kw_actor:         .asciz "KW:actor"
tn_kw_awareness:     .asciz "KW:awareness"
tn_kw_salience:      .asciz "KW:salience"
tn_kw_trace:         .asciz "KW:trace"
tn_kw_urgency:       .asciz "KW:urgency"
tn_kw_event_signal:  .asciz "KW:event_signal"
tn_kw_question_signal: .asciz "KW:question_signal"
tn_kw_order_signal:  .asciz "KW:order_signal"
tn_kw_command_signal:.asciz "KW:command_signal"
tn_kw_request_signal:.asciz "KW:request_signal"
tn_kw_forward:       .asciz "KW:forward"
tn_kw_backward:      .asciz "KW:backward"
tn_kw_reflex:        .asciz "KW:reflex"
tn_kw_reason_by:     .asciz "KW:reason_by"
tn_kw_pattern_match: .asciz "KW:pattern_match"
tn_kw_analogize:     .asciz "KW:analogize"
tn_kw_weight_by:     .asciz "KW:weight_by"
tn_kw_drift_toward:  .asciz "KW:drift_toward"
tn_kw_shift:         .asciz "KW:shift"
tn_kw_let:           .asciz "KW:let"
tn_kw_if:            .asciz "KW:if"
tn_kw_else:          .asciz "KW:else"
tn_kw_while:         .asciz "KW:while"
tn_kw_for:           .asciz "KW:for"
tn_kw_in:            .asciz "KW:in"
tn_kw_fn:            .asciz "KW:fn"
tn_kw_return:        .asciz "KW:return"
tn_kw_type:          .asciz "KW:type"
tn_kw_struct:        .asciz "KW:struct"
tn_kw_enum:          .asciz "KW:enum"
tn_kw_true:          .asciz "KW:true"
tn_kw_false:         .asciz "KW:false"
tn_kw_none:          .asciz "KW:none"
tn_kw_as:            .asciz "KW:as"
tn_kw_with:          .asciz "KW:with"
tn_kw_against:       .asciz "KW:against"
tn_kw_from:          .asciz "KW:from"
tn_kw_to:            .asciz "KW:to"
tn_kw_where:         .asciz "KW:where"
tn_kw_unless:        .asciz "KW:unless"
tn_kw_was:           .asciz "KW:was"
tn_kw_simultaneously:.asciz "KW:simultaneously"
tn_kw_int_type:      .asciz "KW:int"
tn_kw_float_type:    .asciz "KW:float"
tn_kw_str_type:      .asciz "KW:str"
tn_kw_bool_type:     .asciz "KW:bool"
