# 类 / 继承 / 魔法方法
class Vec:
    def __init__(int x) -> none:
        self.x <- x
    end
    def __add__(Vec o) -> Vec:
        return Vec(self.x + o.x)
    end
    def __eq__(Vec o) -> bool:
        return self.x == o.x
    end
    def __str__() -> str:
        return "Vec(" + str(self.x) + ")"
    end
end

a <- Vec(2)
b <- Vec(3)
print(a + b)
print(a == Vec(2), a == b)

class Animal:
    def name() -> str:
        return "animal"
    end
end

class Dog(Animal):
end

d <- Dog()
print(d.name())
