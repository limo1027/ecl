"""文档一、二、三、十五：变量、基础类型、字面量、空值规则。"""

import unittest

from base import EclTest


class TestVariables(EclTest):
    def test_auto_infer(self):
        self.assert_output('a <- 7\nprint(a, type(a))\n', '7 int\n')

    def test_explicit_type(self):
        self.assert_output(
            'int x <- 9\nstr s <- "hi"\nfloat f <- 1.5\n'
            'bool b <- True\nprint(x, s, f, b)\n',
            '9 hi 1.5 True\n')

    def test_lowercase_booleans(self):
        self.assert_output('print(true, false)\n', 'True False\n')

    def test_chained_define(self):
        # int x <- double b <- 9：先声明 b（double），再把 b 转成 int 赋给 x
        self.assert_output(
            'int x <- double b <- 9\nprint(x, b, type(x), type(b))\n',
            '9 9 int double\n')

    def test_swap(self):
        self.assert_output(
            'int p <- 1\nint q <- 2\np <-> q\nprint(p, q)\n', '2 1\n')

    def test_define_any_is_null(self):
        self.assert_output('define any x\nprint(x, type(x))\n', 'null null\n')

    def test_ref_is_deprecated(self):
        self.assert_check_error(
            'int a <- 100\nref b <- a\n', 'ref 已废弃')

    def test_ref_requires_variable(self):
        self.assert_check_error('ref r <- 1\n', 'ref 已废弃')

    def test_const_cannot_change(self):
        self.assert_check_error(
            'const MAX <- 999\nMAX <- 0\n', '不能重新赋值常量')

    def test_duplicate_declare(self):
        self.assert_check_error('int x <- 1\nint x <- 2\n', '重复声明')

    def test_semicolons_and_comments(self):
        src = '''/// 文档注释
/* 多行
   注释 */
a <- 1; b <- 2; print(a + b);;
'''
        self.assert_output(src, '3\n')


class TestLiterals(EclTest):
    def test_integer_bases(self):
        self.assert_output(
            'print(0b1010, 0o755, 0xFF, 1_000_000)\n',
            '10 493 255 1000000\n')

    def test_float_and_double_suffix(self):
        self.assert_output(
            'print(3.14159)\nprint(3.14159d)\nprint(1.5e10)\n',
            '3.14159\n3.14159\n15000000000.0\n')

    def test_double_high_precision(self):
        self.assert_output(
            'print(0.1 + 0.2 == 0.3)\nprint(0.1d + 0.2d == 0.3d)\n'
            'print(0.1d + 0.2d)\n',
            'False\nTrue\n0.3\n')

    def test_string_interpolation(self):
        self.assert_output(
            'str n <- "world"\nint x <- 1\nprint("hi, $n")\n'
            'print("x = ${x + 1}")\nprint("$$5")\n',
            'hi, world\nx = 2\n$5\n')

    def test_code_literal(self):
        self.assert_output(
            'code c <- `print("from code")`\nc.run()\n',
            'from code\n')

    def test_list_dict_tuple_literals(self):
        self.assert_output(
            'l <- [1, 2, 3]\nd <- {"a": 1, "b": 2}\nt <- (1, 2, 3)\n'
            'print(l)\nprint(d)\nprint(t, len(t), t[0], type(t))\n',
            '[1, 2, 3]\n{\'a\': 1, \'b\': 2}\n(1, 2, 3) 3 1 tuple\n')


class TestScalarTypes(EclTest):
    def test_int_arbitrary_precision(self):
        self.assert_output(
            'big <- 123456789012345678901234567890\n'
            'print(big + 1)\nprint(type(big))\n',
            '123456789012345678901234567891\nint\n')

    def test_i64_wraps(self):
        self.assert_output(
            'i64 x <- 9223372036854775807\nx <- x + 1\n'
            'print(x, type(x))\n',
            '-9223372036854775808 i64\n')

    def test_i64_wraps_negative(self):
        self.assert_output(
            'i64 x <- -9223372036854775807 - 1\nx <- x - 1\nprint(x)\n',
            '9223372036854775807\n')

    def test_bool_type(self):
        self.assert_output(
            'print(type(True), type(1), type(1.0), type("s"))\n',
            'bool int float str\n')


class TestEmptyValues(EclTest):
    def test_none_null_unknown_values(self):
        self.assert_output(
            'print(none, type(none))\nprint(null, type(null))\n'
            'print(unknown, type(unknown))\n',
            'none none\nnull null\nunknown unknown\n')

    def test_unknown_from_undefined_variable(self):
        result = self.assert_output(
            'print(zzz, type(zzz))\n', 'unknown unknown\n')
        self.assertTrue(any('未定义的变量' in w for w in result.warnings))

    def test_unknown_equality(self):
        self.assert_output(
            'print(unknown == unknown)\nprint(unknown == null)\n'
            'print(unknown == none)\nprint(unknown == 1)\n',
            'True\nFalse\nFalse\nFalse\n')

    def test_unknown_falsy(self):
        self.assert_output(
            'if unknown:\n print("yes")\nelse:\n print("no")\nend\n',
            'no\n')

    def test_unknown_arithmetic_raises_type_error(self):
        self.assert_runtime_error('print(unknown + 1)\n', 'TypeError')

    def test_null_is_not_rvalue(self):
        self.assert_check_error('x <- null\n', 'null 不能作为右值')
        self.assert_check_error('x <- unknown\n', 'unknown 不能作为右值')

    def test_null_not_return(self):
        self.assert_check_error(
            'def f() -> any:\n return null\nend\n', 'null 不能作为返回值')

    def test_unknown_not_return(self):
        self.assert_check_error(
            'def f() -> any:\n return unknown\nend\n',
            'unknown 不能作为返回值')


class TestCompositeTypes(EclTest):
    def test_fixed_length_list_ok(self):
        self.assert_output(
            'list[3] int xs <- [1, 2, 3]\nprint(len(xs), type(xs))\n',
            '3 list\n')

    def test_fixed_length_list_mismatch(self):
        self.assert_runtime_error(
            'list[2] int xs <- [1, 2, 3]\n', 'ValueError')

    def test_list_type_parameter(self):
        self.assert_output(
            'list[int] xs <- [1, 2]\nprint(xs)\n',
            '[1, 2]\n')


if __name__ == '__main__':
    unittest.main()
