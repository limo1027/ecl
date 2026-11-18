# 解包
t <- (1, 2, 3)
l <- [1, *t, 4]
print(l)

d <- {**{"a": 1}, **{"b": 2}}
print(d)

f(int a, int b, int c) -> int <- a + b + c
print(f(*[10, 20, 30]))

a, *rest <- [5, 6, 7]
print(a, rest)

*init, last <- [1, 2, 3]
print(init, last)
