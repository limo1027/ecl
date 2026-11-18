"""ECLC 缓存：新常量类型（double / i64 / 大整数）与异常表往返。"""

import os
import pathlib
import subprocess
import sys
import time
import unittest
import uuid
from decimal import Decimal

import dl
from class_and_vars import I64
from harness import ROOT, temp_dir

IR = [
    ('const', 't1', Decimal('0.1')),
    ('const', 't2', I64(9223372036854775807)),
    ('const', 't3', 123456789012345678901234567890),
    ('const', 't4', 'hi'),
    ('func', 'f', [('return_none',), ('halt',)],
     [{'name': 'a', 'type': 'int', 'default': False}],
     None, None, 'int', {}),
]
EXC = [{'try_start': 1, 'try_end': 2, 'handler_label': 'L1',
        'exc_type': None, 'exc_name': None}]


class TestCache(unittest.TestCase):
    def test_binary_roundtrip(self):
        with temp_dir() as tmp:
            path = tmp / 'a.eclc'
            src_hash = dl.make_src_hash('x <- 1')
            dl.save_eclc(path, IR, EXC, False, src_hash)
            ir, exc, aot, got_hash = dl.read_eclc(path)
            self.assertFalse(aot)
            self.assertEqual(got_hash, src_hash)
            self.assertEqual(ir[0], ('const', 't1', Decimal('0.1')))
            self.assertEqual(ir[1], ('const', 't2', I64(9223372036854775807)))
            self.assertEqual(
                ir[2], ('const', 't3', 123456789012345678901234567890))
            self.assertEqual(ir[3], ('const', 't4', 'hi'))
            self.assertEqual(ir[4][0], 'func')
            self.assertEqual(ir[4][2], [('return_none',), ('halt',)])
            self.assertEqual(exc, EXC)

    def test_is_valid(self):
        with temp_dir() as tmp:
            path = tmp / 'a.eclc'
            src = 'x <- 1'
            dl.save_eclc(path, IR, EXC, False, dl.make_src_hash(src))
            self.assertTrue(dl.is_valid(path, src.encode(), False))
            self.assertFalse(dl.is_valid(path, b'changed', False))
            self.assertFalse(dl.is_valid(path, src.encode(), True))

    def test_text_dump_and_load(self):
        with temp_dir() as tmp:
            path = tmp / 'a.eclc'
            src_hash = dl.make_src_hash('x <- 1')
            dl.save_eclc(path, IR, EXC, False, src_hash)
            text = dl.dump_eclc_text(path)
            self.assertIn("__double__", text)
            self.assertIn("__i64__", text)
            self.assertIn("__bigint__", text)

            ir2, exc2 = dl.load_eclc_text(text, False, src_hash)
            self.assertEqual(ir2[0], ('const', 't1', Decimal('0.1')))
            self.assertEqual(ir2[1][0:2], ('const', 't2'))
            self.assertIsInstance(ir2[1][2], I64)
            self.assertEqual(int(ir2[1][2]), 9223372036854775807)
            self.assertEqual(
                ir2[2], ('const', 't3', 123456789012345678901234567890))
            self.assertEqual(exc2, EXC)

    def test_flat_dump_and_load(self):
        with temp_dir() as tmp:
            path = tmp / 'a.eclc'
            src_hash = dl.make_src_hash('x <- 1')
            dl.save_eclc(path, IR, EXC, False, src_hash)
            text = dl.dump_eclc_text(path, flat=True)
            ir2, exc2 = dl.load_eclc_text(text, False, src_hash, flat=True)
            self.assertEqual(len(ir2), len(IR))
            self.assertEqual(ir2[0][0], 'const')
            self.assertEqual(ir2[4][0], 'func')
            self.assertEqual(ir2[4][2], [('return_none',), ('halt',)])


class TestCacheFreshness(unittest.TestCase):
    """源文件 / import 模块的修改时间与 --used-cache。"""

    def _run(self, source, *extra):
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        return subprocess.run(
            [sys.executable, 'main.py', 'run', str(source), *extra],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding='utf-8', errors='replace', env=env)

    def test_source_mtime_invalidates_cache(self):
        with temp_dir() as tmp:
            source = tmp / 'prog.ecl'
            source.write_text('print("v1")\n', encoding='utf-8')

            first = self._run(source, '--verbose')
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, 'v1\n')
            self.assertIn('[cache] 写入', first.stderr)

            # 内容不变，只把源文件时间改到缓存之后
            future = time.time() + 5
            os.utime(source, (future, future))

            second = self._run(source, '--verbose')
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stdout, 'v1\n')
            self.assertIn('[cache] 失效', second.stderr)
            self.assertIn('源文件比缓存新', second.stderr)

    def test_used_cache_forces_stale_cache(self):
        with temp_dir() as tmp:
            source = tmp / 'prog.ecl'
            source.write_text('print("v1")\n', encoding='utf-8')
            first = self._run(source)
            self.assertEqual(first.stdout, 'v1\n')

            source.write_text('print("v2")\n', encoding='utf-8')

            forced = self._run(source, '--used-cache', '--verbose')
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertEqual(forced.stdout, 'v1\n')
            self.assertIn('[cache] 强制使用', forced.stderr)

            normal = self._run(source)
            self.assertEqual(normal.returncode, 0, normal.stderr)
            self.assertEqual(normal.stdout, 'v2\n')

    def test_imported_module_mtime_invalidates_cache(self):
        mod_name = '_cache_dep_' + uuid.uuid4().hex
        mod_path = ROOT / (mod_name + '.ecl')
        mod_path.write_text('val() -> str <- "m1"\n', encoding='utf-8')
        try:
            with temp_dir() as tmp:
                source = tmp / 'main.ecl'
                source.write_text(
                    f'import {mod_name}\n'
                    f'print({mod_name}_val())\n', encoding='utf-8')
                first = self._run(source, '--verbose')
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(first.stdout, 'm1\n')

                mod_path.write_text('val() -> str <- "m2"\n',
                                    encoding='utf-8')
                second = self._run(source, '--verbose')
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual(second.stdout, 'm2\n')
                self.assertIn('[cache] 失效', second.stderr)
                self.assertIn('import 的模块比缓存新', second.stderr)
        finally:
            mod_path.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()

