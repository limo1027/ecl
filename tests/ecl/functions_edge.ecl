print("=== 5. 函数 ===")
def add(int a, int b = 10) -> int:
    return a + b
end
print(add(1), add(1, 2))

def sum_all(*args) -> int:
    total <- 0
    for v in args:
        total <- total + v
    end
    return total
end
print("varargs:", sum_all(1, 2, 3))

def kw(**kw) -> int:
    return len(kw)
end
print("kwargs:", kw(a = 1, b = 2))

def outer() -> none:
    v <- 10
    def inner() -> none:
        last.v <- 20
    end
    inner()
    print("last:", v)
end
outer()

g <- 1
def touch_global() -> none:
    global.g <- 2
end
touch_global()
print("global:", g)
