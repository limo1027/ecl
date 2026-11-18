#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ECL (Easy Control Language) 解释器 / AOT 编译器 / 缓存工具

用法:
  python main.py                                 # 交互式 REPL
  python main.py test.ecl                        # 解释执行（兼容旧用法）
  python main.py run test.ecl                    # 解释执行
  python main.py compile test.ecl                # AOT 编译到 .asm
  python main.py compile test.ecl --target exe   # AOT 编译到 .exe
  python main.py dump test.eclc                  # 反汇编缓存为文本
  python main.py load test.eclc.txt -o x.eclc    # 从文本重建缓存（调试用）
"""

import argparse
import os
import pathlib
import subprocess
import sys

import dl
from token_parse import split_token
from ast_parse import Parser
from class_and_vars import NULL, ImportStmt
from checker import Checker, CheckError
from compiler import Compiler, CompileError
from errors import EclException, LexerError, ParseError
from interpreter import Interpreter
from asm import AsmGenerator
from optimizer import Optimizer

ROOT_DIR = pathlib.Path(__file__).resolve().parent
LOCAL_ASSEMBLER = ROOT_DIR / 'uasm64.exe'
LOCAL_LINKER = ROOT_DIR / 'lld-link.exe'
LOCAL_COMPILER = ROOT_DIR / 'clang-cl.exe'


# ============================================================
# 编译（带缓存）
# ============================================================

# 模块搜索目录（与 checker / compiler 保持一致）
MODULE_SEARCH_DIRS = (pathlib.Path('.'), pathlib.Path('example'),
                      pathlib.Path('lib'))


def resolve_module_path(module):
    """把 import 的模块名解析成 .ecl 文件；找不到返回 None。"""
    for base in MODULE_SEARCH_DIRS:
        candidate = base / (module + '.ecl')
        if candidate.exists():
            return candidate
    return None


def collect_imported_files(content, seen=None):
    """递归收集源码 import 的模块文件（只处理顶层 import）。"""
    if seen is None:
        seen = set()
    files = set()
    try:
        program = Parser(split_token(content)).parse()
    except (LexerError, ParseError):
        return files
    for stmt in program.body:
        if not isinstance(stmt, ImportStmt):
            continue
        path = resolve_module_path(stmt.module)
        if path is None:
            continue
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        files.add(path)
        try:
            sub = path.read_text(encoding='utf-8')
        except OSError:
            continue
        files |= collect_imported_files(sub, seen)
    return files


def newer_imported_file(content, cache_mtime):
    """返回比缓存更新的被 import 模块文件；没有则返回 None。"""
    for path in collect_imported_files(content):
        try:
            if path.stat().st_mtime > cache_mtime:
                return path
        except OSError:
            continue
    return None


def compile_with_cache(content, aot_mode, cache_path, no_cache=False,
                       verbose=False, without_check=False,
                       source_path=None, used_cache=False):
    """词法 -> 语法 -> 检查 -> 编译 -> 优化，带 .eclc 缓存。

    缓存策略：
      - .ecl 内容 hash 必须匹配；
      - .ecl（以及它 import 的模块）的修改时间比 .eclc 新时自动重编；
      - used_cache=True 时跳过上述检查，强制读现有缓存。
    返回 (ir, exception_table)。
    """
    src_hash = dl.make_src_hash(content)
    src_bytes = content.encode('utf-8')

    if not no_cache and cache_path.exists():
        if used_cache:
            try:
                ir, exc, _, _ = dl.read_eclc(
                    cache_path, expected_aot_mode=aot_mode)
                if verbose:
                    print(f"[cache] 强制使用 {cache_path}",
                          file=sys.stderr)
                return ir, exc
            except (dl.EclcFormatError, OSError) as e:
                if verbose:
                    print(f"[cache] 无法强制使用，重新编译: {e}",
                          file=sys.stderr)
        else:
            fresh = dl.is_valid(cache_path, src_bytes, aot_mode)
            reason = None if fresh else "源码内容或缓存格式变化"
            cache_mtime = None
            try:
                cache_mtime = cache_path.stat().st_mtime
            except OSError:
                pass

            if fresh and source_path is not None and cache_mtime is not None:
                try:
                    if source_path.stat().st_mtime > cache_mtime:
                        fresh = False
                        reason = f"源文件比缓存新: {source_path}"
                except OSError:
                    pass

            if fresh and cache_mtime is not None:
                newer = newer_imported_file(content, cache_mtime)
                if newer is not None:
                    fresh = False
                    reason = f"import 的模块比缓存新: {newer}"

            if fresh:
                try:
                    ir, exc, _, _ = dl.read_eclc(
                        cache_path, expected_aot_mode=aot_mode)
                    if verbose:
                        print(f"[cache] 命中 {cache_path}",
                              file=sys.stderr)
                    return ir, exc
                except (dl.EclcFormatError, OSError) as e:
                    if verbose:
                        print(f"[cache] 损坏，重新编译: {e}",
                              file=sys.stderr)
            elif verbose:
                print(f"[cache] 失效（{reason}），重新编译: {cache_path}",
                      file=sys.stderr)

    try:
        tokens = split_token(content)
    except LexerError as e:
        print(f"词法错误: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        parser = Parser(tokens)
        program = parser.parse()
    except ParseError as e:
        print(f"语法错误: {e}", file=sys.stderr)
        sys.exit(1)

    if not without_check:
        checker = Checker()
        try:
            warnings = checker.check(program)
            for w in warnings:
                print(w, file=sys.stderr)
        except CheckError as e:
            # 检查器提前失败时，也要把已经收集到的警告（例如 import
            # 模块解析失败）打印出来，否则真正的原因会被掩盖。
            for w in checker.warnings:
                print(w, file=sys.stderr)
            print(f"静态检查错误: {e}", file=sys.stderr)
            sys.exit(1)

    compiler = Compiler(aot_mode=aot_mode)
    try:
        ir, exception_table = compiler.compile_program(program)
    except (CompileError, ParseError, LexerError) as e:
        print(f"编译错误: {e}", file=sys.stderr)
        sys.exit(1)
    optimizer = Optimizer()
    ir = optimizer.optimize(ir)

    # 未做静态检查的 IR 不写入缓存，避免后续正常 run 命中后跳过检查。
    if not no_cache and not without_check:
        try:
            dl.save_eclc(cache_path, ir, exception_table,
                         aot_mode, src_hash)
            if verbose:
                print(f"[cache] 写入 {cache_path}", file=sys.stderr)
        except OSError as e:
            if verbose:
                print(f"[cache] 写入失败: {e}", file=sys.stderr)

    return ir, exception_table


def cache_path_for(source_path, aot_mode, output_path=None):
    """确定 .eclc 路径。

    - output_path 指定时，放 output_path 同目录
    - 否则放源文件同目录
    - 文件名后缀 .run.eclc / .aot.eclc 区分模式
    """
    mode_suffix = '.aot.eclc' if aot_mode else '.run.eclc'
    if output_path is not None:
        base = output_path.with_suffix('')
    elif source_path is not None:
        base = source_path.with_suffix('')
    else:
        base = pathlib.Path('a')
    return base.parent / (base.name + mode_suffix)


# ============================================================
# 解释执行
# ============================================================

def run_ecl(content, source_path=None, without_check=False,
            no_cache=False, verbose=False, allow_dll=True,
            used_cache=False):
    """完整执行 ECL 代码：编译（带缓存） -> VM 执行"""
    # 直接跑 .eclc：跳过词法/语法/检查/编译，只读缓存
    if source_path is not None and source_path.suffix == '.eclc':
        try:
            ir, exception_table, aot_mode, _ = dl.read_eclc(source_path)
        except dl.EclcFormatError as e:
            print(f"缓存格式错误: {e}", file=sys.stderr)
            return None
        if aot_mode:
            print(f"错误：{source_path} 是 AOT 模式缓存，"
                  f"不能直接解释执行（用 run 读 .run.eclc）",
                  file=sys.stderr)
            return None
        interp = Interpreter(allow_dll=allow_dll)
        try:
            interp.run_bytecode(ir, exception_table)
        except EclException as e:
            print(f"运行时错误: {e}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"内部错误: {type(e).__name__}: {e}", file=sys.stderr)
            return None
        return interp

    # .ecl 源文件：走缓存编译
    if source_path is not None:
        cpath = cache_path_for(source_path, aot_mode=False)
    else:
        cpath = pathlib.Path('a.run.eclc')

    ir, exception_table = compile_with_cache(
        content, aot_mode=False, cache_path=cpath,
        no_cache=no_cache, verbose=verbose, without_check=without_check,
        source_path=source_path, used_cache=used_cache)

    interp = Interpreter(allow_dll=allow_dll)
    try:
        interp.run_bytecode(ir, exception_table)
    except EclException as e:
        print(f"运行时错误: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"内部错误: {type(e).__name__}: {e}", file=sys.stderr)
        return None

    return interp


# ============================================================
# AOT 编译
# ============================================================

def find_msvc_bin():
    """通过 vswhere 找到 MSVC x64 工具目录；找不到返回 None。"""
    pf86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
    vswhere = pathlib.Path(pf86) / 'Microsoft Visual Studio' / 'Installer' \
        / 'vswhere.exe'
    if vswhere.exists():
        try:
            out = subprocess.run(
                [str(vswhere), '-products', '*', '-requires',
                 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                 '-property', 'installationPath'],
                capture_output=True, text=True, check=False).stdout.strip()
        except OSError:
            out = ''
        if out:
            msvc_root = pathlib.Path(
                out.splitlines()[0]) / 'VC' / 'Tools' / 'MSVC'
            if msvc_root.exists():
                versions = sorted(msvc_root.iterdir(),
                                  key=lambda p: p.name, reverse=True)
                for version in versions:
                    bin_dir = version / 'bin' / 'Hostx64' / 'x64'
                    if (bin_dir / 'ml64.exe').exists() \
                            or (bin_dir / 'cl.exe').exists():
                        return bin_dir
    # 退化：PATH 里找
    for tool in ('ml64.exe', 'cl.exe', 'link.exe'):
        if subprocess.run(['where', tool], capture_output=True).returncode == 0:
            import shutil
            found = shutil.which(tool)
            if found:
                return pathlib.Path(found).parent
    return None


def _msvc_env(msvc_bin):
    """构造 cl/ml64/link 需要的 INCLUDE/LIB 环境。"""
    env = dict(os.environ)
    msvc_bin = pathlib.Path(msvc_bin)
    # .../VC/Tools/MSVC/<ver>/bin/Hostx64/x64
    if len(msvc_bin.parents) < 3:
        return env
    msvc_ver = msvc_bin.parents[2]
    includes = [msvc_ver / 'include']
    libs = [msvc_ver / 'lib' / 'x64']

    sdk_root = pathlib.Path(
        os.environ.get('WindowsSdkDir', r'C:\Program Files (x86)\Windows Kits\10'))
    sdk_include = sdk_root / 'Include'
    sdk_lib = sdk_root / 'Lib'
    if sdk_include.exists():
        versions = sorted((p for p in sdk_include.iterdir() if p.is_dir()),
                          key=lambda p: p.name, reverse=True)
        if versions:
            v = versions[0]
            includes += [sdk_include / v.name / 'ucrt',
                         sdk_include / v.name / 'shared',
                         sdk_include / v.name / 'um']
            libs += [sdk_lib / v.name / 'ucrt' / 'x64',
                     sdk_lib / v.name / 'um' / 'x64']
    env['INCLUDE'] = os.pathsep.join(str(p) for p in includes if p.exists())
    env['LIB'] = os.pathsep.join(str(p) for p in libs if p.exists())
    env['PATH'] = str(msvc_bin) + os.pathsep + env.get('PATH', '')
    return env


def find_compiler():
    """优先使用项目目录的 clang-cl.exe，其次 MSVC cl.exe。"""
    if LOCAL_COMPILER.exists():
        return LOCAL_COMPILER
    msvc_bin = find_msvc_bin()
    if msvc_bin is not None:
        candidate = pathlib.Path(msvc_bin) / 'cl.exe'
        if candidate.exists():
            return candidate
    return None


def find_assembler():
    """优先使用项目目录的 uasm64.exe，其次 MSVC ml64.exe。"""
    if LOCAL_ASSEMBLER.exists():
        return LOCAL_ASSEMBLER
    msvc_bin = find_msvc_bin()
    if msvc_bin is not None:
        candidate = pathlib.Path(msvc_bin) / 'ml64.exe'
        if candidate.exists():
            return candidate
    return None


def find_linker():
    """优先使用项目目录的 lld-link.exe，其次 MSVC link.exe。"""
    if LOCAL_LINKER.exists():
        return LOCAL_LINKER
    msvc_bin = find_msvc_bin()
    if msvc_bin is not None:
        candidate = pathlib.Path(msvc_bin) / 'link.exe'
        if candidate.exists():
            return candidate
    return None


def find_sdk_lib_dirs():
    """找不到 MSVC 时，直接用 Windows SDK 的 x64 库目录。"""
    sdk_root = pathlib.Path(
        os.environ.get('WindowsSdkDir',
                       r'C:\Program Files (x86)\Windows Kits\10'))
    sdk_lib = sdk_root / 'Lib'
    dirs = []
    if sdk_lib.exists():
        versions = sorted((p for p in sdk_lib.iterdir() if p.is_dir()),
                          key=lambda p: p.name, reverse=True)
        for version in versions:
            for sub in (version / 'um' / 'x64',
                        version / 'ucrt' / 'x64'):
                if sub.exists() and sub not in dirs:
                    dirs.append(sub)
    return dirs


def _link_lib_dirs(msvc_bin):
    """lld-link 需要的 /LIBPATH 列表。"""
    dirs = []
    if msvc_bin is not None:
        env = _msvc_env(msvc_bin)
        for item in env.get('LIB', '').split(os.pathsep):
            if item:
                p = pathlib.Path(item)
                if p.exists() and p not in dirs:
                    dirs.append(p)
    for p in find_sdk_lib_dirs():
        if p not in dirs:
            dirs.append(p)
    return dirs


def _assemble_command(assembler, asm_file, obj_file):
    if assembler.name.lower().startswith('uasm'):
        # UASM 的 -Fo 后面必须跟空格，且需要 -win64 生成 COFF64
        return [str(assembler), '-win64', '-q', '-c',
                '-Fo', str(obj_file), str(asm_file)]
    return [str(assembler), '/c', f'/Fo{obj_file}', str(asm_file)]


def _link_command(linker, objs, out_file, target, def_file, lib_dirs):
    lld = linker.name.lower().startswith('lld')
    args = [str(linker), *[str(o) for o in objs], f'/OUT:{out_file}']
    if lld:
        args.append('/MACHINE:X64')
    args += ['/NODEFAULTLIB', '/DEBUG:NONE', '/OPT:REF', '/OPT:ICF']
    if target == 'dll':
        args += ['/DLL', '/NOENTRY', f'/DEF:{def_file}']
    else:
        args += ['/SUBSYSTEM:CONSOLE', '/ENTRY:startup']
    if lld:
        for d in lib_dirs:
            args.append(f'/LIBPATH:{d}')
    args.append('kernel32.lib')
    return args


def compile_aot(content, source_path, args, remove_output=None):
    """AOT 编译 .ecl -> .asm -> (可选) .exe

    remove_output=True 时在成功链接后删除本次生成的中间文件
    （.asm/.obj/.def/.lib/.exp/.eclc），只保留最终 .exe/.dll。
    """
    if remove_output is None:
        remove_output = getattr(args, 'remove_output', False)
    cpath = cache_path_for(source_path, aot_mode=True,
                           output_path=args.output)
    ir, exception_table = compile_with_cache(
        content, aot_mode=True, cache_path=cpath,
        no_cache=getattr(args, 'no_cache', False),
        verbose=getattr(args, 'verbose', False),
        source_path=source_path,
        used_cache=getattr(args, 'used_cache', False))

    gen = AsmGenerator(ir, target=args.target)
    asm = gen.generate()

    if args.output:
        asm_file = args.output.with_suffix('.asm')
    elif source_path is not None:
        asm_file = source_path.with_suffix('.asm')
    else:
        asm_file = pathlib.Path('a.asm')
    asm_file.parent.mkdir(parents=True, exist_ok=True)
    asm_file.write_text(asm, encoding='utf-8')
    print(f"生成汇编: {asm_file}")

    if args.target == 'asm':
        if remove_output:
            # .asm 是最终产物，保留；只清理编译缓存。
            try:
                if cpath.exists():
                    cpath.unlink()
            except OSError as e:
                print(f"警告: 无法删除 {cpath}: {e}", file=sys.stderr)
        return
    # ---------- 组装 + 链接 ----------
    # 优先使用项目目录的 uasm64.exe / lld-link.exe，而不是 MSVC 的 ml64/link。
    assembler = find_assembler()
    linker = find_linker()
    if assembler is None:
        print("找不到汇编器：请提供 uasm64.exe 或安装 MSVC（ml64.exe）",
              file=sys.stderr)
        sys.exit(1)
    if linker is None:
        print("找不到链接器：请提供 lld-link.exe 或安装 MSVC（link.exe）",
              file=sys.stderr)
        sys.exit(1)

    msvc_bin = args.msvc_bin or find_msvc_bin()
    msvc_bin = pathlib.Path(msvc_bin) if msvc_bin is not None else None
    env = _msvc_env(msvc_bin) if msvc_bin is not None else dict(os.environ)
    compiler = find_compiler()
    obj_file = asm_file.with_suffix('.obj')

    # 1. 汇编用户代码
    subprocess.run(
        _assemble_command(assembler, asm_file, obj_file),
        check=True, env=env)

    # 2. 汇编 setjmp.asm
    setjmp_asm = ROOT_DIR / 'setjmp.asm'
    setjmp_obj = ROOT_DIR / 'setjmp.obj'
    if not setjmp_asm.exists():
        print(f"找不到 setjmp.asm: {setjmp_asm}", file=sys.stderr)
        sys.exit(1)

    need_build_setjmp = (
        not setjmp_obj.exists()
        or setjmp_asm.stat().st_mtime > setjmp_obj.stat().st_mtime
    )
    if need_build_setjmp:
        subprocess.run(
            _assemble_command(assembler, setjmp_asm, setjmp_obj),
            check=True, env=env)

    # 3. 编译 runtime.c（需要 cl.exe；没有 cl 时使用已有的 runtime.obj）
    runtime_c = ROOT_DIR / 'runtime.c'
    runtime_obj = ROOT_DIR / 'runtime.obj'
    need_rebuild = (
        not runtime_obj.exists()
        or runtime_c.stat().st_mtime > runtime_obj.stat().st_mtime
    )
    if need_rebuild:
        if compiler is not None:
            # runtime.c 自带 memcpy/memset，必须无 CRT 编译（/Zl）
            if compiler.name.lower().startswith('clang-cl'):
                # clang /O1 会对本运行时的 union/指针别名做过度优化，
                # 导致关键字参数、默认参数等路径出错；用 /Od 保证语义正确。
                # -fno-builtin 防止把 my_strlen 优化成未定义的 strlen。
                cmd = [str(compiler), '/c', '/Od', '/GS-', '/Gy', '/nologo',
                       '/Zl', '/utf-8', '-fno-builtin',
                       str(runtime_c), f'/Fo{runtime_obj}']
            else:
                cmd = [str(compiler), '/c', '/O1', '/GS-', '/Gy', '/RTC-',
                       '/nologo', '/Zl', '/utf-8',
                       str(runtime_c), f'/Fo{runtime_obj}']
            subprocess.run(cmd, check=True, env=env)
        elif runtime_obj.exists():
            print("警告: 找不到 clang-cl.exe / cl.exe，使用已有的 runtime.obj"
                  "（不会重新编译 runtime.c）", file=sys.stderr)
        else:
            print("找不到 clang-cl.exe / cl.exe，且没有 runtime.obj；"
                  "请提供编译器或用 --msvc-bin 指定 MSVC 工具目录",
                  file=sys.stderr)
            sys.exit(1)

    if args.target == 'dll':
        if args.output:
            out_file = args.output.with_suffix('.dll')
        elif source_path is not None:
            out_file = source_path.with_suffix('.dll')
        else:
            out_file = pathlib.Path('a.dll')
    else:
        if args.output:
            out_file = args.output.with_suffix('.exe')
        elif source_path is not None:
            out_file = source_path.with_suffix('.exe')
        else:
            out_file = pathlib.Path('a.exe')
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # 4. 链接
    def_file = None
    if args.target == 'dll':
        def_file = out_file.with_suffix('.def')
        lines = ['EXPORTS']
        for export_name, wrapper in gen.export_symbols:
            lines.append(f'    {export_name}={wrapper}')
        def_file.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    link_args = _link_command(
        linker,
        [obj_file, runtime_obj, setjmp_obj],
        out_file, args.target, def_file,
        _link_lib_dirs(msvc_bin),
    )
    subprocess.run(link_args, check=True, env=env)
    kind = '动态库' if args.target == 'dll' else '可执行文件'
    print(f"生成{kind}: {out_file}")

    if remove_output:
        # 只删除本次编译的中间产物，保留最终 exe/dll。
        intermediates = [asm_file, obj_file, cpath]
        if def_file is not None:
            intermediates.append(def_file)
            intermediates.append(out_file.with_suffix('.lib'))
            intermediates.append(out_file.with_suffix('.exp'))
        removed = []
        for path in dict.fromkeys(intermediates):
            try:
                if path.exists():
                    path.unlink()
                    removed.append(path)
            except OSError as e:
                print(f"警告: 无法删除 {path}: {e}", file=sys.stderr)
        if removed and getattr(args, 'verbose', False):
            for path in removed:
                print(f"已删除中间文件: {path}", file=sys.stderr)


# ============================================================
# REPL
# ============================================================

def _format_repl_value(v):
    if v is NULL:
        return "null"
    if v is None:
        return "none"
    if isinstance(v, bool):
        return "True" if v else "False"
    return str(v)


def run_repl(args):
    interp = Interpreter(allow_dll=not getattr(args, 'no_dll', False))
    print("ECL 交互式解释器（输入 exit 或 Ctrl-D 退出；空行执行块）")
    buffer = []

    def repl_exec(src):
        tokens = split_token(src)
        p = Parser(tokens)
        program = p.parse()
        if not args.without_check:
            warnings = Checker().check(program)
            for w in warnings:
                print(w, file=sys.stderr)
        compiler = Compiler(aot_mode=False)
        ir, exception_table = compiler.compile_program(program)
        optimizer = Optimizer()
        ir = optimizer.optimize(ir)
        # REPL 复用同一个 Interpreter，globals 持续累积
        from vm import VM
        vm = VM(ir, exception_table, interp.globals, interp)
        vm.run()

    def block_depth(buf):
        depth = 0
        for b in buf:
            s = b.strip()
            if not s or s.startswith('#'):
                continue
            first = s.split()[0] if s.split() else ''
            if first in ('def', 'if', 'for', 'while', 'class',
                         'try', 'with', 'match', 'using'):
                depth += 1
            elif first == 'end':
                depth -= 1
        return depth

    BLOCK_KEYWORDS = ('def', 'if', 'for', 'while', 'class',
                      'try', 'with', 'match', 'using', 'end',
                      'else', 'elif', 'except', 'finally', 'case')

    while True:
        prompt = ">>> " if not buffer else "... "
        try:
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        stripped = line.strip()

        if not buffer and stripped in ('exit', 'quit', 'exit()', 'quit()'):
            break

        if not stripped:
            if buffer:
                d = block_depth(buffer)
                if d > 0:
                    buffer.extend(['end'] * d)
                try:
                    repl_exec("\n".join(buffer))
                except LexerError as e:
                    print(f"词法错误: {e}", file=sys.stderr)
                except ParseError as e:
                    print(f"语法错误: {e}", file=sys.stderr)
                except CheckError as e:
                    print(f"静态检查错误: {e}", file=sys.stderr)
                except EclException as e:
                    print(f"运行时错误: {e}", file=sys.stderr)
                except Exception as e:
                    print(f"内部错误: {type(e).__name__}: {e}",
                          file=sys.stderr)
                buffer.clear()
            continue

        buffer.append(line)

        # 单行非块语句直接执行
        if len(buffer) == 1 and not stripped.endswith(':'):
            first = stripped.split()[0] if stripped.split() else ''
            if first not in BLOCK_KEYWORDS:
                try:
                    repl_exec(stripped)
                except LexerError as e:
                    print(f"词法错误: {e}", file=sys.stderr)
                except ParseError as e:
                    print(f"语法错误: {e}", file=sys.stderr)
                except CheckError as e:
                    print(f"静态检查错误: {e}", file=sys.stderr)
                except EclException as e:
                    print(f"运行时错误: {e}", file=sys.stderr)
                except Exception as e:
                    print(f"内部错误: {type(e).__name__}: {e}",
                          file=sys.stderr)
                buffer.clear()
                continue


# ============================================================
# 命令行
# ============================================================

def build_parser():
    parser = argparse.ArgumentParser(
        prog='ecl',
        description='ECL (Easy Control Language) 解释器 / AOT 编译器 / 缓存工具',
    )
    parser.add_argument('--no-dll', action='store_true',
                        help='REPL 中禁用 dll_import（默认允许）')
    sub = parser.add_subparsers(dest='cmd')

    # ---- run ----
    p_run = sub.add_parser('run', help='解释执行 .ecl（省略则为 REPL）')
    p_run.add_argument('source', nargs='?', type=pathlib.Path,
                       help='输入 .ecl 或 .eclc 文件（省略则进入 REPL）')
    p_run.add_argument('--without-check', action='store_true',
                       help='跳过静态检查')
    p_run.add_argument('--no-cache', action='store_true',
                       help='跳过 .eclc 缓存')
    p_run.add_argument('--used-cache', action='store_true',
                       help='强制使用现有 .eclc 缓存（即使 .ecl 已更新）')
    p_run.add_argument('--verbose', action='store_true',
                       help='打印缓存命中/失效信息')
    p_run.add_argument('--no-dll', action='store_true',
                       help='禁用 dll_import（默认允许）')

    # ---- compile ----
    p_compile = sub.add_parser('compile', help='AOT 编译 .ecl')
    p_compile.add_argument('source', type=pathlib.Path)
    p_compile.add_argument('--target', choices=['asm', 'exe', 'dll'],
                           default='exe',
                           help='目标：asm（只生成 .asm）/ exe（生成 .exe）'
                                '/ dll（生成 .dll）')
    p_compile.add_argument('-o', '--output', type=pathlib.Path,
                           help='输出文件（.asm / .exe）')
    p_compile.add_argument('--msvc-bin', type=pathlib.Path,
                           default=None,
                           help='MSVC 工具链目录（ml64.exe / cl.exe / link.exe），'
                                '默认自动探测')
    p_compile.add_argument('--no-crt', action='store_true',
                           help='兼容旧参数（runtime.c 始终按无 CRT 方式编译/链接）')
    p_compile.add_argument('--remove-output', action='store_true',
                           help='编译成功后删除中间产物（.asm/.obj/.def/.lib/.exp/.eclc），'
                                '保留最终 .exe/.dll')
    p_compile.add_argument('--no-cache', action='store_true',
                           help='跳过 .eclc 缓存')
    p_compile.add_argument('--used-cache', action='store_true',
                           help='强制使用现有 .eclc 缓存（即使 .ecl 已更新）')
    p_compile.add_argument('--verbose', action='store_true',
                           help='打印缓存命中/失效信息')

    # ---- dump ----
    p_dump = sub.add_parser('dump', help='反汇编 .eclc 为文本')
    p_dump.add_argument('cache', type=pathlib.Path)
    p_dump.add_argument('-o', '--output', type=pathlib.Path,
                        help='输出文件（默认 stdout）')
    p_dump.add_argument('--flat', action='store_true',
                        help='拍平嵌套 func/method_func')

    # ---- load ----
    p_load = sub.add_parser('load', help='从文本重建 .eclc（调试用）')
    p_load.add_argument('text', type=pathlib.Path)
    p_load.add_argument('-o', '--output', type=pathlib.Path, required=True,
                        help='输出 .eclc 路径')
    p_load.add_argument('--aot', action='store_true',
                        help='重建为 AOT 模式缓存（默认解释模式）')
    p_load.add_argument('--flat', action='store_true',
                        help='输入是 flat 格式文本')

    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # 兼容旧用法：python main.py test.ecl -> python main.py run test.ecl
    if argv and argv[0] not in ('run', 'compile', 'dump', 'load') \
            and not argv[0].startswith('-'):
        argv.insert(0, 'run')

    parser = build_parser()
    args = parser.parse_args(argv)

    # ---- 无子命令：REPL ----
    if args.cmd is None:
        # 复制一份 run 的默认值，REPL 不需要 source
        args.without_check = False
        args.no_cache = False
        args.used_cache = False
        args.verbose = False
        args.no_dll = getattr(args, 'no_dll', False)
        run_repl(args)
        sys.exit(0)

    # ---- run ----
    if args.cmd == 'run':
        if args.source is None:
            run_repl(args)
            sys.exit(0)
        if not args.source.exists():
            print(f"错误：文件不存在: {args.source}", file=sys.stderr)
            sys.exit(1)

        if args.source.suffix == '.eclc':
            # .eclc 是二进制，不读 text
            run_ecl(None, source_path=args.source,
                    without_check=args.without_check,
                    no_cache=args.no_cache,
                    verbose=args.verbose,
                    allow_dll=not args.no_dll,
                    used_cache=args.used_cache)
        else:
            content = args.source.read_text(encoding='utf-8')
            run_ecl(content, source_path=args.source,
                    without_check=args.without_check,
                    no_cache=args.no_cache,
                    verbose=args.verbose,
                    allow_dll=not args.no_dll,
                    used_cache=args.used_cache)
        sys.exit(0)

    # ---- compile ----
    if args.cmd == 'compile':
        if not args.source.exists():
            print(f"错误：文件不存在: {args.source}", file=sys.stderr)
            sys.exit(1)
        content = args.source.read_text(encoding='utf-8')
        try:
            compile_aot(content, args.source, args)
        except subprocess.CalledProcessError as e:
            print(f"工具链错误: {e}", file=sys.stderr)
            sys.exit(1)
        except (NotImplementedError, CompileError) as e:
            print(f"编译错误: {e}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    # ---- dump ----
    if args.cmd == 'dump':
        if not args.cache.exists():
            print(f"错误：文件不存在: {args.cache}", file=sys.stderr)
            sys.exit(1)
        try:
            text = dl.dump_eclc_text(args.cache, flat=args.flat)
        except dl.EclcFormatError as e:
            print(f"缓存格式错误: {e}", file=sys.stderr)
            sys.exit(1)
        if args.output:
            args.output.write_text(text, encoding='utf-8', newline='\n')
            print(f"反汇编: {args.output}")
        else:
            sys.stdout.write(text)
        sys.exit(0)

    # ---- load ----
    if args.cmd == 'load':
        if not args.text.exists():
            print(f"错误：文件不存在: {args.text}", file=sys.stderr)
            sys.exit(1)
        try:
            text = args.text.read_text(encoding='utf-8')
            src_hash = dl.make_src_hash(text)
            dl.load_eclc_file(args.text, args.output, args.aot,
                              src_hash, flat=args.flat)
        except dl.EclcFormatError as e:
            print(f"文本格式错误: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"重建缓存: {args.output}")
        sys.exit(0)

    parser.print_help()
    sys.exit(2)


if __name__ == '__main__':
    main()
