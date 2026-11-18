"""运行全部测试：python tests/run_tests.py"""

import pathlib
import sys
import unittest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS_DIR))

if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS_DIR), pattern='test_*.py',
                            top_level_dir=str(TESTS_DIR))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
