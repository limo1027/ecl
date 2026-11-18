# dl.py — ECLC (ECL cache) 序列化 / 反序列化库
#
# 职责:
#   save_eclc / read_eclc   二进制 .eclc 读写
#   dump_eclc / load_eclc   二进制 <-> 文本 (.eclc.txt) 互转
#   is_valid                缓存失效检查
#
# 不是 CLI。由 main.py 的子命令和测试代码 import 使用。
#
# 文件格式 (.eclc):
#   magic:    b'ECLC'          (4)
#   version:  uint32 LE        (IR_VERSION)
#   flags:    uint32 LE        (bit0 = aot_mode)
#   src_hash: 32 bytes         (源文件 SHA-256)
#   ir_len:   uint64 LE
#   exc_len:  uint64 LE
#   --- 之后是 IR 序列化（递归 TAG_* 编码）---

import ast
import hashlib
import struct
from decimal import Decimal

from class_and_vars import NULL, I64, WStr


# ============================================================
# 常量
# ============================================================

MAGIC = b'ECLC'
HEADER_FMT = '<4sII32sQQ'
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# IR 格式版本。改 Compiler 的 IR 指令形状就 +1。
# 放在 dl.py 里而不是 compiler.py，因为 dl.py 不依赖 compiler，
# 避免循环 import；compiler.py 可以 `from dl import IR_VERSION`。
IR_VERSION = 3

# 文本格式里 NULL 的哨兵（None 用 'None'，NULL 用 '#NULL#'）
NULL_SENTINEL = '#NULL#'


class EclcFormatError(Exception):
    """缓存文件格式错误 / 版本不匹配 / 损坏"""
    pass


# ============================================================
# 二进制编码：递归 TAG_*
# ============================================================

TAG_NONE = 0
TAG_NULL = 1
TAG_BOOL = 2
TAG_INT = 3
TAG_FLOAT = 4
TAG_STR = 5
TAG_TUPLE = 6
TAG_LIST = 7
TAG_DICT = 8
TAG_DECIMAL = 9
TAG_I64 = 10
TAG_BIGINT = 11
TAG_WSTR = 12

_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1


def _write_val(f, v):
    if v is None:
        f.write(bytes([TAG_NONE]))
    elif v is NULL:
        f.write(bytes([TAG_NULL]))
    elif isinstance(v, bool):
        f.write(bytes([TAG_BOOL, 1 if v else 0]))
    elif isinstance(v, I64):
        b = str(int(v)).encode('ascii')
        f.write(bytes([TAG_I64]))
        f.write(struct.pack('<I', len(b)))
        f.write(b)
    elif isinstance(v, int):
        if _INT64_MIN <= v <= _INT64_MAX:
            f.write(bytes([TAG_INT]))
            f.write(struct.pack('<q', v))
        else:
            b = str(v).encode('ascii')
            f.write(bytes([TAG_BIGINT]))
            f.write(struct.pack('<I', len(b)))
            f.write(b)
    elif isinstance(v, Decimal):
        b = str(v).encode('ascii')
        f.write(bytes([TAG_DECIMAL]))
        f.write(struct.pack('<I', len(b)))
        f.write(b)
    elif isinstance(v, float):
        f.write(bytes([TAG_FLOAT]))
        f.write(struct.pack('<d', v))
    elif isinstance(v, WStr):
        b = str(v).encode('utf-8')
        f.write(bytes([TAG_WSTR]))
        f.write(struct.pack('<I', len(b)))
        f.write(b)
    elif isinstance(v, str):
        b = v.encode('utf-8')
        f.write(bytes([TAG_STR]))
        f.write(struct.pack('<I', len(b)))
        f.write(b)
    elif isinstance(v, tuple):
        f.write(bytes([TAG_TUPLE]))
        f.write(struct.pack('<I', len(v)))
        for x in v:
            _write_val(f, x)
    elif isinstance(v, list):
        f.write(bytes([TAG_LIST]))
        f.write(struct.pack('<I', len(v)))
        for x in v:
            _write_val(f, x)
    elif isinstance(v, dict):
        f.write(bytes([TAG_DICT]))
        f.write(struct.pack('<I', len(v)))
        for k, val in v.items():
            _write_val(f, k)
            _write_val(f, val)
    else:
        raise EclcFormatError(
            f"IR 缓存不支持的类型: {type(v).__name__} (值: {v!r})")


