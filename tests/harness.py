"""ECL 测试工具：直接把源码跑完整条流水线（不落缓存）。"""

import contextlib
import io
import os
import pathlib
import shutil
import subprocess
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from token_parse import split_token                    # noqa: E402
from ast_parse import Parser                          # noqa: E402
from checker import Checker                           # noqa: E402
from compiler import Compiler                         # noqa: E402
from optimizer import Optimizer                       # noqa: E402
from interpreter import Interpreter                   # noqa: E402
from errors import EclException                       # noqa: E402


class RunResult:
    def __init__(self, stdout, stderr, warnings, error):
        self.stdout = stdout
        self.stderr = stderr
        self.warnings = warnings
        self.error = error

    def __repr__(self):
        return (f"RunResult(stdout={self.stdout!r}, stderr={self.stderr!r}, "
                f"warnings={self.warnings!r}, error={self.error!r})")


@contextlib.contextmanager
def chdir(path):
    old = os.getcwd()
    os.chdir(str(path))
    try:
        yield
    finally:
        os.chdir(old)


@contextlib.contextmanager
def temp_dir():
    """工作区内的临时目录（沙箱允许写入工作区，系统 Temp 可能被拒绝）。"""
    base = ROOT / 'tests' / '.tmp'
    base.mkdir(exist_ok=True)
    path = base / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def compile_source(src, aot_mode=False):
    """词法 -> 语法 -> 检查 -> 编译 -> 优化。返回 (ir, exc, warnings)。"""
    program = Parser(split_token(src)).parse()
    warnings = Checker().check(program)
    compiler = Compiler(aot_mode=aot_mode)
    ir, exc = compiler.compile_program(program)
    ir = Optimizer().optimize(ir)
    return ir, exc, warnings


def run_source(src, interpreter=None):
    """执行 ECL 源码。

    解析/检查/编译错误会直接抛出（测试用 assertRaises 捕获）；
    运行时 EclException 放在 RunResult.error 里返回，方便断言。
    """
    with chdir(ROOT):
        ir, exc, warnings = compile_source(src)
        interp = interpreter or Interpreter()
        out = io.StringIO()
        err = io.StringIO()
        error = None
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                interp.run_bytecode(ir, exc)
        except EclException as e:
            error = e
        return RunResult(out.getvalue(), err.getvalue(), warnings, error)


def run_file(rel_path):
    """用子进程跑 `python main.py run <file>`，返回 (code, stdout, stderr)。"""
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    proc = subprocess.run(
        [sys.executable, 'main.py', 'run', str(rel_path), '--no-cache'],
        cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8',
        errors='replace', env=env)
    return proc.returncode, proc.stdout, proc.stderr
