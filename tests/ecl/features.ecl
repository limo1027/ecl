print("tuple")
t <- (1, 2, 3)
print(t, type(t), len(t), t[1])
l <- [1, *t, 4]
print(l)
print("match")
int x <- 2
match x:
    case 1:
        print("one")
    case 2, 3:
        print("two")
    case _:
        print("other")
end
match x:
    case int if x > 1:
        print("big int")
    case _:
        print("no")
end
print("unpack")
a, *rest <- [1, 2, 3, 4]
print(a, rest)
*init, z <- [1, 2, 3]
print(init, z)
print("iter")
for k in {"a": 1, "b": 2}:
    print(k)
end
for ch in "hi":
    print(ch)
end
print("using")
class box:
    def __init__() -> none:
        self.v <- 7
    end
    def show() -> int:
        return self.v
    end
end
b <- box()
using b
print(show())
v <- 9
print(v, b.v)
print("try")
try:
    raise ValueError("boom")
except TypeError:
    print("wrong")
except ValueError as e:
    print("caught", e.message)
else:
    print("else")
finally:
    print("finally")
end
