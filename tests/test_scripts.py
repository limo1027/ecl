"""端到端：用 main.py 执行 tests/ecl 下的脚本，比对期望输出。

也覆盖 AOT asm 生成（无需 MSVC 链接器）。
"""

import os
import pathlib
import subprocess
import sys
import unittest

from harness import ROOT, run_file, temp_dir

ECL_DIR = pathlib.Path(__file__).resolve().parent / 'ecl'
SCRIPTS = ['hello', 'match', 'with_using', 'unpack', 'oop',
           'features', 'oop_adv', 'functions_adv', 'builtins_adv',
           'with_adv', 'numbers', 'code_objects', 'with_propagate',
           'closures', 'floats', 'functions_edge', 'unknown']


class TestScripts(unittest.TestCase):
    maxDiff = None

    def test_golden_scripts(self):
        for name in SCRIPTS:
            with self.subTest(script=name):
                source = ECL_DIR / f'{name}.ecl'
                expected = (ECL_DIR / f'{name}.expected').read_text(
                    encoding='utf-8')
                code, out, err = run_file(source.relative_to(ROOT))
                self.assertEqual(code, 0, f"{name} 退出码 {code}\n{err}")
                self.assertEqual(out, expected, f"{name} stderr:\n{err}")

    def test_check_error_exit_code(self):
        code, out, err = run_file('tests/ecl/bad_null.ecl')
        self.assertNotEqual(code, 0)
        self.assertIn('静态检查错误', err)
        self.assertIn('null 不能作为右值', err)

    def test_without_check_skips_checker(self):
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        proc = subprocess.run(
            [sys.executable, 'main.py', 'run', 'tests/ecl/bad_null.ecl',
             '--without-check', '--no-cache'],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding='utf-8', errors='replace', env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn('静态检查错误', proc.stderr)


def _run_compile(args):
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    return subprocess.run(
        [sys.executable, 'main.py', 'compile'] + args,
        cwd=str(ROOT), capture_output=True, text=True,
        encoding='utf-8', errors='replace', env=env)


class TestAotAsm(unittest.TestCase):
    maxDiff = None

    def test_asm_generation(self):
        with temp_dir() as tmp:
            out = tmp / "prog"
            proc = _run_compile([
                'tests/ecl/aot_ok.ecl', '--target', 'asm',
                '-o', str(out), '--no-cache'])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            asm_file = pathlib.Path(str(out) + '.asm')
            self.assertTrue(asm_file.exists(), proc.stderr)
            text = asm_file.read_text(encoding='utf-8')
            self.assertIn('main PROC', text)

    def test_match_compiles_to_asm(self):
        with temp_dir() as tmp:
            out = tmp / "match_prog"
            proc = _run_compile([
                'tests/ecl/match.ecl', '--target', 'asm',
                '-o', str(out), '--no-cache'])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            asm_file = pathlib.Path(str(out) + '.asm')
            self.assertTrue(asm_file.exists())
            self.assertIn('runtime_is_type', asm_file.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
