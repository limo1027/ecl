"""文档十：match-case。"""

import unittest

from base import EclTest
from errors import ParseError


class TestMatch(EclTest):
    def test_literal_and_multi(self):
        src = '''int x <- 2
match x:
    case 1:
        print("one")
    case 2, 3:
        print("two-or-three")
    case _:
        print("other")
end
'''
        self.assert_output(src, 'two-or-three\n')

    def test_no_match_does_nothing(self):
        src = '''int x <- 9
match x:
    case 1:
        print("one")
end
print("after")
'''
        self.assert_output(src, 'after\n')

    def test_wildcard(self):
        src = '''int x <- 9
match x:
    case 1:
        print("one")
    case _:
        print("other")
end
'''
        self.assert_output(src, 'other\n')

    def test_type_patterns(self):
        src = '''a <- 3
match a:
    case int:
        print("int")
    case str:
        print("str")
end
b <- "s"
match b:
    case str:
        print("str")
    case _:
        print("other")
end
c <- True
match c:
    case bool:
        print("bool")
    case _:
        print("other")
end
'''
        self.assert_output(src, 'int\nstr\nbool\n')

    def test_more_type_patterns(self):
        src = '''d <- 1.5d
match d:
    case double:
        print("double")
    case _:
        print("other")
end
i64 iv <- 9
match iv:
    case i64:
        print("i64")
    case _:
        print("other")
end
l <- [1]
match l:
    case list:
        print("list")
    case _:
        print("other")
end
dd <- {"a": 1}
match dd:
    case dict:
        print("dict")
    case _:
        print("other")
end
t <- (1,)
match t:
    case tuple:
        print("tuple")
    case _:
        print("other")
end
'''
        self.assert_output(src, 'double\ni64\nlist\ndict\ntuple\n')

    def test_none_null_patterns(self):
        src = '''a <- none
match a:
    case none:
        print("none")
    case _:
        print("other")
end
define any b
match b:
    case null:
        print("null")
    case _:
        print("other")
end
'''
        self.assert_output(src, 'none\nnull\n')

    def test_class_type_pattern(self):
        src = '''class Point:
    def __init__() -> none:
        self.x <- 1
    end
end
p <- Point()
match p:
    case Point:
        print("point")
    case _:
        print("other")
end
'''
        self.assert_output(src, 'point\n')

    def test_guard(self):
        src = '''int y <- 0
match y:
    case int if y > 0:
        print("positive")
    case int if y == 0:
        print("zero")
    case _:
        print("negative")
end
'''
        self.assert_output(src, 'zero\n')

    def test_break_in_match(self):
        src = '''int x <- 2
match x:
    case int:
        print("int")
        break
    case _:
        print("no")
end
print("after")
'''
        self.assert_output(src, 'int\nafter\n')

    def test_continue_in_match_is_error(self):
        self.assert_check_error(
            'int x <- 2\nmatch x:\n case int:\n  continue\nend\n',
            "'continue' 只能在循环内使用")

    def test_double_wildcard_is_error(self):
        self.assert_check_error(
            'int x <- 1\nmatch x:\n case _:\n  print(1)\n'
            ' case _:\n  print(2)\nend\n',
            "'_' 只能出现一次")

    def test_wildcard_not_last_warns(self):
        src = '''int x <- 1
match x:
    case _:
        print(1)
    case 1:
        print(2)
end
'''
        self.assert_warning(src, "之后的 case 不可达")

    def test_match_expr_can_be_literal_or_expression(self):
        # 文档要求变量/简单左值；这里放宽为任意表达式（仅检查器警告）
        result = self.assert_output(
            'match 3:\n case 3:\n  print("three")\nend\n'
            'match 1 + 2:\n case 3:\n  print("expr")\nend\n',
            'three\nexpr\n')
        self.assertTrue(any('不是变量或简单左值' in w
                            for w in result.warnings))

    def test_no_variable_binding(self):
        with self.assertRaises(ParseError):
            self.run_ecl('int x <- 1\nmatch x:\n case int n:\n  print(n)\nend\n')

    def test_fallthrough(self):
        # FALLTHROUGH 一旦声明，整个程序的所有 match 都 fallthrough
        src = '''define const FALLTHROUGH
int m <- 2
match m:
    case 1:
        print("one")
    case 2:
        print("two")
    case 3:
        print("three")
end
'''
        self.assert_output(src, 'two\nthree\n')

    def test_fallthrough_break(self):
        src = '''define const FALLTHROUGH
int m <- 2
match m:
    case 1:
        print("one")
    case 2:
        print("two")
        break
    case 3:
        print("three")
end
'''
        self.assert_output(src, 'two\n')

    def test_fallthrough_no_match(self):
        src = '''define const FALLTHROUGH
int m <- 9
match m:
    case 1:
        print("one")
end
print("after")
'''
        self.assert_output(src, 'after\n')

    def test_fallthrough_cannot_be_value(self):
        self.assert_check_error(
            'define const FALLTHROUGH\nprint(FALLTHROUGH)\n',
            'FALLTHROUGH 不能作为值使用')


if __name__ == '__main__':
    unittest.main()