def _read_val(f):
    tag_b = f.read(1)
    if not tag_b:
        raise EclcFormatError("IR 数据意外结束")
    tag = tag_b[0]

    if tag == TAG_NONE:
        return None
    if tag == TAG_NULL:
        return NULL
    if tag == TAG_BOOL:
        b = f.read(1)
        if not b:
            raise EclcFormatError("bool 数据截断")
        return bool(b[0])
    if tag == TAG_INT:
        b = f.read(8)
        if len(b) != 8:
            raise EclcFormatError("int 数据截断")
        return struct.unpack('<q', b)[0]
    if tag == TAG_FLOAT:
        b = f.read(8)
        if len(b) != 8:
            raise EclcFormatError("float 数据截断")
        return struct.unpack('<d', b)[0]
    if tag == TAG_WSTR:
        b = f.read(4)
        if len(b) != 4:
            raise EclcFormatError("wstr 长度截断")
        n = struct.unpack('<I', b)[0]
        s = f.read(n)
        if len(s) != n:
            raise EclcFormatError("wstr 内容截断")
        return WStr(s.decode('utf-8'))
    if tag == TAG_STR:
        b = f.read(4)
        if len(b) != 4:
            raise EclcFormatError("str 长度截断")
        n = struct.unpack('<I', b)[0]
        s = f.read(n)
        if len(s) != n:
            raise EclcFormatError("str 内容截断")
        try:
            return s.decode('utf-8')
        except UnicodeDecodeError as e:
            raise EclcFormatError(f"str 不是合法 UTF-8: {e}")
    if tag == TAG_DECIMAL:
        b = f.read(4)
        if len(b) != 4:
            raise EclcFormatError("decimal 长度截断")
        n = struct.unpack('<I', b)[0]
        s = f.read(n)
        if len(s) != n:
            raise EclcFormatError("decimal 内容截断")
        try:
            return Decimal(s.decode('ascii'))
        except Exception as e:
            raise EclcFormatError(f"decimal 内容非法: {e}")
    if tag == TAG_I64:
        b = f.read(4)
        if len(b) != 4:
            raise EclcFormatError("i64 长度截断")
        n = struct.unpack('<I', b)[0]
        s = f.read(n)
        if len(s) != n:
            raise EclcFormatError("i64 内容截断")
        return I64(int(s.decode('ascii')))
    if tag == TAG_BIGINT:
        b = f.read(4)
        if len(b) != 4:
            raise EclcFormatError("bigint 长度截断")
        n = struct.unpack('<I', b)[0]
        s = f.read(n)
        if len(s) != n:
            raise EclcFormatError("bigint 内容截断")
        return int(s.decode('ascii'))
    if tag == TAG_TUPLE:
        b = f.read(4)
        if len(b) != 4:
            raise EclcFormatError("tuple 长度截断")
        n = struct.unpack('<I', b)[0]
        return tuple(_read_val(f) for _ in range(n))
    if tag == TAG_LIST:
        b = f.read(4)
        if len(b) != 4:
            raise EclcFormatError("list 长度截断")
        n = struct.unpack('<I', b)[0]
        return [_read_val(f) for _ in range(n)]
    if tag == TAG_DICT:
        b = f.read(4)
        if len(b) != 4:
            raise EclcFormatError("dict 长度截断")
        n = struct.unpack('<I', b)[0]
        d = {}
        for _ in range(n):
            k = _read_val(f)
            val = _read_val(f)
            d[k] = val
        return d

    raise EclcFormatError(f"未知 TAG: {tag}")


# ============================================================
# 二进制 .eclc 读写
# ============================================================

def _write_eclc_to(f, ir, exc, aot_mode, src_hash):
    flags = 1 if aot_mode else 0
    f.write(struct.pack(HEADER_FMT, MAGIC, IR_VERSION, flags,
                        src_hash, len(ir), len(exc)))
    for op in ir:
        _write_val(f, op)
    for entry in exc:
        _write_val(f, entry)


