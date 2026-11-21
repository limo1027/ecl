# ============================================================
# ECL 完整特性测试 full_test.ecl
# 覆盖：变量/类型、运算、控制流、函数（含默认/可变/关键字参数、
#       递归、闭包）、类/OOP/继承、match、with、using、解包、
#       异常、字符串插值、list/dict/tuple、内置函数、struct
# ============================================================

pass_count <- 0
fail_count <- 0

def check(name, cond):
    if cond:
        global.pass_count <- pass_count + 1
    else:
        global.fail_count <- fail_count + 1
        eprint("[FAIL]", name, "\n")
    end
end

# ---------- 1. 变量与类型 ----------
x <- 10
y <- 3.5
s <- "hello"
b <- True
check("int 变量", x == 10)
check("float 变量", y == 3.5)
check("str 变量", s == "hello")
check("bool 变量", b == True)
check("type()", type(x) == "int" and type(y) == "float" and type(s) == "str")

int a <- 7
double d <- 2.5d
check("类型注解 int", a == 7)
check("double 字面量", d == 2.5d)

# 常量
const PI <- 3.14159
check("const", PI == 3.14159)

# i64 溢出回绕
i64 big <- 9223372036854775807
big <- big + 1
check("i64 溢出回绕", big == -9223372036854775808)

