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

    # Print token summary
    call print_tokens

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