def save_eclc(path, ir, exc, aot_mode, src_hash):
    """把 IR + exception_table 写成 .eclc 二进制文件。

    src_hash: 32 字节 bytes（源文件 SHA-256）
    """
    if not isinstance(src_hash, (bytes, bytearray)) or len(src_hash) != 32:
        raise ValueError("src_hash 必须是 32 字节 bytes")
    with open(path, 'wb') as f:
        _write_eclc_to(f, ir, exc, aot_mode, bytes(src_hash))


def _read_eclc_header(f):
    hdr = f.read(HEADER_SIZE)
    if len(hdr) < HEADER_SIZE:
        raise EclcFormatError("缓存头不完整")
    magic, ver, flags, src_hash, ir_len, exc_len = struct.unpack(
        HEADER_FMT, hdr)
    if magic != MAGIC:
        raise EclcFormatError(
            f"缓存 magic 错误: {magic!r}，期望 {MAGIC!r}")
    return ver, flags, src_hash, ir_len, exc_len


def read_eclc(path, expected_aot_mode=None):
    """读 .eclc，返回 (ir, exc, aot_mode, src_hash)。

    expected_aot_mode 非 None 时校验模式匹配；不匹配抛 EclcFormatError。
    版本不匹配也抛 EclcFormatError（调用方捕获后回退重编）。
    """
    with open(path, 'rb') as f:
        ver, flags, src_hash, ir_len, exc_len = _read_eclc_header(f)

        if ver != IR_VERSION:
            raise EclcFormatError(
                f"缓存版本 {ver} 不匹配当前 IR_VERSION {IR_VERSION}")

        aot_mode = bool(flags & 1)
        if expected_aot_mode is not None and aot_mode != expected_aot_mode:
            raise EclcFormatError(
                f"缓存模式不匹配: 文件里 aot_mode={aot_mode}，"
                f"期望 {expected_aot_mode}")

        ir = []
        for _ in range(ir_len):
            op = _read_val(f)
            if not isinstance(op, tuple):
                raise EclcFormatError(
                    f"IR 指令不是 tuple: {type(op).__name__}")
            ir.append(op)

        exc = []
        for _ in range(exc_len):
            entry = _read_val(f)
            if not isinstance(entry, dict):
                raise EclcFormatError(
                    f"exception_table 条目不是 dict: {type(entry).__name__}")
            exc.append(entry)

        # 检查文件尾没有多余数据
        tail = f.read(1)
        if tail:
            raise EclcFormatError("缓存文件尾有多余数据")

    return ir, exc, aot_mode, src_hash


def is_valid(path, src_bytes, aot_mode):
    """缓存是否有效：magic / 版本 / src_hash / aot_mode 四项全匹配。

    读失败（文件不存在、损坏）一律返回 False，不抛异常。
    """
    try:
        with open(path, 'rb') as f:
            ver, flags, src_hash, ir_len, exc_len = _read_eclc_header(f)
    except (OSError, EclcFormatError):
        return False

    if ver != IR_VERSION:
        return False
    if bool(flags & 1) != aot_mode:
        return False
    if hashlib.sha256(src_bytes).digest() != src_hash:
        return False
    return True


def make_src_hash(content):
    """从源码字符串或 bytes 算 SHA-256"""
    if isinstance(content, str):
        content = content.encode('utf-8')
    return hashlib.sha256(content).digest()


# ============================================================
# 文本编码：一行一指令，repr 风格
# ============================================================

def _val_to_text(v):
    """把 IR 里的值转成文本 token。dict 键排序保证确定性。"""
    if v is None:
        return 'None'
    if v is NULL:
        return NULL_SENTINEL
    if isinstance(v, Decimal):
        return ("('__double__', " + repr(str(v)) + ")")
    if isinstance(v, I64):
        return ("('__i64__', " + repr(str(int(v))) + ")")
    if isinstance(v, bool):
        return 'True' if v else 'False'
    if isinstance(v, int):
        if _INT64_MIN <= v <= _INT64_MAX:
            return repr(v)
        return ("('__bigint__', " + repr(str(v)) + ")")
    if isinstance(v, float):
        return repr(v)          # repr 保证往返精度
    if isinstance(v, str):
        return repr(v)          # repr 加引号 + 转义
    if isinstance(v, tuple):
        inner = ', '.join(_val_to_text(x) for x in v)
        if len(v) == 1:
            inner += ','
        return '(' + inner + ')'
    if isinstance(v, list):
        return '[' + ', '.join(_val_to_text(x) for x in v) + ']'
    if isinstance(v, dict):
        items = sorted(v.items(), key=lambda kv: repr(kv[0]))
        return '{' + ', '.join(
            f'{_val_to_text(k)}: {_val_to_text(val)}'
            for k, val in items) + '}'
    raise EclcFormatError(
        f"文本 dump 不支持的类型: {type(v).__name__}")


