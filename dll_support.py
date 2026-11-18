"""ECL dll_import 运行时：ctypes 加载 DLL、struct 布局与参数编组。

struct 在 ECL 里用 dict 表示，例如 POINT -> {"x": 0, "y": 0}。
"""

import ctypes
import os

from class_and_vars import I32, I64

_CTYPE = {
    'bool': ctypes.c_int32,          # BOOL (4 字节)
    'i32': ctypes.c_int32,
    'i64': ctypes.c_int64,
    'int': ctypes.c_int64,
    'float': ctypes.c_float,
    'double': ctypes.c_double,
    'str': ctypes.c_wchar_p,
    'cstr': ctypes.c_char_p,
    'wstr': ctypes.c_wchar_p,
    'ptr': ctypes.c_void_p,
}


def bit_cast_double(bits):
    """把裸指针里保存的 64 位数据还原成 double（用于 double 返回值）。"""
    import struct
    if bits is None:
        return 0.0
    return struct.unpack('<d', struct.pack('<Q', int(bits) & 0xFFFFFFFFFFFFFFFF))[0]


def wcslen(buf):
    """在 ctypes 宽字符数组里找到 NUL 结尾。"""
    n = 0
    while buf[n] != '\0':
        n += 1
    return n


class StructInfo:
    def __init__(self, name, fields, cls, registry=None):
        self.name = name
        self.fields = fields        # [(type_name, field_name)]
        self.cls = cls
        self.registry = registry

    def size(self):
        return ctypes.sizeof(self.cls)


class StructRegistry:
    def __init__(self):
        self.raw = {}               # name -> [(type_name, field_name)]
        self.infos = {}
        self._building = set()

    def define(self, name, fields):
        self.raw[name] = [(str(t), str(f)) for t, f in fields]
        self.infos.pop(name, None)

    def known(self, name):
        return name in self.raw

    def get(self, name):
        if name in self.infos:
            return self.infos[name]
        if name not in self.raw:
            raise KeyError(name)
        if name in self._building:
            raise ValueError(f"struct 递归嵌套: {name}")
        self._building.add(name)
        try:
            ctypes_fields = []
            for type_name, field_name in self.raw[name]:
                if type_name in self.raw:
                    sub = self.get(type_name)
                    ctypes_fields.append((field_name, sub.cls))
                elif type_name in _CTYPE:
                    ctypes_fields.append((field_name, _CTYPE[type_name]))
                else:
                    raise ValueError(
                        f"struct '{name}' 字段 '{field_name}' 的类型 "
                        f"'{type_name}' 不支持")
            cls = type(f"ECLStruct_{name}", (ctypes.Structure,),
                       {'_fields_': ctypes_fields})
            info = StructInfo(name, list(self.raw[name]), cls, self)
            self.infos[name] = info
            return info
        finally:
            self._building.discard(name)

    def default_dict(self, name):
        return {fname: self.default_value(t)
                for t, fname in self.raw[name]}

    def default_value(self, type_name):
        if type_name in self.raw:
            return self.default_dict(type_name)
        if type_name == 'bool':
            return False
        if type_name in ('float', 'double'):
            return 0.0
        return "" if type_name in ('str', 'cstr') else 0


def struct_to_native(registry, info, value):
    """dict -> ctypes.Structure（按字段类型递归转换）。"""
    if not isinstance(value, dict):
        value = {}
    obj = info.cls()
    for type_name, field_name in info.fields:
        v = value.get(field_name, registry.default_value(type_name))
        if type_name in registry.raw:
            sub = registry.get(type_name)
            setattr(obj, field_name, struct_to_native(registry, sub, v))
        elif type_name == 'bool':
            setattr(obj, field_name, ctypes.c_int32(1 if v else 0))
        elif type_name == 'i32':
            setattr(obj, field_name, ctypes.c_int32(I32(v)))
        elif type_name in ('i64', 'int'):
            setattr(obj, field_name, ctypes.c_int64(int(v)))
        elif type_name == 'float':
            setattr(obj, field_name, ctypes.c_float(float(v)))
        elif type_name == 'double':
            setattr(obj, field_name, ctypes.c_double(float(v)))
        elif type_name == 'str':
            setattr(obj, field_name, str(v))
        elif type_name == 'cstr':
            setattr(obj, field_name,
                    v if isinstance(v, bytes) else str(v).encode('utf-8'))
        elif type_name == 'ptr':
            setattr(obj, field_name, ctypes.c_void_p(int(v or 0)))
    return obj


