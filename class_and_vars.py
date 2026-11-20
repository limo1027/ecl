"""ECL 的 AST 节点、运行时值类型与语言常量。

本文件同时被词法/语法/检查/编译/VM/解释器使用，保持零依赖。
"""

import decimal
import ctypes
from dataclasses import dataclass, field
from typing import Any, List, Optional

# double: IEEE 754 binary128 有 34 位十进制有效数字，
# 用 Decimal + prec=34 提供一个可用的高精度近似实现。
decimal.getcontext().prec = 34


BUILTIN_TYPES = {
    'int', 'i64', 'i32', 'str', 'float', 'double', 'bool', 'boolean',
    'list', 'dict', 'set', 'tuple', 'bytes', 'code', 'function',
    'any', 'none', 'null', 'unknown', 'bi', 'cstr', 'ptr', 'buffer',
    'wstr',
}

# 可以在 match 中作为类型模式使用的内置类型
MATCH_TYPE_NAMES = {
    'int', 'i64', 'i32', 'str', 'float', 'double', 'bool', 'list', 'dict',
    'tuple', 'code', 'function', 'any', 'none', 'cstr', 'ptr', 'buffer',
    'wstr',
}

# C ABI 可用的类型（DLL 导出 / dll_import 签名）
C_ABI_TYPES = {
    'int', 'i64', 'i32', 'float', 'double', 'bool', 'str', 'cstr',
    'ptr', 'none', 'wstr',
}

# struct 字段允许的标量类型（嵌套 struct 名由检查器额外允许）
STRUCT_SCALAR_TYPES = {
    'bool', 'i32', 'i64', 'int', 'float', 'double', 'str', 'cstr', 'wstr',
    'ptr',
}

MAGIC_METHODS = {
    '+': '__add__', '-': '__sub__', '*': '__mul__',
    '/': '__div__', '//': '__floordiv__', '%': '__mod__',
    '**': '__pow__', '<': '__lt__', '>': '__gt__',
    '<=': '__le__', '>=': '__ge__', '=': '__eq__',
    '==': '__eq__', '!=': '__ne__', '|': '__bor__',
    '&': '__band__', '^': '__xor__', '<<': '__lshift__',
    '>>': '__rshift__',
}

REVERSE_METHODS = {
    '+': '__radd__', '-': '__rsub__', '*': '__rmul__',
    '/': '__rdiv__', '//': '__rfloordiv__', '%': '__rmod__',
    '**': '__rpow__', '<': '__gt__', '>': '__lt__',
    '<=': '__ge__', '>=': '__le__', '=': '__eq__',
    '==': '__eq__', '!=': '__ne__', '|': '__ror__',
    '&': '__rand__', '^': '__rxor__', '<<': '__rlshift__',
    '>>': '__rrshift__',
}

KEYWORDS = {
    'def', 'end', 'if', 'elif', 'else', 'for', 'while', 'continue',
    'break', 'return', 'class', 'import', 'from', 'as', 'try', 'except',
    'finally', 'raise', 'global', 'last', 'using', 'ref', 'define',
    'with', 'match', 'case', 'goto', 'label', 'public', 'private',
    'const', 'True', 'False', 'true', 'false', 'null', 'none', 'unknown',
    'any', 'in', 'out', 'struct', 'dll_import',
    'and', 'or', 'not', }
OPERATORS = [
    '**=', '//=', '..=', '+=', '-=', '*=', '/=', '%=', '<->',
    '**', '//', '..', '==', '!=', '<=', '>=', '->', '<-', '<<', '>>',
    '+', '-', '*', '/', '%', '=', '<', '>', '!', '|', '&', '^', '~',
    '(', ')', '[', ']', '{', '}', '.', ',', ':', ';', '@',
]


class Node:
    """AST 节点标记基类（不定义 dataclass 字段，避免继承默认值顺序问题）"""
    line: int
    col: int


class Expr(Node):
    pass


class Stmt(Node):
    pass


# ---------- 表达式 ----------

@dataclass
class Literal(Expr):
    value: Any
    line: int = 0
    col: int = 0


