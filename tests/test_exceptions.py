"""文档八：异常处理。"""

import unittest

from base import EclTest


class TestTryExcept(EclTest):
    def test_matching_type(self):
        src = '''try:
    raise ValueError("boom")
except TypeError:
    print("wrong")
except ValueError as e:
    print("caught", e.message)
end
'''
        self.assert_output(src, 'caught boom\n')

    def test_zero_division(self):
        src = '''try:
    int a <- 1
    int b <- 0
    print(a / b)
except ZeroDivisionError:
    print("zero")
end
'''
        self.assert_output(src, 'zero\n')

    def test_else_only_on_success(self):
        src = '''try:
    print("ok")
except Exception:
    print("bad")
else:
    print("else")
finally:
    print("finally")
end
'''
        self.assert_output(src, 'ok\nelse\nfinally\n')

    def test_else_skipped_on_error(self):
        src = '''try:
    raise ValueError("x")
except ValueError:
    print("handled")
else:
    print("else")
finally:
    print("finally")
end
'''
        self.assert_output(src, 'handled\nfinally\n')

    def test_finally_runs_when_except_raises(self):
        src = '''try:
    raise ValueError("a")
except ValueError:
    raise TypeError("b")
finally:
    print("finally")
end
'''
        result = self.assert_runtime_error(src, 'TypeError')
        self.assertEqual(result.stdout, 'finally\n')

    def test_unmatched_exception_propagates(self):
        src = '''try:
    raise ValueError("a")
except TypeError:
    print("nope")
finally:
    print("finally")
end
'''
        result = self.assert_runtime_error(src, 'ValueError')
        self.assertEqual(result.stdout, 'finally\n')

    def test_nested_try(self):
        src = '''try:
    try:
        raise ValueError("inner")
    except ValueError:
        print("inner caught")
        raise TypeError("outer")
    end
except TypeError:
    print("outer caught")
end
'''
        self.assert_output(src, 'inner caught\nouter caught\n')

    def test_bare_except(self):
        src = '''try:
    raise KeyError("k")
except:
    print("any")
end
'''
        self.assert_output(src, 'any\n')

    def test_custom_exception(self):
        src = '''try:
    raise ConstChangingError("const!")
except ConstChangingError as e:
    print("caught", e.message)
end
'''
        self.assert_output(src, 'caught const!\n')

    def test_raise_non_exception(self):
        self.assert_runtime_error('raise "oops"\n', 'Exception')


if __name__ == '__main__':
    unittest.main()
