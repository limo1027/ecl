# ==========================================
# 用 ECL 调 ECL 编译出的 .dll
# 编译 dll: python main.py compile example/test.ecl --target dll
# 运行宿主: python main.py run example/simple.ecl
# ==========================================

dll_import "example/test.dll":
    def add(int a, int b) -> int
    def sub(int a, int b) -> int
    def mul(int a, int b) -> int
end

print("add(3, 5) =", add(3, 5))
print("sub(10, 4) =", sub(10, 4))
print("mul(6, 7) =", mul(6, 7))