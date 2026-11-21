from errors import EclException, LexerError, ReturnSignal
from class_and_vars import (NULL, UNKNOWN, Cell, EclInstance, Program,
                            EclFunction, EclClass, EclOverload, BaseProxy,
                            ScopeView, I64, EclStruct)


class GlobalView:
    """code 对象执行后返回的全局视图"""

    def __init__(self, cells, interpreter):
        self.cells = cells          # dict[str, Cell]
        self.interpreter = interpreter

    def get(self, name):
        if name in self.cells:
            return self.cells[name].value
        return None

    def set(self, name, value):
        if name in self.cells:
            self.cells[name].value = value
        else:
            self.cells[name] = Cell(value)

    def keys(self):
        return list(self.cells.keys())

    def __repr__(self):
        return f"<global {list(self.cells.keys())}>"


class CodeObject:
    """code 类型的值：延迟解析的代码块"""

    def __init__(self, source, scope, interpreter, line=0, col=0):
        self.source = source
        self.scope = scope
        self.interpreter = interpreter
        self.line = line
        self.col = col
        self._stmts = None

    def _parse(self):
        if self._stmts is not None:
            return self._stmts
        try:
            from token_parse import split_token
            from ast_parse import Parser
            tokens = split_token(self.source)
            parser = Parser(tokens)
            program = parser.parse()
        except (LexerError, Exception) as e:
            self.interpreter.error(
                f"code 对象解析失败: {e}", self, 'SyntaxError')
        self._stmts = program.body
        return self._stmts

    def run(self):
        from compiler import Compiler
        stmts = self._parse()
        code_globals = {}
        parent_scope = dict(self.scope)
        for k, v in parent_scope.items():
            code_globals[k] = v
        for k, v in self.interpreter.globals.items():
            if k not in code_globals:
                code_globals[k] = v
        compiler = Compiler()
        code, exception_table = compiler.compile_program(Program(stmts))
        vm = VM(code, exception_table, code_globals,
                self.interpreter, parent_scope=parent_scope)
        vm.run()
        new_cells = {k: v for k, v in code_globals.items()
                     if k not in parent_scope
                     and k not in self.interpreter.globals}
        return GlobalView(new_cells, self.interpreter)

    def __repr__(self):
        return f"<code object at {self.line}:{self.col}>"


