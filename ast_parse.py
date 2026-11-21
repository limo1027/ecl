from token_parse import split_token, LexerError
from class_and_vars import *
from errors import ParseError


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0
        self.n = len(tokens)
        self._prev_line = 0

    # ---------- 基础工具 ----------

    def current(self):
        return self.tokens[self.pos] if self.pos < self.n else None

    def peek(self, offset=1):
        idx = self.pos + offset
        return self.tokens[idx] if idx < self.n else None

    def advance(self):
        tok = self.current()
        self.pos += 1
        if tok is not None:
            self._prev_line = tok.line
        return tok

    def _same_line(self):
        """当前 token 是否与上一个已消费 token 在同一行。

        ECL 没有换行 token，语句靠语法边界区分；行首的 * 号
        （例如赋值解包 *init, last <- ...）不能被当成上一行的乘号。
        """
        tok = self.current()
        return tok is not None and tok.line == self._prev_line

    def error(self, msg, tok=None):
        if tok is None:
            tok = self.current()
        if tok is not None:
            raise ParseError(msg, tok.line, tok.col)
        last = self.tokens[-1] if self.tokens else None
        if last:
            raise ParseError(msg, last.line, last.col)
        raise ParseError(msg, 0, 0)

    def expect(self, type_, value=None):
        tok = self.current()
        if not tok or tok.type != type_ or (value is not None and tok.value != value):
            expected = f"'{value}'" if value is not None else f"类型 {type_}"
            actual = f"'{tok.value}'" if tok else "文件末尾"
            self.error(f"期望 {expected}，但得到 {actual}", tok)
        return self.advance()

    def match(self, type_, value=None):
        tok = self.current()
        return bool(tok and tok.type == type_ and (value is None or tok.value == value))

    def match_value(self, value):
        tok = self.current()
        return bool(tok and tok.value == value)

    # ---------- 入口 ----------

    def parse(self):
        stmts = []
        while not self.match('EOF'):
            # 跳过开头的分号
            while self.match('SYMBOL', ';'):
                self.advance()
            if self.match('EOF'):
                break
            # 顶层多余的 end：直接报语法错误（using 不需要 end），
            # 否则 parse_statement() 返回 None 但位置不前进，会死循环。
            if self.match('KEYWORD', 'end'):
                self.error("多余的 'end'（using 不需要 end，"
                           "请检查块是否已闭合）", self.current())
            before = self.pos
            stmt = self.parse_statement()
            if self.pos == before:
                self.error("无法继续解析", self.current())
            if stmt is not None:
                stmts.append(stmt)
            # 跳过语句后的分号（一个或多个）
            while self.match('SYMBOL', ';'):
                self.advance()
        return Program(stmts)

    # ---------- 语句 ----------

    def parse_statement(self):
        tok = self.current()
        if tok is None or self.match('EOF'):
            return None
        if tok.type == 'KEYWORD' and tok.value == 'end':
            return None

        # public / private 修饰符（仅 class 内合法，由检查器检查）
        if tok.type == 'KEYWORD' and tok.value in ('public', 'private'):
            vis = tok.value
            self.advance()
            inner = self.parse_statement()
            if inner is None:
                self.error("public/private 修饰符后缺少声明", tok)
            if hasattr(inner, 'visibility'):
                inner.visibility = vis
                return inner
            self.error("public/private 只能修饰声明", tok)

        # 赋值解包: a, *rest <- [1, 2, 3]
        unpack = self.try_parse_unpack_assign()
        if unpack is not None:
            return unpack

        # 类型声明（int x <- 1 / list[5] int m <- [...]）
        if isinstance(tok.value, str) and tok.value in BUILTIN_TYPES \
                and tok.type in ('KEYWORD', 'IDENTIFIER', 'TYPE') \
                and (self._type_name_ahead()
                     or (self.peek() and self.peek().type == 'SYMBOL'
                         and self.peek().value == '[')):
            return self.parse_typed_define()

        # 用户自定义类型声明：ClassName varName <- ...
        # 形如：Point p <- Point(3, 4)
        if (tok.type == 'IDENTIFIER'
                and self.peek()
                and self.peek().type == 'IDENTIFIER'
                and self.peek(2)
                and self.peek(2).type == 'SYMBOL'
                and self.peek(2).value == '<-'
                and tok.line == self.peek().line == self.peek(2).line):
            return self.parse_user_typed_define()

        # 关键字语句
        if tok.type == 'KEYWORD':
            kw = tok.value
            if kw == 'global':
                # 判断是 global g <- expr 还是 global.x <- expr
                nxt = self.peek()
                if nxt and nxt.type == 'IDENTIFIER' and self.peek(2) and self.peek(2).value == '<-':
                    # global g <- expr：变量声明
                    self.advance()  # 跳过 global
                    name = self.expect('IDENTIFIER')
                    self.expect('SYMBOL', '<-')
                    value = self.parse_expr()
                    return DefineStmt(name.value, 'global', value,
                                      is_ref=False, is_const=False,
                                      line=tok.line, col=tok.col)
                else:
                    # global.x <- expr 或 global.x：作用域限定，作为表达式处理
                    return self.parse_expression_statement()
            if kw == 'if':
                return self.parse_if()
            if kw == 'for':
                return self.parse_for()
            if kw == 'while':
                return self.parse_while()
            if kw == 'def':
                return self.parse_function()
            if kw == 'class':
                return self.parse_class()
            if kw == 'struct':
                return self.parse_struct()
            if kw == 'dll_import':
                return self.parse_dll_import()
            if kw == 'return':
                return self.parse_return()
            if kw == 'break':
                self.advance()
                return BreakStmt(line=tok.line, col=tok.col)
            if kw == 'continue':
                self.advance()
                return ContinueStmt(line=tok.line, col=tok.col)
            if kw == 'goto':
                self.advance()
                label = self.expect('IDENTIFIER')
                return GotoStmt(label.value, line=tok.line, col=tok.col)
            if kw == 'label':
                self.advance()
                name = self.expect('IDENTIFIER')
                self.expect('SYMBOL', ':')
                return LabelStmt(name.value, line=tok.line, col=tok.col)
            if kw == 'import':
                res = self.parse_import()
                return res
            if kw == 'from':
                return self.parse_from()
            if kw == 'try':
                return self.parse_try()
            if kw == 'raise':
                self.advance()
                exc = self.parse_expr()
                return RaiseStmt(exc, line=tok.line, col=tok.col)
            if kw == 'with':
                return self.parse_with()
            if kw == 'match':
                return self.parse_match()
            if kw == 'using':
                return self.parse_using()
            if kw == 'ref':
                return self.parse_ref()
            if kw == 'define':
                return self.parse_define()
            if kw == 'const':
                return self.parse_const()

        # 单行函数检测: f(x) -> int <- ...
        if tok.type == 'IDENTIFIER' and self.peek() and self.peek().value == '(':
            result = self.try_parse_single_line_function()
            if result is not None:
                return result

        # 装饰器（@@ 是外部语言执行语法，不在此处理）
        if tok.type == 'SYMBOL' and tok.value == '@':
            nxt = self.peek()
            if not (nxt and nxt.type == 'SYMBOL' and nxt.value == '@'):
                return self.parse_decorator()

        # 表达式语句
        return self.parse_expression_statement()

    def try_parse_unpack_assign(self):
        """尝试解析赋值解包: a, *rest <- [1, 2, 3]，失败则回退。"""
        saved = self.pos
        targets = []
        try:
            while True:
                star = False
                if self.match('SYMBOL', '*'):
                    self.advance()
                    star = True
                if not self.match('IDENTIFIER') and not (
                        self.match('KEYWORD', 'last')):
                    self.pos = saved
                    return None
                name_tok = self.advance()
                targets.append(UnpackTarget(name_tok.value, star))
                if self.match('SYMBOL', ','):
                    self.advance()
                    continue
                break
            if len(targets) == 1 and not targets[0].is_star:
                self.pos = saved
                return None
            if not self.match('SYMBOL', '<-'):
                self.pos = saved
                return None
            start = self.current()
            self.advance()
            value = self.parse_expr()

            stars = [t for t in targets if t.is_star]
            if len(stars) > 1:
                self.error("赋值解包中 *name 只能出现一次", start)
            names = [t.name for t in targets]
            if len(set(names)) != len(names):
                self.error("赋值解包中存在重复变量名", start)
            return UnpackAssignStmt(targets, value,
                                    line=start.line, col=start.col)
        except ParseError:
            self.pos = saved
            return None

    def _is_type_token(self, tok):
        return bool(tok and isinstance(tok.value, str)
                    and (tok.type in ('IDENTIFIER', 'TYPE')
                         or (tok.type == 'KEYWORD'
                             and tok.value in BUILTIN_TYPES)))

    def _looks_like_typed_define(self):
        tok = self.current()
        if not (tok and isinstance(tok.value, str)
                and tok.value in BUILTIN_TYPES
                and tok.type in ('KEYWORD', 'IDENTIFIER', 'TYPE')):
            return False
        name = self.peek()
        arrow = self.peek(2)
        return bool(name and name.type == 'IDENTIFIER'
                    and arrow and arrow.value == '<-')

    def parse_user_typed_define(self):
        """解析用户自定义类型声明：ClassName varName <- expr"""
        start = self.current()
        type_name = self.advance().value          # 类名
        name = self.expect('IDENTIFIER')          # 变量名
        self.expect('SYMBOL', '<-')
        value = self.parse_expr()
        return DefineStmt(name.value, type_name, value,
                          is_ref=False, is_const=False,
                          line=start.line, col=start.col)

    def parse_typed_define(self):
        start = self.current()
        base_type = self.advance().value
        type_params = []
        if self.match('SYMBOL', '['):
            self.advance()
            if self.match('INT') or self.match('IDENTIFIER') or self.match('TYPE'):
                type_params.append(self.advance().value)
            else:
                type_params.append(self.parse_expr())
            self.expect('SYMBOL', ']')

        elem_type = None
        nxt = self.current()
        if nxt and (nxt.type in ('IDENTIFIER', 'TYPE') and nxt.value in BUILTIN_TYPES):
            elem_type = self.advance().value

        name = self.expect('IDENTIFIER')

        value = None
        nested = None
        if self.match('SYMBOL', '<-'):
            self.advance()
            # 链式声明: int x <- double b <- 9
            if self._looks_like_typed_define():
                nested = self.parse_typed_define()
            else:
                value = self.parse_expr()

        full_type = base_type
        for p in type_params:
            full_type += f'[{p}]'
        if elem_type:
            full_type += ' ' + elem_type

        first = DefineStmt(name.value, full_type, value,
                           is_ref=False, is_const=False,
                           line=start.line, col=start.col)
        if nested is not None:
            if isinstance(nested, CompoundStmt):
                stmts = list(nested.stmts)
            else:
                stmts = [nested]
            last = stmts[-1]
            first.value = Variable(getattr(last, 'name', ''),
                                   line=start.line, col=start.col)
            stmts.append(first)
            return CompoundStmt(stmts, line=start.line, col=start.col)
        return first

    def parse_ref(self):
        start = self.current()
        self.expect('KEYWORD', 'ref')
        name = self.expect('IDENTIFIER')
        self.expect('SYMBOL', '<-')
        value = self.parse_expr()
        return DefineStmt(name.value, None, value, is_ref=True, is_const=False,
                          line=start.line, col=start.col)

    def parse_define(self):
        start = self.current()
        self.expect('KEYWORD', 'define')
        is_const = False
        if self.match('KEYWORD', 'const'):
            self.advance()
            is_const = True
        type_name = None
        if self._type_name_ahead():
            type_name = self.advance().value
        name = self.expect('IDENTIFIER')
        value = None
        if self.match('SYMBOL', '<-'):
            self.advance()
            value = self.parse_expr()
        return DefineStmt(name.value, type_name, value,
                          is_ref=False, is_const=is_const,
                          line=start.line, col=start.col)

    def _type_name_ahead(self):
        """当前 token 是否是「类型名 + 变量名」里的类型名（同一行）。"""
        tok = self.current()
        nxt = self.peek()
        if not tok or not isinstance(tok.value, str) \
                or not nxt or nxt.type != 'IDENTIFIER':
            return False
        if tok.line != nxt.line:
            return False
        if tok.type in ('IDENTIFIER', 'TYPE'):
            return True
        return tok.type == 'KEYWORD' and tok.value in BUILTIN_TYPES

    def parse_const(self):
        start = self.current()
        self.expect('KEYWORD', 'const')
        type_name = None
        if self._type_name_ahead():
            type_name = self.advance().value
        name = self.expect('IDENTIFIER')
        value = None
        if self.match('SYMBOL', '<-'):
            self.advance()
            value = self.parse_expr()
        return DefineStmt(name.value, type_name, value,
                          is_ref=False, is_const=True,
                          line=start.line, col=start.col)

    def parse_return(self):
        tok = self.current()
        self.advance()
        if self.match('SYMBOL', ':') or self.match('EOF') or self.match('KEYWORD', 'end'):
            return ReturnStmt(None, line=tok.line, col=tok.col)
        val = self.parse_expr()
        return ReturnStmt(val, line=tok.line, col=tok.col)

    def parse_expression_statement(self):
        expr = self.parse_expr()
        return ExprStmt(expr, line=expr.line, col=expr.col)

    # ---------- 函数 ----------

    def try_parse_single_line_function(self):
        """尝试解析单行函数 f(int x) -> int <- expr，失败则回退"""
        saved = self.pos
        try:
            name_tok = self.advance()
            name = name_tok.value
            self.expect('SYMBOL', '(')
            params = []
            while not self.match('SYMBOL', ')'):
                nxt = self.current()
                type_ = None
                # 参数可以带类型: int x
                if nxt and (nxt.type in ('IDENTIFIER', 'TYPE')
                            or (nxt.type == 'KEYWORD'
                                and nxt.value in BUILTIN_TYPES)):
                    type_ = self.advance().value
                if self.match('IDENTIFIER'):
                    pname_tok_value = self.expect('IDENTIFIER').value
                    ptype = type_
                else:
                    pname_tok_value = type_
                    ptype = None
                params.append({'name': pname_tok_value,
                              'type': ptype, 'default': None})
                if self.match('SYMBOL', ','):
                    self.advance()
                else:
                    break
            self.expect('SYMBOL', ')')

            return_type = None
            if self.match('SYMBOL', '->'):
                self.advance()
                rt = self.current()
                if rt and rt.type in ('IDENTIFIER', 'TYPE', 'KEYWORD'):
                    return_type = self.advance().value
                else:
                    self.error("期望返回值类型", rt)

            if not self.match('SYMBOL', '<-'):
                self.pos = saved
                return None
            self.advance()

            body_expr = self.parse_expr()
            # ★ 关键修复：单行函数体是返回值，不是普通表达式语句
            body_stmt = ReturnStmt(
                body_expr, line=body_expr.line, col=body_expr.col)

            return FuncDefStmt(
                name=name,
                params=params,
                varargs=None,
                varargs_type=None,
                kwargs=None,
                kwargs_type=None,
                defaults={},
                body=[body_stmt],
                is_single_line=True,
                return_type=return_type,
                line=name_tok.line,
                col=name_tok.col,
            )
        except (ParseError, LexerError):
            self.pos = saved
            return None

    def parse_function(self):
        start = self.current()
        self.expect('KEYWORD', 'def')
        name = self.expect('IDENTIFIER')
        self.expect('SYMBOL', '(')

        params = []
        varargs = None
        varargs_type = None
        varargs_index = None
        kwargs = None
        kwargs_type = None
        defaults = {}

        while not self.match('SYMBOL', ')'):
            # 带类型的可变参数: str *args / int **kw
            if (self.current() and self._is_type_token(self.current())
                    and self.peek() and self.peek().type == 'SYMBOL'
                    and self.peek().value in ('*', '**')
                    and self.peek(2) and self.peek(2).type == 'IDENTIFIER'
                    and self.peek(3) and self.peek(3).value in (',', ')')):
                ptype = self.advance().value
                star = self.advance()
                pname = self.expect('IDENTIFIER').value
                if star.value == '**':
                    kwargs = pname
                    kwargs_type = ptype
                else:
                    varargs = pname
                    varargs_type = ptype
                    varargs_index = len(params)
                if self.match('SYMBOL', ','):
                    self.advance()
                continue

            if self.match('SYMBOL', '**'):
                self.advance()
                # 可选类型: int **kw
                if self.current() and self.current().type == 'IDENTIFIER' \
                        and self.peek() and self.peek().type == 'IDENTIFIER':
                    kwargs_type = self.advance().value
                kwargs = self.expect('IDENTIFIER').value
                if self.match('SYMBOL', ','):
                    self.advance()
                continue

            if self.match('SYMBOL', '*'):
                self.advance()
                # 可选类型: str *args
                if self.current() and self.current().type == 'IDENTIFIER' \
                        and self.peek() and self.peek().type == 'IDENTIFIER':
                    varargs_type = self.advance().value
                varargs = self.expect('IDENTIFIER').value
                varargs_index = len(params)
                if self.match('SYMBOL', ','):
                    self.advance()
                continue

            ptype = None
            nxt = self.current()
            # 类型可以是内置类型名（int/str/...）或用户类名
            if nxt and (nxt.type in ('IDENTIFIER', 'TYPE')
                        or (nxt.type == 'KEYWORD' and nxt.value in BUILTIN_TYPES)):
                peek_tok = self.peek()
                if peek_tok and peek_tok.type in ('IDENTIFIER', 'TYPE'):
                    ptype = self.advance().value

            pname = self.expect('IDENTIFIER').value

            default = None
            if self.match('SYMBOL', '='):
                self.advance()
                default = self.parse_expr()
                defaults[pname] = default

            params.append({'name': pname, 'type': ptype, 'default': default})

            if self.match('SYMBOL', ','):
                self.advance()
            elif not self.match('SYMBOL', ')'):
                self.error("期望 ',' 或 ')'", self.current())

        self.expect('SYMBOL', ')')

        # 返回值类型
        return_type = None
        if self.match('SYMBOL', '->'):
            self.advance()
            rt = self.current()
            if rt and rt.type in ('IDENTIFIER', 'TYPE', 'KEYWORD'):
                return_type = self.advance().value
            else:
                self.error("期望返回值类型", rt)

        self.expect('SYMBOL', ':')

        body = []
        while not self.match('KEYWORD', 'end') and not self.match('EOF'):
            stmt = self.parse_statement()
            if stmt is not None:
                body.append(stmt)
        self.expect('KEYWORD', 'end')

        return FuncDefStmt(
            name=name.value,
            params=params,
            varargs=varargs,
            varargs_type=varargs_type,
            kwargs=kwargs,
            kwargs_type=kwargs_type,
            defaults=defaults,
            body=body,
            is_single_line=False,
            return_type=return_type,
            varargs_index=varargs_index,
            line=start.line,
            col=start.col,
        )

    def parse_struct(self):
        start = self.current()
        self.expect('KEYWORD', 'struct')
        name = self.expect('IDENTIFIER')
        self.expect('SYMBOL', ':')
        fields = []
        while not self.match('KEYWORD', 'end') and not self.match('EOF'):
            tok = self.current()
            if not self._is_type_token(tok):
                self.error("struct 字段需要类型名", tok)
            type_name = self.advance().value
            field_name = self.expect('IDENTIFIER')
            fields.append(StructField(type_name, field_name.value,
                                      line=tok.line, col=tok.col))
            while self.match('SYMBOL', ';'):
                self.advance()
        self.expect('KEYWORD', 'end')
        return StructDefStmt(name.value, fields,
                             line=start.line, col=start.col)

    def _parse_dll_param(self):
        # ptr 有两种写法：
        #   ptr POINT p     -> 修饰符 ptr + 类型 POINT + 参数名 p
        #   ptr lpSecurity  -> 类型 ptr + 参数名 lpSecurity
        # 用第三个 token 是否还是 IDENTIFIER 来区分。
        modifier = None
        tok = self.current()
        if tok and tok.type == 'KEYWORD' and tok.value in ('in', 'out'):
            second = self.peek()
            if second and self._is_type_token(second):
                modifier = self.advance().value
        elif tok and tok.type == 'IDENTIFIER' and tok.value == 'ptr':
            second = self.peek()
            third = self.peek(2)
            if second and self._is_type_token(second) \
                    and third and third.type == 'IDENTIFIER':
                modifier = self.advance().value
        type_tok = self.current()
        if not self._is_type_token(type_tok):
            self.error("dll_import 参数需要类型名", type_tok)
        type_name = self.advance().value
        while self.match('SYMBOL', '*'):
            self.advance()
            type_name += '*'
        pname = self.expect('IDENTIFIER')
        return {'name': pname.value, 'type': type_name,
                'modifier': modifier}

    def parse_dll_import(self):
        start = self.current()
        self.expect('KEYWORD', 'dll_import')
        path_tok = self.expect('STRING')
        self.expect('SYMBOL', ':')
        funcs = []
        while not self.match('KEYWORD', 'end') and not self.match('EOF'):
            visibility = None
            if self.match('KEYWORD', 'public') or self.match('KEYWORD', 'private'):
                visibility = self.advance().value
            def_tok = self.expect('KEYWORD', 'def')
            name = self.expect('IDENTIFIER')
            self.expect('SYMBOL', '(')
            params = []
            while not self.match('SYMBOL', ')'):
                params.append(self._parse_dll_param())
                if self.match('SYMBOL', ','):
                    self.advance()
                else:
                    break
            self.expect('SYMBOL', ')')
            ret_type = 'none'
            if self.match('SYMBOL', '->'):
                self.advance()
                rt = self.current()
                if not self._is_type_token(rt):
                    self.error("dll_import 返回类型错误", rt)
                ret_type = self.advance().value
            funcs.append(DllFuncDecl(
                name.value, params, ret_type, visibility,
                line=def_tok.line, col=def_tok.col))
            while self.match('SYMBOL', ';'):
                self.advance()
        self.expect('KEYWORD', 'end')
        return DllImportStmt(path_tok.value, funcs,
                             line=start.line, col=start.col)

    def parse_class(self):
        start = self.current()
        self.expect('KEYWORD', 'class')
        name = self.expect('IDENTIFIER')
        bases = []
        if self.match('SYMBOL', '('):
            self.advance()
            while not self.match('SYMBOL', ')'):
                bases.append(self.expect('IDENTIFIER').value)
                if self.match('SYMBOL', ','):
                    self.advance()
            self.expect('SYMBOL', ')')
        self.expect('SYMBOL', ':')
        body = []
        while not self.match('KEYWORD', 'end') and not self.match('EOF'):
            stmt = self.parse_statement()
            if stmt is not None:
                body.append(stmt)
        self.expect('KEYWORD', 'end')
        return ClassDefStmt(name.value, bases, body,
                            line=start.line, col=start.col)

    def parse_if(self):
        start = self.current()
        self.expect('KEYWORD', 'if')
        cond = self.parse_expr()
        self.expect('SYMBOL', ':')
        body = []
        while not self.match('KEYWORD', 'end') and not self.match('KEYWORD', 'elif') \
                and not self.match('KEYWORD', 'else') and not self.match('EOF'):
            s = self.parse_statement()
            if s is not None:
                body.append(s)

        elifs = []
        while self.match('KEYWORD', 'elif'):
            self.advance()
            ec = self.parse_expr()
            self.expect('SYMBOL', ':')
            eb = []
            while not self.match('KEYWORD', 'end') and not self.match('KEYWORD', 'elif') \
                    and not self.match('KEYWORD', 'else') and not self.match('EOF'):
                s = self.parse_statement()
                if s is not None:
                    eb.append(s)
            elifs.append((ec, eb))

        else_body = []
        if self.match('KEYWORD', 'else'):
            self.advance()
            self.expect('SYMBOL', ':')
            while not self.match('KEYWORD', 'end') and not self.match('EOF'):
                s = self.parse_statement()
                if s is not None:
                    else_body.append(s)

        self.expect('KEYWORD', 'end')
        return IfStmt(cond, body, elifs, else_body,
                      line=start.line, col=start.col)

    def parse_for(self):
        start = self.current()
        self.expect('KEYWORD', 'for')

        variable = None
        iterable = None
        range_expr = None
        count = None

        if self.match('INT'):
            count = self.parse_expr()
        else:
            variable = self.expect('IDENTIFIER').value
            self.expect('KEYWORD', 'in')
            # 判断是否为 range 写法
            if self.match('INT') and (self.peek() and self.peek().value in ('..', '..=')):
                range_expr = self.parse_range()
            else:
                iterable = self.parse_expr()

        self.expect('SYMBOL', ':')
        body = []
        while not self.match('KEYWORD', 'end') and not self.match('EOF'):
            s = self.parse_statement()
            if s is not None:
                body.append(s)
        self.expect('KEYWORD', 'end')
        return ForStmt(variable, iterable, range_expr, count, body,
                       line=start.line, col=start.col)

    def parse_range(self):
        start = self.current()
        left = self.parse_expr()
        inclusive = False
        if self.match('SYMBOL', '..='):
            self.advance()
            inclusive = True
        elif self.match('SYMBOL', '..'):
            self.advance()
        else:
            self.error("期望 '..' 或 '..='", self.current())
        # 1..10+2：终点 10，步长 2（先乘除层，再处理 +step）
        right = self.parse_mul_expr()
        step = None
        if self.match('SYMBOL', '+'):
            self.advance()
            step = self.parse_expr()
        return RangeExpr(left, right, step, inclusive,
                         line=start.line, col=start.col)

    def parse_while(self):
        start = self.current()
        self.expect('KEYWORD', 'while')
        cond = self.parse_expr()
        self.expect('SYMBOL', ':')
        body = []
        while not self.match('KEYWORD', 'end') and not self.match('EOF'):
            s = self.parse_statement()
            if s is not None:
                body.append(s)
        self.expect('KEYWORD', 'end')
        return WhileStmt(cond, body, line=start.line, col=start.col)

    def parse_import(self):
        start = self.current()
        self.expect('KEYWORD', 'import')
        module = self.expect('IDENTIFIER').value
        alias = None
        if self.match('KEYWORD', 'as'):
            self.advance()
            alias = self.expect('IDENTIFIER').value
        return ImportStmt(module, alias, [], line=start.line, col=start.col)

    def parse_from(self):
        start = self.current()
        self.expect('KEYWORD', 'from')
        parts = [self.expect('IDENTIFIER').value]
        while self.match('SYMBOL', '/') or self.match('SYMBOL', '.'):
            sep = self.advance().value
            parts.append(sep)
            parts.append(self.expect('IDENTIFIER').value)
        module = ''.join(parts)
        self.expect('KEYWORD', 'import')
        names = []
        while True:
            name = self.expect('IDENTIFIER').value
            alias = None
            if self.match('KEYWORD', 'as'):
                self.advance()
                alias = self.expect('IDENTIFIER').value
            names.append((name, alias))
            if not self.match('SYMBOL', ','):
                break
            self.advance()
        return ImportStmt(module, None, names, line=start.line, col=start.col)

    def parse_try(self):
        start = self.current()
        self.expect('KEYWORD', 'try')
        self.expect('SYMBOL', ':')
        body = []
        while not self.match('KEYWORD', 'end') and not self.match('KEYWORD', 'except') \
                and not self.match('KEYWORD', 'else') and not self.match('KEYWORD', 'finally') \
                and not self.match('EOF'):
            s = self.parse_statement()
            if s is not None:
                body.append(s)

        excepts = []
        while self.match('KEYWORD', 'except'):
            self.advance()
            exc_type = None
            exc_name = None
            if self.match('IDENTIFIER'):
                exc_type = self.advance().value
                if self.match('KEYWORD', 'as'):
                    self.advance()
                    exc_name = self.expect('IDENTIFIER').value
            self.expect('SYMBOL', ':')
            eb = []
            while not self.match('KEYWORD', 'end') and not self.match('KEYWORD', 'except') \
                    and not self.match('KEYWORD', 'else') and not self.match('KEYWORD', 'finally') \
                    and not self.match('EOF'):
                s = self.parse_statement()
                if s is not None:
                    eb.append(s)
            excepts.append({'type': exc_type, 'name': exc_name, 'body': eb})

        else_body = None
        if self.match('KEYWORD', 'else'):
            self.advance()
            self.expect('SYMBOL', ':')
            else_body = []
            while not self.match('KEYWORD', 'end') and not self.match('KEYWORD', 'finally') \
                    and not self.match('EOF'):
                s = self.parse_statement()
                if s is not None:
                    else_body.append(s)

        finally_body = None
        if self.match('KEYWORD', 'finally'):
            self.advance()
            self.expect('SYMBOL', ':')
            finally_body = []
            while not self.match('KEYWORD', 'end') and not self.match('EOF'):
                s = self.parse_statement()
                if s is not None:
                    finally_body.append(s)

        self.expect('KEYWORD', 'end')
        return TryStmt(body, excepts, else_body, finally_body,
                       line=start.line, col=start.col)

    def parse_with(self):
        start = self.current()
        self.expect('KEYWORD', 'with')
        items = []
        while True:
            value = self.parse_expr()
            name = None
            if self.match('KEYWORD', 'as'):
                self.advance()
                name = self.expect('IDENTIFIER').value
            items.append((value, name))
            if not self.match('SYMBOL', ','):
                break
            self.advance()
        self.expect('SYMBOL', ':')
        body = []
        while not self.match('KEYWORD', 'end') and not self.match('EOF'):
            s = self.parse_statement()
            if s is not None:
                body.append(s)
        self.expect('KEYWORD', 'end')
        return WithStmt(items, body, line=start.line, col=start.col)

    def parse_match(self):
        start = self.current()
        self.expect('KEYWORD', 'match')
        value = self.parse_expr()
        self.expect('SYMBOL', ':')
        cases = []
        while not self.match('KEYWORD', 'end') and not self.match('EOF'):
            case_tok = self.expect('KEYWORD', 'case')
            pattern = self.parse_match_pattern()
            guard = None
            if self.match('KEYWORD', 'if'):
                self.advance()
                guard = self.parse_expr()
            self.expect('SYMBOL', ':')
            cb = []
            while not self.match('KEYWORD', 'end') and not self.match('KEYWORD', 'case') \
                    and not self.match('EOF'):
                s = self.parse_statement()
                if s is not None:
                    cb.append(s)
            cases.append(MatchCase(pattern, guard, cb,
                                   line=case_tok.line, col=case_tok.col))
        self.expect('KEYWORD', 'end')
        return MatchStmt(value, cases, line=start.line, col=start.col)

    def _match_follows_colon(self):
        nxt = self.peek()
        return bool(nxt and (nxt.value in (':', 'if')))

    def parse_match_pattern(self):
        tok = self.current()
        if tok is None:
            self.error("match 缺少 case 模式")

        # 通配
        if tok.type == 'IDENTIFIER' and tok.value == '_':
            self.advance()
            return MatchWildcard(line=tok.line, col=tok.col)

        # 类型模式：int / str / Point / any，后面紧跟 ':' 或 'if'
        if tok.type == 'IDENTIFIER' and self.peek() and self.peek().value in (':', 'if'):
            self.advance()
            return MatchType(type_name=tok.value, line=tok.line, col=tok.col)
        if tok.type == 'KEYWORD' and tok.value in ('any', 'unknown') \
                and self._match_follows_colon():
            self.advance()
            return MatchType(type_name=tok.value, line=tok.line, col=tok.col)

        # 字面量 / 多值
        values = [self.parse_match_literal()]
        while self.match('SYMBOL', ','):
            self.advance()
            if self.match('SYMBOL', ':'):
                break
            values.append(self.parse_match_literal())
        if len(values) > 1:
            return MatchMulti(values=values, line=tok.line, col=tok.col)
        return MatchLiteral(value=values[0], line=tok.line, col=tok.col)

    def parse_match_literal(self):
        tok = self.current()
        if tok is None:
            self.error("match 字面量模式缺失")
        sign = 1
        if tok.type == 'SYMBOL' and tok.value == '-':
            self.advance()
            sign = -1
            tok = self.current()
        if tok and tok.type in ('INT', 'FLOAT', 'DOUBLE'):
            self.advance()
            value = tok.value if sign == 1 else -tok.value
            return value
        if tok and tok.type == 'STRING':
            self.advance()
            return tok.value
        if tok and tok.type == 'KEYWORD' and tok.value in (
                'True', 'False', 'true', 'false', 'none', 'null'):
            self.advance()
            return {'True': True, 'False': False,
                    'true': True, 'false': False,
                    'none': None, 'null': NULL}[tok.value]
        self.error("case 后只能跟字面量/类型/`_`/多值", tok)

    def parse_using(self):
        start = self.current()
        self.expect('KEYWORD', 'using')
        expr = self.parse_expr()
        # 可选分号
        if self.match('SYMBOL', ';'):
            self.advance()
        return UsingStmt(expr, line=start.line, col=start.col)

    def parse_decorator(self):
        start = self.current()
        self.expect('SYMBOL', '@')
        target = self.expect('IDENTIFIER').value
        name = None
        if self.match('SYMBOL', '.'):
            self.advance()
            name = self.expect('IDENTIFIER').value
        func = self.parse_function()
        return DecoratorStmt(target, name, func.body,
                             line=start.line, col=start.col)

    def parse_external_exec(self):
        """@@flat_class(code)：外部语言执行语法。

        脱糖为 flat_class().__exec__(code)，这里只构造 AST 节点。
        """
        start = self.current()
        self.expect('SYMBOL', '@')
        self.expect('SYMBOL', '@')
        name = self.expect('IDENTIFIER')
        if not self.match('SYMBOL', '('):
            self.error(
                "@@ 后面必须紧跟 (代码字符串)，例如 @@py_py(code)", name)
        args, kwargs = self.parse_call_arguments()
        node = ExternalExec(name.value, args, kwargs,
                            line=start.line, col=start.col)
        return self._parse_postfix(node)

    # ================================================================
    # 表达式解析（全部携带 line/col）
    # ================================================================

    def parse_expr(self):
        node = self.parse_assignment()

        # 三目：expr if cond else expr（必须同一行，避免吃掉下一条 if 语句）
        if self.match('KEYWORD', 'if') and self._same_line():
            saved = self.pos
            self.advance()
            try:
                self.parse_expr()
                if self.match('SYMBOL', ':'):
                    self.pos = saved
                    return node
            except (ParseError, LexerError):
                pass
            self.pos = saved

            self.advance()
            condition = self.parse_expr()
            self.expect('KEYWORD', 'else')
            else_expr = self.parse_expr()
            return IfExpr(condition, node, else_expr,
                          line=node.line, col=node.col)

        return node

    def parse_assignment(self):
        left = self.parse_or_expr()

        if self.match('SYMBOL', '<-'):
            op_tok = self.advance()
            right = self.parse_expr()
            if isinstance(left, (Variable, Attribute, Subscript)):
                return Assign(left, right, is_ref=False,
                              line=op_tok.line, col=op_tok.col)
            self.error("赋值左侧必须是变量或属性", op_tok)

        if self.match('SYMBOL', '<->'):
            op_tok = self.advance()
            right = self.parse_expr()
            if isinstance(left, Variable) and isinstance(right, Variable):
                return Swap(left.name, right.name,
                            line=op_tok.line, col=op_tok.col)
            self.error("交换操作需要两个变量", op_tok)

        return left

    def parse_or_expr(self):
        left = self.parse_and_expr()
        while self.match('KEYWORD', 'or') and self._same_line():
            op = self.advance()
            right = self.parse_and_expr()
            left = Binary(left, 'or', right, line=op.line, col=op.col)
        return left

    def parse_and_expr(self):
        left = self.parse_compare_expr()
        while self.match('KEYWORD', 'and') and self._same_line():
            op = self.advance()
            right = self.parse_compare_expr()
            left = Binary(left, 'and', right, line=op.line, col=op.col)
        return left

    def parse_compare_expr(self):
        left = self.parse_bor_expr()
        ops = {'=', '==', '!=', '<', '>', '<=', '>='}
        while self.match('SYMBOL') and self.current().value in ops \
                and self._same_line():
            op = self.advance()
            right = self.parse_bor_expr()
            left = Binary(left, op.value, right, line=op.line, col=op.col)
        return left

    def parse_bor_expr(self):
        left = self.parse_bxor_expr()
        while self.match('SYMBOL', '|') and self._same_line():
            op = self.advance()
            right = self.parse_bxor_expr()
            left = Binary(left, '|', right, line=op.line, col=op.col)
        return left

    def parse_bxor_expr(self):
        left = self.parse_band_expr()
        while self.match('SYMBOL', '^') and self._same_line():
            op = self.advance()
            right = self.parse_band_expr()
            left = Binary(left, '^', right, line=op.line, col=op.col)
        return left

    def parse_band_expr(self):
        left = self.parse_shift_expr()
        while self.match('SYMBOL', '&') and self._same_line():
            op = self.advance()
            right = self.parse_shift_expr()
            left = Binary(left, '&', right, line=op.line, col=op.col)
        return left

    def parse_shift_expr(self):
        left = self.parse_add_expr()
        while self.match('SYMBOL') and self.current().value in ('<<', '>>') \
                and self._same_line():
            op = self.advance()
            right = self.parse_add_expr()
            left = Binary(left, op.value, right, line=op.line, col=op.col)
        return left

    def parse_add_expr(self):
        left = self.parse_mul_expr()
        while self.match('SYMBOL') and self.current().value in ('+', '-') \
                and self._same_line():
            op = self.advance()
            right = self.parse_mul_expr()
            left = Binary(left, op.value, right, line=op.line, col=op.col)
        return left

    def parse_mul_expr(self):
        left = self.parse_pow_expr()
        while self.match('SYMBOL') and self.current().value in ('*', '/', '//', '%') \
                and self._same_line():
            op = self.advance()
            right = self.parse_pow_expr()
            left = Binary(left, op.value, right, line=op.line, col=op.col)
        return left

    def parse_pow_expr(self):
        left = self.parse_unary_expr()
        if self.match('SYMBOL', '**') and self._same_line():
            op = self.advance()
            right = self.parse_unary_expr()
            return Binary(left, '**', right, line=op.line, col=op.col)
        return left

    def parse_unary_expr(self):
        tok = self.current()
        if tok and tok.type == 'SYMBOL' and tok.value == '&':
            op = self.advance()
            right = self.parse_unary_expr()
            return AddressOf(right, line=op.line, col=op.col)
        if tok and tok.type == 'SYMBOL' and tok.value in ('-', '~', '!'):
            op = self.advance()
            right = self.parse_unary_expr()
            return Unary(op.value, right, line=op.line, col=op.col)
        if tok and tok.type == 'KEYWORD' and tok.value == 'not':
            op = self.advance()
            right = self.parse_unary_expr()
            return Unary('not', right, line=op.line, col=op.col)
        return self.parse_primary()

    def parse_primary(self):
        tok = self.current()
        if tok is None:
            self.error("意外的文件末尾")

        # @@flat_class(code)：外部语言执行（纯语法糖）
        if tok.type == 'SYMBOL' and tok.value == '@':
            nxt = self.peek()
            if nxt and nxt.type == 'SYMBOL' and nxt.value == '@':
                return self.parse_external_exec()

        # 字面量
        if tok.type in ('INT', 'FLOAT', 'DOUBLE', 'STRING', 'WSTRING'):
            self.advance()
            value = WStr(tok.value) if tok.type == 'WSTRING' else tok.value
            node = Literal(value, line=tok.line, col=tok.col)
            return self._parse_postfix(node)

        # 关键字字面量
        if tok.type == 'KEYWORD' and tok.value in (
                'True', 'False', 'true', 'false', 'none', 'null', 'unknown'):
            self.advance()
            value = {'True': True, 'False': False,
                     'true': True, 'false': False,
                     'none': None, 'null': NULL,
                     'unknown': UNKNOWN}[tok.value]
            node = Literal(value, line=tok.line, col=tok.col)
            return self._parse_postfix(node)

        if tok.type == 'KEYWORD' and tok.value in ('global', 'last'):
            self.advance()
            node = Variable(tok.value, line=tok.line, col=tok.col)
            return self._parse_postfix(node)

        # 括号表达式 / 元组
        if tok.type == 'SYMBOL' and tok.value == '(':
            self.advance()
            if self.match('SYMBOL', ')'):
                self.advance()
                return self._parse_postfix(
                    TupleExpr([], line=tok.line, col=tok.col))
            first = self.parse_expr()
            if self.match('SYMBOL', ','):
                elements = [first]
                while self.match('SYMBOL', ','):
                    self.advance()
                    if self.match('SYMBOL', ')'):
                        break
                    if self.match('SYMBOL', '*'):
                        star_tok = self.advance()
                        elements.append(Starred(
                            self.parse_expr(),
                            line=star_tok.line, col=star_tok.col))
                    else:
                        elements.append(self.parse_expr())
                self.expect('SYMBOL', ')')
                return self._parse_postfix(
                    TupleExpr(elements, line=tok.line, col=tok.col))
            self.expect('SYMBOL', ')')
            return self._parse_postfix(first)

        # 列表
        if tok.type == 'SYMBOL' and tok.value == '[':
            return self._parse_postfix(self.parse_list())

        # 字典
        if tok.type == 'SYMBOL' and tok.value == '{':
            return self._parse_postfix(self.parse_dict())

        # 标识符 / 调用
        if tok.type in ('IDENTIFIER', 'TYPE'):
            return self.parse_identifier_or_call()

        # 代码字符串 `...`
        if tok.type == 'CODE_STRING':
            self.advance()
            return self._parse_postfix(
                CodeLiteral(tok.value, line=tok.line, col=tok.col))
        # 插值字符串
        if tok.type == 'INTERP_STRING':
            self.advance()
            return self._parse_interp_string(tok.value, tok.line, tok.col)
        self.error(f"意外的 token: {tok.type} {tok.value!r}", tok)

    def _parse_interp_string(self, segments, line, col):
        """把插值字符串的 segments 拆成 Binary('+', ...) 拼接"""
        parts = []
        for kind, content, sline, scol in segments:
            if kind == 'str':
                parts.append(Literal(content, line=sline, col=scol))
            elif kind == 'var':
                inner = Variable(content, line=sline, col=scol)
                parts.append(Call(
                    Variable('str', line=sline, col=scol),
                    [inner], [], line=sline, col=scol))
            elif kind == 'expr':
                sub_tokens = split_token(content)
                sub_parser = Parser(sub_tokens)
                try:
                    expr = sub_parser.parse_expr()
                except ParseError as e:
                    self.error(f"插值表达式语法错误: {e}", self.current())
                parts.append(Call(
                    Variable('str', line=sline, col=scol),
                    [expr], [], line=sline, col=scol))
        if not parts:
            return Literal("", line=line, col=col)
        node = parts[0]
        for p in parts[1:]:
            node = Binary(node, '+', p, line=line, col=col)
        return node

    def parse_call_arguments(self):
        """解析调用的实参：f(a, *args, b=1, **kwargs)"""
        self.expect('SYMBOL', '(')
        args = []
        kwargs = []
        while not self.match('SYMBOL', ')'):
            if self.match('SYMBOL', '**'):
                star = self.advance()
                args.append(Starred(self.parse_expr(), double=True,
                                    line=star.line, col=star.col))
            elif self.match('SYMBOL', '*'):
                star = self.advance()
                args.append(Starred(self.parse_expr(), double=False,
                                    line=star.line, col=star.col))
            elif self.match('IDENTIFIER') and self.peek() and self.peek().value == '=':
                key = self.advance().value
                self.expect('SYMBOL', '=')
                kwargs.append((key, self.parse_expr()))
            else:
                args.append(self.parse_expr())
            if self.match('SYMBOL', ','):
                self.advance()
            else:
                break
        self.expect('SYMBOL', ')')
        return args, kwargs

    def parse_identifier_or_call(self):
        tok = self.advance()
        name = tok.value

        # 函数调用
        if self.match('SYMBOL', '('):
            args, kwargs = self.parse_call_arguments()
            node = Call(Variable(name, line=tok.line, col=tok.col),
                        args, kwargs, line=tok.line, col=tok.col)
            return self._parse_postfix(node)

        node = Variable(name, line=tok.line, col=tok.col)
        return self._parse_postfix(node)

    def _parse_postfix(self, node):
        """处理属性访问、下标访问、调用链"""
        while True:
            if self.match('SYMBOL', '.'):
                dot = self.advance()
                attr = self.expect('IDENTIFIER')
                node = Attribute(node, attr.value,
                                 line=dot.line, col=dot.col)
            elif self.match('SYMBOL', '['):
                lb = self.advance()
                index = self.parse_expr()
                self.expect('SYMBOL', ']')
                node = Subscript(node, index, line=lb.line, col=lb.col)
            elif self.match('SYMBOL', '('):
                args, kwargs = self.parse_call_arguments()
                node = Call(node, args, kwargs,
                            line=node.line, col=node.col)
            else:
                break
        return node

    def parse_list(self):
        start = self.current()
        self.expect('SYMBOL', '[')
        elements = []
        if not self.match('SYMBOL', ']'):
            while True:
                if self.match('SYMBOL', '*'):
                    star = self.advance()
                    elements.append(Starred(self.parse_expr(),
                                            line=star.line, col=star.col))
                else:
                    elements.append(self.parse_expr())
                if not self.match('SYMBOL', ','):
                    break
                self.advance()
                if self.match('SYMBOL', ']'):
                    break
        self.expect('SYMBOL', ']')
        return ListExpr(elements, line=start.line, col=start.col)

    def parse_dict(self):
        start = self.current()
        self.expect('SYMBOL', '{')
        items = []
        if not self.match('SYMBOL', '}'):
            while True:
                if self.match('SYMBOL', '**'):
                    star = self.advance()
                    items.append((Starred(self.parse_expr(), double=True,
                                          line=star.line, col=star.col), None))
                else:
                    key = self.parse_expr()
                    self.expect('SYMBOL', ':')
                    value = self.parse_expr()
                    items.append((key, value))
                if not self.match('SYMBOL', ','):
                    break
                self.advance()
                if self.match('SYMBOL', '}'):
                    break
        self.expect('SYMBOL', '}')
        return DictExpr(items, line=start.line, col=start.col)
