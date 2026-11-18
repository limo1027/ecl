.code

; int64_t runtime_setjmp(jmp_buf_t* env)
; 参数 env 在 rcx 中
; 返回 0 表示首次调用，返回非 0 表示从 longjmp 恢复
runtime_setjmp PROC
    ; 保存非易失性寄存器和栈指针
    mov [rcx + 0], rbx
    mov [rcx + 8], rbp
    mov [rcx + 16], rdi
    mov [rcx + 24], rsi
    mov [rcx + 32], rsp
    mov [rcx + 40], r12
    mov [rcx + 48], r13
    mov [rcx + 56], r14
    mov [rcx + 64], r15
    
    ; 保存返回地址 (RIP)
    mov rax, [rsp]
    mov [rcx + 72], rax
    
    ; 返回 0
    xor eax, eax
    ret
runtime_setjmp ENDP

; void runtime_longjmp(jmp_buf_t* env, int64_t val)
; 参数 env 在 rcx，val 在 rdx
runtime_longjmp PROC
    ; 恢复非易失性寄存器
    mov rbx, [rcx + 0]
    mov rbp, [rcx + 8]
    mov rdi, [rcx + 16]
    mov rsi, [rcx + 24]
    mov rsp, [rcx + 32]
    mov r12, [rcx + 40]
    mov r13, [rcx + 48]
    mov r14, [rcx + 56]
    mov r15, [rcx + 64]
    
    ; 恢复 RIP，并跳转过去
    mov rax, [rcx + 72]
    mov [rsp], rax
    
    ; 返回 val
    mov rax, rdx
    ret
runtime_longjmp ENDP

; uint64_t ecl_invoke(void* fn, uint64_t* slots, uint64_t types, int64_t nargs)
; 按 Windows x64 ABI 动态调用：前 4 个参数按位置进 rcx/rdx/r8/r9 或 xmm0-3，
; 其余参数放到栈上。types 每 2 位表示一个寄存器参数类型：
;   0 = 整数/指针, 1 = float, 2 = double
; 返回值 rax；xmm0 写入 runtime.c 的 ecl_xmm0_result。
EXTERN ecl_xmm0_result:QWORD

ecl_invoke PROC
    push rbp
    mov rbp, rsp
    push rdi
    push rsi
    mov r10, rcx            ; fn
    mov r11, rdx            ; slots
    mov rdi, r8             ; types
    mov rsi, r9             ; nargs

    ; 栈参数个数 = max(0, nargs - 4)
    xor rax, rax
    cmp rsi, 4
    jle no_stack_args
    mov rax, rsi
    sub rax, 4
    ; 分配 shadow space + 栈参数，16 字节对齐
    mov rcx, rax
    shl rcx, 3
    add rcx, 47
    and rcx, -16
    sub rsp, rcx
    xor rcx, rcx
copy_stack_args:
    mov rdx, qword ptr [r11 + rcx * 8 + 32]
    mov qword ptr [rsp + rcx * 8 + 32], rdx
    inc rcx
    cmp rcx, rax
    jl copy_stack_args
    jmp load_regs

no_stack_args:
    sub rsp, 32

load_regs:
    cmp rsi, 0
    jle do_call
    mov rax, rdi
    and rax, 3
    cmp rax, 1
    je arg0_float
    cmp rax, 2
    je arg0_double
    mov rcx, qword ptr [r11]
    jmp arg1
arg0_float:
    movss xmm0, dword ptr [r11]
    jmp arg1
arg0_double:
    movsd xmm0, qword ptr [r11]

arg1:
    cmp rsi, 1
    jle do_call
    mov rax, rdi
    shr rax, 2
    and rax, 3
    cmp rax, 1
    je arg1_float
    cmp rax, 2
    je arg1_double
    mov rdx, qword ptr [r11 + 8]
    jmp arg2
arg1_float:
    movss xmm1, dword ptr [r11 + 8]
    jmp arg2
arg1_double:
    movsd xmm1, qword ptr [r11 + 8]

arg2:
    cmp rsi, 2
    jle do_call
    mov rax, rdi
    shr rax, 4
    and rax, 3
    cmp rax, 1
    je arg2_float
    cmp rax, 2
    je arg2_double
    mov r8, qword ptr [r11 + 16]
    jmp arg3
arg2_float:
    movss xmm2, dword ptr [r11 + 16]
    jmp arg3
arg2_double:
    movsd xmm2, qword ptr [r11 + 16]

arg3:
    cmp rsi, 3
    jle do_call
    mov rax, rdi
    shr rax, 6
    and rax, 3
    cmp rax, 1
    je arg3_float
    cmp rax, 2
    je arg3_double
    mov r9, qword ptr [r11 + 24]
    jmp do_call
arg3_float:
    movss xmm3, dword ptr [r11 + 24]
    jmp do_call
arg3_double:
    movsd xmm3, qword ptr [r11 + 24]

do_call:
    mov rax, r10
    call rax
    movsd qword ptr [ecl_xmm0_result], xmm0
    lea rsp, [rbp - 16]
    pop rsi
    pop rdi
    pop rbp
    ret
ecl_invoke ENDP

END