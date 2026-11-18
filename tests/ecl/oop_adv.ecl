class b:
    def __init__() -> none:
        self.bname <- "bee"
        self.tag <- 10
    end
    def hi() -> str:
        return "hi from " + self.bname
    end
    def val() -> int:
        return 10
    end
end
class c:
    def __init__() -> none:
        self.cname <- "cee"
    end
    def hi() -> str:
        return "hi from " + self.cname
    end
end
class a(b, c):
    def __init__() -> none:
        b.__init__()
        c.__init__()
        using b
        print(hi())
        print(val())
        tag <- 20
        print(tag)
        using c
        print(hi())
    end
end
a inst <- a()
print(inst.val())
print(inst.tag)

class Animal:
    def name() -> str:
        return "animal"
    end
end
class Dog(Animal):
end
d <- Dog()
print(d.name())

class Vec:
    def __init__(int x) -> none:
        self.x <- x
    end
    def __add__(Vec o) -> Vec:
        return Vec(self.x + o.x)
    end
    def __str__() -> str:
        return "Vec(" + str(self.x) + ")"
    end
end
va <- Vec(2)
vb <- Vec(3)
print(va + vb)

class counter:
    private int start <- 5
    def get() -> int:
        return self.start
    end
end
cc <- counter()
print(cc.get())
