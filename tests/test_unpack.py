"""文档十三：解包（* / **）。"""

import unittest

from base import EclTest


class TestCallSpread(EclTest):
    def test_star_positional(self):
        src = '''def f(int a, int b, int c) -> int:
    return a + b + c
end
print(f(*[1, 2, 3]))
print(f(*[1, 2], 3))
'''
        self.assert_output(src, '6\n6\n')

    def test_double_star_keywords(self):
        src = '''def f(int a, int b) -> int:
    return a + b
end
print(f(**{"a": 1, "b": 2}))
'''
        self.assert_output(src, '3\n')

    def test_spread_requires_list_or_dict(self):
        self.assert_check_error(
            'def f(int a) -> none:\n print(a)\nend\nf(*5)\n',
            '* 解包要求 list/tuple')
        self.assert_check_error(
            'def f(int a) -> none:\n print(a)\nend\nf(1, **5)\n',
            '** 解包要求 dict')

    def test_non_str_key_type_error(self):
        src = '''def f(int a) -> none:
    print(a)
end
f(1, **{2: 3})
'''
        self.assert_runtime_error(src, 'TypeError')

    def test_keyword_conflict_type_error(self):
        src = '''def f(int a) -> none:
    print(a)
end
f(1, **{"a": 2})
'''
        self.assert_runtime_error(src, 'TypeError')


class TestLiteralSpread(EclTest):
    def test_list_spread(self):
        self.assert_output(
            't <- (2, 3)\nl <- [1, *t, 4]\nprint(l)\n',
            '[1, 2, 3, 4]\n')

    def test_dict_spread(self):
        self.assert_output(
            'd <- {**{"a": 1}, **{"b": 2}, "a": 9}\nprint(d)\n',
            "{'a': 9, 'b': 2}\n")

    def test_tuple_spread(self):
        self.assert_output(
            't <- (2, 3)\nu <- (1, *t, 4)\nprint(u)\n',
            '(1, 2, 3, 4)\n')


class TestAssignUnpack(EclTest):
    def test_tail(self):
        self.assert_output(
            'a, *rest <- [1, 2, 3, 4]\nprint(a, rest)\n',
            '1 [2, 3, 4]\n')

    def test_head_and_middle(self):
        self.assert_output(
            '*init, last <- [1, 2, 3]\nprint(init, last)\n'
            'a, *mid, z <- [1, 2, 3, 4]\nprint(a, mid, z)\n',
            '[1, 2] 3\n1 [2, 3] 4\n')

    def test_star_empty(self):
        self.assert_output(
            'a, *rest <- [1]\nprint(a, rest)\n', '1 []\n')

    def test_exact_match(self):
        self.assert_output(
            'a, b, c <- [1, 2, 3]\nprint(a, b, c)\n', '1 2 3\n')

    def test_too_few_values(self):
        self.assert_runtime_error('a, b, c <- [1, 2]\n', 'ValueError')

    def test_too_many_values(self):
        self.assert_runtime_error('a, b <- [1, 2, 3]\n', 'ValueError')

    def test_rhs_must_be_list_or_tuple(self):
        self.assert_runtime_error('a, b <- {"a": 1, "b": 2}\n', 'TypeError')

    def test_duplicate_star_is_parse_error(self):
        from errors import ParseError
        with self.assertRaises(ParseError):
            self.run_ecl('a, *b, *c <- [1, 2, 3]\n')

    def test_duplicate_names_is_parse_error(self):
        from errors import ParseError
        with self.assertRaises(ParseError):
            self.run_ecl('a, *a <- [1, 2, 3]\n')


if __name__ == '__main__':
    unittest.main()