def _text_to_val(node):
    """literal_eval 后的值 -> IR 值（还原 NULL / double / i64 / bigint）。"""
    if isinstance(node, str):
        return NULL if node == NULL_SENTINEL else node
    if isinstance(node, tuple):
        values = [_text_to_val(e) for e in node]
        if len(values) == 2 and values[0] in ('__double__', '__i64__',
                                              '__bigint__'):
            marker, text = values
            if marker == '__double__':
                return Decimal(text)
            if marker == '__i64__':
                return I64(int(text))
            return int(text)
        return tuple(values)
    if isinstance(node, list):
        return [_text_to_val(e) for e in node]
    if isinstance(node, dict):
        return {_text_to_val(k): _text_to_val(v)
                for k, v in node.items()}
    return node


def _flatten_ir(ir, prefix=''):
    """把 func/method_func 的嵌套 sub_code 拍平成 (name, op) 列表"""
    out = []
    for i, op in enumerate(ir):
        name = f'{prefix}{i}' if not prefix else f'{prefix}.{i}'
        if op[0] == 'func':
            _, fname, sub_code, params, varargs, kwargs, ret, defaults = op
            flat_head = ('func', fname, f'<code {len(sub_code)} 条>',
                         params, varargs, kwargs, ret, defaults)
            out.append((name, flat_head))
            out.extend(_flatten_ir(sub_code, prefix=name))
        elif op[0] == 'method_func':
            _, cls_name, mname, sub_code, params, varargs, kwargs, ret, defaults = op
            flat_head = ('method_func', cls_name, mname,
                         f'<code {len(sub_code)} 条>',
                         params, varargs, kwargs, ret, defaults)
            out.append((name, flat_head))
            out.extend(_flatten_ir(sub_code, prefix=name))
        else:
            out.append((name, op))
    return out


def _unflatten_ir(flat):
    """把拍平的 (name, op) 列表重建出嵌套 IR"""
    # flat 是按 DFS 顺序排列的 (name, op)，name 形如 '3', '3.0', '3.0.1'
    # 用栈按前缀深度重建
    root = []
    # stack: list of (depth, container_list, full_name)
    stack = [(0, root, '')]

    for name, op in flat:
        depth = name.count('.') + 1 if name else 0
        # 弹出比当前浅或同级的
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if not stack:
            raise EclcFormatError(f"flat IR 名称层级错误: {name!r}")
        parent_depth, parent_list, parent_name = stack[-1]

        if op[0] in ('func', 'method_func'):
            # 把 <code N 条> 占位符替换成空列表，稍后子指令填进去
            if op[0] == 'func':
                _, fname, _placeholder, params, varargs, kwargs, ret, defaults = op
                sub = []
                new_op = ('func', fname, sub, params,
                          varargs, kwargs, ret, defaults)
            else:
                _, cls_name, mname, _placeholder, params, varargs, kwargs, ret, defaults = op
                sub = []
                new_op = ('method_func', cls_name, mname, sub,
                          params, varargs, kwargs, ret, defaults)
            parent_list.append(new_op)
            stack.append((depth, sub, name))
        else:
            parent_list.append(op)

    return root


