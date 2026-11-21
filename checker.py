"""ECL 静态检查器。

职责：
  - 作用域 / 类型 / 常量 / ref / goto 检查
  - 未定义变量 -> unknown（警告，不中断编译）
  - match / with / using / 解包 / 重载 / null 规则检查
  - 收集 warnings，由 main.py 打印到 stderr
"""

from decimal import Decimal
import pathlib

from class_and_vars import *
from ast_parse import ParseError, Parser
from token_parse import split_token, LexerError
from errors import CheckError


BUILTIN_NAMES = {
    'print', 'write', 'eprint', 'input',
    'int', 'i64', 'float', 'double', 'str', 'bool', 'bi', 'type', 'code',
    'len', 'range', 'list', 'tuple', 'dict', 'slice',
    'abs', 'max', 'min', 'sum', 'pow', 'sqrt', 'floor', 'ceil', 'round',
    'random', 'free', 'clone', 'id', 'hash', 'exit',
    'new_buffer', 'sizeof',
    'Exception', 'ValueError', 'TypeError', 'IndexError', 'KeyError',
    'ZeroDivisionError', 'RuntimeError', 'NameError', 'ConstChangingError',
    'DllNotFoundError',
}

NUMERIC_TYPES = {'int', 'i64', 'i32', 'float', 'double'}
SCALAR_TYPES = {'int', 'i64', 'i32', 'float', 'double', 'str', 'bool',
                'none', 'null', 'unknown', 'code', 'function', 'list',
                'dict', 'tuple', 'cstr', 'ptr'}


