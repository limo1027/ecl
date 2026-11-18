"""struct / dll_import / i32 / DLL 导出测试。

需要 MSVC：用于生成原生测试 DLL 和 AOT 编译；找不到时跳过。
"""

import ctypes
import os
import pathlib
import shutil
import subprocess
import sys
import unittest
import uuid

from base import EclTest
from harness import ROOT, run_source
from main import find_msvc_bin, _msvc_env
from interpreter import Interpreter

MSVC = find_msvc_bin()
BUILD_ROOT = ROOT / 'tests' / '.tmp'


def _build_native_dll():
    """编译 tests/native/testdll.c，返回绝对路径。"""
    BUILD_ROOT.mkdir(exist_ok=True)
    tmp = BUILD_ROOT / f"native_{uuid.uuid4().hex}"
    tmp.mkdir()
    source = ROOT / 'tests' / 'native' / 'testdll.c'
    env = _msvc_env(MSVC)
    subprocess.run(
        [str(pathlib.Path(MSVC) / 'cl.exe'), '/nologo', '/LD', '/O2', '/MT',
         str(source), '/Fe:testdll.dll'],
        cwd=str(tmp), check=True, capture_output=True, env=env)
    dll = tmp / 'testdll.dll'
    assert dll.exists(), '测试 DLL 编译失败'
    return tmp, dll


@unittest.skipUnless(MSVC, "未找到 MSVC 工具链，跳过 dll_import 测试")
class TestDllImport(EclTest):
    @classmethod
    def setUpClass(cls):
        cls._tmp, cls._dll = _build_native_dll()
        cls.dll_path = cls._dll.as_posix()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def source(self, body):
        return f'''struct POINT:
    i32 x
    i32 y
end

struct PAIR:
    POINT a
    POINT b
end

dll_import "{self.dll_path}":
    def add32(i32 a, i32 b) -> i32
    def add64(i64 a, i64 b) -> i64
    def sum5(int a, int b, int c, int d, int e) -> int
    def addf(float a, float b) -> float
    def addd(double a, double b) -> double
    def wlen(str s) -> i32
    def clen(cstr s) -> i32
    def greet() -> str
    def cgreet() -> cstr
    def point_sum(ptr POINT p) -> i32
    def point_sum_val(POINT p) -> i32
    def pair_sum(ptr PAIR p) -> i32
    def set_point(ptr POINT p, i32 x, i32 y) -> none
    def get_point(out POINT p) -> none
end

{body}
'''

    def test_scalar_and_string_calls(self):
        result = self.run_ecl(self.source('''
print(add32(20, 22))
print(add64(1099511627776, 1))
print(sum5(1, 2, 3, 4, 5))
print(addf(1.5, 2.25))
print(addd(1.5, 2.25))
print(wlen("你好abc"))
print(clen("hello"))
print(greet())
print(cgreet())
print(type(add32(1, 2)))
print(type(greet()))
'''))
        self.assertIsNone(result.error, result.error)
        self.assertEqual(result.stdout.splitlines(), [
            '42', '1099511627777', '15', '3.75', '3.75',
            '5', '5', 'hello', 'hi', 'i32', 'str',
        ])

    def test_i32_wrap(self):
        result = self.run_ecl(self.source(
            'print(add32(2147483647, 2))\n'))
        self.assertIsNone(result.error, result.error)
        self.assertEqual(result.stdout, '-2147483647\n')

    def test_struct_ptr_and_out(self):
        result = self.run_ecl(self.source('''
p <- {"x": 0, "y": 0}
set_point(p, 3, 4)
print(p["x"], p["y"], point_sum(p))
print(point_sum_val({"x": 5, "y": 6}))
print(pair_sum({"a": {"x": 1, "y": 2}, "b": {"x": 3, "y": 4}}))
q <- {"x": 0, "y": 0}
get_point(q)
print(q["x"], q["y"])
print(type(p["x"]))
x <- add32(2147483647, 1)
print(x, type(x))
print(x + 1, type(x + 1))
print(x | 1, type(x | 1))
print(~x, type(~x))
print(-x, type(-x))
'''))
        self.assertIsNone(result.error, result.error)
        self.assertEqual(result.stdout,
                         '3 4 7\n11\n10\n30 12\ni32\n'
                         '-2147483648 i32\n-2147483647 i32\n'
                         '-2147483647 i32\n2147483647 i32\n'
                         '-2147483648 i32\n')

    def test_dll_not_found_is_catchable(self):
        result = self.run_ecl('''
dll_import "no_such_library_xyz.dll":
    def Foo() -> int
end

try:
    print(Foo())
except DllNotFoundError as e:
    print("caught", type(e))
end
''')
        self.assertIsNone(result.error, result.error)
        self.assertEqual(result.stdout, 'caught DllNotFoundError\n')

    def test_no_dll(self):
        src = self.source('print(add32(1, 2))\n')
        result = run_source(src, interpreter=Interpreter(allow_dll=False))
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.exc_type, 'RuntimeError')


