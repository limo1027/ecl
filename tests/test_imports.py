"""文档九：导入模块。"""

import unittest

from base import EclTest


class TestImports(EclTest):
    def test_import_with_prefix(self):
        src = '''import math
print(math_square(5))
print(math_cube(3))
'''
        self.assert_output(src, '25\n27\n')

    def test_import_as_alias(self):
        src = '''import math as m
print(m_square(4))
'''
        self.assert_output(src, '16\n')

    def test_from_import(self):
        src = '''from math import square
print(square(5))
'''
        self.assert_output(src, '25\n')

    def test_from_import_alias(self):
        src = '''from math import cube as c
print(c(2))
'''
        self.assert_output(src, '8\n')

    def test_missing_module_warns(self):
        result = self.run_ecl('import nosuchmodule\nprint(1)\n')
        self.assertIsNone(result.error)
        self.assertEqual(result.stdout, '1\n')
        self.assertTrue(any('找不到模块' in w for w in result.warnings))


if __name__ == '__main__':
    unittest.main()
