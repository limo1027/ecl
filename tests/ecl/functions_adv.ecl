def power(int base, int exp = 2) -> int:
    return base ** exp
end
print(power(3))
print(power(2, 10))

def rect(int w, int h) -> int:
    return w * h
end
print(rect(w = 3, h = 4))

def f(int a, str *rest, int k = 1, **kw) -> str:
    r <- ""
    for s in rest:
        r <- r + s
    end
    return r + str(k) + str(len(kw))
end
print(f(1, "a", "b", k = 2, x = 3))
print(f(1))

def g(int x) -> str:
    return "int:" + str(x)
end
def g(str x) -> str:
    return "str:" + x
end
print(g(3))
print(g("abc"))

add3(int a, int b, int c) -> int <- a + b + c
print(add3(*[1, 2, 3]))

kw(int a, int b) -> int <- a + b
print(kw(**{"a": 1, "b": 2}))
print(add3(*[1, 2], 3))