# ---------- 2. 运算符 ----------
check("算术", 2 + 3 * 4 == 14)
check("幂", 2 ** 10 == 1024)
check("整除", 7 // 2 == 3)
check("取模", 7 % 3 == 1)
check("比较链", 1 < 2 and 2 <= 2 and 3 > 2 and 2 >= 2)
check("位运算", (6 & 3) == 2 and (6 | 3) == 7 and (6 ^ 3) == 5)
check("移位", (1 << 4) == 16 and (16 >> 2) == 4)
check("not/and/or", not False and (True or False))

# 交换
p <- 1
q <- 2
p <-> q
check("交换 <->", p == 2 and q == 1)

# 三目表达式
z <- ("big" if x > 5 else "small")
check("三目表达式", z == "big")

# ---------- 3. 字符串 ----------
name <- "ECL"
check("字符串拼接", "Hi, " + name == "Hi, ECL")
check("字符串插值", "Hello ${name}!" == "Hello ECL!")
check("str() 转换", str(123) == "123")
check("len(str)", len("abc") == 3)

# ---------- 4. list / dict / tuple ----------
lst <- [1, 2, 3]
lst.append(4)
check("list append", len(lst) == 4 and lst[3] == 4)
check("list 切片", slice(lst, 1, 3) == [2, 3])
check("list 解包扩展", [0, *lst] == [0, 1, 2, 3, 4])

dc <- {"a": 1, "b": 2}
dc["c"] <- 3
check("dict 读写", dc["a"] == 1 and dc["c"] == 3)
check("dict ** 解包", {**dc, "d": 4}["d"] == 4)

tp <- (1, 2, 3)
check("tuple", type(tp) == "tuple" and tp[1] == 2)

# ---------- 5. 控制流 ----------
total <- 0
for i in 1..=5:
    total <- total + i
end
check("for 1..5", total == 15)

total <- 0
for i in 1..10+2:
    total <- total + i
end
check("for 带步长", total == 25)

n <- 0
while n < 10:
    n <- n + 1
    if n == 3:
        continue
    end
    if n == 8:
        break
    end
end
check("while + break + continue", n == 8)

# if / elif / else
grade <- "B"
if x > 90:
    grade <- "A"
elif x > 5:
    grade <- "B"
else:
    grade <- "C"
end
check("if/elif/else", grade == "B")

# goto / label
jumped <- False
goto skip
jumped <- True
label skip:
check("goto 跳过赋值", jumped == False)

# ---------- 6. 函数 ----------
def add(a, b):
    return a + b
end
check("函数调用", add(2, 3) == 5)

def greet(n, prefix = "Hello"):
    return prefix + ", " + n
end
check("默认参数", greet("Bob") == "Hello, Bob")
check("覆盖默认参数", greet("Bob", "Hi") == "Hi, Bob")
check("关键字参数", greet(prefix = "Yo", n = "Tom") == "Yo, Tom")

def sum_all(*args):
    t <- 0
    for v in args:
        t <- t + v
    end
    return t
end
check("*args 可变参数", sum_all(1, 2, 3, 4) == 10)

def info(**kw):
    return kw["name"]
end
check("**kwargs", info(name = "ecl") == "ecl")

# 递归
def fib(n):
    if n <= 1:
        return n
    end
    return fib(n - 1) + fib(n - 2)
end
check("递归 fib", fib(10) == 55)

# 单行函数
square(v) -> int <- v * v
check("单行函数", square(6) == 36)

# 闭包 last
def make_counter():
    count <- 0
    def inc():
        last.count <- last.count + 1
        return last.count
    end
    return inc
end
c1 <- make_counter()
c1()
c1()
check("闭包 last", c1() == 3)

# 解包赋值
a1, *rest <- [1, 2, 3, 4]
check("解包赋值", a1 == 1 and rest == [2, 3, 4])

# 调用解包
args_l <- [5, 6]
check("* 解包调用", add(*args_l) == 11)

# ---------- 7. 类与 OOP ----------
class Animal:
    kind <- "animal"

    def __init__(name):
        self.name <- name
    end

    def speak():
        return "..."
    end

    def describe():
        return self.name + " is an animal"
    end
end

class Dog(Animal):
    def __init__(name):
        Animal.__init__(name)
    end

    def speak():
        return "Woof"
    end
end
a <- Animal("Generic")
check("实例属性", a.name == "Generic")
check("类方法调用", a.speak() == "...")

d <- Dog("Rex")
check("继承的方法", d.describe() == "Rex is an animal")
check("方法重写", d.speak() == "Woof")

# 魔术方法
class Vec:
    def __init__(x):
        self.x <- x
    end

    def __add__(other):
        return Vec(self.x + other.x)
    end

    def __eq__(other):
        return self.x == other.x
    end

    def __str__():
        return "Vec(" + str(self.x) + ")"
    end
end
v1 <- Vec(3)
v2 <- Vec(4)
check("运算符重载 __add__", (v1 + v2) == Vec(7))
check("__str__", str(v1) == "Vec(3)")

# ---------- 8. match ----------
match_result <- none
val <- 42
match val:
    case 0:
        match_result <- "zero"
    case int:
        match_result <- "int"
    case _:
        match_result <- "other"
end
check("match 类型模式", match_result == "int")

match val:
    case 1, 2, 3:
        match_result <- "small"
    case 42 if val > 40:
        match_result <- "the answer"
    case _:
        match_result <- "?"
end
check("match 多值 + 守卫", match_result == "the answer")

# ---------- 9. with ----------
class Ctx:
    def __enter__():
        log <- []
        self.log <- log
        return self.log
    end

    def __leave__(etype, evalue):
        self.log.append("closed")
        return False
    end
end

log <- []
with Ctx() as ctx:
    ctx.append("working")
end
check("with 上下文", ctx == ["working", "closed"])

# ---------- 10. 异常 ----------
caught <- False
try:
    raise ValueError("boom")
except ValueError as e:
    caught <- True
    msg <- str(e)
end
check("try/except 捕获", caught == True)

finally_ran <- False
try:
    print()
finally:
    finally_ran <- True
end
check("finally 执行", finally_ran == True)

# 内置异常
div_ok <- False
try:
    z <- 1 // 0
except ZeroDivisionError:
    div_ok <- True
end
check("除零异常", div_ok == True)

# ---------- 11. 内置函数 ----------
check("abs/min/max", abs(-3) == 3 and min(1, 2) == 1 and max(1, 2) == 2)
check("sum", sum([1, 2, 3]) == 6)
check("pow", pow(2, 8) == 256)
check("floor/ceil/round", floor(2.7) == 2 and ceil(2.1) == 3 and round(2.5) == 2)
check("tuple()/list() 转换", tuple([1, 2]) == (1, 2))
# 深拷贝
orig <- [1, [2, 3]]
copied <- clone(orig)
copied[1][0] <- 99
check("clone 深拷贝", orig[1][0] == 2 and copied[1][0] == 99)
# ---------- 12. struct ----------
struct POINT:
    i32 x
    i32 y
end

pt <- POINT()
pt.x <- 10
pt.y <- 20
check("struct 字段读写", pt.x == 10 and pt.y == 20)
check("sizeof(P)", sizeof(POINT) == 8)

# ---------- 13. 代码对象（解释器模式） ----------
code code_src <- `r1 <- 1
                  r2 <- 2`
code_src.run()
# 注意：动态 code() 在 AOT 下不可用，仅解释器执行

# ---------- 汇总 ----------
print("========================================")
print("通过:", pass_count, "失败:", fail_count)
if fail_count == 0:
    print("ALL TESTS PASSED")
else:
    eprint("SOME TESTS FAILED")
end