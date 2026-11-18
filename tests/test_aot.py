"""AOT 原生程序与解释器行为一致性测试。

需要 uasm64/lld-link（或 MSVC）；找不到时整体跳过。
"""

import os
import pathlib
import subprocess
import sys
import unittest

from harness import ROOT, temp_dir
from main import (ROOT_DIR, find_assembler, find_compiler, find_linker)

SCRIPTS = ['hello', 'match', 'with_using', 'unpack', 'oop',
           'features', 'oop_adv', 'functions_adv', 'builtins_adv',
           'with_adv', 'numbers', 'code_objects', 'with_propagate',
           'closures', 'floats', 'functions_edge', 'unknown']

# 汇编器/链接器来自 uasm64/lld-link；C 编译器只用于 runtime.c。
# 没有编译器时，如果已有预编译 runtime.obj 也可以跑 AOT 测试。
AOT_AVAILABLE = (
    find_assembler() is not None
    and find_linker() is not None
    and (find_compiler() is not None
         or (ROOT_DIR / 'runtime.obj').exists())
)


def _subprocess_env():
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


@unittest.skipUnless(AOT_AVAILABLE, "未找到 AOT 工具链，跳过 AOT 测试")
class TestAotParity(unittest.TestCase):
    maxDiff = None

    def _compile(self, source, output):
        proc = subprocess.run(
            [sys.executable, 'main.py', 'compile', str(source),
             '--target', 'exe', '-o', str(output), '--no-cache'],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding='utf-8', errors='replace', env=_subprocess_env())
        self.assertEqual(proc.returncode, 0,
                         f"编译失败:\n{proc.stdout}\n{proc.stderr}")
        exe = pathlib.Path(str(output) + '.exe')
        self.assertTrue(exe.exists(), proc.stderr)
        return exe

    def test_scripts_match_interpreter(self):
        for name in SCRIPTS:
            with self.subTest(script=name):
                source = ROOT / 'tests' / 'ecl' / f'{name}.ecl'
                expected = (ROOT / 'tests' / 'ecl' / f'{name}.expected').read_text(
                    encoding='utf-8')
                with temp_dir() as tmp:
                    exe = self._compile(source, tmp / name)
                    proc = subprocess.run(
                        [str(exe)], cwd=str(ROOT), capture_output=True,
                        text=True, encoding='utf-8', errors='replace',
                        env=_subprocess_env())
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, expected,
                                 f"{name} AOT 输出与解释器不一致\n"
                                 f"stderr:\n{proc.stderr}")

    def test_example_scripts_match_interpreter(self):
        """example/ 下的完整示例：AOT 输出必须和解释器逐字节一致。"""
        # 部分示例依赖运行时 DLL；DLL 不存在时跳过，不阻断其它测试。
        needs_dll = {
            'simple.ecl': ROOT / 'example' / 'test.dll',
            'dll_test.ecl': ROOT / 'example' / 'testdll.dll',
        }
        for name in ('simple.ecl', 'general_test.ecl', 'interp_test.ecl',
                     'dll_test.ecl'):
            with self.subTest(script=name):
                required = needs_dll.get(name)
                if required is not None and not required.exists():
                    continue
                source = ROOT / 'example' / name
                run = subprocess.run(
                    [sys.executable, 'main.py', 'run', str(source),
                     '--no-cache'],
                    cwd=str(ROOT), capture_output=True, text=True,
                    encoding='utf-8', errors='replace',
                    env=_subprocess_env())
                self.assertEqual(run.returncode, 0, run.stderr)
                with temp_dir() as tmp:
                    exe = self._compile(source, tmp / 'prog')
                    aot = subprocess.run(
                        [str(exe)], cwd=str(ROOT), capture_output=True,
                        text=True, encoding='utf-8', errors='replace',
                        env=_subprocess_env())
                self.assertEqual(aot.returncode, 0, aot.stderr)
                self.assertEqual(aot.stdout, run.stdout,
                                 f"{name} AOT 与解释器不一致")

    def test_input_matches_interpreter(self):
        source = ROOT / 'tests' / 'ecl' / 'input.ecl'
        text = 'Bob\n'
        run = subprocess.run(
            [sys.executable, 'main.py', 'run', str(source), '--no-cache'],
            cwd=str(ROOT), input=text, capture_output=True, text=True,
            encoding='utf-8', errors='replace', env=_subprocess_env())
        self.assertEqual(run.returncode, 0, run.stderr)
        with temp_dir() as tmp:
            exe = self._compile(source, tmp / 'input')
            aot = subprocess.run(
                [str(exe)], cwd=str(ROOT), input=text, capture_output=True,
                text=True, encoding='utf-8', errors='replace',
                env=_subprocess_env())
        self.assertEqual(aot.returncode, 0, aot.stderr)
        self.assertEqual(aot.stdout, run.stdout)

    def test_deep_copy_free_buffer_semantics(self):
        """AOT 的深拷贝 / free / new_buffer 语义。"""
        src = '''a <- [1, 2]
b <- a
b[0] <- 9
print("a:", a)
print("b:", b)

l <- [10, 20]
free(l[0])
print("l:", l)

buf <- new_buffer(8)
buf[0] <- 65
buf[1] <- 66
print("buf:", buf[0], buf[1], sizeof(buf), type(buf))
print("addr:", (&buf) != 0)
free(buf)
print("after free:", buf)

struct P:
    i32 x
    i32 y
end
p <- P()
p.x <- 3
p.y <- 4
print("struct:", p.x + p.y, sizeof(P), type(p))
q <- p
q.x <- 9
print("copy:", p.x, q.x)

w <- w"hello 世界"
print("wstr:", w, type(w))
'''
        with temp_dir() as tmp:
            source = tmp / 'mem_semantics.ecl'
            source.write_text(src, encoding='utf-8')
            exe = self._compile(source, tmp / 'mem_semantics')
            proc = subprocess.run(
                [str(exe)], cwd=str(ROOT), capture_output=True, text=True,
                encoding='utf-8', errors='replace', env=_subprocess_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, (
            'a: [1, 2]\n'
            'b: [9, 2]\n'
            'l: [null, 20]\n'
            'buf: 65 66 8 buffer\n'
            'addr: True\n'
            'after free: null\n'
            'struct: 7 8 P\n'
            'copy: 3 9\n'
            'wstr: hello 世界 wstr\n'
        ))

    def test_external_exec_sugar(self):
        """@@X(code) 脱糖为 X().__exec__(code)，AOT 下同样可用。"""
        src = '''class PyCtx:
    def __init__(str code) -> none:
        self.code <- code
    end

    def run(int a, int b) -> int:
        return a + b
    end
end

class PyRunner:
    def __exec__(str code) -> any:
        return PyCtx(code)
    end
end

ctx <- @@PyRunner("def add(a, b): return a + b")
print(ctx.run(3, 5))
'''
        with temp_dir() as tmp:
            source = tmp / 'external_exec.ecl'
            source.write_text(src, encoding='utf-8')
            exe = self._compile(source, tmp / 'external_exec')
            proc = subprocess.run(
                [str(exe)], cwd=str(ROOT), capture_output=True, text=True,
                encoding='utf-8', errors='replace', env=_subprocess_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, '8\n')

    def test_int_string_conversion(self):
        """AOT 的 int("...") 必须和解释器一样解析进制/符号/下划线。"""
        src = '''print(int("42"))
print(int("-7"))
print(int("0xFF"))
print(int("0b1010"))
print(int("0o755"))
print(int("1_000_000"))
print(int(" 3 ") + int("4"))
print(i64("7"))
print(int("18446744073709551615"))
print(int("18446744073709551615") * 7)
print(int(int("18446744073709551615")))
print(i64("18446744073709551615"))
print(i64("18446744073709551615") * 7)
'''
        with temp_dir() as tmp:
            source = tmp / 'int_string.ecl'
            source.write_text(src, encoding='utf-8')
            exe = self._compile(source, tmp / 'int_string')
            proc = subprocess.run(
                [str(exe)], cwd=str(ROOT), capture_output=True, text=True,
                encoding='utf-8', errors='replace', env=_subprocess_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout,
                         '42\n-7\n255\n10\n493\n1000000\n7\n7\n'
                         '18446744073709551615\n'
                         '129127208515966861305\n'
                         '18446744073709551615\n'
                         '-1\n-7\n')

    def test_remove_output(self):
        """--remove-output 只保留最终 .exe，清理中间产物和缓存。"""
        with temp_dir() as tmp:
            source = ROOT / 'tests' / 'ecl' / 'hello.ecl'
            out = tmp / 'clean'
            base = [sys.executable, 'main.py', 'compile', str(source),
                    '--target', 'exe', '-o', str(out)]
            first = subprocess.run(
                base, cwd=str(ROOT), capture_output=True, text=True,
                encoding='utf-8', errors='replace', env=_subprocess_env())
            self.assertEqual(first.returncode, 0,
                             f"编译失败:\n{first.stdout}\n{first.stderr}")
            self.assertTrue((tmp / 'clean.aot.eclc').exists())
            self.assertTrue((tmp / 'clean.asm').exists())

            second = subprocess.run(
                [*base, '--remove-output'], cwd=str(ROOT),
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', env=_subprocess_env())
            self.assertEqual(second.returncode, 0,
                             f"编译失败:\n{second.stdout}\n{second.stderr}")
            self.assertTrue((tmp / 'clean.exe').exists())
            for suffix in ('.asm', '.obj', '.aot.eclc'):
                self.assertFalse((tmp / f'clean{suffix}').exists(), suffix)

    def test_compile_error_is_clean(self):
        # 动态 code() 在 AOT 下应给出明确错误，而不是静默错误行为
        with temp_dir() as tmp:
            source = tmp / 'dyn.ecl'
            source.write_text(
                'str s <- "print(1)"\ncode c <- code(s)\nc.run()\n',
                encoding='utf-8')
            proc = subprocess.run(
                [sys.executable, 'main.py', 'compile', str(source),
                 '--target', 'exe', '-o', str(tmp / 'dyn'), '--no-cache'],
                cwd=str(ROOT), capture_output=True, text=True,
                encoding='utf-8', errors='replace', env=_subprocess_env())
        # 允许编译通过但运行时报错；这里只要求不崩溃在编译器内部
        self.assertNotIn('Traceback', proc.stderr)


if __name__ == '__main__':
    unittest.main()
