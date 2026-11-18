"""文档十一：with 上下文管理。"""

import unittest

from base import EclTest


CTX = '''class res:
    def __init__(str n) -> none:
        self.n <- n
    end
    def __enter__() -> str:
        print("enter", self.n)
        return "handle-" + self.n
    end
    def __leave__(any t, any v) -> bool:
        print("leave", self.n, t)
        return false
    end
end
'''


class TestWith(EclTest):
    def test_basic_and_as_binding(self):
        src = CTX + '''with res("a") as h:
    print("body", h)
end
print("done")
'''
        self.assert_output(
            src,
            'enter a\nbody handle-a\nleave a none\ndone\n')

    def test_multiple_resources_lifo(self):
        src = CTX + '''with res("a") as x, res("b") as y:
    print("body", x, y)
end
'''
        self.assert_output(
            src,
            'enter a\nenter b\nbody handle-a handle-b\n'
            'leave b none\nleave a none\n')

    def test_leave_receives_exception(self):
        src = '''class res:
    def __enter__() -> none:
        return none
    end
    def __leave__(any t, any v) -> bool:
        print("type", t.exc_type)
        return false
    end
end
try:
    with res():
        raise ValueError("boom")
    end
except ValueError:
    print("caught")
end
'''
        self.assert_output(
            src, 'type ValueError\ncaught\n')

    def test_exception_swallowed(self):
        src = '''class swallow:
    def __enter__() -> none:
        print("enter")
        return none
    end
    def __leave__(any t, any v) -> bool:
        print("leave", t.exc_type)
        return true
    end
end
with swallow():
    raise ValueError("swallowed")
end
print("after")
'''
        self.assert_output(
            src, 'enter\nleave ValueError\nafter\n')

    def test_missing_enter_or_leave(self):
        self.assert_runtime_error(
            'with 5:\n print(1)\nend\n', 'TypeError')

        src = '''class half:
    def __enter__() -> none:
        return none
    end
end
with half():
    print(1)
end
'''
        self.assert_runtime_error(src, 'TypeError')

    def test_break_unwinds_with(self):
        src = '''class res:
    def __enter__() -> none:
        return none
    end
    def __leave__(any t, any v) -> bool:
        print("leave")
        return false
    end
end
for i in 0..3:
    with res():
        if i = 1:
            break
        end
        print("iter", i)
    end
end
print("after")
'''
        self.assert_output(
            src, 'iter 0\nleave\nleave\nafter\n')

    def test_return_unwinds_with(self):
        src = '''class res:
    def __enter__() -> none:
        return none
    end
    def __leave__(any t, any v) -> bool:
        print("leave")
        return false
    end
end
def f() -> none:
    with res():
        return none
    end
end
f()
print("done")
'''
        self.assert_output(src, 'leave\ndone\n')

    def test_object_expression_evaluated_once(self):
        src = '''int made <- 0
class res:
    def __enter__() -> none:
        return none
    end
    def __leave__(any t, any v) -> bool:
        return false
    end
end
def make() -> res:
    last.made <- last.made + 1
    return res()
end
with make():
    print("body")
end
print("made", made)
'''
        self.assert_output(src, 'body\nmade 1\n')


if __name__ == '__main__':
    unittest.main()
