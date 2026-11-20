# ==========================================
# lib/bf.ecl — Brainfuck 解释器（纯 ECL）
# ==========================================

class bf:
    def __exec__(str code) -> any:
        # 1. 过滤出 8 个有效字符
        ops <- []
        for ch in code:
            if ch == ">" or ch == "<" or ch == "+" or ch == "-" or ch == "." or ch == "," or ch == "[" or ch == "]":
                ops.append(ch)
            end
        end

        # 2. 括号配对：用栈生成跳转表
        jump <- dict()
        stack <- []
        for i in 0..len(ops):
            if ops[i] == "[":
                stack.append(i)
            elif ops[i] == "]":
                if len(stack) == 0:
                    ValueError("bf: unmatched ']' at index " + str(i))
                end
                j <- stack.pop()
                jump[j] <- i
                jump[i] <- j
            end
        end
        if len(stack) != 0:
            ValueError("bf: unmatched '[' at index " + str(stack[0]))
        end

        return BfContext(ops, jump)
    end
end

class BfContext:
    def __init__(list ops, dict jump) -> none:
        self.ops <- ops
        self.jump <- jump
    end

    def run() -> none:
        tape <- [0]          # 纸带，至少一格
        ptr <- 0             # 指针
        ip <- 0              # 指令指针

        while ip < len(self.ops):
            op <- self.ops[ip]

            if op == ">":
                ptr <- ptr + 1
                if ptr >= len(tape):
                    tape.append(0)
                end
            elif op == "<":
                if ptr == 0:
                    ValueError("bf: tape pointer underflow")
                end
                ptr <- ptr - 1
            elif op == "+":
                tape[ptr] <- tape[ptr] + 1
            elif op == "-":
                tape[ptr] <- tape[ptr] - 1
            elif op == ".":
                write(str(tape[ptr]) + " ")
            elif op == ",":
                # 读入一个字符，取第一个字节
                line <- input("")
                if len(line) > 0:
                    tape[ptr] <- int(line[0])
                else:
                    tape[ptr] <- 0
                end
            elif op == "[":
                if tape[ptr] == 0:
                    ip <- self.jump[ip]
                end
            elif op == "]":
                if tape[ptr] != 0:
                    ip <- self.jump[ip]
                end
            end

            ip <- ip + 1
        end

        print("")   # 收尾换行
    end
end

print("=== BF: 3 + 2 = 5 ===")
# +++>++ 在纸带上写 [3, 2]，然后 < 回第一格，[->+<] 把第一格加到第二格
code1 <- "+++>++<[->+<]>."
ctx1 <- bf().__exec__(code1)
ctx1.run()    # 期望输出 5

print("=== BF: 简单循环 ===")
# 5 次 +1，然后输出
code2 <- "+++++."
ctx2 <- @@bf(code2)
ctx2.run()    # 期望输出 5