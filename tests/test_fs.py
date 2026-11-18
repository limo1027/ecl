"""example/fs.ecl 的端到端测试：编译成 DLL，再由 ECL 调用。

覆盖：write/append/read、size、rename、exists、mkdir/rmdir、
delete、list_dir，以及解释器与 AOT 输出一致。
"""

import pathlib
import shutil
import subprocess
import sys
import unittest
import uuid

from harness import ROOT, run_source
from main import find_assembler, find_compiler, find_linker

BUILD_ROOT = ROOT / 'tests' / '.tmp'
TOOLCHAIN = all((find_compiler(), find_assembler(), find_linker()))


@unittest.skipUnless(TOOLCHAIN, "未找到 AOT 工具链，跳过 fs 测试")
class TestFsLibrary(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        BUILD_ROOT.mkdir(exist_ok=True)
        cls.tmp = BUILD_ROOT / f"fs_{uuid.uuid4().hex}"
        cls.tmp.mkdir()
        cls.work = cls.tmp / 'work'
        cls.work.mkdir()
        source = ROOT / 'example' / 'fs.ecl'
        proc = subprocess.run(
            [sys.executable, 'main.py', 'compile', str(source),
             '--target', 'dll', '-o', str(cls.tmp / 'fs'), '--no-cache',
             '--remove-output'],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding='utf-8', errors='replace')
        if proc.returncode != 0:
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise AssertionError(f"fs.ecl 编译失败:\n{proc.stdout}\n{proc.stderr}")
        cls.dll = (cls.tmp / 'fs.dll').as_posix()
        # --remove-output 应清理中间产物，只留下 DLL。
        for suffix in ('.asm', '.obj', '.def', '.lib', '.exp', '.aot.eclc'):
            if (cls.tmp / f'fs{suffix}').exists():
                shutil.rmtree(cls.tmp, ignore_errors=True)
                raise AssertionError(f"中间产物未清理: fs{suffix}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def source(self):
        d = self.dll
        base = self.work.as_posix()
        return f'''dll_import "{d}":
    def read_text(cstr path) -> str
    def write_text(cstr path, str content) -> bool
    def append_text(cstr path, str content) -> bool
    def exists(cstr path) -> bool
    def is_dir(cstr path) -> bool
    def file_size(cstr path) -> int
    def delete_file(cstr path) -> bool
    def rename_file(cstr old_path, cstr new_path) -> bool
    def create_dir(cstr path) -> bool
    def delete_dir(cstr path) -> bool
    def list_dir(cstr path) -> str
end

file <- "{base}/demo.txt"
renamed <- "{base}/demo2.txt"
unicode_file <- "{base}/测试.txt"
folder <- "{base}/dir"
missing <- "{base}/none.txt"
inner <- folder + "/文件.txt"

print("exists before:", exists(file))
print("write:", write_text(file, "hello\\n"))
print("append:", append_text(file, "world\\n"))
content <- read_text(file)
print("content ok:", content == "hello\\nworld\\n")
print("size:", file_size(file))
print("missing size:", file_size(missing))
print("unicode write:", write_text(unicode_file, "你好\\n"))
print("unicode read:", read_text(unicode_file) == "你好\\n")
print("unicode delete:", delete_file(unicode_file))
print("rename:", rename_file(file, renamed))
print("old:", exists(file), "new:", exists(renamed))

print("mkdir:", create_dir(folder))
print("is_dir:", is_dir(folder))
print("write inner:", write_text(inner, "x"))
found <- false
for name in list_dir(folder).split("\\n"):
    if name == "文件.txt":
        found <- true
    end
end
print("listed 文件.txt:", found)
print("rmdir non-empty:", delete_dir(folder))
print("delete inner:", delete_file(inner))
print("rmdir:", delete_dir(folder))
print("delete:", delete_file(renamed))
print("exists after:", exists(renamed))
print("done")
'''

    def test_interpreter_and_aot(self):
        body = self.source()
        interpreted = run_source(body)
        self.assertIsNone(interpreted.error, interpreted.error)
        expected = (
            'exists before: False\n'
            'write: True\n'
            'append: True\n'
            'content ok: True\n'
            'size: 12\n'
            'missing size: -1\n'
            'unicode write: True\n'
            'unicode read: True\n'
            'unicode delete: True\n'
            'rename: True\n'
            'old: False new: True\n'
            'mkdir: True\n'
            'is_dir: True\n'
            'write inner: True\n'
            'listed 文件.txt: True\n'
            'rmdir non-empty: False\n'
            'delete inner: True\n'
            'rmdir: True\n'
            'delete: True\n'
            'exists after: False\n'
            'done\n'
        )
        self.assertEqual(interpreted.stdout, expected)

        source = self.tmp / 'fs_usage.ecl'
        source.write_text(body, encoding='utf-8')
        exe_base = self.tmp / 'fs_usage'
        proc = subprocess.run(
            [sys.executable, 'main.py', 'compile', str(source),
             '--target', 'exe', '-o', str(exe_base), '--no-cache'],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding='utf-8', errors='replace')
        self.assertEqual(proc.returncode, 0,
                         f"编译失败:\n{proc.stdout}\n{proc.stderr}")
        proc = subprocess.run(
            [str(exe_base) + '.exe'], cwd=str(ROOT), capture_output=True,
            text=True, encoding='utf-8', errors='replace')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, expected,
                         f"AOT 与解释器不一致\nstderr:\n{proc.stderr}")


if __name__ == '__main__':
    unittest.main()