@dataclass
class Variable(Expr):
    name: str
    line: int = 0
    col: int = 0


@dataclass
class Binary(Expr):
    left: Expr
    op: str
    right: Expr
    line: int = 0
    col: int = 0


@dataclass
class Unary(Expr):
    op: str
    right: Expr
    line: int = 0
    col: int = 0


@dataclass
class AddressOf(Expr):
    """&expr：取缓冲区/结构体的裸地址。"""
    right: Expr
    line: int = 0
    col: int = 0


@dataclass
class Assign(Expr):
    name: Any          # Variable / Attribute / Subscript
    value: Expr
    is_ref: bool = False
    line: int = 0
    col: int = 0


@dataclass
class Swap(Expr):
    left: str
    right: str
    line: int = 0
    col: int = 0


@dataclass
class Call(Expr):
    func: Expr
    args: List[Expr]
    kwargs: List[tuple]
    line: int = 0
    col: int = 0


@dataclass
class Starred(Expr):
    """解包表达式：*expr / **expr（调用、字面量、赋值共用）"""
    value: Expr
    double: bool = False
    line: int = 0
    col: int = 0


@dataclass
class Attribute(Expr):
    obj: Expr
    attr: str
    line: int = 0
    col: int = 0


@dataclass
class ExternalExec(Expr):
    """@@flat_class(code)：脱糖为 flat_class().__exec__(code)。

    编译器只做语法糖转换，不解析外部语言语法；
    目标类必须实现 __exec__（由检查器校验）。
    """
    class_name: str
    args: List[Expr]
    kwargs: List[tuple]
    line: int = 0
    col: int = 0

    def desugar(self):
        """返回等价的普通调用表达式（实例化 + __exec__ 方法调用）。"""
        cls_call = Call(
            Variable(self.class_name, line=self.line, col=self.col),
            [], [], line=self.line, col=self.col)
        method = Attribute(cls_call, '__exec__',
                           line=self.line, col=self.col)
        return Call(method, self.args, self.kwargs,
                    line=self.line, col=self.col)


@dataclass
class IfExpr(Expr):
    condition: Expr
    then_expr: Expr
    else_expr: Expr
    line: int = 0
    col: int = 0


@dataclass
class ListExpr(Expr):
    elements: List[Expr]
    fixed_length: Optional[int] = None
    line: int = 0
    col: int = 0


@dataclass
class TupleExpr(Expr):
    elements: List[Expr]
    line: int = 0
    col: int = 0


@dataclass
class DictExpr(Expr):
    items: List[tuple]
    line: int = 0
    col: int = 0


@dataclass
class Subscript(Expr):
    obj: Expr
    index: Expr
    line: int = 0
    col: int = 0


@dataclass
class RangeExpr(Expr):
    start: Expr
    end: Expr
    step: Optional[Expr] = None
    inclusive: bool = False
    line: int = 0
    col: int = 0


@dataclass
class CodeLiteral(Expr):
    source: str
    line: int = 0
    col: int = 0


# ---------- 语句 ----------

@dataclass
class ExprStmt(Stmt):
    expr: Expr
    line: int = 0
    col: int = 0


@dataclass
class CompoundStmt(Stmt):
    """一组语句（链式声明等语法糖展开用）"""
    stmts: List[Stmt]
    line: int = 0
    col: int = 0


@dataclass
class DefineStmt(Stmt):
    name: str
    type_name: Optional[str]
    value: Optional[Expr] = None
    is_ref: bool = False
    is_const: bool = False
    visibility: Optional[str] = None
    line: int = 0
    col: int = 0


@dataclass
class UnpackTarget:
    name: str
    is_star: bool = False


@dataclass
class UnpackAssignStmt(Stmt):
    """a, *rest <- [1, 2, 3] 赋值解包"""
    targets: List[UnpackTarget]
    value: Expr
    line: int = 0
    col: int = 0


