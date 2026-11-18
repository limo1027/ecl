# with / using
class res:
    def __init__(str n) -> none:
        self.n <- n
    end
    def __enter__() -> str:
        print("enter", self.n)
        return self.n
    end
    def __leave__(any t, any v) -> bool:
        print("leave", self.n)
        return false
    end
end

with res("a") as h, res("b") as k:
    print("body", h, k)
end

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
v <- 8
print(v, b.v)
