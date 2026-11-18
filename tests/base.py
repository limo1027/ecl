"""ECL 测试公共断言基类。"""

import unittest

try:
    from harness import run_source
except ImportError:  # 作为 tests 包被导入时
    from .harness import run_source
from errors import EclException, CheckError


class EclTest(unittest.TestCase):
    maxDiff = None

    def run_ecl(self, src):
        return run_source(src)

    def assert_output(self, src, expected):
        result = self.run_ecl(src)
        if result.error is not None:
            self.fail(f"运行时错误: {result.error}\n输出: {result.stdout!r}")
        self.assertEqual(result.stdout, expected)
        return result

    def assert_runtime_error(self, src, exc_type):
        result = self.run_ecl(src)
        self.assertIsNotNone(result.error, "期望运行时错误，但程序正常结束")
        self.assertIsInstance(result.error, EclException)
        self.assertEqual(result.error.exc_type, exc_type,
                         f"实际错误: {result.error}")
        return result

    def assert_check_error(self, src, fragment=''):
        with self.assertRaises(CheckError) as ctx:
            self.run_ecl(src)
        if fragment:
            self.assertIn(fragment, str(ctx.exception))
        return ctx.exception

    def assert_warning(self, src, fragment):
        result = self.run_ecl(src)
        joined = '\n'.join(result.warnings)
        self.assertIn(fragment, joined)
        return result