@dataclass
class FuncDefStmt(Stmt):
    name: str
    params: List[dict]
    varargs: Optional[str]
    varargs_type: Optional[str]
    kwargs: Optional[str]
    kwargs_type: Optional[str]
    defaults: dict
    body: List[Stmt]
    is_single_line: bool
    return_type: Optional[str] = None
    visibility: Optional[str] = None
    varargs_index: Optional[int] = None
    line: int = 0
    col: int = 0


@dataclass
class ClassDefStmt(Stmt):
    name: str
    bases: List[str]
    body: List[Stmt]
    visibility: Optional[str] = None
    line: int = 0
    col: int = 0


@dataclass
class IfStmt(Stmt):
    condition: Expr
    body: List[Stmt]
    elifs: List[tuple]
    else_body: List[Stmt]
    line: int = 0
    col: int = 0


@dataclass
class ForStmt(Stmt):
    variable: Optional[str]
    iterable: Optional[Expr]
    range_expr: Optional[RangeExpr]
    count: Optional[Expr]
    body: List[Stmt]
    line: int = 0
    col: int = 0


@dataclass
class WhileStmt(Stmt):
    condition: Expr
    body: List[Stmt]
    line: int = 0
    col: int = 0


@dataclass
class ReturnStmt(Stmt):
    value: Optional[Expr]
    line: int = 0
    col: int = 0


@dataclass
class BreakStmt(Stmt):
    line: int = 0
    col: int = 0


@dataclass
class ContinueStmt(Stmt):
    line: int = 0
    col: int = 0


@dataclass
class GotoStmt(Stmt):
    label: str
    line: int = 0
    col: int = 0


@dataclass
class LabelStmt(Stmt):
    name: str
    line: int = 0
    col: int = 0


@dataclass
class ImportStmt(Stmt):
    module: str
    alias: Optional[str]
    names: List[tuple]
    line: int = 0
    col: int = 0


@dataclass
class TryStmt(Stmt):
    body: List[Stmt]
    excepts: List[dict]
    else_body: Optional[List[Stmt]]
    finally_body: Optional[List[Stmt]]
    line: int = 0
    col: int = 0


@dataclass
class RaiseStmt(Stmt):
    exception: Expr
    line: int = 0
    col: int = 0


@dataclass
class WithStmt(Stmt):
    # items: [(expr, name_or_None), ...]
    items: List[tuple]
    body: List[Stmt]
    line: int = 0
    col: int = 0


# ---------- match 模式 ----------

@dataclass
class MatchPattern:
    line: int = 0
    col: int = 0


@dataclass
class MatchLiteral(MatchPattern):
    value: Any = None


@dataclass
class MatchType(MatchPattern):
    type_name: str = ''


@dataclass
class MatchMulti(MatchPattern):
    values: List[Any] = field(default_factory=list)


@dataclass
class MatchWildcard(MatchPattern):
    pass


@dataclass
class MatchCase:
    pattern: MatchPattern
    guard: Optional[Expr]
    body: List[Stmt]
    line: int = 0
    col: int = 0


@dataclass
class MatchStmt(Stmt):
    value: Expr
    cases: List[MatchCase]
    line: int = 0
    col: int = 0


@dataclass
class UsingStmt(Stmt):
    """using expr：作用域持续到当前块结束（不需要 end）"""
    expr: Expr
    line: int = 0
    col: int = 0


@dataclass
class StructField:
    type_name: str
    name: str
    line: int = 0
    col: int = 0


@dataclass
class StructDefStmt(Stmt):
    name: str
    fields: List[StructField]
    line: int = 0
    col: int = 0


@dataclass
class DllFuncDecl:
    name: str
    params: List[dict]      # {'name', 'type', 'modifier'}
    return_type: Optional[str] = None
    visibility: Optional[str] = None
    line: int = 0
    col: int = 0


@dataclass
class DllImportStmt(Stmt):
    path: str
    funcs: List[DllFuncDecl]
    line: int = 0
    col: int = 0


@dataclass
class DecoratorStmt(Stmt):
    target: str
    name: Optional[str]
    body: List[Stmt]
    line: int = 0
    col: int = 0


