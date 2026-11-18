"""文档十四：内置函数。"""

import unittest

from base import EclTest


class TestIO(EclTest):
    def test_write_no_newline(self):
        self.assert_output(
            'write("a")\nwrite("b")\nprint("")\n', 'ab\n')

    def test_eprint_goes_to_stderr(self):
        result = self.run_ecl('eprint("to stderr")\nprint("out")\n')
        self.assertIsNone(result.error)
        self.assertEqual(result.stdout, 'out\n')
        self.assertEqual(result.stderr, 'to stderr\n')

    def test_print_empty_values(self):
        self.assert_output(
            'print(none)\nprint(null)\nprint(unknown)\n',
            'none\nnull\nunknown\n')


class TestConversions(EclTest):
    def test_conversions(self):
        self.assert_output(
            'print(int("42"), int(3.9), float(2), double(1), str(5), bool(0))\n'
            'print(type(double(1)), type(i64(1)), type(bi(1)))\n',
            '42 3 2.0 1 5 False\ndouble i64 int\n')


class TestContainers(EclTest):
    def test_len_range_list_tuple_dict(self):
        src = '''print(len("abc"), len([1, 2]), len({"a": 1}))
print(range(3))
print(range(1, 4, 2))
print(list("ab"))
print(tuple([1, 2]))
print(dict([["a", 1]]))
'''
        self.assert_output(
            src,
            "3 2 1\n[0, 1, 2]\n[1, 3]\n['a', 'b']\n(1, 2)\n{'a': 1}\n")

    def test_list_and_dict_methods(self):
        src = '''l <- [1]
l.append(2)
print(l)
d <- {"a": 1, "b": 2}
print(d.has("a"), d.has("z"), d.get("z", 9))
print(d.keys(), d.values())
print(slice([1, 2, 3, 4], 1, 3))
'''
        self.assert_output(
            src,
            "[1, 2]\nTrue False 9\n['a', 'b'] [1, 2]\n[2, 3]\n")


class TestMath(EclTest):
    def test_math_builtins(self):
        src = '''print(abs(-3), max(1, 5), min([2, 1]), sum([1, 2, 3]))
print(pow(2, 3), sqrt(9), floor(3.7), ceil(3.1))
print(round(3.14159, 2))
r <- random(10)
print(r >= 0 and r < 10)
'''
        self.assert_output(
            src, '3 5 1 6\n8 3.0 3 4\n3.14\nTrue\n')


class TestStrings(EclTest):
    def test_string_methods(self):
        src = '''print(" Hi ".strip())
print(" Hi ".strip().upper())
print("abc".lower())
print("a,b,c".split(","))
print("-".join(["x", "y"]))
print("abc".find("b"))
print("abc".replace("b", "X"))
print("{} {}".format(1, 2))
'''
        self.assert_output(
            src, "Hi\nHI\nabc\n['a', 'b', 'c']\nx-y\n1\naXc\n1 2\n")


class TestMemory(EclTest):
    def test_free_sets_null(self):
        self.assert_output(
            'l <- [1, 2]\nfree(l)\nprint(l, type(l))\n', 'null null\n')

    def test_clone_is_deep(self):
        src = '''a <- [1, 2]
b <- clone(a)
b.append(3)
print(a, b)
'''
        self.assert_output(src, '[1, 2] [1, 2, 3]\n')

    def test_id_and_hash(self):
        self.assert_output(
            'print(id("x") > 0)\nprint(hash("x") == hash("x"))\n',
            'True\nTrue\n')


class TestCodeObject(EclTest):
    def test_code_literal_run(self):
        src = '''code c <- `print("hi")`
c.run()
global g <- `int z <- 5`.run()
print(g.z)
'''
        self.assert_output(src, 'hi\n5\n')

    def test_code_builtin(self):
        self.assert_output(
            'c <- code("print(7)")\nc.run()\n', '7\n')


class TestExit(EclTest):
    def test_exit(self):
        with self.assertRaises(SystemExit):
            self.run_ecl('exit(0)\n')


if __name__ == '__main__':
    unittest.main()
