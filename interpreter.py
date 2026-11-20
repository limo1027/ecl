import copy
import math
import random as _random
import sys
from decimal import Decimal, InvalidOperation

from class_and_vars import (
    EclInstance, EclClass, EclFunction, EclOverload, Cell, NULL, UNKNOWN,
    BaseProxy, ScopeView, I64, I32, EclBuffer, EclStruct, WStr,
    CodeLiteral, _NO_MAGIC, MAGIC_METHODS, REVERSE_METHODS,
)
from vm import CodeObject, GlobalView, VM
from compiler import Compiler
from errors import EclException, ReturnSignal


class Interpreter:
    def __init__(self, allow_dll=True):
        self.globals = {}
        self.allow_dll = allow_dll
        self.structs = None      # 延迟导入 dll_support.StructRegistry
        self.dll_libraries = {}  # path -> dll_support.NativeLibrary
        self._int_vm = None      # int 特化函数专用 VM（惰性创建）
        self._init_builtins()

    # ============================================================
    # 内置函数
    # ============================================================

    def _init_builtins(self):
        g = self.globals
        g['print'] = Cell(self._builtin_print)
        g['write'] = Cell(self._builtin_write)
        g['eprint'] = Cell(self._builtin_eprint)
        g['input'] = Cell(self._builtin_input)
        g['type'] = Cell(self._builtin_type)
        g['len'] = Cell(self._builtin_len)
        g['str'] = Cell(self._builtin_str)
        g['int'] = Cell(self._builtin_int)
        g['i64'] = Cell(self._builtin_i64)
        g['float'] = Cell(self._builtin_float)
        g['double'] = Cell(self._builtin_double)
        g['bool'] = Cell(self._builtin_bool)
        g['bi'] = Cell(self._builtin_int)
        g['code'] = Cell(self._builtin_code)
        g['range'] = Cell(self._builtin_range)
        g['list'] = Cell(self._builtin_list)
        g['tuple'] = Cell(self._builtin_tuple)
        g['dict'] = Cell(self._builtin_dict)
        g['slice'] = Cell(self._builtin_slice)
        g['abs'] = Cell(self._builtin_abs)
        g['max'] = Cell(self._builtin_max)
        g['min'] = Cell(self._builtin_min)
        g['sum'] = Cell(self._builtin_sum)
        g['pow'] = Cell(self._builtin_pow)
        g['sqrt'] = Cell(self._builtin_sqrt)
        g['floor'] = Cell(self._builtin_floor)
        g['ceil'] = Cell(self._builtin_ceil)
        g['round'] = Cell(self._builtin_round)
        g['random'] = Cell(self._builtin_random)
        g['free'] = Cell(self._builtin_free)
        g['clone'] = Cell(self._builtin_clone)
        g['id'] = Cell(self._builtin_id)
        g['hash'] = Cell(self._builtin_hash)
        g['exit'] = Cell(self._builtin_exit)
        g['new_buffer'] = Cell(self._builtin_new_buffer)
        g['sizeof'] = Cell(self._builtin_sizeof)
        g['pop'] = Cell(self._builtin_pop)
        for ename in ('Exception', 'ValueError', 'TypeError', 'IndexError',
                      'KeyError', 'ZeroDivisionError', 'RuntimeError',
                      'NameError', 'AttributeError', 'ConstChangingError',
                      'SyntaxError', 'DllNotFoundError'):
            g[ename] = Cell(self._make_exception_class(ename))

    @staticmethod
    def _make_exception_class(name):
        class _Exc:
            exc_type = name

            def __init__(self, msg=""):
                self.message = str(msg)

            def __repr__(self):
                return f"{name}({self.message!r})"

            def __str__(self):
                return self.message
        return _Exc

    # ============================================================
    # struct / dll_import
    # ============================================================

    def _struct_registry(self):
        if self.structs is None:
            from dll_support import StructRegistry
            self.structs = StructRegistry()
        return self.structs

    def register_struct(self, name, fields):
        self._struct_registry().define(name, fields)

    def new_struct(self, name):
        registry = self._struct_registry()
        return EclStruct(name, registry.get(name))

    def get_library(self, path):
        from dll_support import NativeLibrary
        library = self.dll_libraries.get(path)
        if library is None:
            library = NativeLibrary(path)
            self.dll_libraries[path] = library
        return library

    def install_dll(self, path, descs, scope=None):
        if not self.allow_dll:
            self.error("dll_import 已被 --no-dll 禁用", None, 'RuntimeError')
        from dll_support import LazyNativeFunction
        registry = self._struct_registry()
        for decl in descs:
            fn = LazyNativeFunction(self, path, decl, registry)
            self.globals[decl[0]] = Cell(fn)
            if scope is not None and scope is not self.globals:
                scope[decl[0]] = Cell(fn)

    # ---------- 输入输出 ----------

    def _format(self, value):
        return self._builtin_str(value)

    def _builtin_print(self, *args):
        print(' '.join(self._format(a) for a in args))
        return None

    def _builtin_write(self, *args):
        sys.stdout.write(' '.join(self._format(a) for a in args))
        return None

    def _builtin_eprint(self, *args):
        print(' '.join(self._format(a) for a in args), file=sys.stderr)
        return None

    def _builtin_input(self, prompt=""):
        return input(str(prompt))

    # ---------- 类型 ----------

    def _builtin_type(self, obj):
        if obj is None:
            return 'none'
        if obj is NULL:
            return 'null'
        if obj is UNKNOWN:
            return 'unknown'
        if isinstance(obj, bool):
            return 'bool'
        if isinstance(obj, EclException):
            return obj.exc_type
        if isinstance(obj, I64):
            return 'i64'
        if isinstance(obj, I32):
            return 'i32'
        if isinstance(obj, int):
            return 'int'
        if isinstance(obj, Decimal):
            return 'double'
        if isinstance(obj, float):
            return 'float'
        if isinstance(obj, WStr):
            return 'wstr'
        if isinstance(obj, str):
            return 'str'
        if isinstance(obj, list):
            return 'list'
        if isinstance(obj, dict):
            return 'dict'
        if isinstance(obj, tuple):
            return 'tuple'
        if isinstance(obj, CodeObject):
            return 'code'
        if isinstance(obj, EclBuffer):
            return 'buffer'
        if isinstance(obj, EclStruct):
            return obj.type_name
        if isinstance(obj, EclInstance):
            return obj.cls.name
        if isinstance(obj, EclClass):
            return 'class'
        if isinstance(obj, (EclFunction, EclOverload)):
            return 'function'
        if isinstance(obj, GlobalView):
            return 'global'
        if isinstance(obj, ScopeView):
            return 'last'
        return type(obj).__name__

    def _builtin_len(self, obj):
        if obj is UNKNOWN:
            self.error("unknown 没有长度", None, 'TypeError')
        try:
            return len(obj)
        except TypeError:
            self.error(
                f"{self._builtin_type(obj)} 没有长度", None, 'TypeError')

    def _builtin_pop(self, iterable):
        return iterable.pop()

    def _builtin_str(self, obj):
        if obj is None:
            return "none"
        if obj is NULL:
            return "null"
        if obj is UNKNOWN:
            return "unknown"
        if isinstance(obj, EclInstance):
            method_stmt = obj.cls.find_method('__str__')
            if method_stmt is not None:
                result = self.call_method_ir(method_stmt, obj, [], {})
                return self._builtin_str(result)
            return repr(obj)
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, bool):
            return "True" if obj else "False"
        return str(obj)

    def _builtin_int(self, obj):
        if obj is UNKNOWN:
            self.error("不能把 unknown 转换为 int", None, 'TypeError')
        if obj is NULL:
            return NULL
        try:
            if isinstance(obj, bool):
                return int(obj)
            if isinstance(obj, (int, float, Decimal)):
                return int(obj)
            if isinstance(obj, str):
                return int(obj.strip(), 0)
            return int(obj)
        except (ValueError, TypeError):
            self.error(
                f"无法将 {self._builtin_type(obj)} 转换为 int",
                None, 'TypeError')

    def _builtin_i64(self, obj):
        if obj is NULL:
            return NULL
        try:
            if isinstance(obj, str):
                return I64(int(obj.strip(), 0))
            return I64(obj)
        except (ValueError, TypeError):
            self.error(
                f"无法将 {self._builtin_type(obj)} 转换为 i64",
                None, 'TypeError')

    def _builtin_float(self, obj):
        if obj is UNKNOWN:
            self.error("不能把 unknown 转换为 float", None, 'TypeError')
        if obj is NULL:
            return NULL
        if isinstance(obj, (I64, Decimal)):
            return float(obj)
        try:
            return float(obj)
        except (ValueError, TypeError):
            self.error(
                f"无法将 {self._builtin_type(obj)} 转换为 float",
                None, 'TypeError')

    def _builtin_double(self, obj):
        if obj is UNKNOWN:
            self.error("不能把 unknown 转换为 double", None, 'TypeError')
        if obj is NULL:
            return NULL
        if isinstance(obj, Decimal):
            return obj
        if isinstance(obj, float):
            return Decimal(str(obj))
        try:
            return Decimal(str(obj))
        except InvalidOperation:
            self.error(
                f"无法将 {self._builtin_type(obj)} 转换为 double",
                None, 'TypeError')

    def _builtin_bool(self, obj):
        return self._truthy(obj)

    def _builtin_code(self, s):
        if not isinstance(s, str):
            self.error("code() 的参数必须是 str", None, 'TypeError')
        return CodeObject(s, self.globals, self)

    def _builtin_range(self, *args):
        return list(range(*[int(a) for a in args]))

    def _builtin_list(self, obj=None):
        if obj is None:
            return []
        if obj is NULL or obj is UNKNOWN:
            self.error("无法转换 null/unknown 为 list", None, 'TypeError')
        try:
            return list(obj)
        except TypeError:
            return [obj]

    def _builtin_tuple(self, obj=None):
        if obj is None:
            return ()
        if obj is NULL or obj is UNKNOWN:
            self.error("无法转换 null/unknown 为 tuple", None, 'TypeError')
        if isinstance(obj, dict):
            return tuple(obj.keys())
        try:
            return tuple(obj)
        except TypeError:
            return (obj,)

    def _builtin_dict(self, obj=None):
        if obj is None:
            return {}
        if isinstance(obj, dict):
            return dict(obj)
        if isinstance(obj, (list, tuple)):
            try:
                return dict(obj)
            except (TypeError, ValueError):
                self.error("dict() 需要键值对序列", None, 'TypeError')
        self.error(
            f"无法将 {self._builtin_type(obj)} 转换为 dict",
            None, 'TypeError')

    def _builtin_slice(self, seq, start, end):
        if seq is UNKNOWN:
            self.error("不能对 unknown 切片", None, 'TypeError')
        return seq[int(start):int(end)]

    # ---------- 数学 ----------

    def _builtin_abs(self, x):
        if x is UNKNOWN:
            self.error("unknown 不能参与运算", None, 'TypeError')
        return abs(x)

    def _builtin_max(self, *args):
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            args = tuple(args[0])
        return max(args)

    def _builtin_min(self, *args):
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            args = tuple(args[0])
        return min(args)

    def _builtin_sum(self, seq):
        if isinstance(seq, dict):
            seq = list(seq.values())
        total = 0
        for x in seq:
            total = self.native_binop('add', total, x)
        return total

    def _builtin_pow(self, base, exp):
        return self.native_binop('pow', base, exp)

    def _builtin_sqrt(self, x):
        if isinstance(x, Decimal):
            return x.sqrt()
        return math.sqrt(x)

    def _builtin_floor(self, x):
        if isinstance(x, Decimal):
            return int(x.to_integral_value(rounding='ROUND_FLOOR'))
        return math.floor(x)

    def _builtin_ceil(self, x):
        if isinstance(x, Decimal):
            return int(x.to_integral_value(rounding='ROUND_CEILING'))
        return math.ceil(x)

    def _builtin_round(self, x, digits=0):
        return round(x, int(digits))

    def _builtin_random(self, *args):
        if args:
            return _random.randrange(int(args[0]))
        return _random.random()

    # ---------- 内存 ----------

    def _builtin_free(self, obj):
        if isinstance(obj, EclBuffer):
            obj.free()
        elif isinstance(obj, EclStruct):
            obj.values.clear()
        return NULL

    def _builtin_new_buffer(self, size):
        n = int(size)
        if n <= 0:
            self.error("new_buffer 大小必须大于 0", None, 'ValueError')
        return EclBuffer(n)

    def _builtin_sizeof(self, obj):
        if isinstance(obj, EclBuffer):
            return obj.size
        if isinstance(obj, EclStruct):
            return obj.info.size()
        try:
            return len(obj)
        except TypeError:
            self.error(
                f"{self._builtin_type(obj)} 没有大小", None, 'TypeError')

    def _builtin_clone(self, obj):
        try:
            return copy.deepcopy(obj)
        except Exception:
            return obj

    def _builtin_id(self, obj):
        return id(obj)

    def _builtin_hash(self, obj):
        try:
            return hash(obj)
        except TypeError:
            self.error(
                f"{self._builtin_type(obj)} 不可哈希", None, 'TypeError')

    def _builtin_exit(self, code=0):
        raise SystemExit(int(code))

    # ============================================================
    # 异常 / 类型工具
    # ============================================================

    def error(self, msg, node=None, exc_type='RuntimeError'):
        if node is not None and hasattr(node, 'line'):
            raise EclException(exc_type, msg, line=node.line, col=node.col)
        raise EclException(exc_type, msg)

    @staticmethod
    def _truthy(v):
        if v is NULL or v is None or v is UNKNOWN:
            return False
        if v is NotImplemented:
            return False
        return bool(v)

    def exception_type(self, exc_type):
        cell = self.globals.get(exc_type)
        if cell is not None:
            return cell.value
        return exc_type

    @staticmethod
    def exc_matches(exc_type, wanted):
        if wanted is None:
            return True
        if wanted == 'Exception':
            return True
        return exc_type == wanted

    def convert_type(self, value, type_name, node=None):
        if type_name is None or type_name in ('any',):
            return value
        if value is UNKNOWN:
            self.error("unknown 不能作为右值参与转换", node, 'TypeError')
        if value is NULL:
            return NULL
        base = type_name.split('[')[0].strip().split()[0]
        if base in ('int', 'i64', 'i32', 'float', 'double', 'str',
                    'bool', 'bi', 'cstr', 'ptr'):
            if base == 'int':
                return self._builtin_int(value)
            if base == 'i64':
                return self._builtin_i64(value)
            if base == 'i32':
                return I32(value)
            if base == 'cstr':
                return self._builtin_str(value)
            if base == 'ptr':
                return self._builtin_int(value)
            if base == 'float':
                return self._builtin_float(value)
            if base == 'double':
                return self._builtin_double(value)
            if base == 'bool':
                return self._truthy(value)
            if base == 'str':
                return self._builtin_str(value)
            if base == 'bi':
                return self._builtin_int(value)
        return value

    def is_type(self, value, name):
        if name == 'any':
            return True
        if name == 'unknown':
            return value is UNKNOWN
        if name == 'none':
            return value is None
        if name == 'null':
            return value is NULL
        if name == 'bool':
            return isinstance(value, bool)
        if name == 'i64':
            return isinstance(value, I64)
        if name == 'i32':
            return isinstance(value, I32)
        if name == 'int':
            return isinstance(value, int) and not isinstance(
                value, (bool, I64, I32))
        if name == 'double':
            return isinstance(value, Decimal)
        if name == 'float':
            return isinstance(value, float)
        if name == 'str':
            return isinstance(value, str)
        if name == 'cstr':
            return isinstance(value, str)
        if name == 'ptr':
            return isinstance(value, int) and not isinstance(
                value, (bool, I64, I32))
        if name == 'list':
            return isinstance(value, list)
        if name == 'dict':
            return isinstance(value, dict)
        if name == 'tuple':
            return isinstance(value, tuple)
        if name == 'code':
            return isinstance(value, CodeObject)
        if name == 'buffer':
            return isinstance(value, EclBuffer)
        if name == 'wstr':
            return isinstance(value, WStr)
        if isinstance(value, EclStruct):
            return value.type_name == name
        if name == 'function':
            return isinstance(value, (EclFunction, EclOverload)) \
                or callable(value)
        if isinstance(value, EclInstance):
            return value.cls.name == name
        if isinstance(value, EclClass):
            return value.name == name
        return False

    def match_eq(self, a, b):
        if a is UNKNOWN or b is UNKNOWN:
            return a is UNKNOWN and b is UNKNOWN
        try:
            return bool(a == b)
        except Exception:
            return a is b

    # ============================================================
    # 运算
    # ============================================================

    @staticmethod
    def _normalize_numeric(a, b):
        if isinstance(a, Decimal) and isinstance(b, float):
            b = Decimal(str(b))
        elif isinstance(b, Decimal) and isinstance(a, float):
            a = Decimal(str(a))
        if isinstance(a, I64) and isinstance(b, Decimal):
            a = Decimal(int(a))
        elif isinstance(b, I64) and isinstance(a, Decimal):
            b = Decimal(int(b))
        return a, b

    def native_binop(self, kind, a, b):
        op = {
            'add': '+', 'sub': '-', 'mul': '*', 'div': '/',
            'floordiv': '//', 'mod': '%', 'pow': '**',
            'eq': '==', 'ne': '!=', 'lt': '<', 'gt': '>',
            'le': '<=', 'ge': '>=',
            'bor': '|', 'band': '&', 'xor': '^',
            'lshift': '<<', 'rshift': '>>',
        }.get(kind, kind)

        if kind in ('eq', 'ne'):
            if a is UNKNOWN or b is UNKNOWN:
                both = a is UNKNOWN and b is UNKNOWN
                return both if kind == 'eq' else not both
            return bool(a == b) if kind == 'eq' else bool(a != b)

        if a is UNKNOWN or b is UNKNOWN:
            self.error("unknown 不能参与运算", None, 'TypeError')

        a, b = self._normalize_numeric(a, b)

        try:
            result = None
            if op == '+':
                result = a + b
            elif op == '-':
                result = a - b
            elif op == '*':
                result = a * b
            elif op == '/':
                if b == 0:
                    self.error("除零错误", None, 'ZeroDivisionError')
                result = a / b
            elif op == '//':
                if b == 0:
                    self.error("除零错误", None, 'ZeroDivisionError')
                result = a // b
            elif op == '%':
                if b == 0:
                    self.error("除零错误", None, 'ZeroDivisionError')
                result = a % b
            elif op == '**':
                result = a ** b
            elif op == '<':
                result = a < b
            elif op == '>':
                result = a > b
            elif op == '<=':
                result = a <= b
            elif op == '>=':
                result = a >= b
            elif op == '|':
                result = a | b
            elif op == '&':
                result = a & b
            elif op == '^':
                result = a ^ b
            elif op == '<<':
                result = a << b
            elif op == '>>':
                result = a >> b

            if result is NotImplemented:
                self.error(
                    f"类型不支持运算: {self._builtin_type(a)} {op} "
                    f"{self._builtin_type(b)}",
                    None, 'TypeError')
            return result
        except EclException:
            raise
        except ZeroDivisionError:
            self.error("除零错误", None, 'ZeroDivisionError')
        except TypeError as e:
            self.error(f"类型错误: {e}", None, 'TypeError')
        except Exception as e:
            self.error(f"运算错误: {e}", None, type(e).__name__)
        self.error(f"未知运算符: {op}", None)

    def native_unary(self, op, a):
        if a is UNKNOWN:
            self.error("unknown 不能参与运算", None, 'TypeError')
        if op == '-':
            return -a
        if op == '~':
            return ~a
        if op in ('!', 'not'):
            return not self._truthy(a)
        self.error(f"未知一元运算符: {op}", None)

    def apply_binary(self, op, a, b):
        magic = self.try_magic_method(a, b, op)
        if magic is not _NO_MAGIC:
            return magic
        kind = {
            '+': 'add', '-': 'sub', '*': 'mul', '/': 'div',
            '//': 'floordiv', '%': 'mod', '**': 'pow',
            '=': 'eq', '==': 'eq', '!=': 'ne',
            '<': 'lt', '>': 'gt', '<=': 'le', '>=': 'ge',
            '|': 'bor', '&': 'band', '^': 'xor',
            '<<': 'lshift', '>>': 'rshift',
        }.get(op)
        if kind is None:
            self.error(f"未知二元运算符: {op}", None)
        return self.native_binop(kind, a, b)

    def apply_unary(self, op, a):
        if op in ('-', '~') and isinstance(a, EclInstance):
            method = a.cls.find_method(
                '__neg__' if op == '-' else '__invert__')
            if method:
                return self.call_method_ir(method, a, [], {})
        return self.native_unary(op, a)

    def try_magic_method(self, left, right, op):
        method_name = MAGIC_METHODS.get(op)
        if method_name is None:
            return _NO_MAGIC
        if isinstance(left, (EclInstance, EclClass)):
            result = self.call_magic(left, method_name, [right])
            if result is not _NO_MAGIC:
                return result
        if isinstance(right, (EclInstance, EclClass)):
            reverse_name = REVERSE_METHODS.get(op)
            if reverse_name is not None:
                result = self.call_magic(right, reverse_name, [left])
                if result is not _NO_MAGIC:
                    return result
        return _NO_MAGIC

    def call_magic(self, obj, method_name, args):
        if isinstance(obj, EclInstance):
            method_stmt = obj.cls.find_method(method_name)
            if method_stmt is not None:
                return self.call_method_ir(method_stmt, obj, args, {})
            if method_name in obj.attrs:
                fn = obj.attrs[method_name]
                if callable(fn):
                    return fn(*args)
            return _NO_MAGIC
        if isinstance(obj, EclClass):
            method_stmt = obj.find_method(method_name)
            if method_stmt is not None:
                if len(method_stmt) == 6:
                    code, params, varargs, kwargs, ret, defaults = \
                        method_stmt
                else:
                    code, params, varargs, kwargs, ret = method_stmt
                    defaults = {}
                fn = EclFunction(method_name, code, params, varargs,
                                 kwargs, ret, self, defaults)
                return self.call_function_ir(fn, args, {})
        return _NO_MAGIC

    # ============================================================
    # 属性 / 方法
    # ============================================================

    def _base_proxy_attr(self, proxy, name):
        method = proxy.base_cls.find_method(name)
        if method is not None:
            instance = proxy.instance
            return lambda *a, **kw: self.call_method_ir(
                method, instance, a, kw)
        if name in proxy.instance.attrs:
            return proxy.instance.attrs[name]
        return NULL

    def address_of(self, obj):
        if isinstance(obj, EclBuffer):
            return obj.address()
        if isinstance(obj, EclStruct):
            return obj.address()
        self.error(
            f"不能对 {self._builtin_type(obj)} 取地址", None, 'TypeError')

    def get_attr(self, obj, name):
        # ---------- CodeObject ----------
        if isinstance(obj, CodeObject):
            if name == 'run':
                return obj.run
            self.error(
                f"code 对象没有属性 '{name}'", None, 'AttributeError')

        # ---------- GlobalView ----------
        if isinstance(obj, GlobalView):
            if name == 'keys':
                return obj.keys
            if name == 'get':
                return lambda k, default=None: (
                    obj.cells[k].value if k in obj.cells else default)
            if name == 'has':
                return lambda k: k in obj.cells
            if name in obj.cells:
                return obj.cells[name].value
            self.error(
                f"global 对象没有变量 '{name}'", None, 'AttributeError')

        # ---------- ScopeView (last) ----------
        if isinstance(obj, ScopeView):
            if name in obj.scope:
                return obj.scope[name].value
            return UNKNOWN

        # ---------- BaseProxy ----------
        if isinstance(obj, BaseProxy):
            return self._base_proxy_attr(obj, name)

        # ---------- Struct ----------
        if isinstance(obj, EclStruct):
            return obj.values.get(name, NULL)

        # ---------- EclInstance ----------
        if isinstance(obj, EclInstance):
            if name in obj.attrs:
                return obj.attrs[name]
            method_stmt = obj.cls.find_method(name)
            if method_stmt is not None:
                return lambda *a, **kw: self.call_method_ir(
                    method_stmt, obj, a, kw)
            if name.startswith('__') and name.endswith('__'):
                return NULL
            self.error(
                f"对象 '{obj.cls.name}' 没有属性 '{name}'",
                None, 'AttributeError')

        # ---------- EclClass ----------
        if isinstance(obj, EclClass):
            method_stmt = obj.find_method(name)
            if method_stmt is not None:
                if len(method_stmt) == 6:
                    code, params, varargs, kwargs, ret, defaults = \
                        method_stmt
                else:
                    code, params, varargs, kwargs, ret = method_stmt
                    defaults = {}
                fn = EclFunction(name, code, params, varargs, kwargs,
                                 ret, self, defaults)
                return lambda *a, **kw: self.call_function_ir(fn, a, kw)
            if name.startswith('__') and name.endswith('__'):
                return NULL
            self.error(
                f"类 '{obj.name}' 没有属性 '{name}'",
                None, 'AttributeError')

        # ---------- 原生类型 ----------
        DIV_MAP = {
            '__div__': '__truediv__',
            '__rdiv__': '__rtruediv__',
            '__bor__': '__or__',
            '__band__': '__and__',
        }
        if isinstance(obj, dict) and name == 'has':
            return lambda k: k in obj
        if isinstance(obj, dict) and name == 'get':
            return lambda k, default=None: obj.get(k, default)
        if isinstance(obj, dict) and name == 'keys':
            return lambda: list(obj.keys())
        if isinstance(obj, dict) and name == 'values':
            return lambda: list(obj.values())
        if isinstance(obj, tuple) and name == 'get':
            return lambda i: obj[int(i)]
        try:
            return getattr(obj, name)
        except AttributeError:
            if name in DIV_MAP:
                try:
                    return getattr(obj, DIV_MAP[name])
                except AttributeError:
                    return NULL
            if name.startswith('__') and name.endswith('__'):
                return NULL
            self.error(
                f"对象没有属性 '{name}'", None, 'AttributeError')

    def get_attr_opt(self, obj, name):
        try:
            return self.get_attr(obj, name)
        except EclException:
            return NULL

    def set_attr(self, obj, name, val):
        if isinstance(obj, EclStruct):
            obj.values[name] = val
            obj._obj = None
            return
        if isinstance(obj, GlobalView):
            obj.set(name, val)
            return
        if isinstance(obj, ScopeView):
            if name in obj.scope:
                obj.scope[name].value = val
            else:
                obj.scope[name] = Cell(val)
            return
        if isinstance(obj, BaseProxy):
            obj.instance.attrs[name] = val
            return
        if isinstance(obj, EclInstance):
            obj.attrs[name] = val
            return
        try:
            setattr(obj, name, val)
        except AttributeError:
            self.error(
                f"对象不支持属性赋值: {name}", None, 'AttributeError')

    # ============================================================
    # 调用
    # ============================================================

    def _ensure_int_vm(self):
        """int 特化函数专用的轻量 VM（惰性创建，递归时复用）。"""
        if self._int_vm is None:
            self._int_vm = VM([], [], {}, self)
            self._int_vm.scope = self.globals
            self._int_vm.parent_scope = []
        return self._int_vm

    def call_value(self, func, args, kwargs):
        # ---------- int 特化函数：走 VM 的 int 解释路径 ----------
        if getattr(func, 'is_int_func', False):
            if kwargs:
                self.error(
                    f"int 特化函数 {func.name} 暂不支持关键字调用",
                    None, 'TypeError')
            return self._ensure_int_vm().call_int_func(func, list(args))

        if isinstance(func, EclInstance):
            method = func.cls.find_method('__call__')
            if method is not None:
                return self.call_method_ir(method, func, args, kwargs)
            self.error("对象不可调用（没有 __call__）", None, 'TypeError')

        if not callable(func):
            self.error(f"对象不可调用: {func!r}", None, 'TypeError')

        try:
            return func(*args, **kwargs)
        except EclException:
            raise
        except SystemExit:
            raise
        except TypeError as e:
            self.error(f"调用参数错误: {e}", None, 'TypeError')
        except Exception as e:
            self.error(f"调用错误: {e}", None, type(e).__name__)

    def _bind_bases(self, scope, instance):
        """把实例所属类的所有基类名绑定为 BaseProxy。"""
        seen = set()

        def walk(cls):
            if id(cls) in seen:
                return
            seen.add(id(cls))
            for base in cls.bases:
                if isinstance(base, EclClass):
                    if base.name not in scope:
                        scope[base.name] = Cell(BaseProxy(instance, base))
                    walk(base)

        walk(instance.cls)

    def call_function_ir(self, fn, args, kwargs, self_instance=None):
        scope = {}
        if self_instance is not None:
            scope['self'] = Cell(self_instance)
            if isinstance(self_instance, EclInstance):
                self._bind_bases(scope, self_instance)

        params = fn.params
        pos = 0
        param_names = set()
        positional_names = set()

        for p in params:
            pname = p['name']
            param_names.add(pname)

            if p.get('varargs'):
                rest = list(args[pos:])
                pos = len(args)
                if p.get('type'):
                    rest = [self.convert_type(x, p['type']) for x in rest]
                scope[pname] = Cell(rest)
                continue

            if p.get('kwargs'):
                continue

            if pos < len(args):
                val = args[pos]
                pos += 1
                positional_names.add(pname)
            elif pname in kwargs:
                val = kwargs[pname]
            elif pname in fn.defaults:
                val = fn.defaults[pname]
            else:
                self.error(
                    f"函数 '{fn.name}' 缺少参数 '{pname}'",
                    None, 'TypeError')

            if p.get('type'):
                val = self.convert_type(val, p['type'])
            scope[pname] = Cell(val)

        if pos < len(args):
            self.error(f"函数 '{fn.name}' 参数过多", None, 'TypeError')

        extra_kw = {}
        for k, v in kwargs.items():
            if k in positional_names:
                self.error(
                    f"函数 '{fn.name}' 的关键字参数 '{k}' "
                    f"与位置参数冲突",
                    None, 'TypeError')
            if k in param_names:
                continue
            if fn.kwargs:
                extra_kw[k] = v
            else:
                self.error(
                    f"函数 '{fn.name}' 收到未知关键字参数 '{k}'",
                    None, 'TypeError')

        if fn.kwargs:
            scope[fn.kwargs] = Cell(extra_kw)

        vm = VM(fn.code, [], scope, self, parent_scope=fn.closure)
        try:
            vm.run()
        except ReturnSignal as r:
            ret = r.value
        else:
            ret = None

        if fn.ret_type and ret is not None and ret is not NULL \
                and ret is not UNKNOWN:
            ret = self.convert_type(ret, fn.ret_type)
        return ret

    def call_method_ir(self, method_stmt, instance, args, kwargs):
        if isinstance(method_stmt, tuple):
            if len(method_stmt) == 6:
                code, params, varargs, kwargs_name, ret, defaults = \
                    method_stmt
            else:
                code, params, varargs, kwargs_name, ret = method_stmt
                defaults = {}
            fn = EclFunction('__method__', code, params, varargs,
                             kwargs_name, ret, self, defaults)
            return self.call_function_ir(
                fn, (instance,) + tuple(args), kwargs,
                self_instance=instance)
        self.error("call_method_ir 不支持 AST 版方法", None)

    def call_overload(self, ov, args, kwargs):
        supplied = len(args)
        candidates = []
        for fn in ov.functions:
            positional = [p for p in fn.params
                          if not p.get('varargs') and not p.get('kwargs')]
            required = sum(1 for p in positional
                           if p['name'] not in fn.defaults)
            has_varargs = any(p.get('varargs') for p in fn.params)
            if required <= supplied <= len(positional) or (
                    has_varargs and supplied >= required):
                candidates.append(fn)

        if not candidates:
            self.error(
                f"函数 '{ov.name}' 没有匹配的重载（传入 {supplied} 个参数）",
                None, 'TypeError')

        def score(fn, index):
            positional = [p for p in fn.params
                          if not p.get('varargs') and not p.get('kwargs')]
            s = 0
            for i, arg in enumerate(args):
                if i >= len(positional):
                    break
                expected = positional[i].get('type')
                actual = self._builtin_type(arg)
                if expected and actual == expected:
                    s += 2
                elif expected and expected in ('int', 'i64', 'float',
                                               'double') \
                        and actual in ('int', 'i64', 'float', 'double'):
                    s += 1
            return (-s, index)

        best = min(enumerate(candidates),
                   key=lambda pair: score(pair[1], pair[0]))[1]
        return self.call_value(best, list(args), dict(kwargs))

    def run_bytecode(self, code, exception_table):
        """纯粹的 IR 执行入口"""
        vm = VM(code, exception_table, self.globals, self)
        try:
            vm.run()
        except ReturnSignal:
            pass
        return None

    # ============================================================
    # REPL 钩子 (ecl_repl.py 使用)
    # ============================================================

    def set_global(self, name, value):
        """REPL 的 _ 回写。globals 是 name -> Cell,所以包一层 Cell"""
        self.globals[name] = Cell(value)

    def list_globals(self):
        """REPL 的 dir()。返回所有全局名(含内置),过滤下划线开头可选"""
        return sorted(self.globals.keys())

    def get_global(self, name):
        """REPL 的 ?x 读取。不存在返回 UNKNOWN 而不是报错"""
        cell = self.globals.get(name)
        return cell.value if cell is not None else UNKNOWN

    def type_of(self, value):
        """REPL 的 ?x 类型显示。直接复用 _builtin_type"""
        return self._builtin_type(value)

    def repr_value(self, value):
        """REPL 的回显格式。优先 __str__,再兜底 repr"""
        try:
            return self._builtin_str(value)
        except Exception:
            return repr(value)
