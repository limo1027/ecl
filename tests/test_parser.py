"""解析器回归测试：多余 end 不能导致死循环。"""

import unittest

from base import EclTest
from errors import ParseError


class TestParserRegressions(EclTest):
    def test_stray_top_level_end_is_error(self):
        # 旧实现：parse_statement 返回 None 且不前进 -> 无限循环
        with self.assertRaises(ParseError) as ctx:
            self.run_ecl('print(1)\nend\n')
        self.assertIn('end', str(ctx.exception))

    def test_extra_end_after_block_is_error(self):
        with self.assertRaises(ParseError):
            self.run_ecl('if True:\n print(1)\nend\nend\n')

    def test_using_does_not_need_end(self):
        src = '''class Box:
    def __init__() -> none:
        self.val <- 1
    end
    def show() -> int:
        return self.val
    end
end
box <- Box()
using box
print("using read:", val)
val <- 42
print("using write:", box.val)
print("using method:", show())
'''
        self.assert_output(
            src,
            'using read: 1\nusing write: 42\nusing method: 42\n')


if __name__ == '__main__':
    unittest.main()