class TestStructChecks(EclTest):
    def test_struct_as_variable_type(self):
        self.assert_output(
            'struct P:\n    i32 x\nend\n'
            'P p <- P()\np.x <- 7\nprint(p.x, sizeof(P), type(p))\n',
            '7 4 P\n')

    def test_struct_unknown_field_type(self):
        self.assert_check_error(
            'struct P:\n    foo x\nend\n', '不支持')

    def test_duplicate_struct(self):
        self.assert_check_error(
            'struct P:\n    i32 x\nend\nstruct P:\n    i32 y\nend\n',
            '重复声明')

    def test_dll_conflict_with_function(self):
        self.assert_check_error('''
def Foo() -> int:
    return 1
end
dll_import "kernel32.dll":
    def Foo() -> int
end
''', '与 ECL 函数重名')

    def test_struct_only_top_level(self):
        self.assert_check_error('''
def f() -> none:
    struct P:
        i32 x
    end
end
''', '只能在顶层声明')


@unittest.skipUnless(MSVC, "未找到 MSVC 工具链，跳过 AOT DLL 测试")
class TestAotDll(EclTest):
    @classmethod
    def setUpClass(cls):
        cls._tmp, cls._dll = _build_native_dll()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _compile_run(self, source_text, name):
        source = self._tmp / f'{name}.ecl'
        source.write_text(source_text, encoding='utf-8')
        exe_base = self._tmp / name
        proc = subprocess.run(
            [sys.executable, 'main.py', 'compile', str(source),
             '--target', 'exe', '-o', str(exe_base), '--no-cache'],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding='utf-8', errors='replace')
        self.assertEqual(proc.returncode, 0,
                         f"编译失败:\n{proc.stdout}\n{proc.stderr}")
        return subprocess.run(
            [str(exe_base) + '.exe'], cwd=str(ROOT), capture_output=True,
            text=True, encoding='utf-8', errors='replace')

    def test_native_dll_parity(self):
        dll = self._dll.as_posix()
        body = f'''struct POINT:
    i32 x
    i32 y
end
struct PAIR:
    POINT a
    POINT b
end
dll_import "{dll}":
    def add32(i32 a, i32 b) -> i32
    def add64(i64 a, i64 b) -> i64
    def sum5(int a, int b, int c, int d, int e) -> int
    def addf(float a, float b) -> float
    def addd(double a, double b) -> double
    def wlen(str s) -> i32
    def clen(cstr s) -> i32
    def greet() -> str
    def cgreet() -> cstr
    def point_sum(ptr POINT p) -> i32
    def point_sum_val(POINT p) -> i32
    def pair_sum(ptr PAIR p) -> i32
    def set_point(ptr POINT p, i32 x, i32 y) -> none
    def get_point(out POINT p) -> none
end
print(add32(2147483647, 2))
print(add64(1099511627776, 1))
print(sum5(1, 2, 3, 4, 5))
print(addf(1.5, 2.25))
print(addd(1.5, 2.25))
print(wlen("你好abc"))
print(clen("hello"))
print(greet())
print(cgreet())
p <- {{"x": 0, "y": 0}}
set_point(p, 3, 4)
print(p["x"], p["y"], point_sum(p))
print(point_sum_val({{"x": 5, "y": 6}}))
print(pair_sum({{"a": {{"x": 1, "y": 2}}, "b": {{"x": 3, "y": 4}}}}))
q <- {{"x": 0, "y": 0}}
get_point(q)
print(q["x"], q["y"])
print(type(add32(1, 2)))
x <- add32(2147483647, 1)
print(x, type(x))
print(x + 1, type(x + 1))
print(x | 1, type(x | 1))
print(~x, type(~x))
print(-x, type(-x))
'''
        interpreted = run_source(body)
        self.assertIsNone(interpreted.error, interpreted.error)
        proc = self._compile_run(body, 'native')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, interpreted.stdout,
                         f"AOT 与解释器不一致\nstderr:\n{proc.stderr}")


