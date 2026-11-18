# ECL 冒烟测试：变量 / 函数 / 循环 / 输出
print("hello, ECL")
int x <- 41
x <- x + 1
print("x =", x)

def add(int a, int b) -> int:
    return a + b
end
print("add =", add(2, 3))

for i in 1..=3:
    write(i)
end
print("")
