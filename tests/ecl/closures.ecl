def outer(int n) -> int:
    def inner() -> none:
        last.n <- 5
    end
    inner()
    return n
end
print(outer(2))

def with_local() -> int:
    y <- 3
    def bump() -> none:
        last.y <- 7
    end
    bump()
    return y
end
print(with_local())

int x <- 9
def f(int x) -> none:
    def inner() -> none:
        last.x <- 20
    end
    inner()
    print(x)
end
f(1)
print(x)
