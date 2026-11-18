# ==========================================
# dll_import 测试（需要 example/testdll.dll）
# 运行：python main.py run example/dll_test.ecl
# ==========================================

print("=== dll_import 测试 ===")

struct DllPoint:
    i32 x
    i32 y
end

dll_import "example/testdll.dll":
    def dll_add32(i32 a, i32 b) -> i32
    def dll_add64(i64 a, i64 b) -> i64
    def dll_sum5(int a, int b, int c, int d, int e) -> int
    def dll_addf(float a, float b) -> float
    def dll_addd(double a, double b) -> double
    def dll_wlen(str s) -> i32
    def dll_clen(cstr s) -> i32
    def dll_hello() -> str
    def dll_chello() -> cstr
    def dll_point_sum(ptr DllPoint p) -> i32
    def dll_set_point(ptr DllPoint p, i32 x, i32 y) -> none
    def dll_get_point(out DllPoint p) -> none
end

print("add32:", dll_add32(20, 22))
print("i32 type:", type(dll_add32(1, 2)))
print("i32 wrap:", dll_add32(2147483647, 1))
print("add64:", dll_add64(1099511627776, 1))
print("sum5:", dll_sum5(1, 2, 3, 4, 5))
print("addf:", dll_addf(1.5, 2.25))
print("addd:", dll_addd(1.5, 2.25))
print("wlen:", dll_wlen("你好ECL"))
print("clen:", dll_clen("hello"))
print("hello:", dll_hello())
print("chello:", dll_chello())

p <- {"x": 0, "y": 0}
dll_set_point(p, 7, 8)
print("ptr struct:", p["x"], p["y"], dll_point_sum(p))

q <- {"x": 0, "y": 0}
dll_get_point(q)
print("out struct:", q["x"], q["y"])

print("=== dll_import 测试完成 ===")
