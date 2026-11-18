"""文档十：@@ 外部语言执行（纯语法糖）。

@@flat_class(code) 等价于 flat_class().__exec__(code)，
不涉及点号访问、模块对象或特殊 IR 指令。
"""

import unittest

from base import EclTest
from errors import ParseError


class TestExternalExec(EclTest):
    def test_context_run_with_args(self):
        src = '''class Adder:
    def __exec__(str code) -> any:
        return self
    end

    def run(*args) -> any:
        return args[0] + args[1]
    end
end

ctx <- @@Adder("ignored")
print(ctx.run(3, 5))
'''
        self.assert_output(src, '8\n')

    def test_context_run_is_reusable(self):
        src = '''class Mul:
    def __exec__(str code) -> any:
        return self
    end

    def run(*args) -> any:
        return args[0] * args[1]
    end
end

ctx <- @@Mul("ignored")
print(ctx.run(6, 7))
print(ctx.run(2, 9))
'''
        self.assert_output(src, '42\n18\n')

    def test_local_class_desugar(self):
        src = '''class Echo:
    def __exec__(str code) -> any:
        self.text <- code
        return self
    end

    def run() -> str:
        return self.text
    end
end

ctx <- @@Echo("hello")
print(ctx.run())
'''
        self.assert_output(src, 'hello\n')

    def test_inherited_exec(self):
        src = '''class Base:
    def __exec__(str code) -> any:
        self.text <- code
        return self
    end

    def run() -> str:
        return self.text
    end
end

class Sub(Base):
end

ctx <- @@Sub("inherited")
print(ctx.run())
'''
        self.assert_output(src, 'inherited\n')

    def test_code_literal_argument(self):
        src = '''class Echo:
    def __exec__(any code) -> any:
        self.text <- code
        return self
    end

    def run() -> str:
        return type(self.text)
    end
end

ctx <- @@Echo(`int x <- 1`)
print(ctx.run())
'''
        self.assert_output(src, 'code\n')

    def test_missing_class_is_check_error(self):
        self.assert_check_error('ctx <- @@Nope("x")\n', '不存在')

    def test_missing_exec_is_check_error(self):
        src = '''class A:
    def f() -> none:
    end
end

ctx <- @@A("x")
'''
        self.assert_check_error(src, '没有实现 __exec__')

    def test_missing_parentheses_is_parse_error(self):
        src = '''class Echo:
    def __exec__(str code) -> any:
        return self
    end
end

ctx <- @@Echo
'''
        with self.assertRaises(ParseError) as ctx:
            self.run_ecl(src)
        self.assertIn('@@', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