def struct_from_native(registry, info, obj):
    """ctypes.Structure -> dict。"""
    out = {}
    for type_name, field_name in info.fields:
        v = getattr(obj, field_name)
        if type_name in registry.raw:
            out[field_name] = struct_from_native(
                registry, registry.get(type_name), v)
        elif type_name == 'bool':
            out[field_name] = bool(v)
        elif type_name == 'i32':
            out[field_name] = I32(v)
        elif type_name == 'i64':
            out[field_name] = I64(v)
        elif type_name == 'int':
            out[field_name] = int(v)
        elif type_name in ('float', 'double'):
            out[field_name] = float(v)
        elif type_name == 'cstr':
            out[field_name] = (v.decode('utf-8', errors='replace')
                               if isinstance(v, bytes) else (v or ""))
        elif type_name == 'str':
            out[field_name] = v or ""
        elif type_name == 'ptr':
            out[field_name] = int(v or 0)
        else:
            out[field_name] = v
    return out


class NativeLibrary:
    def __init__(self, path):
        from errors import EclException
        self.path = path
        load_path = path
        if not os.path.isabs(load_path) and os.path.exists(load_path):
            load_path = os.path.abspath(load_path)
        try:
            self.lib = ctypes.WinDLL(load_path)
        except OSError as e:
            raise EclException(
                'DllNotFoundError', f"无法加载 DLL '{path}': {e}")
        self.cache = {}

    def get(self, name):
        if name in self.cache:
            return self.cache[name]
        try:
            fn = getattr(self.lib, name)
        except AttributeError:
            return None
        self.cache[name] = fn
        return fn


