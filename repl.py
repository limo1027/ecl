#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ECL REPL —— 纯交互层,不 import compiler/vm。

启动方式:
    1) 由 main 注入执行函数:  ecl_repl.main(run_fragment)
    2) 独立启动:             python ecl_repl.py [file.ecl]
       (自动从 main 构造 runner,Interpreter 里没有 compiler)

run_fragment(source) 契约:
    - 执行一段 ECL 源码(编译 + run_bytecode)
    - 复用同一个 Interpreter(会话状态跨行保留)
    - 失败时抛 EclException 或其它异常,由 REPL 捕获打印
    - 返回值忽略
"""

import os
import sys
import time

ECL_VERSION = "0.1.0"
HISTFILE = os.path.expanduser("~/.ecl_history")

# ============================================================
# 补全词表
# ============================================================
BUILTIN_NAMES = [
    "print", "write", "eprint", "input", "type", "len", "str", "int",
    "i64", "float", "double", "bool", "bi", "code", "range", "list",
    "tuple", "dict", "slice", "abs", "max", "min", "sum", "pow", "sqrt",
    "floor", "ceil", "round", "random", "free", "clone", "id", "hash",
    "exit", "new_buffer", "sizeof", "help", "dir", "save", "load",
    "clear", "timeit",
    "Exception", "ValueError", "TypeError", "IndexError", "KeyError",
    "ZeroDivisionError", "RuntimeError", "NameError", "AttributeError",
    "ConstChangingError", "SyntaxError", "DllNotFoundError",
    "True", "False", "true", "false", "none", "null", "unknown",
]

# ============================================================
# 独立启动时的默认 runner(延迟 import,避免 REPL 单独被
# import 时拖起 compiler/vm)
# ============================================================


def build_default_runner():
    """独立启动时用。编译旁路缓存,Interpreter 全会话共享。"""
    from interpreter import Interpreter
    from ecl import compile_with_cache   # ← 你 compile_with_cache 所在模块名

    interp = Interpreter()

    def run_fragment(source, source_path=None):
        ir, exception_table = compile_with_cache(
            source,
            aot_mode=False,
            cache_path=None,      # ← 若不接受 None,改为给临时路径
            no_cache=True,
            source_path=None,
        )
        interp.run_bytecode(ir, exception_table)
        return interp

    return run_fragment


# ============================================================
# 块检测:ECL 用显式 end,好判
# ============================================================
BLOCK_OPEN = (
    "def ", "class ", "struct ", "if ", "elif ", "else", "while ", "for ",
    "try", "except", "finally", "match ", "case ", "with ", "using ",
    "dll_import",
)


def _strip_comment(line):
    idx = line.find("#")
    if idx != -1:
        line = line[:idx]
    return line.strip()


def line_opens_block(line):
    s = _strip_comment(line)
    return bool(s) and (s.startswith(BLOCK_OPEN) or s in ("else", "try"))


def line_is_end(line):
    return _strip_comment(line) == "end"


def line_closes_open_block(buf):
    """buf 是已收集的行列表。最后一行是 end 就认为块可能闭合。
    ECL 无缩进依赖,end 数量配对即可:统计 open 与 end。"""
    opens = 0
    for ln in buf:
        s = _strip_comment(ln)
        if line_opens_block(s):
            opens += 1
        elif s == "end":
            opens -= 1
    return opens <= 0


# ============================================================
# REPL 主体
# ============================================================
class ECLRepl:
    PS1 = ">>> "
    PS2 = "..  "

    def __init__(self, run_fragment):
        self.run_fragment = run_fragment   # 注入的执行函数
        self.session_log = []              # 成功执行的语句,save 用

    # ---------- 执行:异常不杀死会话 ----------
    def execute(self, source):
        t0 = time.perf_counter()
        try:
            self.run_fragment(source, "<repl>")
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt")
            return False
        except SystemExit:
            raise
        except Exception as e:
            self.print_error(e)
            return False
        dt = (time.perf_counter() - t0) * 1000
        if dt > 100:
            print(f"  # 耗时 {dt:.0f}ms")
        return True

    def print_error(self, e):
        """EclException 打 ECL 风格 traceback;其它异常打解释器 bug 报告"""
        line = getattr(e, "line", None)
        exc_type = getattr(e, "exc_type", None)
        msg = getattr(e, "message", None)

        if exc_type and msg is not None:
            print("  Traceback (most recent call last):")
            if line:
                print(f"    line {line}, in <repl>")
            print(f"  {exc_type}: {msg}")
        elif msg is not None:               # 语法/检查错误等
            print(f"  {type(e).__name__}: {msg}")
        else:
            import traceback
            print(f"  [interpreter bug] {type(e).__name__}: {e}")
            traceback.print_exc()

    # ---------- 特殊命令 ----------
    def try_command(self, line):
        s = line.strip()

        if s in ("exit", "quit", "q", "exit()", "quit()"):
            raise SystemExit(0)

        if s == "clear":
            self.run_fragment = build_default_runner()
            self.session_log.clear()
            print("# 会话已清空")
            return True

        if s.startswith("save"):
            parts = s.split(maxsplit=1)
            if len(parts) < 2:
                print('# 用法: save "文件名.ecl"')
                return True
            fname = parts[1].strip().strip('"').strip("'")
            if not fname.endswith(".ecl"):
                fname += ".ecl"
            with open(fname, "w", encoding="utf-8") as f:
                f.write("\n".join(self.session_log) + "\n")
            print(f"# 已保存 {len(self.session_log)} 条语句到 {fname}")
            return True

        if s.startswith("load"):
            parts = s.split(maxsplit=1)
            if len(parts) < 2:
                print('# 用法: load "文件名.ecl"')
                return True
            fname = parts[1].strip().strip('"').strip("'")
            if not os.path.exists(fname):
                print(f"  FileNotFoundError: {fname}")
                return True
            with open(fname, "r", encoding="utf-8") as f:
                src = f.read()
            # 整文件一次编译执行(保持文件语义),成功后状态保留
            if self.execute(src):
                print(f"# {fname} 执行成功,会话状态已更新")
                self.session_log.append(f'load "{fname}"')
            return True

        if s.startswith("timeit"):
            expr = s[len("timeit"):].strip()
            if not expr:
                print("# 用法: timeit <表达式>")
                return True
            n = 100
            t0 = time.perf_counter()
            for _ in range(n):
                if not self.execute(expr):
                    return True
            dt = (time.perf_counter() - t0) / n * 1000
            print(f"# 平均 {dt:.3f}ms/次 ({n} 次)")
            return True

        if s in ("?", "help", "help()"):
            self.print_repl_help()
            return True

        if s.startswith("?"):
            expr = s[1:].strip()
            if expr:
                self.inspect(expr)
            return True

        return False

    def inspect(self, expr):
        """?x —— 执行表达式并显示类型+值"""
        if self.execute(expr):
            # 类型靠 type(expr) 再跑一次
            ok_src = f'print(type({expr}), {expr})' if expr.isidentifier() \
                else None
            if ok_src:
                try:
                    self.run_fragment(ok_src, "<repl>")
                except Exception:
                    pass
        # 注意:值回显由解释器 print 完成;若你的解释器支持取值钩子,
        # 可在此改为 interp.get_global(expr) 精确输出

    def print_repl_help(self):
        print(f"""
