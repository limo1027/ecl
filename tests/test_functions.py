"""文档五：函数、参数收集、重载、作用域访问。"""

import unittest

from base import EclTest


class TestFunctions(EclTest):
    def test_define_and_call(self):
        src = '''def add(int a, int b) -> int:
    return a + b
end
print(add(3, 5))
'''
        self.assert_output(src, '8\n')

    def test_no_return_gives_none(self):
        src = '''def f() -> any:
end
print(f(), type(f()))
'''
        self.assert_output(src, 'none none\n')

    def test_explicit_return_none(self):
        src = '''def f() -> any:
    return none
end
print(f())
'''
        self.assert_output(src, 'none\n')

    def test_single_line_function(self):
        self.assert_output(
            'square(int n) -> int <- n * n\nprint(square(6))\n', '36\n')

    def test_varargs_only_function(self):
        src = '''def sum_all(*args) -> int:
    total <- 0
    for v in args:
        total <- total + v
    end
    return total
end
print("varargs:", sum_all(1, 2, 3))
'''
        self.assert_output(src, 'varargs: 6\n')

    def test_kwargs_only_function(self):
        src = '''def kw(**kw) -> int:
    return len(kw)
end
print("kwargs:", kw(a = 1, b = 2))
'''
        self.assert_output(src, 'kwargs: 2\n')

    def test_default_plus_keyword(self):
        src = '''def add(int a, int b = 10) -> int:
    return a + b
end
print(add(1), add(1, 2), add(a = 1, b = 2))
'''
        self.assert_output(src, '11 3 3\n')

    def test_default_parameter(self):
        src = '''def power(int base, int exp = 2) -> int:
    return base ** exp
end
print(power(3))
print(power(2, 10))
'''
        self.assert_output(src, '9\n1024\n')

    def test_keyword_arguments(self):
        src = '''def rect(int w, int h) -> int:
    return w * h
end
print(rect(w = 3, h = 4))
'''
        self.assert_output(src, '12\n')

    def test_varargs_and_kwargs(self):
        src = '''def f(int a, str *rest, int k = 1, **kw) -> str:
    r <- ""
    for s in rest:
        r <- r + s
    end
    return r + str(k) + str(len(kw))
end
print(f(1, "a", "b", k = 2, x = 3))
print(f(1))
'''
        self.assert_output(src, 'ab21\n10\n')

    def test_untyped_varargs_kwargs(self):
        src = '''def g(int a, *rest, **kw) -> none:
    print(a, rest, kw)
end
g(1, 2, 3, x = 4)
'''
        self.assert_output(src, "1 [2, 3] {'x': 4}\n")

    def test_recursion(self):
        self.assert_output(
            'fact(int n) -> int <- 1 if n < 1 else n * fact(n - 1)\n'
            'print(fact(5))\n', '120\n')

    def test_function_as_value(self):
        src = '''def add(int a, int b) -> int:
    return a + b
end
f <- add
print(f(10, 20))
'''
        self.assert_output(src, '30\n')

    def test_overload_by_type(self):
        src = '''def f(int x) -> str:
    return "int:" + str(x)
end
def f(str x) -> str:
    return "str:" + x
end
print(f(3))
print(f("abc"))
'''
        self.assert_output(src, 'int:3\nstr:abc\n')

    def test_return_type_mismatch(self):
        self.assert_check_error(
            'def f() -> int:\n return "x"\nend\n', '返回值类型错误')

    def test_wrong_argument_count(self):
        self.assert_check_error(
            'def f(int a) -> int:\n return a\nend\nf()\n',
            '参数数量不匹配')


class TestScopes(EclTest):
    def test_global_read_write(self):
        src = '''int g <- 1
def f() -> none:
    print(global.g)
    global.g <- 9
end
f()
print(g)
'''
        self.assert_output(src, '1\n9\n')

    def test_last_closure(self):
        src = '''def outer() -> int:
    n <- 1
    def inner() -> none:
        last.n <- 5
    end
    inner()
    return n
end
print(outer())
'''
        self.assert_output(src, '5\n')

    def test_goto_in_function(self):
        src = '''def f() -> int:
    i <- 0
    label again:
    i <- i + 1
    if i < 3:
        goto again
    end
    return i
end
print(f())
'''
        self.assert_output(src, '3\n')

    def test_goto_cannot_cross_function(self):
        src = '''label outer:
def f() -> none:
    goto outer
end
'''
        self.assert_check_error(src, 'goto 目标标签不存在')

    def test_local_assignment_shadows_global(self):
        src = '''int x <- 1
def f() -> int:
    x <- 2
    return x
end
print(f(), x)
'''
        self.assert_output(src, '2 1\n')


if __name__ == '__main__':
    unittest.main()
