"""文档四：控制流（if / for / while / goto）。"""

import unittest

from base import EclTest


class TestConditionals(EclTest):
    def test_if_elif_else(self):
        src = '''int score <- 85
if score >= 90:
    print("A")
elif score >= 80:
    print("B")
else:
    print("C")
end
'''
        self.assert_output(src, 'B\n')

    def test_ternary(self):
        self.assert_output(
            'int x <- 10\nint y <- 10 if x > 0 else 20\nprint(y)\n',
            '10\n')

    def test_compare_with_equals(self):
        self.assert_output(
            'int x <- 42\nif x = 42:\n print("eq")\nend\n'
            'if x == 42:\n print("eq2")\nend\n'
            'if x >= 42:\n print("ge")\nend\n',
            'eq\neq2\nge\n')


class TestLoops(EclTest):
    def test_for_range_exclusive(self):
        self.assert_output(
            'for i in 1..4:\n print(i)\nend\n', '1\n2\n3\n')

    def test_for_range_inclusive(self):
        self.assert_output(
            'for i in 1..=3:\n print(i)\nend\n', '1\n2\n3\n')

    def test_for_range_step(self):
        # 1..10+2 表示终点 10、步长 2（文档四）
        self.assert_output(
            'for i in 1..10+2:\n print(i)\nend\n',
            '1\n3\n5\n7\n9\n')

    def test_for_count(self):
        self.assert_output(
            'int n <- 0\nfor 3:\n n <- n + 1\nend\nprint(n)\n', '3\n')

    def test_for_list_dict_str(self):
        src = '''for x in [10, 20]:
    print(x)
end
for k in {"a": 1, "b": 2}:
    print(k)
end
for ch in "hi":
    print(ch)
end
'''
        self.assert_output(src, '10\n20\na\nb\nh\ni\n')

    def test_while_break_continue(self):
        src = '''int i <- 0
int total <- 0
while i < 10:
    i <- i + 1
    if i % 2 = 0:
        continue
    end
    total <- total + i
    if i >= 7:
        break
    end
end
print(total)
'''
        # 1 + 3 + 5 + 7 = 16
        self.assert_output(src, '16\n')

    def test_nested_break_only_inner(self):
        src = '''for i in 0..3:
    for j in 0..3:
        if j = 1:
            break
        end
        print(i, j)
    end
end
'''
        self.assert_output(src, '0 0\n1 0\n2 0\n')

    def test_break_outside_loop_is_error(self):
        self.assert_check_error('break\n', "'break' 必须在循环或 match 内使用")
        self.assert_check_error('continue\n', "'continue' 只能在循环内使用")


class TestGoto(EclTest):
    def test_goto_label(self):
        src = '''int n <- 0
label loop_start:
n <- n + 1
if n < 3:
    goto loop_start
end
print(n)
'''
        self.assert_output(src, '3\n')

    def test_goto_unknown_label(self):
        self.assert_check_error('goto nowhere\n', 'goto 目标标签不存在')


class TestOperators(EclTest):
    def test_bitwise_and_shift(self):
        self.assert_output(
            'print(5 | 3, 5 & 3, 5 ^ 3, 1 << 4, 16 >> 2, ~5)\n',
            '7 1 6 16 4 -6\n')

    def test_bitwise_precedence(self):
        # << 比 | 紧
        self.assert_output('print(1 | 2 << 1)\n', '5\n')

    def test_arithmetic(self):
        self.assert_output(
            'print(1 + 2, 10 - 3, 4 * 5, 10 // 3, 10 % 3, 2 ** 8)\n',
            '3 7 20 3 1 256\n')

    def test_logical(self):
        self.assert_output(
            'print(True and False, True or False, not True)\n',
            'False True False\n')


if __name__ == '__main__':
    unittest.main()