ECL {ECL_VERSION} REPL 帮助
────────────────────────
- 表达式回车执行,print 查看结果
- def/class/if/while/for/match/try 开头自动进入多行模式,以 end 结束
- Ctrl+C 取消当前行/块,会话保留
命令:
  save "f.ecl"    保存本次会话成功语句
  load "f.ecl"    执行文件,状态保留
  timeit <expr>   表达式计时(100 次平均)
  ?x              执行 x 并显示
  dir()           列出当前全局(交给解释器执行)
  clear           清空会话
  exit / quit     退出
""")

    # ---------- 执行一条/一块 ----------
    def run_block(self, source):
        if self.execute(source):
            self.session_log.append(source)

    # ---------- 主循环 ----------
    def repl(self):
        session = self._make_prompt()

        print(f"ECL {ECL_VERSION} (repl) on {sys.platform}")
        print("输入 help 查看帮助, exit 退出")

        while True:
            try:
                line = session(self.PS1)
            except KeyboardInterrupt:
                print()
                continue
            except EOFError:
                print()
                break

            if not line.strip():
                continue

            try:
                if self.try_command(line):
                    continue
            except SystemExit:
                print("再见!")
                break

            if line_opens_block(line):
                buf = [line]
                cancelled = False
                while not line_closes_open_block(buf):
                    try:
                        more = session(self.PS2)
                    except KeyboardInterrupt:
                        print("\n# 块已取消")
                        cancelled = True
                        break
                    except EOFError:
                        print("\n# 块未闭合,已取消")
                        cancelled = True
                        break
                    buf.append(more)
                if cancelled:
                    continue
                self.run_block("\n".join(buf))
            else:
                self.run_block(line)

    def _make_prompt(self):
        """prompt_toolkit 可用则用之,否则退回 input"""
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

            ps = PromptSession(
                history=FileHistory(HISTFILE),
                completer=WordCompleter(BUILTIN_NAMES, sentence=True),
                auto_suggest=AutoSuggestFromHistory(),
            )
            return ps.prompt
        except ImportError:
            return input


# ============================================================
# 入口
# ============================================================

def main(run_fragment=None):
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if run_fragment is None:
        run_fragment = build_default_runner()

    repl = ECLRepl(run_fragment)

    # 命令行参数:启动时先执行一个文件
    if len(sys.argv) > 1:
        fname = sys.argv[1]
        if os.path.exists(fname):
            with open(fname, "r", encoding="utf-8") as f:
                repl.execute(f.read())
            print(f"# 已载入 {fname}")
        else:
            print(f"# 找不到 {fname}")

    repl.repl()


if __name__ == "__main__":
    main()