@dataclass
class Program:
    body: List[Stmt]


# ============================================================
# 运行时值类型
# ============================================================

class Cell:
    __slots__ = ('value', 'is_const')

    def __init__(self, value=None, is_const=False):
        self.value = value
        self.is_const = is_const


class NullValue:
    """ECL 的 null：空指针（未初始化 / 已释放），不能作为右值/返回值。"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return "null"

    def __str__(self):
        return "null"

    def __bool__(self):
        return False


class UnknownValue:
    """ECL 的 unknown：使用未声明变量得到的结果。"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return "unknown"

    def __str__(self):
        return "unknown"

    def __bool__(self):
        return False


class _I64Meta(type):
    pass


_MASK64 = (1 << 64) - 1
_SIGN64 = 1 << 63


def i64_wrap(value):
    """把整数按补码回绕到有符号 64 位。"""
    value = int(value) & _MASK64
    if value >= _SIGN64:
        value -= (1 << 64)
    return value


_MASK32 = (1 << 32) - 1
_SIGN32 = 1 << 31


def i32_wrap(value):
    """把整数按补码回绕到有符号 32 位。"""
    value = int(value) & _MASK32
    if value >= _SIGN32:
        value -= (1 << 32)
    return value


class I64(int):
    """固定 64 位整数，溢出回绕。"""

    __slots__ = ()

    def __new__(cls, value=0):
        if isinstance(value, I64):
            value = int(value)
        elif isinstance(value, bool):
            value = int(value)
        elif isinstance(value, float):
            value = int(value)
        elif isinstance(value, decimal.Decimal):
            value = int(value)
        elif isinstance(value, str):
            value = int(value.strip(), 0)
        return super().__new__(cls, i64_wrap(value))

    # ---- 算术 ----
    def __add__(self, other):
        return I64(int(self) + int(other))

    def __radd__(self, other):
        return I64(int(other) + int(self))

    def __sub__(self, other):
        return I64(int(self) - int(other))

    def __rsub__(self, other):
        return I64(int(other) - int(self))

    def __mul__(self, other):
        return I64(int(self) * int(other))

    def __rmul__(self, other):
        return I64(int(other) * int(self))

    def __floordiv__(self, other):
        return I64(int(self) // int(other))

    def __rfloordiv__(self, other):
        return I64(int(other) // int(self))

    def __mod__(self, other):
        return I64(int(self) % int(other))

    def __rmod__(self, other):
        return I64(int(other) % int(self))

    def __pow__(self, other):
        return I64(int(self) ** int(other))

    def __rpow__(self, other):
        return I64(int(other) ** int(self))

    def __neg__(self):
        return I64(-int(self))

    def __invert__(self):
        return I64(~int(self))

    def __or__(self, other):
        return I64(int(self) | int(other))

    def __ror__(self, other):
        return I64(int(other) | int(self))

    def __and__(self, other):
        return I64(int(self) & int(other))

    def __rand__(self, other):
        return I64(int(other) & int(self))

    def __xor__(self, other):
        return I64(int(self) ^ int(other))

    def __rxor__(self, other):
        return I64(int(other) ^ int(self))

    def __lshift__(self, other):
        return I64(int(self) << int(other))

    def __rlshift__(self, other):
        return I64(int(other) << int(self))

    def __rshift__(self, other):
        return I64(int(self) >> int(other))

    def __rrshift__(self, other):
        return I64(int(other) >> int(self))

    def __truediv__(self, other):
        return int(self) / int(other)

    def __rtruediv__(self, other):
        return int(other) / int(self)

    def __repr__(self):
        return str(int(self))

    def __str__(self):
        return str(int(self))


class I32(int):
    """固定 32 位整数，溢出回绕（Win32 结构体 / DLL 签名用）。"""

    __slots__ = ()

    def __new__(cls, value=0):
        if isinstance(value, I32):
            value = int(value)
        elif isinstance(value, bool):
            value = int(value)
        elif isinstance(value, float):
            value = int(value)
        elif isinstance(value, decimal.Decimal):
            value = int(value)
        elif isinstance(value, str):
            value = int(value.strip(), 0)
        return super().__new__(cls, i32_wrap(value))

    def __add__(self, other):
        return I32(int(self) + int(other))

    def __radd__(self, other):
        return I32(int(other) + int(self))

    def __sub__(self, other):
        return I32(int(self) - int(other))

    def __rsub__(self, other):
        return I32(int(other) - int(self))

    def __mul__(self, other):
        return I32(int(self) * int(other))

    def __rmul__(self, other):
        return I32(int(other) * int(self))

    def __floordiv__(self, other):
        return I32(int(self) // int(other))

    def __rfloordiv__(self, other):
        return I32(int(other) // int(self))

    def __mod__(self, other):
        return I32(int(self) % int(other))

    def __rmod__(self, other):
        return I32(int(other) % int(self))

    def __pow__(self, other):
        return I32(int(self) ** int(other))

    def __rpow__(self, other):
        return I32(int(other) ** int(self))

    def __neg__(self):
        return I32(-int(self))

    def __invert__(self):
        return I32(~int(self))

    def __or__(self, other):
        return I32(int(self) | int(other))

    def __ror__(self, other):
        return I32(int(other) | int(self))

    def __and__(self, other):
        return I32(int(self) & int(other))

    def __rand__(self, other):
        return I32(int(other) & int(self))

    def __xor__(self, other):
        return I32(int(self) ^ int(other))

    def __rxor__(self, other):
        return I32(int(other) ^ int(self))

    def __lshift__(self, other):
        return I32(int(self) << int(other))

    def __rlshift__(self, other):
        return I32(int(other) << int(self))

    def __rshift__(self, other):
        return I32(int(self) >> int(other))

    def __rrshift__(self, other):
        return I32(int(other) >> int(self))

    def __truediv__(self, other):
        return int(self) / int(other)

    def __rtruediv__(self, other):
        return int(other) / int(self)

    def __repr__(self):
        return str(int(self))

    def __str__(self):
        return str(int(self))


class WStr(str):
    """宽字符串字面量 w"..."。"""
    __slots__ = ()


class EclBuffer:
    """new_buffer(n) 返回的裸字节缓冲区。"""

    __slots__ = ('size', '_buf', '_addr')

    def __init__(self, size):
        self.size = max(1, int(size))
        self._buf = ctypes.create_string_buffer(self.size)
        self._addr = ctypes.addressof(self._buf)

    def __len__(self):
        return self.size

    def __getitem__(self, idx):
        i = int(idx)
        if i < 0:
            i += self.size
        if i < 0 or i >= self.size:
            raise IndexError("buffer index out of range")
        value = self._buf[i]
        if isinstance(value, bytes):
            return value[0] if value else 0
        return value

    def __setitem__(self, idx, value):
        i = int(idx)
        if i < 0:
            i += self.size
        if i < 0 or i >= self.size:
            raise IndexError("buffer index out of range")
        self._buf[i] = int(value) & 0xFF

    def address(self):
        return self._addr

    def free(self):
        self._buf = None
        self.size = 0

    def __repr__(self):
        return f"<buffer {self.size}>"


class EclStruct:
    """真 struct：字段值 + 可同步到 ctypes 的连续内存。"""

    __slots__ = ('type_name', 'info', 'values', '_obj')

    def __init__(self, type_name, info):
        self.type_name = type_name
        self.info = info
        self.values = {}
        for ftype, fname in info.fields:
            self.values[fname] = self._default(ftype)
        self._obj = None

    def _default(self, ftype):
        if ftype == 'bool':
            return False
        if ftype in ('float', 'double'):
            return 0.0
        if ftype in ('str', 'cstr', 'wstr'):
            return ""
        return 0

    def sync(self):
        from dll_support import struct_to_native
        self._obj = struct_to_native(self.info.registry, self.info,
                                     self.values)
        return self._obj

    def clone(self):
        import copy
        out = EclStruct.__new__(EclStruct)
        out.type_name = self.type_name
        out.info = self.info
        out.values = copy.deepcopy(self.values)
        out._obj = None
        return out

    def address(self):
        import ctypes
        obj = self.sync()
        return ctypes.addressof(obj)

    def __repr__(self):
        return f"<{self.type_name}>"


class EclInstance:
    def __init__(self, cls):
        self.cls = cls
        self.attrs = {}

    def __repr__(self):
        return f"<{self.cls.name} object>"


class BaseProxy:
    """类体方法中引用基类：把基类方法绑定到当前 self。"""

    def __init__(self, instance, base_cls):
        self.instance = instance
        self.base_cls = base_cls

    def __repr__(self):
        return f"<base {self.base_cls.name}>"


class ScopeView:
    """last 视图：读写上层（闭包）作用域。"""

    def __init__(self, scope):
        self.scope = scope

    def __repr__(self):
        return f"<last {list(self.scope.keys())}>"


class EclFunction:
    def __init__(self, name, code, params, varargs, kwargs,
                 ret_type, interpreter, defaults=None, closure=None):
        self.name = name
        self.code = code
        self.params = params
        self.varargs = varargs
        self.kwargs = kwargs
        self.ret_type = ret_type
        self.interpreter = interpreter
        self.defaults = defaults or {}
        self.closure = closure

    def __call__(self, *args, **kwargs):
        return self.interpreter.call_function_ir(self, args, kwargs)

    def __repr__(self):
        return f"<function {self.name}>"


class EclOverload:
    """同名函数按参数类型重载。"""

    def __init__(self, name, interpreter):
        self.name = name
        self.interpreter = interpreter
        self.functions = []

    def add(self, fn):
        # 完全相同的签名（参数类型序列 + 数量）视为覆盖
        sig = [p.get('type') for p in fn.params]
        for i, old in enumerate(self.functions):
            if [p.get('type') for p in old.params] == sig:
                self.functions[i] = fn
                return
        self.functions.append(fn)

    def __call__(self, *args, **kwargs):
        return self.interpreter.call_overload(self, args, kwargs)

    def __repr__(self):
        return f"<overload {self.name}({len(self.functions)})>"


class EclClass:
    def __init__(self, name, bases, methods, interpreter):
        self.name = name
        self.bases = bases          # List[EclClass]
        # name -> (code, params, varargs, kwargs, ret_type, defaults)
        self.methods = methods
        self.interpreter = interpreter

    def find_method(self, name, _seen=None):
        if name in self.methods:
            return self.methods[name]
        seen = _seen if _seen is not None else set()
        if id(self) in seen:
            return None
        seen.add(id(self))
        for base in self.bases:
            if isinstance(base, EclClass):
                m = base.find_method(name, seen)
                if m is not None:
                    return m
        return None

    def has_base_named(self, name):
        for base in self.bases:
            if isinstance(base, EclClass):
                if base.name == name or base.has_base_named(name):
                    return True
        return False

    def find_base_named(self, name):
        for base in self.bases:
            if isinstance(base, EclClass):
                if base.name == name:
                    return base
                found = base.find_base_named(name)
                if found is not None:
                    return found
        return None

    def __call__(self, *args, **kwargs):
        instance = EclInstance(self)
        instance.attrs['__class__'] = self
        # 类体里声明的属性（含继承链）先设置
        for body in self._class_bodies():
            self.interpreter.call_method_ir(body, instance, [], {})
        init = self.find_method('__init__')
        if init:
            self.interpreter.call_method_ir(init, instance, args, kwargs)
        return instance

    def _class_bodies(self):
        out = []
        seen = set()

        def walk(cls):
            if id(cls) in seen:
                return
            seen.add(id(cls))
            for base in cls.bases:
                if isinstance(base, EclClass):
                    walk(base)
            body = cls.methods.get('__class_body__')
            if body is not None:
                out.append(body)

        walk(self)
        return out

    def __repr__(self):
        return f"<class {self.name}>"


NULL = NullValue()
UNKNOWN = UnknownValue()
_NO_MAGIC = object()