class Checker:
    def __init__(self):
        # 每层作用域: {name: {type, annotated, is_const}}
        self.scope_stack = [{}]
        # 函数签名: name -> [ {param_types, param_names, defaults_count,
        #                       return_type, varargs_type, kwargs_type} ]
        self.function_signatures = {}
        self.current_return_type = None
        self.loop_depth = 0
        self.match_depth = 0
        self.in_function = 0
        self.in_class = 0
        self.labels = set()
        self.warnings = []
        # using 栈: 每层块一个 frame(list of desc)
        self.using_frames = []
        # 已知类名（用于 match 类型模式 / 基类检查）
        self.known_classes = set()
        # 类名 -> 直接定义的方法名集合 / 基类名列表（@@ 的 __exec__ 检查用）
        self.class_methods = {}
        self.class_bases = {}
        # struct 名 -> [StructField]
        self.known_structs = {}
        # dll_import 声明的函数名 -> DllFuncDecl
        self.dll_functions = {}
        # public 顶层函数（DLL 导出候选）
        self.exports = []

    # ---------- 作用域 ----------

    def push_scope(self):
        self.scope_stack.append({})

    def pop_scope(self):
        self.scope_stack.pop()

    def current_scope(self):
        return self.scope_stack[-1]

    def lookup(self, name):
        for scope in reversed(self.scope_stack):
            if name in scope:
                return scope[name]
        return None

    def warn(self, msg, node=None):
        line = getattr(node, 'line', 0) if node is not None else 0
        col = getattr(node, 'col', 0) if node is not None else 0
        text = f"[行 {line}, 列 {col}] 警告: {msg}" if line else f"警告: {msg}"
        if text not in self.warnings:
            self.warnings.append(text)

    def declare(self, name, type_name, annotated=False, is_const=False, node=None):
        scope = self.current_scope()
        if name in scope:
            line = node.line if node else 0
            col = node.col if node else 0
            raise CheckError(f"变量 '{name}' 重复声明", line=line, col=col)
        scope[name] = {
            'type': type_name,
            'annotated': annotated,
            'is_const': is_const,
        }

    def compatible(self, expected, actual):
        if expected is None or actual is None:
            return True
        if expected in ('any', 'unknown', 'null', 'none') \
                or actual in ('any', 'unknown', 'null', 'none'):
            return True
        if expected == actual:
            return True
        if expected in NUMERIC_TYPES and actual in NUMERIC_TYPES:
            return True
        if expected in self.known_structs:
            return actual == expected or actual == 'dict'
        if expected.endswith('*'):
            return actual in ('ptr', 'int', 'unknown', None, expected)
        if expected in ('list', 'dict', 'tuple') and actual in ('list', 'dict', 'tuple'):
            return expected == actual
        e_base = expected.split('[')[0].strip().split()[0]
        a_base = actual.split('[')[0].strip().split()[0]
        return e_base == a_base

    # ---------- 入口 ----------

    def check(self, program):
        self._collect_labels(program.body)
        # 1) struct 先登记名字，再校验字段（允许嵌套后声明的 struct）
        for stmt in program.body:
            if isinstance(stmt, StructDefStmt):
                if stmt.name in self.known_structs:
                    raise CheckError(
                        f"struct '{stmt.name}' 重复声明",
                        line=stmt.line, col=stmt.col)
                self.known_structs[stmt.name] = stmt.fields
        for stmt in program.body:
            if isinstance(stmt, StructDefStmt):
                self._check_struct(stmt)
        # 2) 预注册 import 进来的函数/类
        for stmt in program.body:
            if isinstance(stmt, ImportStmt):
                self._register_import(stmt)
        # 3) 预注册顶层函数/类，允许前向引用
        for stmt in program.body:
            if isinstance(stmt, FuncDefStmt):
                self._register_signature(stmt)
                if stmt.visibility == 'public':
                    self._check_export(stmt)
            elif isinstance(stmt, ClassDefStmt):
                self._register_class_info(stmt, stmt.name, stmt.bases)
        # 4) dll_import 函数（要在 ECL 函数之后，便于检测名字冲突）
        for stmt in program.body:
            if isinstance(stmt, DllImportStmt):
                self._register_dll_import(stmt)
        # 导出函数必须唯一（C ABI 没有重载；private 同名重载也不行）
        for name in self.exports:
            if len(self.function_signatures.get(name, [])) > 1:
                raise CheckError(
                    f"导出函数 '{name}' 不能重载（C ABI 没有重载）",
                    line=program.body[0].line if program.body else 0,
                    col=0)
        self.using_frames.append(set())
        try:
            for stmt in program.body:
                self.check_statement(stmt)
        finally:
            self.using_frames.pop()
        return self.warnings

    def _check_struct(self, stmt):
        seen = set()
        for f in stmt.fields:
            if f.name in seen:
                raise CheckError(
                    f"struct '{stmt.name}' 字段 '{f.name}' 重复",
                    line=f.line, col=f.col)
            seen.add(f.name)
            if f.type_name not in STRUCT_SCALAR_TYPES \
                    and f.type_name not in self.known_structs \
                    and not f.type_name.endswith('*'):
                raise CheckError(
                    f"struct 字段 '{f.name}' 的类型 '{f.type_name}' 不支持",
                    line=f.line, col=f.col)

    def _register_dll_import(self, stmt):
        for f in stmt.funcs:
            if f.name in self.dll_functions:
                raise CheckError(
                    f"dll_import 函数 '{f.name}' 重复声明",
                    line=f.line, col=f.col)
            if f.name in self.function_signatures:
                raise CheckError(
                    f"dll_import 函数 '{f.name}' 与 ECL 函数重名",
                    line=f.line, col=f.col)
            if f.name in BUILTIN_NAMES:
                raise CheckError(
                    f"dll_import 函数 '{f.name}' 与内置函数重名",
                    line=f.line, col=f.col)
            if self.lookup(f.name) is not None:
                raise CheckError(
                    f"dll_import 函数 '{f.name}' 与变量重名",
                    line=f.line, col=f.col)
            for p in f.params:
                t = p.get('type')
                if t not in C_ABI_TYPES and t not in self.known_structs \
                        and not (t or '').endswith('*'):
                    raise CheckError(
                        f"dll_import 函数 '{f.name}' 参数 '{p['name']}' "
                        f"的类型 '{t}' 不能用于 DLL 调用",
                        line=f.line, col=f.col)
                if p.get('modifier') not in (None, 'ptr', 'in', 'out'):
                    raise CheckError(
                        f"dll_import 参数修饰符错误: {p.get('modifier')}",
                        line=f.line, col=f.col)
            if f.return_type not in C_ABI_TYPES \
                    and not (f.return_type or '').endswith('*'):
                raise CheckError(
                    f"dll_import 函数 '{f.name}' 的返回类型 "
                    f"'{f.return_type}' 不能用于 DLL 调用",
                    line=f.line, col=f.col)
            self.dll_functions[f.name] = f

    def _check_export(self, stmt):
        if stmt.name in self.exports:
            raise CheckError(
                f"导出函数 '{stmt.name}' 不能重载（C ABI 没有重载）",
                line=stmt.line, col=stmt.col)
        if stmt.varargs or stmt.kwargs:
            raise CheckError(
                f"导出函数 '{stmt.name}' 不能使用 *args/**kwargs",
                line=stmt.line, col=stmt.col)
        for p in stmt.params:
            if p.get('type') not in C_ABI_TYPES:
                raise CheckError(
                    f"导出函数 '{stmt.name}' 参数 '{p['name']}' 的类型必须是 "
                    f"C ABI 类型（int/i64/i32/float/double/bool/str/cstr/"
                    f"ptr/none）", line=stmt.line, col=stmt.col)
        if stmt.return_type not in C_ABI_TYPES:
            raise CheckError(
                f"导出函数 '{stmt.name}' 的返回类型必须是 C ABI 类型",
                line=stmt.line, col=stmt.col)
        self.exports.append(stmt.name)

    def _register_import(self, stmt):
        """把模块里的函数签名 / 类名注册进当前检查器。"""
        search_paths = [pathlib.Path('.'), pathlib.Path('example'),
                        pathlib.Path('lib')]
        module_path = None
        for base in search_paths:
            candidate = base / (stmt.module + '.ecl')
            if candidate.exists():
                module_path = candidate
                break
        if module_path is None:
            self.warn(f"找不到模块 '{stmt.module}'", stmt)
            return
        try:
            mod_src = module_path.read_text(encoding='utf-8')
            mod_program = Parser(split_token(mod_src)).parse()
        except (OSError, LexerError, ParseError) as e:
            self.warn(f"模块 '{stmt.module}' 解析失败: {e}", stmt)
            return

        from_names = dict((orig, alias or orig)
                          for orig, alias in stmt.names)
        if stmt.names:
            prefix = ''
        elif stmt.alias:
            prefix = stmt.alias + '_'
        else:
            prefix = stmt.module + '_'

        module_classes = {s.name for s in mod_program.body
                          if isinstance(s, ClassDefStmt)}

        for s in mod_program.body:
            if isinstance(s, FuncDefStmt):
                orig = s.name
                if stmt.names:
                    if orig not in from_names:
                        continue
                    new_name = from_names[orig]
                else:
                    new_name = prefix + orig
                s.name = new_name
                self._register_signature(s)
            elif isinstance(s, ClassDefStmt):
                orig = s.name
                if stmt.names:
                    # 编译器会把模块里的类全部注入（未点名的保留原名），
                    # 检查器保持同样的可见性，便于解析继承链。
                    new_name = from_names.get(orig, orig)
                    bases = [from_names.get(b, b) for b in s.bases]
                else:
                    new_name = prefix + orig
                    bases = [prefix + b if b in module_classes else b
                             for b in s.bases]
                self._register_class_info(s, new_name, bases)

    def _collect_labels(self, stmts):
        for s in stmts:
            if isinstance(s, LabelStmt):
                self.labels.add(s.name)
            elif isinstance(s, CompoundStmt):
                self._collect_labels(s.stmts)
            elif isinstance(s, IfStmt):
                self._collect_labels(s.body)
                for _, eb in s.elifs:
                    self._collect_labels(eb)
                self._collect_labels(s.else_body)
            elif isinstance(s, (ForStmt, WhileStmt, WithStmt)):
                self._collect_labels(s.body)
            elif isinstance(s, MatchStmt):
                for c in s.cases:
                    self._collect_labels(c.body)
            elif isinstance(s, TryStmt):
                self._collect_labels(s.body)
                for ec in s.excepts:
                    self._collect_labels(ec['body'])
                if s.else_body:
                    self._collect_labels(s.else_body)
                if s.finally_body:
                    self._collect_labels(s.finally_body)

    # ---------- 块 ----------

    def _check_block(self, stmts):
        """检查一个块：using 作用域限于本块（label 是函数级/顶层级）。"""
        self.using_frames.append(set())
        try:
            for s in stmts:
                self.check_statement(s)
        finally:
            self.using_frames.pop()

    def _check_function_block(self, stmts):
        """函数体：label 作用域限于本函数，不能跨函数。"""
        old_labels = self.labels
        self.labels = set()
        self._collect_labels(stmts)
        try:
            self._check_block(stmts)
        finally:
            self.labels = old_labels

    # ---------- 语句检查 ----------

    def check_statement(self, stmt):
        vis = getattr(stmt, 'visibility', None)
        if vis and not self.in_class:
            # 顶层的 public/private 只用于标记 DLL 导出函数
            if self.in_function > 0 or not isinstance(stmt, FuncDefStmt) \
                    or len(self.scope_stack) > 1:
                raise CheckError(
                    "public/private 仅在 class 内或顶层 DLL 导出时有效",
                    line=getattr(stmt, 'line', 0),
                    col=getattr(stmt, 'col', 0))

        if isinstance(stmt, (StructDefStmt, DllImportStmt)):
            if self.in_function > 0 or len(self.scope_stack) > 1:
                raise CheckError(
                    "struct / dll_import 只能在顶层声明",
                    line=stmt.line, col=stmt.col)
            return

        if isinstance(stmt, CompoundStmt):
            for s in stmt.stmts:
                self.check_statement(s)

        elif isinstance(stmt, ExprStmt):
            self.check_expr(stmt.expr)

        elif isinstance(stmt, DefineStmt):
            self.check_define(stmt)

        elif isinstance(stmt, UnpackAssignStmt):
            self.check_unpack_assign(stmt)

        elif isinstance(stmt, FuncDefStmt):
            self.check_function_def(stmt)

        elif isinstance(stmt, ClassDefStmt):
            self.check_class_def(stmt)

        elif isinstance(stmt, IfStmt):
            self.check_expr(stmt.condition)
            self._check_block(stmt.body)
            for ec, eb in stmt.elifs:
                self.check_expr(ec)
                self._check_block(eb)
            self._check_block(stmt.else_body)

        elif isinstance(stmt, ForStmt):
            self.check_for(stmt)

        elif isinstance(stmt, WhileStmt):
            self.check_expr(stmt.condition)
            self.push_scope()
            self.loop_depth += 1
            self._check_block(stmt.body)
            self.loop_depth -= 1
            self.pop_scope()

        elif isinstance(stmt, ReturnStmt):
            if self.in_function == 0:
                raise CheckError(
                    "'return' 必须在函数内使用",
                    line=stmt.line, col=stmt.col)
            if stmt.value is not None and isinstance(stmt.value, Literal) \
                    and stmt.value.value is NULL:
                raise CheckError(
                    "null 不能作为返回值（请用 none）",
                    line=stmt.line, col=stmt.col)
            if stmt.value is not None and isinstance(stmt.value, Literal) \
                    and stmt.value.value is UNKNOWN:
                raise CheckError(
                    "unknown 不能作为返回值", line=stmt.line, col=stmt.col)
            value_type = self.check_expr(stmt.value) if stmt.value else 'none'
            if self.current_return_type and value_type:
                if not self.compatible(self.current_return_type, value_type):
                    raise CheckError(
                        f"返回值类型错误: 期望 {self.current_return_type}，"
                        f"但得到 {value_type}",
                        line=stmt.line, col=stmt.col)

        elif isinstance(stmt, BreakStmt):
            if self.loop_depth == 0 and self.match_depth == 0:
                raise CheckError(
                    "'break' 必须在循环或 match 内使用",
                    line=stmt.line, col=stmt.col)

        elif isinstance(stmt, ContinueStmt):
            if self.loop_depth == 0:
                raise CheckError(
                    "'continue' 只能在循环内使用（match 不是循环）",
                    line=stmt.line, col=stmt.col)

        elif isinstance(stmt, GotoStmt):
            if stmt.label not in self.labels:
                raise CheckError(
                    f"goto 目标标签不存在: '{stmt.label}'",
                    line=stmt.line, col=stmt.col)

        elif isinstance(stmt, LabelStmt):
            pass

        elif isinstance(stmt, TryStmt):
            self.push_scope()
            self._check_block(stmt.body)
            self.pop_scope()
            for ec in stmt.excepts:
                self.push_scope()
                if ec['name']:
                    self.declare(ec['name'], 'any', node=stmt)
                self._check_block(ec['body'])
                self.pop_scope()
            if stmt.else_body is not None:
                self._check_block(stmt.else_body)
            if stmt.finally_body is not None:
                self._check_block(stmt.finally_body)

        elif isinstance(stmt, RaiseStmt):
            self.check_expr(stmt.exception)

        elif isinstance(stmt, MatchStmt):
            self.check_match(stmt)

        elif isinstance(stmt, WithStmt):
            self.check_with(stmt)

        elif isinstance(stmt, UsingStmt):
            self.check_using(stmt)

        elif isinstance(stmt, (ImportStmt, DecoratorStmt)):
            pass

    # ---------- 定义检查 ----------

    def check_define(self, stmt):
        # FALLTHROUGH：编译期开关，不是普通变量
        if stmt.name == 'FALLTHROUGH':
            if not stmt.is_const:
                raise CheckError(
                    "FALLTHROUGH 必须用 define const 声明",
                    line=stmt.line, col=stmt.col)
            if stmt.value is not None:
                raise CheckError(
                    "FALLTHROUGH 不能作为值使用",
                    line=stmt.line, col=stmt.col)
            if self.lookup('FALLTHROUGH') is not None:
                raise CheckError(
                    "FALLTHROUGH 重复声明",
                    line=stmt.line, col=stmt.col)
            self.declare('FALLTHROUGH', 'const', annotated=True,
                         is_const=True, node=stmt)
            return

        # ref 已废弃：语言层不再提供别名，改用深拷贝 / buffer
        if stmt.is_ref:
            raise CheckError(
                "ref 已废弃（ECL 0.1.0 新内存模型）；请直接赋值或使用 buffer",
                line=stmt.line, col=stmt.col)

        # x <- null / x <- unknown 是错误
        if stmt.value is not None and isinstance(stmt.value, Literal):
            if stmt.value.value is NULL:
                raise CheckError(
                    "null 不能作为右值（请用 none）",
                    line=stmt.line, col=stmt.col)
            if stmt.value.value is UNKNOWN:
                raise CheckError(
                    "unknown 不能作为右值", line=stmt.line, col=stmt.col)

        # define any x 只声明，赋值为 null
        if stmt.value is None:
            declared_type = stmt.type_name or 'null'
            self.declare(stmt.name, declared_type,
                         annotated=bool(stmt.type_name),
                         is_const=stmt.is_const, node=stmt)
            return

        value_type = self.check_expr(stmt.value)

        if stmt.type_name:
            base = stmt.type_name.split('[')[0].strip().split()[0]
            if value_type and not self.compatible(base, value_type):
                raise CheckError(
                    f"类型错误: 变量 '{stmt.name}' 注解为 {stmt.type_name}，"
                    f"但赋值表达式类型为 {value_type}",
                    line=stmt.line, col=stmt.col)
            self.declare(stmt.name, stmt.type_name, annotated=True,
                         is_const=stmt.is_const, node=stmt)
        else:
            self.declare(stmt.name, value_type, annotated=False,
                         is_const=stmt.is_const, node=stmt)

    def check_unpack_assign(self, stmt):
        vt = self.check_expr(stmt.value)
        if vt not in ('list', 'tuple', 'unknown', None):
            self.warn(
                f"赋值解包的右侧是 {vt}，运行时将抛 TypeError", stmt)
        elem_type = None
        if vt == 'list':
            elem_type = 'any'
        for t in stmt.targets:
            if self.lookup(t.name) is not None:
                # 已存在则视为重新赋值
                info = self.lookup(t.name)
                if info['is_const']:
                    raise CheckError(
                        f"不能重新赋值常量 '{t.name}'",
                        line=stmt.line, col=stmt.col)
                if info['annotated'] and elem_type and not self.compatible(
                        info['type'], elem_type):
                    pass
                continue
            self.declare(t.name, 'list' if t.is_star else elem_type,
                         annotated=False, node=stmt)

    # ---------- 函数 / 方法检查 ----------

    def _register_signature(self, stmt):
        sig = {
            'param_types': [p.get('type') for p in stmt.params],
            'param_names': [p['name'] for p in stmt.params],
            'defaults_count': sum(
                1 for p in stmt.params if p.get('default') is not None),
            'param_defaults': [p.get('default') is not None
                               for p in stmt.params],
            'return_type': stmt.return_type,
            'varargs_type': getattr(stmt, 'varargs_type', None),
            'kwargs_type': getattr(stmt, 'kwargs_type', None),
            'varargs_index': getattr(stmt, 'varargs_index', None),
            'has_varargs': bool(stmt.varargs),
            'has_kwargs': bool(stmt.kwargs),
        }
        sigs = self.function_signatures.setdefault(stmt.name, [])
        key = (tuple(sig['param_types']), sig['varargs_type'])
        for i, old in enumerate(sigs):
            if (tuple(old['param_types']), old['varargs_type']) == key:
                sigs[i] = sig
                return
        sigs.append(sig)

    def check_function_def(self, stmt):
        self._register_signature(stmt)

        self.push_scope()
        for p in stmt.params:
            self.declare(p['name'], p.get('type'),
                         annotated=bool(p.get('type')), node=stmt)
        if stmt.varargs:
            self.declare(stmt.varargs, 'list', annotated=True, node=stmt)
        if stmt.kwargs:
            self.declare(stmt.kwargs, 'dict', annotated=True, node=stmt)

        old_return = self.current_return_type
        self.current_return_type = stmt.return_type
        self.in_function += 1
        self._check_function_block(stmt.body)
        self.in_function -= 1
        self.current_return_type = old_return
        self.pop_scope()

    def _register_class_info(self, stmt, class_name, bases=()):
        """登记类名、直接方法名与基类（供 @@ 的 __exec__ 检查使用）。"""
        self.known_classes.add(class_name)
        self.class_methods[class_name] = {
            s.name for s in stmt.body if isinstance(s, FuncDefStmt)}
        self.class_bases[class_name] = list(bases)

    def _class_has_method(self, class_name, method, seen=None):
        if seen is None:
            seen = set()
        if class_name in seen:
            return False
        seen.add(class_name)
        if method in self.class_methods.get(class_name, ()):
            return True
        for base in self.class_bases.get(class_name, ()):
            if self._class_has_method(base, method, seen):
                return True
        return False

    def check_class_def(self, stmt):
        self._register_class_info(stmt, stmt.name, stmt.bases)
        for base in stmt.bases:
            if self.lookup(base) is None and base not in BUILTIN_NAMES \
                    and base not in self.known_classes:
                self.warn(f"基类 '{base}' 未定义", stmt)
        self.in_class += 1
        self.push_scope()
        try:
            for s in stmt.body:
                if isinstance(s, FuncDefStmt):
                    self.check_method_def(s, stmt.bases)
                else:
                    self.check_statement(s)
        finally:
            self.pop_scope()
            self.in_class -= 1

    def check_method_def(self, method_stmt, base_names=()):
        self._register_signature(method_stmt)
        self.push_scope()
        self.declare('self', 'any', node=method_stmt)
        # 类体方法里可以直接引用基类名（using b / b.xx()）
        for base in base_names:
            if self.lookup(base) is None:
                self.declare(base, 'any', node=method_stmt)
        for p in method_stmt.params:
            if p['name'] == 'self':
                continue
            self.declare(p['name'], p.get('type'),
                         annotated=bool(p.get('type')), node=method_stmt)
        if method_stmt.varargs:
            self.declare(method_stmt.varargs, 'list', annotated=True,
                         node=method_stmt)
        if method_stmt.kwargs:
            self.declare(method_stmt.kwargs, 'dict', annotated=True,
                         node=method_stmt)

        old_return = self.current_return_type
        self.current_return_type = method_stmt.return_type
        self.in_function += 1
        self._check_function_block(method_stmt.body)
        self.in_function -= 1
        self.current_return_type = old_return
        self.pop_scope()

    # ---------- 循环检查 ----------

    def check_for(self, stmt):
        if stmt.count is not None:
            self.check_expr(stmt.count)
        elif stmt.range_expr is not None:
            self.check_expr(stmt.range_expr.start)
            self.check_expr(stmt.range_expr.end)
            if stmt.range_expr.step:
                self.check_expr(stmt.range_expr.step)
        else:
            it = self.check_expr(stmt.iterable)
            it_base = it.split('[')[0].strip().split()[0] if it else None
            if it_base not in ('list', 'tuple', 'str', 'dict', 'range',
                               'unknown', None):
                self.warn(f"for 遍历的对象类型是 {it}，可能无法迭代", stmt)

        self.push_scope()
        if stmt.variable:
            self.declare(stmt.variable, 'any', node=stmt)
        self.loop_depth += 1
        self._check_block(stmt.body)
        self.loop_depth -= 1
        self.pop_scope()

    # ---------- match ----------

    def check_match(self, stmt):
        if not isinstance(stmt.value, (Variable, Attribute, Subscript)):
            # 文档要求变量/简单左值（case 体内通过该变量引用匹配值）。
            # 这里放宽为任意表达式：只影响“体内能否引用匹配值”，不影响匹配本身。
            self.warn(
                "match 的表达式不是变量或简单左值，case 体内无法通过变量"
                "引用匹配值", stmt)
        self.check_expr(stmt.value)

        seen_wildcard = False
        self.match_depth += 1
        try:
            for idx, case in enumerate(stmt.cases):
                pattern = case.pattern
                if isinstance(pattern, MatchWildcard):
                    if seen_wildcard:
                        raise CheckError(
                            "match 中 '_' 只能出现一次",
                            line=case.line, col=case.col)
                    seen_wildcard = True
                    if idx != len(stmt.cases) - 1:
                        self.warn(
                            "'_' 之后的 case 不可达", pattern)
                elif isinstance(pattern, MatchType):
                    name = pattern.type_name
                    if name not in MATCH_TYPE_NAMES \
                            and name not in self.known_classes \
                            and name not in BUILTIN_TYPES:
                        self.warn(f"match 类型模式引用了未知类型 '{name}'",
                                  pattern)
                elif isinstance(pattern, MatchMulti):
                    for v in pattern.values:
                        if isinstance(v, (list, dict, tuple)):
                            raise CheckError(
                                "多值模式只允许字面量",
                                line=case.line, col=case.col)
                if case.guard is not None:
                    self.check_expr(case.guard)
                self.push_scope()
                self._check_block(case.body)
                self.pop_scope()
        finally:
            self.match_depth -= 1

    # ---------- with ----------
    def check_with(self, stmt):
        for expr, name in stmt.items:
            et = self.check_expr(expr)
            if et in SCALAR_TYPES - {'unknown'}:
                self.warn(
                    f"with 的对象类型是 {et}，运行时可能抛 TypeError",
                    stmt)
            if name:
                self.declare(name, 'any', node=stmt)   # 外层作用域

        self.push_scope()
        try:
            self._check_block(stmt.body)
        finally:
            self.pop_scope()

    # ---------- using ----------

    def check_using(self, stmt):
        et = self.check_expr(stmt.expr)
        if et in SCALAR_TYPES - {'unknown'}:
            self.warn(
                f"using 的对象类型是 {et}，运行时可能抛 TypeError", stmt)
        desc = _expr_desc(stmt.expr)
        if self.using_frames:
            frame = self.using_frames[-1]
            if desc in frame:
                self.warn(f"同一个块内重复 using 同一对象 '{desc}'", stmt)
            frame.add(desc)

    # ---------- 表达式检查 ----------

    def check_expr(self, expr):
        if expr is None:
            return 'none'

        if isinstance(expr, CodeLiteral):
            try:
                tokens = split_token(expr.source)
                Parser(tokens).parse()
            except (LexerError, ParseError) as e:
                raise CheckError(
                    f"code 字符串语法错误: {e}",
                    line=expr.line, col=expr.col)
            return 'code'

        if isinstance(expr, ExternalExec):
            return self.check_external_exec(expr)

        if isinstance(expr, Literal):
            return self._infer(expr.value)

        if isinstance(expr, Variable):
            if expr.name == 'FALLTHROUGH':
                raise CheckError(
                    "FALLTHROUGH 不能作为值使用",
                    line=expr.line, col=expr.col)
            info = self.lookup(expr.name)
            if info is not None:
                return info['type']
            if expr.name in self.dll_functions:
                return 'function'
            if expr.name in self.function_signatures:
                return 'function'
            if expr.name in BUILTIN_NAMES:
                return 'function'
            if expr.name in self.known_classes:
                return 'class'
            if expr.name in self.known_structs:
                return expr.name
            if expr.name in ('global', 'last'):
                return 'any'
            if any(self.using_frames):
                # using 引入的成员在编译期不可知，交给运行时
                return 'any'
            self.warn(
                f"使用了未定义的变量 '{expr.name}'，其值为 unknown", expr)
            return 'unknown'

        if isinstance(expr, Binary):
            lt = self.check_expr(expr.left)
            rt = self.check_expr(expr.right)
            if expr.op in ('==', '=', '!=', '<', '>', '<=', '>=',
                           'and', 'or'):
                return 'bool'
            if expr.op in ('+', '-', '*', '/', '//', '%', '**'):
                if lt in NUMERIC_TYPES and rt in NUMERIC_TYPES:
                    if 'double' in (lt, rt):
                        return 'double'
                    if 'float' in (lt, rt):
                        return 'float'
                    if 'i64' in (lt, rt):
                        return 'i64'
                    if 'i32' in (lt, rt):
                        return 'i32'
                    return 'int'
                if expr.op == '+' and lt == 'str' and rt == 'str':
                    return 'str'
                if lt == 'list' and rt == 'list' and expr.op == '+':
                    return 'list'
                return 'unknown'
            if expr.op in ('|', '&', '^', '<<', '>>'):
                if lt in NUMERIC_TYPES and rt in NUMERIC_TYPES:
                    if 'i64' in (lt, rt):
                        return 'i64'
                    if 'i32' in (lt, rt):
                        return 'i32'
                    return 'int'
                return 'unknown'
            return 'unknown'

        if isinstance(expr, Unary):
            if expr.op in ('!', 'not'):
                self.check_expr(expr.right)
                return 'bool'
            return self.check_expr(expr.right)

        if isinstance(expr, Assign):
            vt = self.check_expr(expr.value)
            if isinstance(expr.value, Literal) and expr.value.value is NULL:
                raise CheckError(
                    "null 不能作为右值（请用 none）",
                    line=expr.line, col=expr.col)
            if isinstance(expr.value, Literal) and expr.value.value is UNKNOWN:
                raise CheckError(
                    "unknown 不能作为右值",
                    line=expr.line, col=expr.col)
            if isinstance(expr.name, Variable):
                name = expr.name.name
                if name == 'FALLTHROUGH':
                    raise CheckError(
                        "FALLTHROUGH 不能作为值使用",
                        line=expr.line, col=expr.col)
                info = self.lookup(name)
                if info is None:
                    # 自动推断类型：a <- 7
                    self.declare(name, vt, annotated=False, node=expr)
                else:
                    if info['is_const']:
                        raise CheckError(
                            f"不能重新赋值常量 '{name}'",
                            line=expr.line, col=expr.col)
                    if info['annotated'] and vt and not self.compatible(
                            info['type'], vt):
                        raise CheckError(
                            f"类型错误: 变量 '{name}' 注解为 {info['type']}，"
                            f"但赋值表达式类型为 {vt}",
                            line=expr.line, col=expr.col)
                    if not info['annotated']:
                        info['type'] = vt
            elif isinstance(expr.name, Attribute):
                self.check_expr(expr.name.obj)
            elif isinstance(expr.name, Subscript):
                self.check_expr(expr.name.obj)
                self.check_expr(expr.name.index)
            return vt

        if isinstance(expr, Call):
            return self._check_call(expr)

        if isinstance(expr, AddressOf):
            self.check_expr(expr.right)
            return 'ptr'

        if isinstance(expr, Attribute):
            obj_type = self.check_expr(expr.obj)
            if obj_type in self.known_structs:
                for f in self.known_structs[obj_type]:
                    if f.name == expr.attr:
                        return f.type_name
                raise CheckError(
                    f"struct '{obj_type}' 没有字段 '{expr.attr}'",
                    line=expr.line, col=expr.col)
            return None

        if isinstance(expr, IfExpr):
            self.check_expr(expr.condition)
            t1 = self.check_expr(expr.then_expr)
            t2 = self.check_expr(expr.else_expr)
            return t1 if t1 == t2 else (t1 or t2)

        if isinstance(expr, ListExpr):
            for e in expr.elements:
                if isinstance(e, Starred):
                    st = self.check_expr(e.value)
                    if e.double:
                        raise CheckError(
                            "* 不能出现在 list 里（用 *expr）",
                            line=e.line, col=e.col)
                    if st not in ('list', 'tuple', 'unknown', None):
                        raise CheckError(
                            f"list 解包要求 list/tuple，得到 {st}",
                            line=e.line, col=e.col)
                else:
                    self.check_expr(e)
            return 'list'

        if isinstance(expr, TupleExpr):
            for e in expr.elements:
                if isinstance(e, Starred):
                    st = self.check_expr(e.value)
                    if e.double:
                        raise CheckError(
                            "** 不能出现在 tuple 里", line=e.line, col=e.col)
                    if st not in ('list', 'tuple', 'unknown', None):
                        raise CheckError(
                            f"tuple 解包要求 list/tuple，得到 {st}",
                            line=e.line, col=e.col)
                else:
                    self.check_expr(e)
            return 'tuple'

        if isinstance(expr, DictExpr):
            for k, v in expr.items:
                if isinstance(k, Starred):
                    st = self.check_expr(k.value)
                    if st not in ('dict', 'unknown', None):
                        raise CheckError(
                            f"dict 解包要求 dict，得到 {st}",
                            line=k.line, col=k.col)
                else:
                    self.check_expr(k)
                    self.check_expr(v)
            return 'dict'

        if isinstance(expr, RangeExpr):
            self.check_expr(expr.start)
            self.check_expr(expr.end)
            if expr.step:
                self.check_expr(expr.step)
            return 'list'

        if isinstance(expr, Subscript):
            self.check_expr(expr.obj)
            self.check_expr(expr.index)
            return None

        if isinstance(expr, Starred):
            self.check_expr(expr.value)
            return self.check_expr(expr.value)

        if isinstance(expr, Swap):
            li = self.lookup(expr.left)
            ri = self.lookup(expr.right)
            if li is None:
                raise CheckError(
                    f"交换操作: 变量 '{expr.left}' 未声明",
                    line=expr.line, col=expr.col)
            if ri is None:
                raise CheckError(
                    f"交换操作: 变量 '{expr.right}' 未声明",
                    line=expr.line, col=expr.col)
            if li['is_const'] or ri['is_const']:
                raise CheckError(
                    "不能交换常量", line=expr.line, col=expr.col)
            return None

        return None

    def check_external_exec(self, expr):
        """@@flat_class(code)：校验目标类存在且实现了 __exec__。"""
        name = expr.class_name
        if name not in self.known_classes:
            raise CheckError(
                f"@@ 目标类 '{name}' 不存在（请先 import 对应模块）",
                line=expr.line, col=expr.col)
        if not self._class_has_method(name, '__exec__'):
            raise CheckError(
                f"@@ 目标类 '{name}' 没有实现 __exec__ 方法",
                line=expr.line, col=expr.col)
        for a in expr.args:
            if isinstance(a, Starred):
                t = self.check_expr(a.value)
                if a.double:
                    if t not in ('dict', 'unknown', None):
                        raise CheckError(
                            f"** 解包要求 dict，得到 {t}",
                            line=a.line, col=a.col)
                else:
                    if t not in ('list', 'tuple', 'unknown', None):
                        raise CheckError(
                            f"* 解包要求 list/tuple，得到 {t}",
                            line=a.line, col=a.col)
            else:
                self.check_expr(a)
        for _, v in expr.kwargs:
            self.check_expr(v)
        return 'any'

    def _check_call(self, expr):
        # 实参类型
        spread_pos = False
        spread_kw = False
        for a in expr.args:
            if isinstance(a, Starred):
                t = self.check_expr(a.value)
                if a.double:
                    spread_kw = True
                    if t not in ('dict', 'unknown', None):
                        raise CheckError(
                            f"** 解包要求 dict，得到 {t}",
                            line=a.line, col=a.col)
                else:
                    spread_pos = True
                    if t not in ('list', 'tuple', 'unknown', None):
                        raise CheckError(
                            f"* 解包要求 list/tuple，得到 {t}",
                            line=a.line, col=a.col)
            else:
                self.check_expr(a)
        for _, v in expr.kwargs:
            self.check_expr(v)

        if not isinstance(expr.func, Variable):
            self.check_expr(expr.func)
            return None

        fname = expr.func.name
        if fname in self.known_structs:
            if expr.args or expr.kwargs:
                raise CheckError(
                    f"struct '{fname}' 构造函数不接受参数",
                    line=expr.line, col=expr.col)
            return fname
        if fname == 'sizeof' and len(expr.args) == 1 \
                and isinstance(expr.args[0], Variable) \
                and expr.args[0].name in self.known_structs:
            return 'int'
        if fname in BUILTIN_NAMES:
            # 少量内置函数的简单类型检查
            if fname in ('pow',) and len(expr.args) != 2:
                raise CheckError(
                    "pow(base, exp) 需要 2 个参数",
                    line=expr.line, col=expr.col)
            if fname == 'new_buffer' and len(expr.args) != 1:
                raise CheckError(
                    "new_buffer(size) 需要 1 个参数",
                    line=expr.line, col=expr.col)
            if fname == 'sizeof' and len(expr.args) != 1:
                raise CheckError(
                    "sizeof(x) 需要 1 个参数",
                    line=expr.line, col=expr.col)
            return None

        dll_decl = self.dll_functions.get(fname)
        if dll_decl is not None:
            if expr.kwargs:
                raise CheckError(
                    f"dll_import 函数 '{fname}' 不支持关键字参数",
                    line=expr.line, col=expr.col)
            if spread_pos or spread_kw:
                raise CheckError(
                    f"dll_import 函数 '{fname}' 不支持 * / ** 解包",
                    line=expr.line, col=expr.col)
            if len(expr.args) != len(dll_decl.params):
                raise CheckError(
                    f"dll_import 函数 '{fname}' 需要 "
                    f"{len(dll_decl.params)} 个参数，"
                    f"但给了 {len(expr.args)} 个",
                    line=expr.line, col=expr.col)
            for i, (arg, p) in enumerate(zip(expr.args, dll_decl.params)):
                actual = self.check_expr(arg)
                expected = p.get('type')
                if expected in self.known_structs:
                    if actual not in ('dict', 'unknown', None):
                        raise CheckError(
                            f"dll_import 函数 '{fname}' 参数 '{p['name']}' "
                            f"需要 struct dict（{expected}）",
                            line=expr.line, col=expr.col)
                elif expected not in ('cstr', 'ptr') and actual:
                    # dll_import 的 str/wstr 参数在运行时会从 VAL_STR（UTF-8）
                    # 转成 UTF-16，所以 str/cstr 实参传给 wstr 是安全的。
                    if (expected in ('str', 'wstr')
                            and actual in ('str', 'cstr')):
                        continue
                    if not self.compatible(expected, actual):
                        self.warn(
                            f"dll_import 函数 '{fname}' 参数 '{p['name']}' "
                            f"期望 {expected}，得到 {actual}", expr)
            return dll_decl.return_type

        sigs = self.function_signatures.get(fname)
        if sigs is None:
            self.check_expr(expr.func)
            return None

        pos_supplied = sum(1 for a in expr.args
                           if not (isinstance(a, Starred) and a.double))
        kw_supplied = len(expr.kwargs)
        kw_names = {k for k, _ in expr.kwargs}
        supplied = pos_supplied + kw_supplied

        def positional_types(sig):
            if sig.get('has_varargs'):
                idx = sig.get('varargs_index')
                if idx is None:
                    idx = len(sig['param_types'])
                return (sig['param_types'][:idx]
                        + [sig.get('varargs_type') or 'any'])
            return list(sig['param_types'])

        def arity(sig):
            if sig.get('has_varargs'):
                idx = sig.get('varargs_index')
                if idx is None:
                    idx = len(sig['param_types'])
                defaults = sig.get('param_defaults') or []
                required = sum(1 for d in defaults[:idx] if not d)
                return required, None
            defaults = sig.get('param_defaults') or []
            required = sum(1 for d in defaults if not d)
            return required, len(sig['param_types'])

        def can_call(sig):
            if spread_pos or spread_kw:
                return True
            pnames = sig['param_names']
            ptypes = sig['param_types']
            defaults = sig.get('param_defaults') or []
            varidx = sig.get('varargs_index')
            if sig.get('has_varargs'):
                # 多余位置参数会被 *args 收集，不设上限
                maxpos = 1 << 30
            else:
                maxpos = len(ptypes)
            if pos_supplied > maxpos:
                return False
            if not sig.get('has_kwargs'):
                for name in kw_names:
                    if name not in pnames:
                        return False
            # 每个必填参数都必须由位置参数或关键字参数提供
            for i, name in enumerate(pnames):
                if sig.get('has_varargs') and varidx is not None \
                        and i >= varidx:
                    # *args 之后的参数是关键字参数，不能被位置参数填充
                    if name in kw_names:
                        continue
                    if i < len(defaults) and defaults[i]:
                        continue
                    return False
                if i < pos_supplied:
                    continue
                if name in kw_names:
                    continue
                if i < len(defaults) and defaults[i]:
                    continue
                return False
            return True

        candidates = [sig for sig in sigs if can_call(sig)]
        if not candidates:
            sig = sigs[0]
            required, maximum = arity(sig)
            max_text = '∞' if maximum is None else str(maximum)
            raise CheckError(
                f"调用 '{fname}' 参数数量不匹配: 需要 {required}..{max_text}"
                f" 个，但给了 {supplied} 个",
                line=expr.line, col=expr.col)

        sig = candidates[0]
        pnames = sig['param_names']
        # 重载：优先挑参数类型都兼容的签名
        arg_types = []
        for a in expr.args:
            if isinstance(a, Starred):
                if a.double:
                    continue
                arg_types.append('any')
            else:
                arg_types.append(self.check_expr(a))
        kwarg_types = {}
        for key, val_node in expr.kwargs:
            kwarg_types[key] = self.check_expr(val_node)

        def matches(candidate):
            ptypes_c = positional_types(candidate)
            if not candidate.get('has_varargs') \
                    and not spread_pos \
                    and len(arg_types) > len(ptypes_c):
                return False
            for i, at in enumerate(arg_types):
                if i < len(ptypes_c) and ptypes_c[i] \
                        and not self.compatible(ptypes_c[i], at):
                    return False
            if not candidate.get('has_kwargs'):
                for key, vt in kwarg_types.items():
                    if key not in candidate['param_names']:
                        return False
                    idx = candidate['param_names'].index(key)
                    if idx < len(ptypes_c) and ptypes_c[idx] \
                            and not self.compatible(ptypes_c[idx], vt):
                        return False
            return True

        for candidate in candidates:
            if matches(candidate):
                sig = candidate
                break
        else:
            if arg_types:
                self.warn(
                    f"调用 '{fname}' 没有完全匹配的重载，"
                    f"将按第一个签名检查", expr)
            sig = candidates[0]

        ptypes = sig['param_types']
        pnames = sig['param_names']
        pos_types = positional_types(sig)
        for i, a in enumerate(expr.args):
            if isinstance(a, Starred):
                continue
            if i < len(pos_types) and pos_types[i]:
                at = arg_types[i]
                if not self.compatible(pos_types[i], at):
                    self.warn(
                        f"调用 '{fname}' 第 {i+1} 个参数: 期望 "
                        f"{pos_types[i]}，得到 {at}", expr)
        if not sig.get('has_kwargs'):
            for key, val_node in expr.kwargs:
                if key not in pnames:
                    raise CheckError(
                        f"调用 '{fname}' 未知关键字参数: '{key}'",
                        line=expr.line, col=expr.col)
                idx = pnames.index(key)
                if ptypes[idx]:
                    vt = kwarg_types.get(key, self.check_expr(val_node))
                    if not self.compatible(ptypes[idx], vt):
                        self.warn(
                            f"调用 '{fname}' 关键字参数 '{key}': 期望 "
                            f"{ptypes[idx]}，得到 {vt}", expr)
        return sig['return_type']

    @staticmethod
    def _infer(value):
        if isinstance(value, bool):
            return 'bool'
        if isinstance(value, I32):
            return 'i32'
        if isinstance(value, I64):
            return 'i64'
        if isinstance(value, int):
            return 'int'
        if isinstance(value, Decimal):
            return 'double'
        if isinstance(value, float):
            return 'float'
        if isinstance(value, WStr):
            return 'wstr'
        if isinstance(value, str):
            return 'str'
        if isinstance(value, list):
            return 'list'
        if isinstance(value, dict):
            return 'dict'
        if isinstance(value, tuple):
            return 'tuple'
        if value is None:
            return 'none'
        if value is NULL:
            return 'null'
        if value is UNKNOWN:
            return 'unknown'
        return None


def _expr_desc(expr):
    """给 using 表达式一个稳定的文本描述，用于重复 using 警告。"""
    if isinstance(expr, Variable):
        return expr.name
    if isinstance(expr, Attribute):
        return f"{_expr_desc(expr.obj)}.{expr.attr}"
    if isinstance(expr, Call):
        return f"{_expr_desc(expr.func)}(...)"
    return type(expr).__name__