@unittest.skipUnless(MSVC, "未找到 MSVC 工具链，跳过 DLL 导出测试")
class TestDllExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BUILD_ROOT.mkdir(exist_ok=True)
        cls._tmp = BUILD_ROOT / f"export_{uuid.uuid4().hex}"
        cls._tmp.mkdir()
        cls.source = cls._tmp / 'lib.ecl'
        cls.source.write_text('''
public def add(int a, int b) -> int:
    return a + b
end
public def add32(i32 a, i32 b) -> i32:
    return a + b
end
public def addf(float a, float b) -> float:
    return a + b
end
public def addd(double a, double b) -> double:
    return a + b
end
public def sum5(int a, int b, int c, int d, int e) -> int:
    return a + b + c + d + e
end
public def greet(str name) -> str:
    return "Hello, " + name
end
public def truthy(int x) -> bool:
    return x != 0
end
private def hidden() -> int:
    return 42
end
const BASE <- 1000
boot <- 5
public def with_const(int x) -> int:
    return x + BASE
end
public def next_boot() -> int:
    boot <- boot + 1
    return boot
end
''', encoding='utf-8')
        proc = subprocess.run(
            [sys.executable, 'main.py', 'compile', str(cls.source),
             '--target', 'dll', '-o', str(cls._tmp / 'lib'), '--no-cache'],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding='utf-8', errors='replace')
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cls.dll = cls._tmp / 'lib.dll'

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_exports(self):
        lib = ctypes.WinDLL(str(self.dll))
        lib.add.restype = ctypes.c_int64
        lib.add.argtypes = [ctypes.c_int64, ctypes.c_int64]
        self.assertEqual(lib.add(2, 3), 5)
        lib.add32.restype = ctypes.c_int32
        lib.add32.argtypes = [ctypes.c_int32, ctypes.c_int32]
        self.assertEqual(lib.add32(2147483647, 2), -2147483647)
        lib.addf.restype = ctypes.c_float
        lib.addf.argtypes = [ctypes.c_float, ctypes.c_float]
        self.assertAlmostEqual(lib.addf(1.5, 2.25), 3.75, places=5)
        lib.addd.restype = ctypes.c_double
        lib.addd.argtypes = [ctypes.c_double, ctypes.c_double]
        self.assertAlmostEqual(lib.addd(1.5, 2.25), 3.75, places=5)
        lib.sum5.restype = ctypes.c_int64
        lib.sum5.argtypes = [ctypes.c_int64] * 5
        self.assertEqual(lib.sum5(1, 2, 3, 4, 5), 15)
        lib.greet.restype = ctypes.c_wchar_p
        lib.greet.argtypes = [ctypes.c_wchar_p]
        self.assertEqual(lib.greet('ECL'), 'Hello, ECL')
        lib.truthy.restype = ctypes.c_int32
        lib.truthy.argtypes = [ctypes.c_int64]
        self.assertEqual((lib.truthy(0), lib.truthy(7)), (0, 1))
        # DLL 没有入口点：第一次调用导出函数时必须执行顶层初始化，
        # 否则 const / 全局变量都是 unknown。
        lib.with_const.restype = ctypes.c_int64
        lib.with_const.argtypes = [ctypes.c_int64]
        self.assertEqual(lib.with_const(1), 1001)
        lib.next_boot.restype = ctypes.c_int64
        lib.next_boot.argtypes = []
        self.assertEqual((lib.next_boot(), lib.next_boot()), (6, 7))
        with self.assertRaises(AttributeError):
            lib.hidden

    def test_private_not_in_exports(self):
        text = (self._tmp / 'lib.def').read_text(encoding='utf-8')
        self.assertIn('add=', text)
        self.assertNotIn('hidden', text)


if __name__ == '__main__':
    unittest.main()