def dump_eclc_text(path, flat=False):
    """读 .eclc，返回文本（不写文件）。"""
    ir, exc, aot_mode, src_hash = read_eclc(path)

    lines = []
    lines.append(f"# ECLC v{IR_VERSION}  mode={'aot' if aot_mode else 'interp'}  "
                 f"src_hash={src_hash.hex()}  ir_len={len(ir)}  exc_len={len(exc)}")

    if flat:
        lines.append("# --- IR (flat) ---")
        for name, op in _flatten_ir(ir):
            lines.append(f"{name}: {_val_to_text(op)}")
    else:
        lines.append("# --- IR ---")
        for i, op in enumerate(ir):
            lines.append(f"{i}: {_val_to_text(op)}")

    if exc:
        lines.append("# --- exception_table ---")
        for i, entry in enumerate(exc):
            lines.append(f"exc{i}: {_val_to_text(entry)}")

    return '\n'.join(lines) + '\n'


def load_eclc_text(text, aot_mode, src_hash, flat=False):
    """把文本解析成 (ir, exc)。

    aot_mode / src_hash 由调用方提供（文本里虽然有 header 注释，
    但 load 的语义是"重建 .eclc"，头信息以调用方为准）。
    """
    ir = []
    exc = []
    section = None
    flat_entries = []

    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('#'):
            if '--- IR (flat) ---' in stripped:
                section = 'ir_flat'
            elif '--- IR ---' in stripped:
                section = 'ir'
            elif '--- exception_table ---' in stripped:
                section = 'exc'
            continue

        if section is None:
            raise EclcFormatError(
                f"第 {line_no} 行: 不在任何 section 内: {stripped!r}")

        if section == 'ir_flat':
            if ':' not in stripped:
                raise EclcFormatError(
                    f"第 {line_no} 行: flat IR 格式错误: {stripped!r}")
            name, expr_text = stripped.split(':', 1)
            flat_entries.append((name.strip(), expr_text.strip()))
        elif section == 'ir':
            if ':' not in stripped:
                raise EclcFormatError(
                    f"第 {line_no} 行: IR 格式错误: {stripped!r}")
            _, expr_text = stripped.split(':', 1)
            ir.append(_parse_text_expr(expr_text.strip(), line_no))
        elif section == 'exc':
            if ':' not in stripped:
                raise EclcFormatError(
                    f"第 {line_no} 行: exception_table 格式错误: {stripped!r}")
            _, expr_text = stripped.split(':', 1)
            exc.append(_parse_text_expr(expr_text.strip(), line_no))

    if flat:
        if not flat_entries:
            raise EclcFormatError("flat 模式下没有读到 IR 条目")
        # flat_entries 的 name 来自 dump 的编号，但 load 只关心层级，
        # 重新按顺序组树
        parsed = []
        for name, expr_text in flat_entries:
            parsed.append((name, _parse_text_expr(expr_text, 0)))
        ir = _unflatten_ir(parsed)

    return ir, exc


def _parse_text_expr(expr_text, line_no):
    # flat 模式下 func 的 sub_code 是占位符 '<code N 条>'，
    # 不能直接 ast.literal_eval，先替换成 None
    if expr_text.startswith('(') and '<code ' in expr_text:
        # 用正则把 <code N 条> 替换成 None 再解析
        import re
        safe = re.sub(r"'<code \d+ 条>'", 'None', expr_text)
        try:
            node = ast.literal_eval(safe)
        except (ValueError, SyntaxError) as e:
            raise EclcFormatError(
                f"第 {line_no} 行: 无法解析: {expr_text!r} ({e})")
        return node

    try:
        node = ast.literal_eval(expr_text)
    except (ValueError, SyntaxError) as e:
        raise EclcFormatError(
            f"第 {line_no} 行: 无法解析: {expr_text!r} ({e})")
    return _text_to_val(node)


# ============================================================
# 便捷：文本文件 <-> 二进制文件
# ============================================================

def dump_eclc_file(cache_path, text_path, flat=False):
    """读 .eclc，写 .eclc.txt"""
    text = dump_eclc_text(cache_path, flat=flat)
    with open(text_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def load_eclc_file(text_path, cache_path, aot_mode, src_hash, flat=False):
    """读 .eclc.txt，写 .eclc"""
    with open(text_path, 'r', encoding='utf-8') as f:
        text = f.read()
    ir, exc = load_eclc_text(text, aot_mode, src_hash, flat=flat)
    save_eclc(cache_path, ir, exc, aot_mode, src_hash)