class NativeFunction:
    def __init__(self, library, decl, registry):
        self.library = library
        self.registry = registry
        self.name, self.params, self.return_type = decl
        raw = library.get(self.name)
        if raw is None:
            from errors import EclException
            raise EclException(
                'AttributeError', f"找不到 DLL 函数 '{self.name}'")
        self.raw = raw
        # 返回类型必须按真实 C 类型设置，否则浮点返回值会从错误寄存器读取。
        if self.return_type in ('str', 'cstr', 'ptr', 'wstr') \
                or (self.return_type or '').endswith('*'):
            self.raw.restype = ctypes.c_void_p
        elif self.return_type == 'float':
            self.raw.restype = ctypes.c_float
        elif self.return_type == 'double':
            self.raw.restype = ctypes.c_double
        elif self.return_type == 'i32':
            self.raw.restype = ctypes.c_int32
        elif self.return_type == 'bool':
            self.raw.restype = ctypes.c_int32
        elif self.return_type == 'i64':
            self.raw.restype = ctypes.c_int64
        elif self.return_type == 'int':
            self.raw.restype = ctypes.c_int64
        elif self.return_type in registry.raw:
            self.raw.restype = ctypes.c_void_p
        else:
            self.raw.restype = None
        self.raw.argtypes = None

    def _scalar(self, type_name, value):
        if type_name.endswith('*'):
            if value is None:
                return ctypes.c_void_p(None)
            return ctypes.c_void_p(int(value or 0))
        if type_name == 'bool':
            return ctypes.c_int32(1 if value else 0)
        if type_name == 'i32':
            return ctypes.c_int32(I32(value))
        if type_name in ('i64', 'int'):
            return ctypes.c_int64(int(value))
        if type_name == 'float':
            return ctypes.c_float(float(value))
        if type_name == 'double':
            return ctypes.c_double(float(value))
        if type_name == 'str':
            return ctypes.c_wchar_p(value if value is not None else "")
        if type_name == 'wstr':
            return ctypes.c_wchar_p(value if value is not None else "")
        if type_name == 'cstr':
            if isinstance(value, bytes):
                return ctypes.c_char_p(value)
            if isinstance(value, int) and not isinstance(value, bool):
                # 已经是指向 char 的裸地址（例如 VirtualAlloc 返回的缓冲区）
                return ctypes.c_char_p(value)
            return ctypes.c_char_p(str(value).encode('utf-8'))
        if type_name == 'ptr':
            if value is None:
                return ctypes.c_void_p(None)
            return ctypes.c_void_p(int(value or 0))
        return ctypes.c_int64(int(value))

    def _decode_return(self, value):
        t = self.return_type
        if t in ('none', None):
            return None
        if t in self.registry.raw:
            # 结构体返回：value 是指向结构体内存的指针
            cls = self.registry.get(t).cls
            obj = cls.from_address(value)
            return struct_from_native(self.registry, self.registry.get(t), obj)
        if t.endswith('*'):
            return int(value or 0)
        if t == 'bool':
            return bool(value)
        if t == 'i32':
            return I32(ctypes.c_int32(value).value)
        if t == 'i64':
            return I64(ctypes.c_int64(value).value)
        if t == 'int':
            return ctypes.c_int64(value).value
        if t in ('float', 'double'):
            return float(value)
        if t in ('str', 'wstr'):
            if not value:
                return ""
            buf = ctypes.cast(ctypes.c_void_p(value),
                              ctypes.POINTER(ctypes.c_wchar))
            return buf[:wcslen(buf)]
        if t == 'cstr':
            if not value:
                return ""
            if isinstance(value, bytes):
                return value.decode('utf-8', errors='replace')
            n = ctypes.string_at(value).find(b'\0')
            raw = ctypes.string_at(value) if n < 0 else ctypes.string_at(value, n)
            return raw.decode('utf-8', errors='replace')
        if t == 'ptr':
            return int(value or 0)
        return value

    def __call__(self, *args, **kwargs):
        from errors import EclException
        if kwargs:
            raise EclException('TypeError',
                               f"DLL 函数 '{self.name}' 不支持关键字参数")
        if len(args) != len(self.params):
            raise EclException(
                'TypeError',
                f"DLL 函数 '{self.name}' 需要 {len(self.params)} 个参数，"
                f"得到 {len(args)} 个")
        prepared = []
        sync = []
        for arg, p in zip(args, self.params):
            type_name = p[1]
            modifier = p[2]
            if type_name in self.registry.raw:
                info = self.registry.get(type_name)
                if modifier in ('ptr', 'in', 'out'):
                    native = (info.cls() if modifier == 'out'
                              else struct_to_native(self.registry, info, arg))
                    prepared.append(ctypes.byref(native))
                    if modifier in ('ptr', 'out') and isinstance(arg, dict):
                        sync.append((arg, info, native))
                else:
                    prepared.append(
                        struct_to_native(self.registry, info, arg))
            else:
                cval = self._scalar(type_name, arg)
                if modifier is not None:
                    prepared.append(ctypes.byref(cval))
                else:
                    prepared.append(cval)
        try:
            ret = self.raw(*prepared)
        except EclException:
            raise
        except (OSError, ctypes.ArgumentError) as e:
            raise EclException('OSError', f"DLL 调用失败: {e}")
        for original, info, native in sync:
            original.clear()
            original.update(
                struct_from_native(self.registry, info, native))
        return self._decode_return(ret)

    def __repr__(self):
        return f"<dll function {self.name}>"


class LazyNativeFunction:
    """第一次调用时才 LoadLibrary，让 DllNotFoundError 能在调用处被捕获。"""

    def __init__(self, interpreter, path, decl, registry):
        self.interpreter = interpreter
        self.path = path
        self.decl = decl
        self.registry = registry
        self.resolved = None

    @property
    def name(self):
        return self.decl[0]

    def __call__(self, *args, **kwargs):
        if self.resolved is None:
            library = self.interpreter.get_library(self.path)
            self.resolved = NativeFunction(
                library, self.decl, self.registry)
        return self.resolved(*args, **kwargs)

    def __repr__(self):
        return f"<dll function {self.name}>"
