from class_and_vars import *
from struct_layout import compute_layouts
import pathlib
import re
from decimal import Decimal
from ast_parse import Parser
from token_parse import split_token
from errors import ParseError, LexerError


class CompileError(Exception):
    def __init__(self, message, node=None):
        self.message = message
        self.node = node
        if node and hasattr(node, 'line'):
            super().__init__(f"[行 {node.line}, 列 {node.col}] {message}")
        else:
            super().__init__(message)


class Compiler:
    """AST -> 三地址码 IR"""

    # AOT 后端（asm.py）已经支持的指令
    BUILTIN_CALLS = {'print', 'str', 'len',
                     'int', 'float', 'bool',
                     'type', 'range', 'free'}

    # 只由解释器 VM 实现的内置函数
    VM_BUILTINS = {
        'write', 'eprint', 'input', 'i64', 'double', 'bi', 'code',
        'list', 'tuple', 'dict', 'slice',
        'abs', 'max', 'min', 'sum', 'pow', 'sqrt', 'floor', 'ceil',
        'round', 'random', 'clone', 'id', 'hash', 'exit',
        'new_buffer', 'sizeof',
    }

    BUILTIN_RET_TYPES = {
        'print': 'none', 'write': 'none', 'eprint': 'none',
        'input': 'str', 'str': 'str', 'len': 'int', 'int': 'int',
        'i64': 'i64', 'float': 'float', 'double': 'double', 'bool': 'bool',
        'bi': 'bi', 'type': 'str', 'range': 'list', 'free': 'none',
        'list': 'list', 'tuple': 'tuple', 'dict': 'dict', 'slice': 'list',
        'abs': None, 'max': None, 'min': None, 'sum': None, 'pow': None,
        'sqrt': None, 'floor': 'int', 'ceil': 'int', 'round': None,
        'random': None, 'clone': None, 'id': 'int', 'hash': 'int',
        'exit': 'none', 'code': 'code',
        'new_buffer': 'buffer', 'sizeof': 'int',
    }

    BASIC_TYPES = {'int', 'i64', 'float', 'double', 'str', 'bool'}

    def __init__(self, parent=None, aot_mode=False):
        self._parent = parent
        if parent is not None:
            self.aot_mode = parent.aot_mode
            self.var_types = dict(parent.var_types)
            self.func_sigs = parent.func_sigs
            self.func_names = parent.func_names
            self.known_classes = parent.known_classes
            self.known_structs = parent.known_structs
            self.struct_layouts = parent.struct_layouts
        else:
            self.aot_mode = aot_mode
            self.var_types = {}
            self.func_sigs = {}
            self.func_names = set()
            self.known_classes = set()
            self.known_structs = {}
            self.struct_layouts = {}
            self.fallthrough = False
        self.code = []
        self.exception_table = []
        self.temp_counter = 0
        self.label_counter = 0
        self.code_counter = 0
        self.break_label = None
        self.continue_label = None
        self.loop_depth = 0
        self.break_depth = 0
        self.aot_with_stack = []
        self.temp_type = {}
        self.int_mode = False
        self._int_func_name = None
        self.last_expr_temp = None
        # using 引入的成员（栈，元素是保存对象的临时变量名）
        self.using_stack = []
        # 当前作用域里已知绑定的名字（参数、self、基类名）
        self.bound_names = set()
        # 类体方法可用的基类名
        self.base_names = set()
        # 当前函数/code 对象里定义过的变量名
        self.assigned_names = set()
        self.assigned_order = []
        # 被嵌套函数通过 last.x 捕获的变量：AOT 下提升为全局槽
        self.force_globals = {}
        # 本函数的 last.x 映射到上层函数的捕获槽
        self.last_name_map = {}

    # ---------- 工具 ----------

    def _clean_params(self, stmt):
        """把 stmt.params 清洗成 IR 安全的 params + defaults。

        params 里 default 只存 bool（有没有默认值），
        defaults 里存实际值（降成 Python 常量）。
        AST 节点不能进 IR。
        """
        clean_params = []
        defaults = {}
        for p in stmt.params:
            d = p.get('default')
            has_default = d is not None
            if has_default:
                if isinstance(d, Literal):
                    defaults[p['name']] = d.value
                elif isinstance(d, Unary) and isinstance(d.right, Literal):
                    v = d.right.value
                    if d.op == '-':
                        defaults[p['name']] = -v
                    elif d.op == 'not':
                        defaults[p['name']] = not v
                    else:
                        raise CompileError(
                            f"默认参数 '{p['name']}' 不支持的一元运算: {d.op}",
                            stmt)
                else:
                    raise CompileError(
                        f"默认参数 '{p['name']}' 必须是字面量"
                        f"（不支持 {type(d).__name__}）", stmt)
            clean_params.append({
                'name': p['name'],
                'type': p.get('type'),
                'default': has_default,
            })
        # 把 *args / **kwargs 作为参数列表里的标记，VM 绑定参数时按位置处理
        if stmt.varargs:
            idx = stmt.varargs_index
            if idx is None:
                idx = len(clean_params)
            clean_params.insert(idx, {
                'name': stmt.varargs,
                'type': getattr(stmt, 'varargs_type', None),
                'default': False,
                'varargs': True,
            })
        if stmt.kwargs:
            clean_params.append({
                'name': stmt.kwargs,
                'type': getattr(stmt, 'kwargs_type', None),
                'default': False,
                'kwargs': True,
            })
        return clean_params, defaults

    def _root(self):
        c = self
        while c._parent is not None:
            c = c._parent
        return c

    def new_temp(self, type_name=None):
        root = self._root()
        root.temp_counter += 1
        t = f"t{root.temp_counter}"
        if type_name is not None:
            root.temp_type[t] = type_name
        return t

    def new_label(self):
        root = self._root()
        root.label_counter += 1
        return f"L{root.label_counter}"

    def set_type(self, t, type_name):
        self._root().temp_type[t] = type_name

    def get_type(self, t):
        return self._root().temp_type.get(t)

    def _mark_assigned(self, name):
        if name not in self.assigned_names:
            self.assigned_names.add(name)
            self.assigned_order.append(name)

    def _collect_last_attrs(self, node, out):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                self._collect_last_attrs(item, out)
            return
        if isinstance(node, dict):
            for item in node.values():
                self._collect_last_attrs(item, out)
            return
        if isinstance(node, Attribute) and isinstance(node.obj, Variable) \
                and node.obj.name == 'last':
            out.add(node.attr)
        fields = getattr(node, '__dataclass_fields__', None)
        if fields:
            for fname in fields:
                if fname in ('line', 'col'):
                    continue
                self._collect_last_attrs(getattr(node, fname, None), out)

    def _emit_global_get(self, name):
        vt = self.var_types.get(name)
        t = self.new_temp(vt)
        self.emit('global_get', t, name)
        return t

    def _emit_global_set(self, name, val):
        self.emit('global_set', name, val)

    def emit(self, *instr):
        self.code.append(instr)

    def emit_label(self, name):
        self.code.append(('label', name))

    # ---------- 入口 ----------
    def compile_program(self, program):
        def load_module_ast(module_name):
            """加载模块 AST，每次调用都重新读取和解析文件"""
            # 搜索模块文件
            search_paths = [pathlib.Path("."), pathlib.Path(
                "example"), pathlib.Path("lib")]
            module_path = None
            for base in search_paths:
                candidate = base / (module_name + '.ecl')
                if candidate.exists():
                    module_path = candidate
                    break

            if not module_path:
                print(f"警告: 找不到模块 '{module_name}'")
                return None

            # 读取并解析
            mod_code = module_path.read_text(encoding='utf-8')
            mod_parser = Parser(split_token(mod_code))
            try:
                mod_program = mod_parser.parse()
            except (ParseError, LexerError) as e:
                raise CompileError(
                    f"模块 '{module_name}' ({module_path}) 解析失败: {e}")

            # 本模块自己声明的类名（导入时这些类会被加前缀，
            # 模块内部 class A(B) 的基类引用也要同步改名）
            own_class_names = {s.name for s in mod_program.body
                               if isinstance(s, ClassDefStmt)}

            # 递归处理模块内部的 import
            resolved_body = []
            for stmt in mod_program.body:
                if isinstance(stmt, ImportStmt):
                    resolved_body.extend(resolve_import(stmt))
                else:
                    resolved_body.append(stmt)

            return resolved_body, own_class_names

        def resolve_import(import_stmt):
            """处理单个 import 或 from-import，返回要注入 AST 的语句列表"""
            module_name = import_stmt.module

            # 加载模块 AST
            loaded = load_module_ast(module_name)
            if loaded is None:
                return []
            mod_body, own_class_names = loaded

            # 建立别名映射
            # e.g., from math import cube as c -> {"cube": "c"}
            from_imports = {}
            if import_stmt.names:
                for orig, alias in import_stmt.names:
                    from_imports[orig] = alias if alias else orig

            # 决定前缀
            if import_stmt.names:
                prefix = ""  # from-import 模式下函数名保持原名或别名
            elif import_stmt.alias:
                prefix = import_stmt.alias + "_"
            else:
                prefix = module_name + "_"

            expanded_body = []
            for stmt in mod_body:
                if isinstance(stmt, (FuncDefStmt, ClassDefStmt)):
                    orig_name = stmt.name

                    if import_stmt.names:
                        # from-import 模式：如果在导入名单里，直接改名
                        if orig_name in from_imports:
                            stmt.name = from_imports[orig_name]
                    else:
                        # 普通 import 模式：全部加上前缀
                        stmt.name = prefix + orig_name

                    if isinstance(stmt, ClassDefStmt):
                        # 基类引用遵循同一套扁平命名：
                        # 模块内部的 class A(B) -> class mod_A(mod_B)
                        if import_stmt.names:
                            stmt.bases = [from_imports.get(b, b)
                                          for b in stmt.bases]
                        else:
                            stmt.bases = [
                                prefix + b if b in own_class_names else b
                                for b in stmt.bases]

                    expanded_body.append(stmt)
                else:
                    expanded_body.append(stmt)

            return expanded_body

        # ---------- 主循环：处理顶层 AST ----------
        new_body = []
        for stmt in program.body:
            if isinstance(stmt, ImportStmt):
                new_body.extend(resolve_import(stmt))
            else:
                new_body.append(stmt)

        program.body = new_body

        # ---------- 收集 struct 布局 ----------
        for stmt in program.body:
            if isinstance(stmt, StructDefStmt):
                self.known_structs[stmt.name] = [
                    (f.type_name, f.name) for f in stmt.fields]
        self.struct_layouts = compute_layouts(self.known_structs)

        # ---------- 收集函数签名（支持同名重载） ----------
        self.func_names = set()
        for stmt in program.body:
            if isinstance(stmt, FuncDefStmt):
                self.func_names.add(stmt.name)
                self.func_sigs.setdefault(stmt.name, []).append((
                    [p.get('type') for p in stmt.params],
                    stmt.return_type,
                ))
            elif isinstance(stmt, ClassDefStmt):
                self.known_classes.add(stmt.name)

        # ---------- FALLTHROUGH 是程序级开关 ----------
        self._root().fallthrough = self._scan_fallthrough(program.body)

        # ---------- 生成 IR ----------
        self.last_expr_temp = None
        # struct 先全部登记，dll_import 的签名可以在声明之前引用
        for stmt in program.body:
            if isinstance(stmt, StructDefStmt):
                self.emit('struct_def', stmt.name,
                          [(f.type_name, f.name) for f in stmt.fields])
        self.compile_block(program.body)
        # DLL 导出信息（--target dll 用）
        exports = []
        for stmt in program.body:
            if isinstance(stmt, FuncDefStmt) and stmt.visibility == 'public':
                exports.append((
                    stmt.name,
                    [(p['name'], p.get('type')) for p in stmt.params],
                    stmt.return_type))
        if exports:
            self.emit('exports', exports)
        self.emit('halt')
        return self.code, self.exception_table

    def _scan_fallthrough(self, stmts):
        for stmt in stmts:
            if isinstance(stmt, DefineStmt):
                if stmt.name == 'FALLTHROUGH' and stmt.is_const:
                    return True
            for field_name in ('body', 'else_body', 'finally_body'):
                sub = getattr(stmt, field_name, None)
                if isinstance(sub, list) and self._scan_fallthrough(sub):
                    return True
            if isinstance(stmt, CompoundStmt) and self._scan_fallthrough(stmt.stmts):
                return True
            if isinstance(stmt, MatchStmt):
                for c in stmt.cases:
                    if self._scan_fallthrough(c.body):
                        return True
            if isinstance(stmt, TryStmt):
                for ec in stmt.excepts:
                    if self._scan_fallthrough(ec['body']):
                        return True
            if isinstance(stmt, IfStmt):
                for _, eb in stmt.elifs:
                    if self._scan_fallthrough(eb):
                        return True
        return False

    # ---------- 块（using 作用域） ----------

    def compile_block(self, stmts):
        saved = len(self.using_stack)
        try:
            for stmt in stmts:
                if isinstance(stmt, UsingStmt):
                    src = self.compile_expr(stmt.expr)
                    tmp = self.new_temp('any')
                    self.emit('using_enter', tmp, src)
                    self.using_stack.append(tmp)
                else:
                    self.compile_stmt(stmt)
        finally:
            del self.using_stack[saved:]

    def compile_stmt(self, stmt):
        if self.int_mode:
            return self.compile_stmt_int(stmt)

        if isinstance(stmt, ExprStmt):
            self.compile_expr(stmt.expr)
            return
        if isinstance(stmt, DefineStmt):
            return self.compile_define(stmt)
        if isinstance(stmt, FuncDefStmt):
            return self.compile_func_def(stmt)
        if isinstance(stmt, ClassDefStmt):
            return self.compile_class_def(stmt)
        if isinstance(stmt, IfStmt):
            return self.compile_if(stmt)
        if isinstance(stmt, WhileStmt):
            return self.compile_while(stmt)
        if isinstance(stmt, ForStmt):
            return self.compile_for(stmt)
        if isinstance(stmt, ReturnStmt):
            self._emit_aot_cleanup(None)
            if stmt.value is not None:
                src = self.compile_expr(stmt.value)
                self.emit('return', src)
            else:
                self.emit('return_none')
            return
        if isinstance(stmt, BreakStmt):
            if self.break_label is None:
                raise CompileError("'break' 必须在循环内使用", stmt)
            self._emit_aot_cleanup(self.break_depth)
            self.emit('goto', self.break_label)
            return
        if isinstance(stmt, ContinueStmt):
            if self.continue_label is None:
                raise CompileError("'continue' 必须在循环内使用", stmt)
            self._emit_aot_cleanup(self.break_depth)
            self.emit('goto', self.continue_label)
            return
        if isinstance(stmt, GotoStmt):
            self._emit_aot_cleanup(None)
            self.emit('goto', f"label_{stmt.label}")
            return
        if isinstance(stmt, LabelStmt):
            self.emit_label(f"label_{stmt.name}")
            return
        if isinstance(stmt, TryStmt):
            return self.compile_try(stmt)
        if isinstance(stmt, RaiseStmt):
            src = self.compile_expr(stmt.exception)
            self.emit('raise', src)
            return
        if isinstance(stmt, CompoundStmt):
            for s in stmt.stmts:
                self.compile_stmt(s)
            return
        if isinstance(stmt, StructDefStmt):
            # 程序开头已经统一登记，这里不重复生成
            return
        if isinstance(stmt, DllImportStmt):
            descs = []
            for f in stmt.funcs:
                descs.append((
                    f.name,
                    [(p['name'], p.get('type'), p.get('modifier'))
                     for p in f.params],
                    f.return_type))
            self.emit('dll_import', stmt.path, descs)
            return
        if isinstance(stmt, UnpackAssignStmt):
            return self.compile_unpack_assign(stmt)
        if isinstance(stmt, MatchStmt):
            return self.compile_match(stmt)
        if isinstance(stmt, WithStmt):
            return self.compile_with(stmt)
        if isinstance(stmt, UsingStmt):
            raise CompileError("using 只能在语句块中使用", stmt)
        if isinstance(stmt, ImportStmt):
            return
        raise NotImplementedError(f"未实现的语句: {type(stmt).__name__}")

    def compile_stmt_int(self, stmt):
        if isinstance(stmt, ReturnStmt):
            if stmt.value is not None:
                src = self.compile_expr_int(stmt.value)
                self.emit('int_ret', src)
            else:
                self.emit('int_ret_zero')
            return
        if isinstance(stmt, DefineStmt):
            if stmt.value is not None:
                src = self.compile_expr_int(stmt.value)
            else:
                src = ('imm', 0)
            self.emit('int_store_var', stmt.name, src)
            return
        if isinstance(stmt, IfStmt):
            end_label = self.new_label()
            cond = self.compile_expr_int(stmt.condition)
            next_label = self.new_label()
            self.emit('int_jz', cond, next_label)
            for s in stmt.body:
                self.compile_stmt_int(s)
            self.emit('goto', end_label)
            self.emit_label(next_label)
            for ec, eb in stmt.elifs:
                cond = self.compile_expr_int(ec)
                next_label = self.new_label()
                self.emit('int_jz', cond, next_label)
                for s in eb:
                    self.compile_stmt_int(s)
                self.emit('goto', end_label)
                self.emit_label(next_label)
            if stmt.else_body:
                for s in stmt.else_body:
                    self.compile_stmt_int(s)
            self.emit_label(end_label)
            return
        if isinstance(stmt, WhileStmt):
            start_label = self.new_label()
            end_label = self.new_label()
            self.emit_label(start_label)
            cond = self.compile_expr_int(stmt.condition)
            self.emit('int_jz', cond, end_label)
            for s in stmt.body:
                self.compile_stmt_int(s)
            self.emit('goto', start_label)
            self.emit_label(end_label)
            return
        if isinstance(stmt, ExprStmt):
            self.compile_expr_int(stmt.expr)
            return
        raise NotImplementedError(f"int_mode 不支持的语句: {type(stmt).__name__}")

    # ---------- 定义 ----------

    def compile_define(self, stmt):
        # FALLTHROUGH 是编译期开关
        if stmt.name == 'FALLTHROUGH' and stmt.is_const:
            return
        self._mark_assigned(stmt.name)
        if self.aot_mode and stmt.name in self.force_globals \
                and stmt.value is not None:
            src = self.compile_expr(stmt.value)
            if stmt.type_name:
                base = stmt.type_name.split('[')[0].strip().split()[0]
                src = self._maybe_convert(src, base)
                self.var_types[stmt.name] = base
            self._emit_global_set(self.force_globals[stmt.name], src)
            return
        if stmt.is_ref:
            if isinstance(stmt.value, Variable):
                self.emit('ref', stmt.name, stmt.value.name)
                if stmt.value.name in self.var_types:
                    self.var_types[stmt.name] = self.var_types[stmt.value.name]
            else:
                raise CompileError("ref 必须绑定到变量", stmt)
            return
        if stmt.value is not None:
            src = self.compile_expr(stmt.value)
        else:
            # define any x：只声明，赋值为 null
            src = self.new_temp('null')
            self.emit('const', src, NULL)
        if stmt.type_name and stmt.type_name != 'global':
            base_type = stmt.type_name.split('[')[0].strip().split()[0]
            if base_type == 'bi':
                bi_t = self.new_temp('bi')
                self.emit('to_bi', bi_t, src)
                src = bi_t
                self.var_types[stmt.name] = 'bi'
            else:
                src = self._maybe_convert(src, base_type)
                self.var_types[stmt.name] = base_type
                fixed = self._fixed_length(stmt.type_name)
                if fixed is not None:
                    self.emit('expect_len', src, fixed)
        elif self.get_type(src):
            self.var_types[stmt.name] = self.get_type(src)
        self.emit('store', stmt.name, src)

    @staticmethod
    def _fixed_length(type_name):
        """解析 list[5] 里的固定长度 5。"""
        if not type_name:
            return None
        m = re.match(r'list\[(\d+)\]', type_name)
        if m:
            return int(m.group(1))
        return None

    def _maybe_convert(self, src, target_base):
        """注解类型与表达式类型不同时插入运行时转换指令。"""
        if target_base not in self.BASIC_TYPES:
            return src
        src_type = self.get_type(src)
        if src_type in (None, 'unknown', 'any'):
            return src
        if src_type == target_base:
            # i64/double 在原生运算后可能丢失标签，赋值时重新标注
            if target_base in ('i64', 'double'):
                t = self.new_temp(target_base)
                self.emit('convert_type', t, src, target_base)
                return t
            return src
        if src_type not in self.BASIC_TYPES and src_type != 'none':
            return src
        t = self.new_temp(target_base)
        self.emit('convert_type', t, src, target_base)
        return t

    # ---------- 函数 ----------

    def compile_func_def(self, stmt):
        self.func_names.add(stmt.name)
        captured = set()
        self._collect_last_attrs(stmt.body, captured)
        capture_map = {n: f"__cap_{stmt.name}_{n}" for n in captured}
        sub = Compiler(parent=self)
        sub.last_name_map = dict(self.force_globals)
        sub.force_globals = capture_map
        for p in stmt.params:
            if p.get('type'):
                sub.var_types[p['name']] = p['type']
            sub.bound_names.add(p['name'])
            if p['name'] in captured and self.aot_mode:
                t = sub.new_temp(p.get('type'))
                sub.emit('var', t, p['name'])
                sub.emit('global_set', capture_map[p['name']], t)
        if stmt.varargs:
            sub.var_types[stmt.varargs] = 'list'
            sub.bound_names.add(stmt.varargs)
        if stmt.kwargs:
            sub.var_types[stmt.kwargs] = 'dict'
            sub.bound_names.add(stmt.kwargs)

        # int 特化会生成独立 ABI 的函数，导致 *args/**kwargs/变量调用取不到
        # 函数值；AOT 以行为一致优先，关闭特化。
        is_int_func = self._is_int_func(stmt)
        if is_int_func:
            sub.int_mode = True
            sub._int_func_name = stmt.name
            old_break = self.break_label
            old_continue = self.continue_label
            self.break_label = None
            self.continue_label = None
            for s in stmt.body:
                sub.compile_stmt_int(s)
            self.break_label = old_break
            self.continue_label = old_continue
            sub.emit('int_ret_zero')
            sub.emit('halt')
            clean_params, _ = self._clean_params(stmt)
            self.emit('func_int', stmt.name, sub.code, clean_params,
                      stmt.return_type)
            return

        old_break = self.break_label
        old_continue = self.continue_label
        self.break_label = None
        self.continue_label = None
        sub.compile_block(stmt.body)
        self.break_label = old_break
        self.continue_label = old_continue
        sub.emit('return_none')
        sub.emit('halt')
        clean_params, defaults = self._clean_params(stmt)
        self.emit('func', stmt.name, sub.code, clean_params,
                  stmt.varargs, stmt.kwargs, stmt.return_type, defaults)

    def _is_int_func(self, stmt):
        if stmt.varargs or stmt.kwargs:
            return False
        if len(stmt.params) > 4:
            return False
        if stmt.return_type != 'i64':
            return False
        for p in stmt.params:
            if p.get('type') != 'i64':
                return False
        return self._body_is_int_only(stmt.body, stmt.name)

    def _body_is_int_only(self, stmts, fname):
        for s in stmts:
            if isinstance(s, ReturnStmt):
                if s.value is None:
                    return False
                if not self._expr_is_int_only(s.value, fname):
                    return False
            elif isinstance(s, DefineStmt):
                if s.type_name is None:
                    return False
                base = s.type_name.split('[')[0].strip().split()[0]
                if base != 'int':
                    return False
                if s.value is not None and not self._expr_is_int_only(s.value, fname):
                    return False
            elif isinstance(s, IfStmt):
                if not self._expr_is_int_only(s.condition, fname):
                    return False
                if not self._body_is_int_only(s.body, fname):
                    return False
                for ec, eb in s.elifs:
                    if not self._expr_is_int_only(ec, fname):
                        return False
                    if not self._body_is_int_only(eb, fname):
                        return False
                if not self._body_is_int_only(s.else_body, fname):
                    return False
            elif isinstance(s, WhileStmt):
                if not self._expr_is_int_only(s.condition, fname):
                    return False
                if not self._body_is_int_only(s.body, fname):
                    return False
            elif isinstance(s, ExprStmt):
                if not self._expr_is_int_only(s.expr, fname):
                    return False
            else:
                return False
        return True

    def _expr_is_int_only(self, expr, fname):
        if isinstance(expr, Literal):
            return isinstance(expr.value, int) and not isinstance(expr.value, bool)
        if isinstance(expr, Variable):
            return True
        if isinstance(expr, Binary):
            if expr.op not in ('+', '-', '*', '<', '>', '<=', '>=', '==', '!=', 'and', 'or'):
                return False
            return (self._expr_is_int_only(expr.left, fname) and
                    self._expr_is_int_only(expr.right, fname))
        if isinstance(expr, Unary):
            return expr.op in ('-', 'not') and self._expr_is_int_only(expr.right, fname)
        if isinstance(expr, IfExpr):
            return (self._expr_is_int_only(expr.condition, fname) and
                    self._expr_is_int_only(expr.then_expr, fname) and
                    self._expr_is_int_only(expr.else_expr, fname))
        if isinstance(expr, Assign):
            return (isinstance(expr.name, Variable) and
                    self._expr_is_int_only(expr.value, fname))
        if isinstance(expr, Call):
            if isinstance(expr.func, Variable) and expr.func.name == fname:
                return all(self._expr_is_int_only(a, fname) for a in expr.args)
            return False
        return False

    # ---------- 类 ----------

    def compile_class_def(self, stmt):
        self.known_classes.add(stmt.name)
        methods = {}
        # 类体里的变量声明 -> 实例初始属性
        class_defs = [s for s in stmt.body if isinstance(s, DefineStmt)]
        for s in stmt.body:
            if isinstance(s, FuncDefStmt):
                sub = Compiler(parent=self)
                # ★ 方法参数前插入 self（default 用 False，和 _clean_params 语义一致）
                method_params = [
                    {'name': 'self', 'type': None, 'default': False}]
                sub.var_types['self'] = None
                sub.bound_names.add('self')
                sub.base_names = set(stmt.bases)
                for base in stmt.bases:
                    sub.bound_names.add(base)
                captured = set()
                self._collect_last_attrs(s.body, captured)
                capture_map = {n: f"__cap_{stmt.name}_{s.name}_{n}"
                               for n in captured}
                sub.force_globals = capture_map
                sub.last_name_map = dict(self.force_globals)
                for p in s.params:
                    if p.get('type'):
                        sub.var_types[p['name']] = p['type']
                    sub.bound_names.add(p['name'])
                    if p['name'] in captured and self.aot_mode:
                        t = sub.new_temp(p.get('type'))
                        sub.emit('var', t, p['name'])
                        sub.emit('global_set', capture_map[p['name']], t)
                if s.varargs:
                    sub.var_types[s.varargs] = 'list'
                    sub.bound_names.add(s.varargs)
                if s.kwargs:
                    sub.var_types[s.kwargs] = 'dict'
                    sub.bound_names.add(s.kwargs)
                old_break = self.break_label
                old_continue = self.continue_label
                self.break_label = None
                self.continue_label = None
                sub.compile_block(s.body)
                self.break_label = old_break
                self.continue_label = old_continue
                sub.emit('return_none')
                sub.emit('halt')
                clean_params, defaults = self._clean_params(s)
                all_params = method_params + clean_params
                methods[s.name] = (sub.code, all_params, s.varargs,
                                   s.kwargs, s.return_type, defaults)
                self.emit('method_func', stmt.name, s.name, sub.code,
                          all_params, s.varargs, s.kwargs, s.return_type, defaults)
        if class_defs:
            sub = Compiler(parent=self)
            sub.var_types['self'] = None
            sub.bound_names.add('self')
            self_tmp = sub.new_temp('any')
            sub.emit('var', self_tmp, 'self')
            for d in class_defs:
                if d.value is not None:
                    val = sub.compile_expr(d.value)
                else:
                    val = sub.new_temp('null')
                    sub.emit('const', val, NULL)
                if d.type_name:
                    base = d.type_name.split('[')[0].strip().split()[0]
                    val = sub._maybe_convert(val, base)
                sub.emit('set_attr', self_tmp, d.name, val)
            sub.emit('return_none')
            sub.emit('halt')
            body_params = [{'name': 'self', 'type': None, 'default': False}]
            methods['__class_body__'] = (sub.code, body_params, None, None,
                                         'none', {})
            self.emit('method_func', stmt.name, '__class_body__', sub.code,
                      body_params, None, None, 'none', {})
        self.emit('class', stmt.name, stmt.bases, methods)
    # ---------- if ----------

    def compile_if(self, stmt):
        end_label = self.new_label()
        cond = self.compile_expr(stmt.condition)
        next_label = self.new_label()
        self.emit('if_not_goto', cond, next_label)
        self.compile_block(stmt.body)
        self.emit('goto', end_label)
        self.emit_label(next_label)
        for ec, eb in stmt.elifs:
            cond = self.compile_expr(ec)
            next_label = self.new_label()
            self.emit('if_not_goto', cond, next_label)
            self.compile_block(eb)
            self.emit('goto', end_label)
            self.emit_label(next_label)
        if stmt.else_body:
            self.compile_block(stmt.else_body)
        self.emit_label(end_label)

    # ---------- while ----------

    def compile_while(self, stmt):
        start = self.new_label()
        end = self.new_label()
        self.emit_label(start)
        cond = self.compile_expr(stmt.condition)
        self.emit('if_not_goto', cond, end)
        old_break, old_continue = self.break_label, self.continue_label
        self.break_label = end
        self.continue_label = start
        self.loop_depth += 1
        self.break_depth += 1
        self.compile_block(stmt.body)
        self.loop_depth -= 1
        self.break_depth -= 1
        self.break_label, self.continue_label = old_break, old_continue
        self.emit('goto', start)
        self.emit_label(end)

    # ---------- for ----------

    def compile_for(self, stmt):
        start = self.new_label()
        end = self.new_label()
        continue_label = self.new_label()
        old_break, old_continue = self.break_label, self.continue_label
        self.break_label = end
        self.continue_label = continue_label
        self.loop_depth += 1
        self.break_depth += 1

        if stmt.count is not None:
            count_src = self.compile_expr(stmt.count)
            iter_tmp = self.new_temp('int')
            self.emit('const', iter_tmp, 0)
            self.emit_label(start)
            cmp_tmp = self.new_temp('bool')
            self.emit('lt', cmp_tmp, iter_tmp, count_src)
            self.emit('if_not_goto', cmp_tmp, end)
            self.compile_block(stmt.body)
            self.emit_label(continue_label)
            one = self.new_temp('int')
            self.emit('const', one, 1)
            next_tmp = self.new_temp('int')
            self.emit('add', next_tmp, iter_tmp, one)
            self.emit('assign', iter_tmp, next_tmp)
            self.emit('goto', start)
            self.emit_label(end)

        elif stmt.range_expr is not None:
            r = stmt.range_expr
            start_src = self.compile_expr(r.start)
            end_src = self.compile_expr(r.end)
            step_src = self.compile_expr(r.step) if r.step else None
            if step_src is None:
                step_src = self.new_temp('int')
                self.emit('const', step_src, 1)
            iter_tmp = self.new_temp('int')
            self.emit('assign', iter_tmp, start_src)
            self.emit_label(start)
            cmp_tmp = self.new_temp('bool')
            op = 'le' if r.inclusive else 'lt'
            self.emit(op, cmp_tmp, iter_tmp, end_src)
            self.emit('if_not_goto', cmp_tmp, end)
            self.emit('store', stmt.variable, iter_tmp)
            self._mark_assigned(stmt.variable)
            self.var_types[stmt.variable] = self.get_type(iter_tmp) or 'int'
            self.compile_block(stmt.body)
            self.emit_label(continue_label)
            next_tmp = self.new_temp('int')
            self.emit('add', next_tmp, iter_tmp, step_src)
            self.emit('assign', iter_tmp, next_tmp)
            self.emit('goto', start)
            self.emit_label(end)

        else:
            iterable_src = self.compile_expr(stmt.iterable)
            it_type = self.get_type(iterable_src)
            if it_type not in ('list', 'tuple'):
                list_src = self.new_temp('list')
                self.emit('iter_list', list_src, iterable_src)
                iterable_src = list_src
            idx_tmp = self.new_temp('int')
            self.emit('const', idx_tmp, 0)
            self.emit_label(start)
            len_tmp = self.new_temp()
            self.emit('var', len_tmp, 'len')
            len_result = self.new_temp('int')
            self.emit('call', len_result, len_tmp, [iterable_src], {})
            cmp_tmp = self.new_temp('bool')
            self.emit('lt', cmp_tmp, idx_tmp, len_result)
            self.emit('if_not_goto', cmp_tmp, end)
            item_tmp = self.new_temp()
            self.emit('get_item', item_tmp, iterable_src, idx_tmp)
            self.emit('store', stmt.variable, item_tmp)
            vt = self.get_type(item_tmp)
            if vt:
                self.var_types[stmt.variable] = vt
            self.compile_block(stmt.body)
            self.emit_label(continue_label)
            one = self.new_temp('int')
            self.emit('const', one, 1)
            next_tmp = self.new_temp('int')
            self.emit('add', next_tmp, idx_tmp, one)
            self.emit('assign', idx_tmp, next_tmp)
            self.emit('goto', start)
            self.emit_label(end)

        self.loop_depth -= 1
        self.break_depth -= 1
        self.break_label, self.continue_label = old_break, old_continue
    # ---------- try ----------

    def _compile_with_aot(self, stmt):
        ctxs = []
        for expr, name in stmt.items:
            src = self.compile_expr(expr)
            obj = self.new_temp('any')
            self.emit('assign', obj, src)
            entered = self.new_temp('any')
            leave = self.new_temp('any')
            self.emit('with_enter', entered, leave, obj)
            if name:
                self.emit('store_share', name, entered)
                self._mark_assigned(name)
            ctxs.append({'leave': leave, 'break_depth': self.break_depth})

        except_label = self.new_label()
        finally_label = self.new_label()
        normal_label = self.new_label()
        end_label = self.new_label()
        self.emit('try', except_label, finally_label)
        saved = self.aot_with_stack
        self.aot_with_stack = saved + ctxs
        try:
            self.compile_block(stmt.body)
        finally:
            self.aot_with_stack = saved
        self.emit('end_try', normal_label, finally_label)

        # 异常路径：先 leave（LIFO），只要有 leave 返回 true 就吞掉异常
        self.emit_label(except_label)
        exc = self.new_temp('any')
        self.emit('current_exc', exc)
        etype = self.new_temp('any')
        self.emit('current_exc_type', etype)
        swallowed = self.new_temp('bool')
        self.emit('const', swallowed, False)
        for ctx in reversed(ctxs):
            r = self.new_temp('any')
            self.emit('call', r, ctx['leave'], [etype, exc], {})
            combined = self.new_temp('bool')
            self.emit('bor', combined, swallowed, r)
            self.emit('assign', swallowed, combined)
        reraise_label = self.new_label()
        self.emit('jz', swallowed, reraise_label)
        self.emit('goto', end_label)
        self.emit_label(reraise_label)
        self.emit('reraise')

        # 正常路径：leave(none, none)
        self.emit_label(normal_label)
        for ctx in reversed(ctxs):
            n1 = self.new_temp('none')
            self.emit('const', n1, None)
            n2 = self.new_temp('none')
            self.emit('const', n2, None)
            res = self.new_temp('any')
            self.emit('call', res, ctx['leave'], [n1, n2], {})
        self.emit_label(end_label)
        self.emit_label(finally_label)

    def _emit_aot_cleanup(self, break_depth):
        """AOT：跳转/返回前对已进入的 with 调用 __leave__（正常参数）。"""
        if not self.aot_mode:
            return
        for ctx in reversed(self.aot_with_stack):
            if break_depth is not None and ctx['break_depth'] < break_depth:
                continue
            self.emit('pop_try')
            n1 = self.new_temp('none')
            self.emit('const', n1, None)
            n2 = self.new_temp('none')
            self.emit('const', n2, None)
            res = self.new_temp('any')
            self.emit('call', res, ctx['leave'], [n1, n2], {})

    def compile_try(self, stmt):
        except_label = self.new_label()
        finally_label = self.new_label()
        end_label = self.new_label()
        else_label = self.new_label() if stmt.else_body is not None else None
        normal_label = else_label or finally_label

        if self.aot_mode:
            # AOT：try/except 用 setjmp；handler 里按异常类型分派
            finally_label = self.new_label()
            end_label = self.new_label()
            normal_label = else_label or finally_label
            self.emit('try', except_label, finally_label)
            self.compile_block(stmt.body)
            self.emit('end_try', normal_label, finally_label)
            self.emit_label(except_label)
            for ec in stmt.excepts:
                cond = self.new_temp('bool')
                self.emit('exc_match', cond, ec['type'])
                nxt = self.new_label()
                self.emit('if_not_goto', cond, nxt)
                if ec['name']:
                    exc_val = self.new_temp('any')
                    self.emit('current_exc', exc_val)
                    self.emit('store', ec['name'], exc_val)
                self.compile_block(ec['body'])
                self.emit('goto', finally_label)
                self.emit_label(nxt)
            # 没有匹配的 except：先执行 finally，再继续抛出
            if stmt.finally_body:
                self.compile_block(stmt.finally_body)
            self.emit('reraise')
            if else_label is not None:
                self.emit_label(else_label)
                self.compile_block(stmt.else_body)
                self.emit('goto', finally_label)
            self.emit_label(finally_label)
            if stmt.finally_body:
                self.compile_block(stmt.finally_body)
            self.emit_label(end_label)
            return

        self.emit('try', except_label, finally_label)
        body_start = len(self.code)
        self.compile_block(stmt.body)
        body_end = len(self.code) - 1
        # VM 在正常结束时跳到 normal_label
        self.emit('end_try', normal_label, finally_label)

        # ---- except 分派 ----
        self.emit_label(except_label)
        handler_start = len(self.code)
        for ec in stmt.excepts:
            cond = self.new_temp('bool')
            self.emit('exc_match', cond, ec['type'])
            nxt = self.new_label()
            self.emit('if_not_goto', cond, nxt)
            if ec['name']:
                self.emit('store_exc', ec['name'])
            self.compile_block(ec['body'])
            self.emit('goto', finally_label)
            self.emit_label(nxt)
        self.emit('reraise')
        handler_end = len(self.code) - 1

        # ---- else ----
        else_start = else_end = None
        if else_label is not None:
            self.emit_label(else_label)
            else_start = len(self.code)
            self.compile_block(stmt.else_body)
            else_end = len(self.code) - 1
            self.emit('goto', finally_label)

        # ---- finally（正常路径） ----
        self.emit_label(finally_label)
        fin_start = len(self.code)
        if stmt.finally_body:
            self.compile_block(stmt.finally_body)
        fin_end = len(self.code) - 1
        self.emit('goto', end_label)

        # ---- finally（异常路径：执行完 finally 后继续抛出） ----
        fr_label = self.new_label()
        self.emit_label(fr_label)
        fr_start = len(self.code)
        if stmt.finally_body:
            self.compile_block(stmt.finally_body)
        fr_end = len(self.code) - 1
        self.emit('reraise')
        self.emit_label(end_label)

        # ---- 异常表（标签由 VM 解析成 pc） ----
        def add_region(start, end, handler):
            if end >= start:
                self.exception_table.append({
                    'try_start': start + 1,
                    'try_end': end + 1,
                    'handler_label': handler,
                    'exc_type': None,
                    'exc_name': None,
                })

        add_region(body_start, body_end, except_label)
        add_region(handler_start, handler_end, fr_label)
        if else_start is not None:
            add_region(else_start, else_end, fr_label)
        add_region(fin_start, fin_end, fr_label)
        add_region(fr_start, fr_end, fr_label)

    # ---------- unpack 赋值 ----------

    def compile_unpack_assign(self, stmt):
        src = self.compile_expr(stmt.value)
        targets = [(t.name, t.is_star) for t in stmt.targets]
        if not self.aot_mode:
            self.emit('unpack_assign', src, targets)
            for t in stmt.targets:
                self.var_types[t.name] = self.get_type(src) or 'any'
            return
        # AOT：把解包降级为 len/get_item/store 组合
        length = self.new_temp('int')
        self.emit('len', length, [src])
        star_idx = next((i for i, (_, s) in enumerate(targets) if s), None)

        def const_int(v):
            t = self.new_temp('int')
            self.emit('const', t, v)
            return t

        def raise_msg(msg):
            self.emit('raise_error', msg)

        if star_idx is None:
            ok = self.new_label()
            eq_t = self.new_temp('bool')
            self.emit('eq', eq_t, length, const_int(len(targets)))
            self.emit('if_goto', eq_t, ok)
            raise_msg("ValueError: 赋值解包元素数量不匹配")
            self.emit_label(ok)
            for i, (name, _) in enumerate(targets):
                item = self.new_temp()
                self.emit('get_item', item, src, const_int(i))
                self.emit('store', name, item)
        else:
            pre = targets[:star_idx]
            post = targets[star_idx + 1:]
            need = len(pre) + len(post)
            ok = self.new_label()
            lt_t = self.new_temp('bool')
            self.emit('lt', lt_t, length, const_int(need))
            nxt = self.new_label()
            self.emit('if_not_goto', lt_t, nxt)
            raise_msg("ValueError: 赋值解包元素不足")
            self.emit_label(nxt)
            self.emit_label(ok)
            for i, (name, _) in enumerate(pre):
                item = self.new_temp()
                self.emit('get_item', item, src, const_int(i))
                self.emit('store', name, item)
            for j, (name, _) in enumerate(post):
                idx = self.new_temp('int')
                self.emit('sub', idx, length, const_int(len(post) - j))
                item = self.new_temp()
                self.emit('get_item', item, src, idx)
                self.emit('store', name, item)
            # star = src[pre : len-post]
            star_name = targets[star_idx][0]
            star_list = self.new_temp('list')
            self.emit('new_list', star_list)
            idx = self.new_temp('int')
            self.emit('assign', idx, const_int(len(pre)))
            limit = self.new_temp('int')
            self.emit('sub', limit, length, const_int(len(post)))
            loop = self.new_label()
            done = self.new_label()
            self.emit_label(loop)
            cond = self.new_temp('bool')
            self.emit('lt', cond, idx, limit)
            self.emit('if_not_goto', cond, done)
            item = self.new_temp()
            self.emit('get_item', item, src, idx)
            self.emit('list_append', star_list, item)
            one = const_int(1)
            nxt = self.new_temp('int')
            self.emit('add', nxt, idx, one)
            self.emit('assign', idx, nxt)
            self.emit('goto', loop)
            self.emit_label(done)
            self.emit('store', star_name, star_list)
        for t in stmt.targets:
            self.var_types[t.name] = 'any'
            self._mark_assigned(t.name)

    # ---------- match ----------

    def compile_pattern(self, pattern, value_src):
        if isinstance(pattern, MatchWildcard):
            t = self.new_temp('bool')
            self.emit('const', t, True)
            return t
        if isinstance(pattern, MatchLiteral):
            lit = self.new_temp(self._infer_literal_type(pattern.value))
            self.emit('const', lit, pattern.value)
            t = self.new_temp('bool')
            self.emit('match_eq', t, value_src, lit)
            return t
        if isinstance(pattern, MatchMulti):
            first = pattern.values[0]
            lit = self.new_temp(self._infer_literal_type(first))
            self.emit('const', lit, first)
            t = self.new_temp('bool')
            self.emit('match_eq', t, value_src, lit)
            for value in pattern.values[1:]:
                lit = self.new_temp(self._infer_literal_type(value))
                self.emit('const', lit, value)
                part = self.new_temp('bool')
                self.emit('match_eq', part, value_src, lit)
                combined = self.new_temp('bool')
                self.emit('bor', combined, t, part)
                t = combined
            return t
        if isinstance(pattern, MatchType):
            t = self.new_temp('bool')
            self.emit('is_type', t, value_src, pattern.type_name)
            return t
        raise CompileError(f"未知 match 模式: {type(pattern).__name__}")

    def compile_match(self, stmt):
        value_src = self.compile_expr(stmt.value)
        end_label = self.new_label()
        body_labels = [self.new_label() for _ in stmt.cases]
        fallthrough = self._root().fallthrough

        old_break, old_continue = self.break_label, self.continue_label
        self.break_label = end_label
        self.continue_label = None
        self.break_depth += 1
        try:
            # 分派：找到第一个匹配的 case
            for i, case in enumerate(stmt.cases):
                cond = self.compile_pattern(case.pattern, value_src)
                nxt = self.new_label()
                self.emit('if_not_goto', cond, nxt)
                if case.guard is not None:
                    guard = self.compile_expr(case.guard)
                    self.emit('if_not_goto', guard, nxt)
                self.emit('goto', body_labels[i])
                self.emit_label(nxt)
            self.emit('goto', end_label)

            # case 体：fallthrough 时顺序掉落，默认执行完跳出
            for i, case in enumerate(stmt.cases):
                self.emit_label(body_labels[i])
                self.compile_block(case.body)
                if not fallthrough:
                    self.emit('goto', end_label)
            self.emit_label(end_label)
        finally:
            self.break_depth -= 1
            self.break_label, self.continue_label = old_break, old_continue

    # ---------- with ----------

    def compile_with(self, stmt):
        if self.aot_mode:
            return self._compile_with_aot(stmt)
        end_labels = [self.new_label() for _ in stmt.items]
        for (expr, name), end in zip(stmt.items, end_labels):
            src = self.compile_expr(expr)
            obj = self.new_temp('any')
            self.emit('with_begin', obj, src, name, end)
        self.compile_block(stmt.body)
        for end in reversed(end_labels):
            self.emit('with_end')
            self.emit_label(end)

    # ---------- 二元运算 ----------

    @staticmethod
    def _infer_binop_type(op, lt_type, rt_type):
        numeric = {'int', 'i64', 'float', 'double'}
        if op in ('+', '-', '*', '/', '//', '%', '**'):
            if lt_type in numeric and rt_type in numeric:
                if 'double' in (lt_type, rt_type):
                    return 'double'
                if 'float' in (lt_type, rt_type):
                    return 'float'
                if 'i64' in (lt_type, rt_type):
                    return 'i64'
                return 'int'
            if op == '+' and lt_type == 'str' and rt_type == 'str':
                return 'str'
            if op == '+' and lt_type == 'list' and rt_type == 'list':
                return 'list'
            return None
        if op in ('==', '=', '!=', '<', '>', '<=', '>=', 'and', 'or'):
            return 'bool'
        if op in ('|', '&', '^', '<<', '>>'):
            if lt_type in numeric and rt_type in numeric:
                return 'i64' if 'i64' in (lt_type, rt_type) else 'int'
            return None
        return None

    @staticmethod
    def _can_inline(lt_type, rt_type):
        builtin = {'int', 'i64', 'float', 'double', 'str', 'bool', 'list',
                   'dict', 'tuple', 'unknown', 'null', 'none'}
        return lt_type in builtin and rt_type in builtin

    def compile_binary(self, expr):
        op = expr.op
        lt = self.compile_expr(expr.left)
        rt = self.compile_expr(expr.right)

        if op == 'and':
            t = self.new_temp('bool')
            end_label = self.new_label()
            end2 = self.new_label()
            self.emit('jz', lt, end_label)
            self.emit('assign', t, rt)
            self.emit('goto', end2)
            self.emit_label(end_label)
            self.emit('assign', t, lt)
            self.emit_label(end2)
            return t

        if op == 'or':
            t = self.new_temp('bool')
            end_label = self.new_label()
            end2 = self.new_label()
            self.emit('jnz', lt, end_label)
            self.emit('assign', t, rt)
            self.emit('goto', end2)
            self.emit_label(end_label)
            self.emit('assign', t, lt)
            self.emit_label(end2)
            return t

        lt_type = self.get_type(lt)
        rt_type = self.get_type(rt)
        result_type = self._infer_binop_type(op, lt_type, rt_type)

        method_map = {
            '+': ('__add__', '__radd__', 'add'),
            '-': ('__sub__', '__rsub__', 'sub'),
            '*': ('__mul__', '__rmul__', 'mul'),
            '/': ('__div__', '__rdiv__', 'div'),
            '//': ('__floordiv__', '__rfloordiv__', 'floordiv'),
            '%': ('__mod__', '__rmod__', 'mod'),
            '**': ('__pow__', '__rpow__', 'pow'),
            '=': ('__eq__', '__eq__', 'eq'),
            '==': ('__eq__', '__eq__', 'eq'),
            '!=': ('__ne__', '__ne__', 'ne'),
            '<': ('__lt__', '__gt__', 'lt'),
            '>': ('__gt__', '__lt__', 'gt'),
            '<=': ('__le__', '__ge__', 'le'),
            '>=': ('__ge__', '__le__', 'ge'),
            '|': ('__bor__', '__ror__', 'bor'),
            '&': ('__band__', '__rand__', 'band'),
            '^': ('__xor__', '__rxor__', 'xor'),
            '<<': ('__lshift__', '__rlshift__', 'lshift'),
            '>>': ('__rshift__', '__rrshift__', 'rshift'),
        }
        if op not in method_map:
            raise CompileError(f"未知二元运算符: {op}", expr)
        m, rm, native = method_map[op]
        return self._compile_binary_with_magic(lt, rt, m, rm, native,
                                               lt_type, rt_type, result_type)

    def _compile_binary_with_magic(self, lt, rt, method, reverse_method,
                                   native_op, lt_type, rt_type, result_type):
        if self._can_inline(lt_type, rt_type):
            t = self.new_temp(result_type)
            self.emit(native_op, t, lt, rt)
            return t

        t = self.new_temp(result_type)
        end_label = self.new_label()

        m = self.new_temp()
        self.emit('get_attr', m, lt, method)
        no_m = self.new_label()
        self.emit('jz', m, no_m)
        r = self.new_temp()
        self.emit('call', r, m, [rt], {})
        ni_1 = self.new_label()
        self.emit('jni', r, ni_1)
        self.emit('assign', t, r)
        self.emit('goto', end_label)
        self.emit_label(ni_1)
        self.emit_label(no_m)

        rm = self.new_temp()
        self.emit('get_attr', rm, rt, reverse_method)
        no_rm = self.new_label()
        self.emit('jz', rm, no_rm)
        r2 = self.new_temp()
        self.emit('call', r2, rm, [lt], {})
        ni_2 = self.new_label()
        self.emit('jni', r2, ni_2)
        self.emit('assign', t, r2)
        self.emit('goto', end_label)
        self.emit_label(ni_2)
        self.emit_label(no_rm)

        self.emit(native_op, t, lt, rt)
        self.emit_label(end_label)
        return t

    def _compile_code_object(self, source):
        """AOT：把静态代码字符串编译成隐藏函数，run() 返回其变量字典。"""
        try:
            program = Parser(split_token(source)).parse()
        except Exception as e:
            raise CompileError(f"code 字符串编译失败: {e}")
        root = self._root()
        root.code_counter += 1
        name = f"__code_{root.code_counter}"
        sub = Compiler(parent=self, aot_mode=True)
        sub.code_object_mode = True
        for stmt in program.body:
            sub.compile_stmt(stmt)
        d = sub.new_temp('dict')
        sub.emit('new_dict', d)
        for vname in sub.assigned_order:
            key = sub.new_temp('str')
            sub.emit('const', key, vname)
            val = sub.new_temp()
            sub.emit('var', val, vname)
            sub.emit('dict_set', d, key, val)
        sub.emit('return', d)
        sub.emit('halt')
        self.emit('func', name, sub.code, [], None, None, 'any', {})
        t = self.new_temp('code')
        self.emit('make_code', t, name)
        return t

    # ---------- 表达式（Value 版） ----------

    def compile_variable(self, name):
        """变量读取：当前作用域 -> using 成员 -> 全局变量"""
        if self.aot_mode and name in self.force_globals:
            return self._emit_global_get(self.force_globals[name])
        if self.aot_mode and name in self.base_names:
            self_tmp = self.new_temp('any')
            self.emit('var', self_tmp, 'self')
            t = self.new_temp('any')
            self.emit('base_view', t, self_tmp, name)
            return t
        vt = self.var_types.get(name)
        t = self.new_temp(vt)
        known = (name in self.var_types
                 or name in self.bound_names
                 or name in self.func_names
                 or name in self.known_classes
                 or name in ('global', 'last'))
        if known or not self.using_stack:
            self.emit('var', t, name)
            return t

        # using 链：后 using 的成员优先
        end_label = self.new_label()
        fallback_label = self.new_label()
        stack = list(reversed(self.using_stack))
        for i, obj_tmp in enumerate(stack):
            v = self.new_temp('any')
            found = self.new_temp('bool')
            self.emit('get_attr_opt', v, obj_tmp, name, found)
            nxt = self.new_label() if i < len(stack) - 1 else fallback_label
            self.emit('jz', found, nxt)
            self.emit('assign', t, v)
            self.emit('goto', end_label)
            if i < len(stack) - 1:
                self.emit_label(nxt)
        self.emit_label(fallback_label)
        self.emit('var', t, name)
        self.emit_label(end_label)
        return t

    def compile_expr(self, expr):
        # @@X(code) -> X().__exec__(code)：纯语法糖，走普通 IR
        if isinstance(expr, ExternalExec):
            return self.compile_expr(expr.desugar())

        if isinstance(expr, Literal):
            v = expr.value
            t = self.new_temp(self._infer_literal_type(v))
            self.emit('const', t, v)
            return t

        if isinstance(expr, Variable):
            return self.compile_variable(expr.name)

        if isinstance(expr, Binary):
            return self.compile_binary(expr)

        if isinstance(expr, Unary):
            src = self.compile_expr(expr.right)
            src_type = self.get_type(src)
            t = self.new_temp(src_type)
            if expr.op in ('-', '~'):
                method = '__neg__' if expr.op == '-' else '__invert__'
                native = 'neg' if expr.op == '-' else 'invert'
                m = self.new_temp()
                self.emit('get_attr', m, src, method)
                no_m = self.new_label()
                end_label = self.new_label()
                self.emit('jz', m, no_m)
                r = self.new_temp()
                self.emit('call', r, m, [], {})
                self.emit('jni', r, no_m)
                self.emit('assign', t, r)
                self.emit('goto', end_label)
                self.emit_label(no_m)
                self.emit(native, t, src)
                self.emit_label(end_label)
            elif expr.op in ('!', 'not'):
                self.emit('not', t, src)
            else:
                raise CompileError(f"未知一元运算符: {expr.op}", expr)
            return t

        if isinstance(expr, Assign):
            val = self.compile_expr(expr.value)
            val_type = self.get_type(val)
            if isinstance(expr.name, Variable):
                name = expr.name.name
                self._mark_assigned(name)
                known = (name in self.var_types
                         or name in self.bound_names
                         or name in self.func_names
                         or name in self.known_classes
                         or name in ('global', 'last'))
                if not known and self.using_stack:
                    # using obj 下的写操作 == obj.name <- value
                    self.emit('set_attr', self.using_stack[-1], name, val)
                    return val
                if self.aot_mode and name in self.force_globals:
                    self._emit_global_set(self.force_globals[name], val)
                    if val_type:
                        self.var_types[name] = val_type
                    return val
                target = self.var_types.get(name)
                if target:
                    val = self._maybe_convert(val, target)
                self.emit('store', name, val)
                if val_type:
                    self.var_types[name] = val_type
            elif isinstance(expr.name, Attribute):
                if self.aot_mode and isinstance(expr.name.obj, Variable) \
                        and expr.name.obj.name == 'global':
                    self.emit('global_set', expr.name.attr, val)
                    return val
                if self.aot_mode and isinstance(expr.name.obj, Variable) \
                        and expr.name.obj.name == 'last':
                    self.emit('global_set',
                              self.last_name_map.get(expr.name.attr,
                                                     expr.name.attr), val)
                    return val
                obj = self.compile_expr(expr.name.obj)
                self.emit('set_attr', obj, expr.name.attr, val)
            elif isinstance(expr.name, Subscript):
                obj_src = self.compile_expr(expr.name.obj)
                idx_src = self.compile_expr(expr.name.index)
                self.emit('set_item', obj_src, idx_src, val)
            return val

        if isinstance(expr, Call):
            if (isinstance(expr.func, Variable)
                    and expr.func.name == 'code'
                    and len(expr.args) == 1 and not expr.kwargs
                    and not isinstance(expr.args[0], Starred)
                    and isinstance(expr.args[0], Literal)
                    and isinstance(expr.args[0].value, str)):
                if self.aot_mode:
                    return self._compile_code_object(expr.args[0].value)
                src = expr.args[0].value
                t = self.new_temp('code')
                self.emit('new_code', t, src)
                return t
            if (isinstance(expr.func, Variable)
                    and expr.func.name == 'code'
                    and len(expr.args) == 1 and not expr.kwargs
                    and not isinstance(expr.args[0], Starred)
                    and not (isinstance(expr.args[0], Literal)
                             and isinstance(expr.args[0].value, str))):
                if self.aot_mode:
                    arg = self.compile_expr(expr.args[0])
                    t = self.new_temp('code')
                    self.emit('builtin_call', t, 'code', [arg], {})
                    return t
                arg = self.compile_expr(expr.args[0])
                t = self.new_temp('code')
                self.emit('new_code_var', t, arg)
                return t

            has_spread = any(isinstance(a, Starred) for a in expr.args)
            ret_type = self._call_ret_type(expr.func)

            if has_spread:
                pos = self.new_temp('list')
                self.emit('new_list', pos)
                for a in expr.args:
                    if isinstance(a, Starred):
                        src = self.compile_expr(a.value)
                        if a.double:
                            continue
                        self.emit('list_extend', pos, src)
                    else:
                        self.emit('list_append', pos,
                                  self.compile_expr(a))
                kwd = self.new_temp('dict')
                self.emit('new_dict', kwd)
                for k, v in expr.kwargs:
                    self.emit('dict_set', kwd, self._const_str(k),
                              self.compile_expr(v))
                for a in expr.args:
                    if isinstance(a, Starred) and a.double:
                        self.emit('dict_update_strict', kwd,
                                  self.compile_expr(a.value))
                func = self.compile_expr(expr.func)
                t = self.new_temp(ret_type)
                self.emit('call_spread', t, func, pos, kwd)
                return t

            fname = expr.func.name if isinstance(expr.func, Variable) else None
            if fname in self.known_structs and not expr.args and not expr.kwargs:
                t = self.new_temp(fname)
                self.emit('struct_new', t, fname)
                return t
            if fname == 'sizeof' and len(expr.args) == 1:
                arg = expr.args[0]
                if isinstance(arg, Variable) and arg.name in self.struct_layouts:
                    size = self.struct_layouts[arg.name]['size']
                    t = self.new_temp('int')
                    self.emit('const', t, size)
                    return t
            if fname in self.BUILTIN_CALLS and not expr.kwargs:
                if fname == 'free' and len(expr.args) == 1:
                    target = expr.args[0]
                    t = self.new_temp('null')
                    if isinstance(target, Variable):
                        self.compile_expr(target)
                        self.emit('free_var', t, target.name)
                        return t
                    if isinstance(target, Subscript):
                        cont = self.compile_expr(target.obj)
                        idx = self.compile_expr(target.index)
                        self.emit('free_item', t, cont, idx)
                        return t
                    if isinstance(target, Attribute):
                        obj = self.compile_expr(target.obj)
                        self.emit('free_attr', t, obj, target.attr)
                        return t
                args = [self.compile_expr(a) for a in expr.args]
                if fname == 'free':
                    t = self.new_temp('none')
                    self.emit('free', t, args)
                    return t
                t = self.new_temp(self.BUILTIN_RET_TYPES.get(fname))
                self.emit(fname, t, args)
                return t
            if fname in self.VM_BUILTINS:
                args = [self.compile_expr(a) for a in expr.args]
                kwargs = {k: self.compile_expr(v) for k, v in expr.kwargs}
                t = self.new_temp(self.BUILTIN_RET_TYPES.get(fname))
                self.emit('builtin_call', t, fname, args, kwargs)
                return t

            func = self.compile_expr(expr.func)
            args = [self.compile_expr(a) for a in expr.args]
            kwargs = {k: self.compile_expr(v) for k, v in expr.kwargs}
            t = self.new_temp(ret_type)
            self.emit('call', t, func, args, kwargs)
            return t

        if isinstance(expr, AddressOf):
            src = self.compile_expr(expr.right)
            t = self.new_temp('ptr')
            self.emit('address_of', t, src)
            return t

        if isinstance(expr, Attribute):
            if self.aot_mode and isinstance(expr.obj, Variable) \
                    and expr.obj.name == 'global':
                t = self.new_temp(None)
                self.emit('global_get', t, expr.attr)
                return t
            if self.aot_mode and isinstance(expr.obj, Variable) \
                    and expr.obj.name == 'last':
                t = self.new_temp(None)
                self.emit('global_get',
                          t, self.last_name_map.get(expr.attr, expr.attr))
                return t
            obj = self.compile_expr(expr.obj)
            t = self.new_temp(None)
            self.emit('get_attr', t, obj, expr.attr)
            return t

        if isinstance(expr, IfExpr):
            cond = self.compile_expr(expr.condition)
            t = self.new_temp(None)
            else_label = self.new_label()
            end_label = self.new_label()
            self.emit('if_not_goto', cond, else_label)
            then_src = self.compile_expr(expr.then_expr)
            self.emit('assign', t, then_src)
            self.emit('goto', end_label)
            self.emit_label(else_label)
            else_src = self.compile_expr(expr.else_expr)
            self.emit('assign', t, else_src)
            self.emit_label(end_label)
            return t

        if isinstance(expr, ListExpr):
            t = self.new_temp('list')
            self.emit('new_list', t)
            for e in expr.elements:
                if isinstance(e, Starred):
                    self.emit('list_extend', t, self.compile_expr(e.value))
                else:
                    self.emit('list_append', t, self.compile_expr(e))
            return t

        if isinstance(expr, TupleExpr):
            ltmp = self.new_temp('list')
            self.emit('new_list', ltmp)
            for e in expr.elements:
                if isinstance(e, Starred):
                    self.emit('list_extend', ltmp, self.compile_expr(e.value))
                else:
                    self.emit('list_append', ltmp, self.compile_expr(e))
            t = self.new_temp('tuple')
            self.emit('to_tuple', t, ltmp)
            return t

        if isinstance(expr, DictExpr):
            t = self.new_temp('dict')
            self.emit('new_dict', t)
            for k, v in expr.items:
                if isinstance(k, Starred):
                    self.emit('dict_update', t, self.compile_expr(k.value))
                else:
                    k_src = self.compile_expr(k)
                    v_src = self.compile_expr(v)
                    self.emit('dict_set', t, k_src, v_src)
            return t

        if isinstance(expr, Subscript):
            obj = self.compile_expr(expr.obj)
            idx = self.compile_expr(expr.index)
            t = self.new_temp(None)
            obj_type = self.get_type(obj)
            if obj_type in ('list', 'dict', 'tuple', 'str'):
                self.emit('get_item', t, obj, idx)
                return t
            # 先尝试 __getitem__ 魔术方法，再退回原生下标
            m = self.new_temp()
            self.emit('get_attr', m, obj, '__getitem__')
            no_m = self.new_label()
            end_label = self.new_label()
            self.emit('jz', m, no_m)
            r = self.new_temp()
            self.emit('call', r, m, [idx], {})
            self.emit('assign', t, r)
            self.emit('goto', end_label)
            self.emit_label(no_m)
            self.emit('get_item', t, obj, idx)
            self.emit_label(end_label)
            return t

        if isinstance(expr, RangeExpr):
            start = self.compile_expr(expr.start)
            end = self.compile_expr(expr.end)
            step = self.compile_expr(expr.step) if expr.step else None
            t = self.new_temp('list')
            self.emit('new_range', t, start, end, expr.inclusive, step)
            return t

        if isinstance(expr, CodeLiteral):
            if self.aot_mode:
                return self._compile_code_object(expr.source)
            t = self.new_temp('code')
            self.emit('new_code', t, expr.source)
            return t

        if isinstance(expr, Swap):
            lt = self.new_temp(None)
            self.emit('var', lt, expr.left)
            rt = self.new_temp(None)
            self.emit('var', rt, expr.right)
            self.emit('store', expr.left, rt)
            self.emit('store', expr.right, lt)
            return lt

        raise NotImplementedError(f"未实现的表达式: {type(expr).__name__}")

    def _const_str(self, s):
        t = self.new_temp('str')
        self.emit('const', t, s)
        return t

    def _call_ret_type(self, func):
        if isinstance(func, Variable):
            sigs = self.func_sigs.get(func.name)
            if sigs:
                return sigs[0][1]
            return self.BUILTIN_RET_TYPES.get(func.name)
        return None

    @staticmethod
    def _infer_literal_type(value):
        if isinstance(value, bool):
            return 'bool'
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
        if isinstance(value, tuple):
            return 'tuple'
        if isinstance(value, list):
            return 'list'
        if isinstance(value, dict):
            return 'dict'
        if value is None:
            return 'none'
        if value is NULL:
            return 'null'
        if value is UNKNOWN:
            return 'unknown'
        return None

    # ---------- 表达式（int 特化） ----------

    def compile_expr_int(self, expr):
        if isinstance(expr, ExternalExec):
            raise CompileError(
                "int_mode 不支持 @@ 外部语言执行", expr)

        if isinstance(expr, Literal):
            if isinstance(expr.value, bool):
                return ('imm', int(expr.value))
            return ('imm', expr.value)

        if isinstance(expr, Variable):
            return ('reg', expr.name)

        if isinstance(expr, Binary):
            op = expr.op
            if op in ('+', '-', '*', '<', '>', '<=', '>=', '==', '!='):
                lt = self.compile_expr_int(expr.left)
                rt = self.compile_expr_int(expr.right)
                t = self.new_temp('int')
                int_op = {
                    '+': 'int_add', '-': 'int_sub', '*': 'int_mul',
                    '<': 'int_lt', '>': 'int_gt', '<=': 'int_le',
                    '>=': 'int_ge', '==': 'int_eq', '!=': 'int_ne',
                }[op]
                self.emit(int_op, t, lt, rt)
                return ('tmp', t)
            if op == 'and':
                lt = self.compile_expr_int(expr.left)
                rt = self.compile_expr_int(expr.right)
                t = self.new_temp('int')
                self.emit('int_and', t, lt, rt)
                return ('tmp', t)
            if op == 'or':
                lt = self.compile_expr_int(expr.left)
                rt = self.compile_expr_int(expr.right)
                t = self.new_temp('int')
                self.emit('int_or', t, lt, rt)
                return ('tmp', t)
            raise CompileError(f"int_mode 不支持的二元运算符: {op}", expr)

        if isinstance(expr, Unary):
            src = self.compile_expr_int(expr.right)
            t = self.new_temp('int')
            if expr.op == '-':
                self.emit('int_neg', t, src)
            elif expr.op == 'not':
                self.emit('int_not', t, src)
            else:
                raise CompileError(f"int_mode 不支持的一元运算符: {expr.op}", expr)
            return ('tmp', t)

        if isinstance(expr, IfExpr):
            cond = self.compile_expr_int(expr.condition)
            t = self.new_temp('int')
            else_label = self.new_label()
            end_label = self.new_label()
            self.emit('int_jz', cond, else_label)
            then_src = self.compile_expr_int(expr.then_expr)
            self.emit('int_assign', t, then_src)
            self.emit('goto', end_label)
            self.emit_label(else_label)
            else_src = self.compile_expr_int(expr.else_expr)
            self.emit('int_assign', t, else_src)
            self.emit_label(end_label)
            return ('tmp', t)

        if isinstance(expr, Assign):
            val = self.compile_expr_int(expr.value)
            if isinstance(expr.name, Variable):
                self.emit('int_store_var', expr.name.name, val)
            return val

        if isinstance(expr, Call):
            if isinstance(expr.func, Variable):
                fname = expr.func.name
                if fname == self._int_func_name:
                    args = [self.compile_expr_int(a) for a in expr.args]
                    t = self.new_temp('int')
                    self.emit('int_call', t, fname, args)
                    return ('tmp', t)
            raise CompileError(
                "int_mode 不支持的调用（只支持递归调用自身）", expr)

        raise CompileError(f"int_mode 不支持的表达式: {type(expr).__name__}", expr)
