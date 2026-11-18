"""文档六、七、十二：类、继承、魔法方法、using。"""

import unittest

from base import EclTest


class TestClasses(EclTest):
    def test_class_basic(self):
        src = '''class Point:
    def __init__(int x, int y) -> none:
        self.x <- x
        self.y <- y
    end
    def dist() -> float:
        return (self.x ** 2 + self.y ** 2) ** 0.5
    end
    def __str__() -> str:
        return "Point(" + str(self.x) + ", " + str(self.y) + ")"
    end
end
Point pt <- Point(3, 4)
print(pt.dist())
print(pt)
'''
        self.assert_output(src, '5.0\nPoint(3, 4)\n')

    def test_method_mutates_self(self):
        src = '''class Counter:
    def __init__() -> none:
        self.n <- 0
    end
    def inc() -> none:
        self.n <- self.n + 1
    end
end
Counter c <- Counter()
c.inc()
c.inc()
print(c.n)
'''
        self.assert_output(src, '2\n')


class TestInheritance(EclTest):
    def test_method_inherited(self):
        src = '''class base:
    def hello() -> str:
        return "base"
    end
end
class sub(base):
end
sub s <- sub()
print(s.hello())
'''
        self.assert_output(src, 'base\n')

    def test_base_call_through_using(self):
        src = '''class b:
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
'''
        self.assert_output(
            src,
            'hi from bee\n10\n20\nhi from cee\n10\n20\n')


class TestMagicMethods(EclTest):
    def test_all_magic_methods(self):
        src = '''class v:
    def __init__(int x) -> none:
        self.x <- x
    end
    def __add__(v o) -> v:
        return v(self.x + o.x)
    end
    def __div__(v o) -> v:
        return v(self.x // o.x)
    end
    def __eq__(v o) -> bool:
        return self.x == o.x
    end
    def __lt__(v o) -> bool:
        return self.x < o.x
    end
    def __getitem__(int i) -> int:
        return self.x + i
    end
    def __call__(int y) -> int:
        return self.x * y
    end
    def __bor__(v o) -> v:
        return v(self.x | o.x)
    end
    def __invert__() -> v:
        return v(~self.x)
    end
    def __str__() -> str:
        return "v(" + str(self.x) + ")"
    end
end
a <- v(6)
b <- v(2)
print(a + b)
print(a / b)
print(a == v(6), a < b)
print(a[4])
print(a(3))
print(a | b)
print(~a)
print(str(a))
'''
        self.assert_output(
            src,
            'v(8)\nv(3)\nTrue False\n10\n18\nv(6)\nv(-7)\nv(6)\n')

    def test_neg_magic_returning_zero(self):
        # 方法返回 0/False 时也要走魔法方法，不能误判为失败
        src = '''class z:
    def __neg__() -> int:
        return 0
    end
end
print(-z())
'''
        self.assert_output(src, '0\n')


class TestUsing(EclTest):
    def test_using_members_and_write(self):
        src = '''class o:
    def __init__() -> none:
        self.v <- 1
    end
    def m() -> str:
        return "m"
    end
end
o oi <- o()
using oi
print(m())
print(v)
v <- 9
print(v)
print(oi.v)
'''
        self.assert_output(src, 'm\n1\n9\n9\n')

    def test_later_using_overrides(self):
        src = '''class o1:
    def __init__() -> none:
        self.v <- 1
    end
    def who() -> str:
        return "one"
    end
end
class o2:
    def __init__() -> none:
        self.v <- 2
    end
    def who() -> str:
        return "two"
    end
end
a <- o1()
b <- o2()
using a
print(who(), v)
using b
print(who(), v)
v <- 9
print(b.v, a.v)
'''
        self.assert_output(src, 'one 1\ntwo 2\n9 1\n')

    def test_using_scope_ends_with_block(self):
        src = '''class o:
    def __init__() -> none:
        self.v <- 1
    end
    def m() -> str:
        return "m"
    end
end
oi <- o()
if True:
    using oi
    print(m())
end
print(v)
'''
        result = self.assert_output(src, 'm\nunknown\n')
        self.assertTrue(any('未定义的变量' in w for w in result.warnings))

    def test_duplicate_using_warns(self):
        src = '''class o:
    def __init__() -> none:
        self.v <- 1
    end
end
oi <- o()
using oi
using oi
print(v)
'''
        self.assert_warning(src, '重复 using')

    def test_using_non_object_type_error(self):
        self.assert_runtime_error('using 5\n', 'TypeError')

    def test_public_private_only_in_class(self):
        self.assert_check_error(
            'public int x <- 1\n',
            'public/private 仅在 class 内或顶层 DLL 导出时有效')

    def test_public_private_in_class(self):
        src = '''class c:
    private int x <- 1
    public def get() -> int:
        return self.x
    end
end
c o <- c()
print(o.get())
'''
        self.assert_output(src, '1\n')


if __name__ == '__main__':
    unittest.main()
