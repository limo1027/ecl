class res:
    def __init__(str n) -> none:
        self.n <- n
    end
    def __enter__() -> str:
        print("enter", self.n)
        return "handle-" + self.n
    end
    def __leave__(any t, any v) -> bool:
        print("leave", self.n)
        return false
    end
end
print("basic")
with res("a") as h:
    print("body", h)
end
print("multi")
with res("x") as p, res("y") as q:
    print("body", p, q)
end
print("break")
for i in 0..3:
    with res("b"):
        if i = 1:
            break
        end
        print("iter", i)
    end
end
print("return")
def f() -> none:
    with res("r"):
        return none
    end
end
f()
print("swallow")
class swallow:
    def __enter__() -> none:
        return none
    end
    def __leave__(any t, any v) -> bool:
        print("swallow leave")
        return true
    end
end
with swallow():
    raise ValueError("x")
end
print("after")
print("done")