class VM:
    def __init__(self, code, exception_table, scope, interpreter,
                 parent_scope=None):
        self.code = code
        self.exception_table = exception_table or []
        self.scope = scope
        self.interpreter = interpreter
        if parent_scope is None:
            self.parent_scope = []
        elif isinstance(parent_scope, dict):
            self.parent_scope = [parent_scope]
        else:
            self.parent_scope = list(parent_scope)
        self.temps = {}
        self.pc = 0
        self.current_exc = None
        self.with_stack = []
        self.labels = {}
        for i, op in enumerate(code):
            if op[0] == 'label':
                self.labels[op[1]] = i + 1
        # 解析异常表里的标签
        for entry in self.exception_table:
            if 'handler_pc' not in entry:
                label = entry.get('handler_label')
                entry['handler_pc'] = self.labels.get(label)

    # ---------- 执行循环 ----------

    def run(self):
        while self.pc < len(self.code):
            try:
                op = self.code[self.pc]
                self.pc += 1
                self.exec_op(op)
            except ReturnSignal:
                raise
            except EclException as e:
                if self._unwind_with_for_exception(e):
                    continue
                if not self.handle_exception(e):
                    raise

    def handle_exception(self, e):
        self.current_exc = e
        for entry in sorted(self.exception_table,
                            key=lambda x: x.get('try_start', 0),
                            reverse=True):
            start = entry.get('try_start', 0)
            end = entry.get('try_end', -1)
            if not (start <= self.pc <= end):
                continue
            exc_type = entry.get('exc_type')
            if exc_type is not None and not self.interpreter.exc_matches(
                    e.exc_type, exc_type):
                continue
            handler = entry.get('handler_pc')
            if handler is None:
                continue
            if entry.get('exc_name'):
                self.scope[entry['exc_name']] = Cell(e)
            self.pc = handler
            return True
        return False

    # ---------- 变量 / 作用域 ----------

    def _scope_cell(self, name):
        for scope in [self.scope, self.interpreter.globals]:
            if name in scope:
                return scope[name], scope
        return None, None

    def get_var(self, name):
        cell, _ = self._scope_cell(name)
        if cell is not None:
            return cell.value
        # 闭包：上层作用域（last）
        for parent in self.parent_scope:
            if name in parent:
                return parent[name].value
        if name == 'global':
            return GlobalView(self.interpreter.globals, self.interpreter)
        if name == 'last':
            return ScopeView(self.parent_scope[0] if self.parent_scope else {})
        return UNKNOWN

    def set_var(self, name, value):
        # 赋值优先写当前作用域；函数内不会隐式修改全局变量
        cell = self.scope.get(name)
        if cell is not None:
            if cell.is_const:
                self.interpreter.error(
                    f"不能重新赋值常量 '{name}'", None, 'ConstChangingError')
            cell.value = value
            return
        self.scope[name] = Cell(value)

    # ---------- with 上下文 ----------

    def _call_leave(self, ctx, exc_type, exc_value):
        result = self.interpreter.call_value(
            ctx['leave'], [exc_type, exc_value], {})
        return self.interpreter._truthy(result)

    def _unwind_with_for_exception(self, e):
        while self.with_stack:
            ctx = self.with_stack.pop()
            exc_type = self.interpreter.exception_type(e.exc_type)
            if self._call_leave(ctx, exc_type, e):
                self.pc = ctx['end_pc']
                return True
        return False

    def _unwind_all_with(self):
        while self.with_stack:
            ctx = self.with_stack.pop()
            self._call_leave(ctx, None, None)

    def _unwind_with_for_jump(self, target):
        while self.with_stack:
            ctx = self.with_stack[-1]
            if ctx['start_pc'] <= target < ctx['end_pc']:
                return
            self.with_stack.pop()
            self._call_leave(ctx, None, None)

    def _jump(self, label):
        target = self.labels[label]
        self._unwind_with_for_jump(target)
        self.pc = target

    def _do_return(self, value):
        self._unwind_all_with()
        raise ReturnSignal(value)

    # ---------- 指令 ----------

    def exec_op(self, op):
        kind = op[0]
        if kind in ('method_func', 'try', 'except', 'finally'):
            return
        if kind == 'func_int':
            self._register_int_func(op)
            return
        if kind == 'struct_def':
            self.interpreter.register_struct(op[1], op[2])
            return
        if kind == 'struct_new':
            self.temps[op[1]] = self.interpreter.new_struct(op[2])
            return
        if kind == 'dll_import':
            self.interpreter.install_dll(op[1], op[2], self.scope)
            return
        if kind == 'exports':
            return
        elif kind == 'end_try':
            # op = ('end_try', normal_label, finally_label)
            self._jump(op[1])
            return
        elif kind == 'const':
            _, dest, value = op
            self.temps[dest] = value
        elif kind == 'free':
            _, dest, args = op
            if args:
                self.temps[args[0]] = NULL
            self.temps[dest] = NULL
        elif kind == 'free_var':
            _, dest, name = op
            self.set_var(name, NULL)
            self.temps[dest] = NULL
        elif kind == 'var':
            _, dest, name = op
            self.temps[dest] = self.get_var(name)
        elif kind == 'store':
            _, name, src = op
            value = self.temps[src]
            if isinstance(value, EclStruct):
                value = value.clone()
            self.set_var(name, value)
        elif kind == 'ref':
            _, name, src_name = op
            cell, _ = self._scope_cell(src_name)
            if cell is None:
                self.interpreter.error(
                    f"ref 目标变量未定义: '{src_name}'", None, 'NameError')
            self.scope[name] = cell
        elif kind == 'assign':
            _, dest, src = op
            self.temps[dest] = self.temps[src]
        elif kind in ('add', 'sub', 'mul', 'div', 'floordiv', 'mod',
                      'pow', 'eq', 'ne', 'lt', 'gt', 'le', 'ge',
                      'bor', 'band', 'xor', 'lshift', 'rshift'):
            _, dest, s1, s2 = op
            self.temps[dest] = self.interpreter.native_binop(
                kind, self.temps[s1], self.temps[s2])
        elif kind == 'neg':
            _, dest, src = op
            self.temps[dest] = self.interpreter.native_unary(
                '-', self.temps[src])
        elif kind == 'invert':
            _, dest, src = op
            self.temps[dest] = self.interpreter.native_unary(
                '~', self.temps[src])
        elif kind == 'not':
            _, dest, src = op
            self.temps[dest] = not self.interpreter._truthy(self.temps[src])
        elif kind == 'convert_type':
            _, dest, src, type_name = op
            self.temps[dest] = self.interpreter.convert_type(
                self.temps[src], type_name)
        elif kind == 'to_bi':
            _, dest, src = op
            self.temps[dest] = self.interpreter.convert_type(
                self.temps[src], 'bi')
        elif kind == 'new_code_var':
            _, dest, src = op
            src_val = self.temps[src]
            if isinstance(src_val, str):
                self.temps[dest] = CodeObject(
                    src_val, self.scope, self.interpreter)
            else:
                self.interpreter.error(
                    f"code() 的参数必须是 str，得到 {type(src_val).__name__}",
                    None, 'TypeError')
        elif kind == 'goto':
            self._jump(op[1])
        elif kind in ('if_goto', 'if_not_goto', 'jz', 'jnz'):
            _, src, label = op
            truthy = self.interpreter._truthy(self.temps[src])
            take = {
                'if_goto': truthy,
                'if_not_goto': not truthy,
                'jnz': truthy,
                'jz': not truthy,
            }[kind]
            if take:
                self._jump(label)
        elif kind == 'jni':
            # 魔术方法返回 NotImplemented 时退回原生运算
            _, src, label = op
            if self.temps[src] is NotImplemented:
                self._jump(label)
        elif kind == 'label':
            pass
        elif kind == 'call':
            _, dest, func_src, args, kwargs = op
            func = self.temps[func_src]
            arg_vals = [self.temps[a] for a in args]
            kwarg_vals = {k: self.temps[v] for k, v in kwargs.items()}
            self.temps[dest] = self.interpreter.call_value(
                func, arg_vals, kwarg_vals)
        elif kind == 'call_spread':
            _, dest, func_src, pos_src, kw_src = op
            func = self.temps[func_src]
            pos = self.temps[pos_src]
            kwd = self.temps[kw_src]
            if not isinstance(pos, (list, tuple)):
                self.interpreter.error(
                    f"* 解包要求 list/tuple，得到 {type(pos).__name__}",
                    None, 'TypeError')
            if not isinstance(kwd, dict):
                self.interpreter.error(
                    f"** 解包要求 dict，得到 {type(kwd).__name__}",
                    None, 'TypeError')
            for k in kwd:
                if not isinstance(k, str):
                    self.interpreter.error(
                        f"** 解包的键必须是 str，得到 {type(k).__name__}",
                        None, 'TypeError')
            self.temps[dest] = self.interpreter.call_value(
                func, list(pos), dict(kwd))
        elif kind == 'builtin_call':
            _, dest, fname, args, kwargs = op
            fn_cell, _ = self._scope_cell(fname)
            if fn_cell is None:
                self.interpreter.error(
                    f"未知内置函数: '{fname}'", None, 'NameError')
            arg_vals = [self.temps[a] for a in args]
            kwarg_vals = {k: self.temps[v] for k, v in kwargs.items()}
            self.temps[dest] = self.interpreter.call_value(
                fn_cell.value, arg_vals, kwarg_vals)
        # ---------- 内置函数（AOT 兼容指令） ----------
        elif kind == 'print':
            _, dest, args = op
            out = [self.interpreter._builtin_str(self.temps[a]) for a in args]
            print(' '.join(out))
            self.temps[dest] = None
        elif kind == 'str':
            _, dest, args = op
            self.temps[dest] = self.interpreter._builtin_str(
                self.temps[args[0]]) if args else ""
        elif kind == 'len':
            _, dest, args = op
            self.temps[dest] = self.interpreter._builtin_len(
                self.temps[args[0]]) if args else 0
        elif kind == 'int':
            _, dest, args = op
            self.temps[dest] = self.interpreter._builtin_int(
                self.temps[args[0]]) if args else 0
        elif kind == 'float':
            _, dest, args = op
            self.temps[dest] = self.interpreter._builtin_float(
                self.temps[args[0]]) if args else 0.0
        elif kind == 'bool':
            _, dest, args = op
            self.temps[dest] = self.interpreter._builtin_bool(
                self.temps[args[0]]) if args else False
        elif kind == 'type':
            _, dest, args = op
            self.temps[dest] = self.interpreter._builtin_type(
                self.temps[args[0]]) if args else "none"
        elif kind == 'range':
            _, dest, args = op
            vals = [self.temps[a] for a in args]
            self.temps[dest] = self.interpreter._builtin_range(*vals)
        elif kind in ('return', 'return_none', 'return_null'):
            if kind == 'return':
                self._do_return(self.temps[op[1]])
            elif kind == 'return_none':
                self._do_return(None)
            else:
                self._do_return(NULL)
        # ---------- 容器 ----------
        elif kind == 'new_list':
            _, dest = op
            self.temps[dest] = []
        elif kind == 'iter_list':
            _, dest, src = op
            val = self.temps[src]
            if isinstance(val, dict):
                self.temps[dest] = list(val.keys())
            elif isinstance(val, (list, tuple, str)):
                self.temps[dest] = list(val)
            else:
                self.interpreter.error(
                    f"{self.interpreter._builtin_type(val)} 不可迭代",
                    None, 'TypeError')
        elif kind == 'list_append':
            _, dest, src = op
            self.temps[dest].append(self.temps[src])
        elif kind == 'list_extend':
            _, dest, src = op
            src_val = self.temps[src]
            if not isinstance(src_val, (list, tuple)):
                self.interpreter.error(
                    f"* 解包要求 list/tuple，得到 {type(src_val).__name__}",
                    None, 'TypeError')
            self.temps[dest].extend(src_val)
        elif kind == 'new_dict':
            _, dest = op
            self.temps[dest] = {}
        elif kind == 'dict_set':
            _, dest, key_src, val_src = op
            self.temps[dest][self.temps[key_src]] = self.temps[val_src]
        elif kind == 'dict_update':
            _, dest, src = op
            src_val = self.temps[src]
            if not isinstance(src_val, dict):
                self.interpreter.error(
                    f"** 解包要求 dict，得到 {type(src_val).__name__}",
                    None, 'TypeError')
            for k in src_val:
                if not isinstance(k, str):
                    self.interpreter.error(
                        f"** 解包的键必须是 str，得到 {type(k).__name__}",
                        None, 'TypeError')
            self.temps[dest].update(src_val)
        elif kind == 'dict_update_strict':
            _, dest, src = op
            src_val = self.temps[src]
            if not isinstance(src_val, dict):
                self.interpreter.error(
                    f"** 解包要求 dict，得到 {type(src_val).__name__}",
                    None, 'TypeError')
            for k, v in src_val.items():
                if not isinstance(k, str):
                    self.interpreter.error(
                        f"** 解包的键必须是 str，得到 {type(k).__name__}",
                        None, 'TypeError')
                if k in self.temps[dest]:
                    self.interpreter.error(
                        f"关键字参数重复: '{k}'", None, 'TypeError')
            self.temps[dest].update(src_val)
        elif kind == 'to_tuple':
            _, dest, src = op
            self.temps[dest] = tuple(self.temps[src])
        elif kind == 'expect_len':
            _, src, expected = op
            val = self.temps[src]
            try:
                actual = len(val)
            except TypeError:
                self.interpreter.error(
                    "固定长度 list 需要可 len 的值", None, 'TypeError')
            if actual != expected:
                self.interpreter.error(
                    f"固定长度 list 需要 {expected} 个元素，得到 {actual} 个",
                    None, 'ValueError')
        elif kind == 'new_range':
            _, dest, start, end, inclusive, step = op
            s = int(self.temps[start])
            e = int(self.temps[end])
            st = int(self.temps[step]) if step else 1
            stop = e + (1 if inclusive else 0)
            self.temps[dest] = list(range(s, stop, st))
        elif kind == 'get_item':
            _, dest, obj, idx = op
            obj_val = self.temps[obj]
            idx_val = self.temps[idx]
            try:
                self.temps[dest] = obj_val[idx_val]
            except (IndexError, KeyError) as e:
                self.interpreter.error(
                    f"下标访问错误: {e}", None, type(e).__name__)
            except TypeError as e:
                self.interpreter.error(
                    f"对象不支持下标访问: {e}", None, 'TypeError')
        elif kind == 'set_item':
            _, obj, idx, src = op
            try:
                self.temps[obj][self.temps[idx]] = self.temps[src]
            except (IndexError, KeyError) as e:
                self.interpreter.error(
                    f"下标赋值错误: {e}", None, type(e).__name__)
            except TypeError as e:
                self.interpreter.error(
                    f"对象不支持下标赋值: {e}", None, 'TypeError')
        elif kind == 'get_attr':
            _, dest, obj, name = op
            self.temps[dest] = self.interpreter.get_attr(
                self.temps[obj], name)
        elif kind == 'address_of':
            _, dest, src = op
            self.temps[dest] = self.interpreter.address_of(self.temps[src])
        elif kind == 'get_attr_opt':
            _, dest, obj, name, found = op
            result = self.interpreter.get_attr_opt(self.temps[obj], name)
            self.temps[dest] = result
            self.temps[found] = result is not NULL
        elif kind == 'set_attr':
            _, obj, name, src = op
            self.interpreter.set_attr(self.temps[obj], name, self.temps[src])
        # ---------- 函数 / 类 ----------
        elif kind == 'func':
            _, name, sub_code, params, varargs, kwargs_name, ret_type, \
                defaults = op
            fn = EclFunction(name, sub_code, params, varargs, kwargs_name,
                             ret_type, self.interpreter, defaults,
                             closure=[self.scope] + self.parent_scope)
            old = self.scope.get(name)
            if old is not None and isinstance(old.value, EclFunction):
                ov = EclOverload(name, self.interpreter)
                ov.add(old.value)
                ov.add(fn)
                self.scope[name] = Cell(ov)
            elif old is not None and isinstance(old.value, EclOverload):
                old.value.add(fn)
            else:
                self.scope[name] = Cell(fn)
        elif kind == 'class':
            _, name, bases, methods = op
            base_classes = []
            for b in bases:
                bv = self.get_var(b)
                if isinstance(bv, EclClass):
                    base_classes.append(bv)
            cls = EclClass(name, base_classes, methods, self.interpreter)
            self.scope[name] = Cell(cls)
        elif kind == 'new_code':
            _, dest, source = op
            self.temps[dest] = CodeObject(source, self.scope,
                                          self.interpreter)
        # ---------- using ----------
        elif kind == 'using_enter':
            _, dest, src = op
            val = self.temps[src]
            if not isinstance(val, (EclInstance, EclClass, BaseProxy)):
                self.interpreter.error(
                    f"using 的对象必须是实例或类，得到 "
                    f"{self.interpreter._builtin_type(val)}",
                    None, 'TypeError')
            self.temps[dest] = val
        # ---------- with ----------
        elif kind == 'with_begin':
            _, dest, src, name, end_label = op
            obj = self.temps[src]
            enter = self.interpreter.get_attr_opt(obj, '__enter__')
            leave = self.interpreter.get_attr_opt(obj, '__leave__')
            if enter is NULL or leave is NULL:
                self.interpreter.error(
                    "with 的对象必须实现 __enter__ 和 __leave__",
                    None, 'TypeError')
            entered = self.interpreter.call_value(enter, [], {})
            self.temps[dest] = entered
            if name:
                self.set_var(name, entered)
            self.with_stack.append({
                'obj': obj,
                'leave': leave,
                'name': name,
                'start_pc': self.pc,
                'end_pc': self.labels.get(end_label, len(self.code)),
            })
        elif kind == 'with_end':
            if self.with_stack:
                ctx = self.with_stack.pop()
                self._call_leave(ctx, None, None)
        # ---------- 异常 ----------
        elif kind == 'raise':
            _, src = op
            exc = self.temps[src]
            if isinstance(exc, EclException):
                raise exc
            if hasattr(exc, 'exc_type'):
                msg = getattr(exc, 'message', str(exc))
                raise EclException(exc.exc_type, msg)
            raise EclException('Exception', str(exc))
        elif kind == 'reraise':
            if self.current_exc is not None:
                raise self.current_exc
            self.interpreter.error("没有可重新抛出的异常", None, 'RuntimeError')
        elif kind == 'exc_match':
            _, dest, type_name = op
            if self.current_exc is None:
                self.temps[dest] = False
            elif type_name is None:
                self.temps[dest] = True
            else:
                self.temps[dest] = self.interpreter.exc_matches(
                    self.current_exc.exc_type, type_name)
        elif kind == 'store_exc':
            _, name = op
            self.set_var(name, self.current_exc)
        # ---------- match ----------
        elif kind == 'match_eq':
            _, dest, src, pat = op
            self.temps[dest] = self.interpreter.match_eq(
                self.temps[src], self.temps[pat])
        elif kind == 'match_multi':
            _, dest, src, values = op
            v = self.temps[src]
            self.temps[dest] = any(
                self.interpreter.match_eq(v, p) for p in values)
        elif kind == 'is_type':
            _, dest, src, type_name = op
            self.temps[dest] = self.interpreter.is_type(
                self.temps[src], type_name)
        # ---------- 赋值解包 ----------
        elif kind == 'unpack_assign':
            _, src, targets = op
            self._unpack_assign(self.temps[src], targets)
        elif kind == 'halt':
            self.pc = len(self.code)
        else:
            raise EclException('RuntimeError', f"未知指令: {kind}")

    def _unpack_assign(self, value, targets):
        if not isinstance(value, (list, tuple)):
            self.interpreter.error(
                f"赋值解包要求 list/tuple，得到 "
                f"{self.interpreter._builtin_type(value)}",
                None, 'TypeError')
        seq = list(value)
        star_idx = None
        for i, (name, star) in enumerate(targets):
            if star:
                star_idx = i
                break
        if star_idx is None:
            if len(seq) < len(targets):
                self.interpreter.error(
                    f"赋值解包元素不足: 需要 {len(targets)} 个，"
                    f"得到 {len(seq)} 个", None, 'ValueError')
            if len(seq) != len(targets):
                self.interpreter.error(
                    f"赋值解包元素数量不匹配: 需要 {len(targets)} 个，"
                    f"得到 {len(seq)} 个", None, 'ValueError')
            for (name, _), v in zip(targets, seq):
                self.set_var(name, v)
            return
        pre = targets[:star_idx]
        post = targets[star_idx + 1:]
        if len(seq) < len(pre) + len(post):
            self.interpreter.error(
                f"赋值解包元素不足: 至少需要 {len(pre) + len(post)} 个，"
                f"得到 {len(seq)} 个", None, 'ValueError')
        for (name, _), v in zip(pre, seq):
            self.set_var(name, v)
        star_name = targets[star_idx][0]
        self.set_var(star_name, seq[len(pre):len(seq) - len(post)])
        for (name, _), v in zip(post, seq[len(seq) - len(post):]):
            self.set_var(name, v)

    # ==========================================================
    # ---------- int 特化函数（与 asm.py gen_int_op 语义对齐） ----------
    # ==========================================================

    _INT_PARAM_TYPES = ('int', 'i64', 'i32', 'bool')

    def _register_int_func(self, op):
        """('func_int', name, body, params, ret_type) -> 注册为可调用函数"""
        _, name, sub_code, params, ret_type = op
        fn = EclFunction(name, sub_code, params, None, None, ret_type,
                         self.interpreter, {},
                         closure=[self.scope] + self.parent_scope)
        fn.is_int_func = True
        # 重载处理与 'func' 指令保持一致
        old = self.scope.get(name)
        if old is not None and isinstance(old.value, EclFunction):
            ov = EclOverload(name, self.interpreter)
            ov.add(old.value)
            ov.add(fn)
            self.scope[name] = Cell(ov)
        elif old is not None and isinstance(old.value, EclOverload):
            old.value.add(fn)
        else:
            self.scope[name] = Cell(fn)

    def _int_load(self, temps, operand):
        """操作数取值：('reg', name) / ('imm', n) / ('tmp', t)"""
        kind, val = operand
        if kind == 'imm':
            return val
        # reg 和 tmp 都按名字存进 temps，取值逻辑一致
        return temps[val]

    def call_int_func(self, fn, args):
        """解释执行 int 特化函数体。指令语义与 asm.py 的 gen_int_op 一一对应。"""
        interpreter = self.interpreter
        params = fn.params

        if len(args) != len(params):
            interpreter.error(
                f"{fn.name}() 需要 {len(params)} 个参数，得到 {len(args)} 个",
                None, 'TypeError')

        temps = {}
        for i, p in enumerate(params):
            v = args[i]
            t = p.get('type')
            # 与 AOT 函数入口的 runtime_convert 对齐
            if t in self._INT_PARAM_TYPES:
                v = interpreter.convert_type(v, t)
            temps[p['name']] = v  # ('reg', name) 取值来源

        body = fn.code
        labels = {}
        for i, op in enumerate(body):
            if op[0] == 'label':
                labels[op[1]] = i + 1

        pc = 0
        n = len(body)
        while pc < n:
            op = body[pc]
            pc += 1
            k = op[0]

            if k == 'label':
                continue

            elif k == 'int_assign':
                temps[op[1]] = self._int_load(temps, op[2])

            elif k == 'int_store_var':
                temps[op[1]] = self._int_load(temps, op[2])

            elif k == 'int_add':
                temps[op[1]] = (self._int_load(temps, op[2])
                                + self._int_load(temps, op[3]))
            elif k == 'int_sub':
                temps[op[1]] = (self._int_load(temps, op[2])
                                - self._int_load(temps, op[3]))
            elif k == 'int_mul':
                temps[op[1]] = (self._int_load(temps, op[2])
                                * self._int_load(temps, op[3]))
            elif k == 'int_lt':
                temps[op[1]] = (self._int_load(temps, op[2])
                                < self._int_load(temps, op[3]))
            elif k == 'int_gt':
                temps[op[1]] = (self._int_load(temps, op[2])
                                > self._int_load(temps, op[3]))
            elif k == 'int_le':
                temps[op[1]] = (self._int_load(temps, op[2])
                                <= self._int_load(temps, op[3]))
            elif k == 'int_ge':
                temps[op[1]] = (self._int_load(temps, op[2])
                                >= self._int_load(temps, op[3]))
            elif k == 'int_eq':
                temps[op[1]] = (self._int_load(temps, op[2])
                                == self._int_load(temps, op[3]))
            elif k == 'int_ne':
                temps[op[1]] = (self._int_load(temps, op[2])
                                != self._int_load(temps, op[3]))

            elif k == 'int_and':
                temps[op[1]] = (bool(self._int_load(temps, op[2]))
                                and bool(self._int_load(temps, op[3])))
            elif k == 'int_or':
                temps[op[1]] = (bool(self._int_load(temps, op[2]))
                                or bool(self._int_load(temps, op[3])))

            elif k == 'int_neg':
                temps[op[1]] = -self._int_load(temps, op[2])
            elif k == 'int_not':
                temps[op[1]] = not self._int_load(temps, op[2])

            elif k == 'int_jz':
                if not self._int_load(temps, op[1]):
                    pc = labels[op[2]]
            elif k == 'int_jnz':
                if self._int_load(temps, op[1]):
                    pc = labels[op[2]]

            elif k == 'goto':
                pc = labels[op[1]]

            elif k == 'int_call':
                _, dest, fname, cargs = op
                callee = self.get_var(fname)
                if callee is UNKNOWN:
                    interpreter.error(
                        f"int 特化调用了未定义函数: '{fname}'",
                        None, 'NameError')
                temps[dest] = interpreter.call_value(
                    callee,
                    [self._int_load(temps, a) for a in cargs], {})

            elif k == 'int_ret':
                return self._int_load(temps, op[1])
            elif k == 'int_ret_zero':
                return 0
            elif k == 'halt':
                return 0

            else:
                interpreter.error(
                    f"int 特化暂不支持指令: {k}", None, 'RuntimeError')

        return 0  # 走到函数末尾，等价 int_ret_zero
