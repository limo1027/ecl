#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ECL 语言服务器 (LSP over stdio, JSON-RPC) — 按 ECL 0.1.0 语法文档实现"""
import json
import re
import sys
# ============ 词典（依据语法文档）============
KEYWORD_DOC = {
    # 变量定义
    "<-":    "赋值（ECL 用 <- 而不是 =）",
    "<->":   "交换两个变量",
    "define": "仅声明变量，赋值为 null。define const FALLTHROUGH 启用 match fallthrough。",
    "ref":   "引用传递，底层共享内存。只能绑定变量。",
    "const": "常量修饰，修改抛 ConstChangingError",
    "public": "属性/DLL 导出可见性",
    "private": "属性可见性",
    # 控制流
    "if":    "条件分支，需 end 结尾。",
    "elif":  "多分支条件，属于 if 块，不占新块。",
    "else":  "else 分支，属于 if/try 块，不占新块。",
    "while": "循环，需 end 结尾。",
    "for":   "遍历循环：for i in Iterable / for i in 1..10 / for 10:，需 end 结尾。",
    "in":    "for 遍历用",
    "match": "模式匹配，case 分支默认不 fallthrough，需 end 结尾。",
    "case":  "match 的分支，属于 match 块，不占新块。支持字面量/类型/多值/_/守卫。",
    "break": "跳出最近循环；在 match 内表示跳出 match。",
    "continue": "进入下一轮循环（match 内不可用）。",
    "goto":  "跳转到 label，不能跨函数。",
    "label": "定义跳转标签。",
    # 函数
    "def":   "定义函数。带 ':' 是函数体（需 end）；带 '->' 无 ':' 是 dll_import 声明；单行函数 f(x) -> int <- expr 自动 return。",
    "return": "返回。无值/none 返回 none；return null 或 unknown 是错误。",
    "global": "访问全局变量：global.x",
    "last":  "访问上层（闭包）变量：last.x",
    "self":  "当前实例，隐式可用，不占参数位置。",
    # 类
    "class": "定义类：class a(b, c): 继承 b、c，需 end 结尾。self 隐式。",
    "using": "成员引入：using obj 后可直接访问 obj 的成员，无需 end，作用到当前块结束。",
    # 异常
    "try":     "异常捕获块，需 end 结尾（try/except/else/finally 共用一个 end）。",
    "except":  "捕获异常，可指定类型：except ZeroDivisionError:，属于 try 块。",
    "finally": "无论是否异常都执行，属于 try 块。",
    "as":      "with 中绑定名字：with expr as name",
    "with":    "上下文管理，调用 __enter__/__leave__，需 end 结尾。",
    "raise":   "抛出异常。",
    # 导入
    "import": "导入模块。成员以「模块名_成员名」扁平形式暴露，无点号访问。",
    "from":   "从模块导入指定函数：from math import square",
    "dll_import": '导入 DLL："user32.dll": 块内 def 声明函数签名（无函数体），需 end 结尾。',
    "@@":     "外部语言执行：@@py_py(code) 脱糖为 py_py().__exec__(code)。需先 import 对应模块。",
    # 特殊值
    "none":    "显式空值，唯一可作为右值/返回值的空值。",
    "null":    "空指针/未初始化。不能作为右值或返回值。",
    "unknown": "使用未定义变量得到的值，编译期警告。不能作为右值或返回值。",
    "True":    "布尔真。",
    "False":   "布尔假。",
    "true":    "布尔真。",
    "false":   "布尔假。",
}
TYPES = {
    "int":    "任意精度整数",
    "i64":    "64 位整数（固定宽度，溢出回绕）",
    "i32":    "32 位整数（Win32 结构体用）",
    "float":  "64 位双精度浮点数（默认）",
    "double": "128 位高精度浮点数",
    "bool":   "True/False",
    "list":   "动态数组，支持 list[int] 或 list[5] int",
    "dict":   "有序哈希字典",
    "tuple":  "不可变序列",
    "code":   "代码字符串，可用 run() 运行",
    "str":    "字符串，长度任意",
    "cstr":   "C 风格字符串（char*，dll_import 用）",
    "ptr":    "裸指针（void*，dll_import 用）",
    "none":   "显式空值",
    "null":   "空指针（不能作为右值）",
    "unknown": "未定义值（不能作为右值）",
    "any":    "任意类型",
    "struct": "结构体（仅 dll_import 签名用）",
    "public": "修饰符：公开",
    "private": "修饰符：私有",
    "const":  "修饰符：常量",
}
BUILTIN_FUNCS = {
    "print": "print(*args) -> none 打印参数，空格分隔，换行",
    "write": "write(*args) -> none 打印参数，不换行",
    "input": 'input(str prompt = "") -> str 读一行',
    "eprint": "eprint(*args) -> none 输出到 stderr",
    "int":   "int(any) -> int 转整数",
    "float": "float(any) -> float 转 64 位浮点",
    "double": "double(any) -> double 转 128 位浮点",
    "str":   "str(any) -> str 转字符串",
    "bool":  "bool(any) -> bool 真值转换",
    "bi":    "bi(any) -> bi 转 BigInt",
    "type":  'type(any) -> str 返回类型名',
    "code":  "code(str) -> code 字符串转代码对象",
    "len":   "len(any) -> int 长度（str/list/dict/tuple）",
    "range": "range(int stop) / range(int start, int stop, int step = 1) -> list",
    "list":  "list(any) -> list",
    "tuple": "tuple(any) -> tuple",
    "dict":  "dict(any) -> dict",
    "slice": "slice(list, int start, int end) -> list",
    "abs":   "abs(number) -> 同类型",
    "max":   "max(*args) -> any",
    "min":   "min(*args) -> any",
    "sum":   "sum(list) -> int/float",
    "pow":   "pow(base, exp) -> any",
    "sqrt":  "sqrt(x) -> float/double",
    "floor": "floor(x) -> int",
    "ceil":  "ceil(x) -> int",
    "round": "round(x, int digits = 0) -> float/double",
    "random": "random() -> float / random(int n) -> int",
    "free":  "free(any) -> null 释放",
    "clone": "clone(any) -> any 深拷贝",
    "id":    "id(any) -> int 对象身份",
    "hash":  "hash(any) -> int 哈希值",
    "exit":  "exit(int code = 0) -> never 退出程序",
    "Exception": "Exception(str msg = \"\") 基础异常",
    "ValueError": "值错误异常",
    "TypeError": "类型错误异常",
    "IndexError": "索引越界异常",
    "KeyError": "键不存在异常",
    "ZeroDivisionError": "除零异常",
    "RuntimeError": "运行时异常",
    "NameError": "名字未定义异常",
    "DllNotFoundError": "DLL 加载失败异常",
}
COMPLETION_KEYWORDS = list(KEYWORD_DOC.keys())
COMPLETION_TYPES = list(TYPES.keys())
COMPLETION_BUILTINS = list(BUILTIN_FUNCS.keys())
# ============ 正则 ============
# 块开语句（需要 end）
RE_BLOCK_DEF = re.compile(r'^\s*def\b.*:\s*(#.*)?$')      # def f(...):
RE_BLOCK_IF = re.compile(r'^\s*if\b.*:\s*(#.*)?$')
RE_BLOCK_WHILE = re.compile(r'^\s*while\b.*:\s*(#.*)?$')
RE_BLOCK_FOR = re.compile(r'^\s*for\b.*:\s*(#.*)?$')
RE_BLOCK_TRY = re.compile(r'^\s*try\s*:\s*(#.*)?$')
RE_BLOCK_CLASS = re.compile(r'^\s*class\b.*:\s*(#.*)?$')
RE_BLOCK_STRUCT = re.compile(r'^\s*struct\b.*:\s*(#.*)?$')
RE_BLOCK_DLLIMPORT = re.compile(r'^\s*dll_import\b.*:\s*(#.*)?$')
RE_BLOCK_MATCH = re.compile(r'^\s*match\b.*:\s*(#.*)?$')
RE_BLOCK_WITH = re.compile(r'^\s*with\b.*:\s*(#.*)?$')
# 不占块
RE_ELIF = re.compile(r'^\s*elif\b.*:\s*(#.*)?$')
RE_ELSE = re.compile(r'^\s*else\s*:\s*(#.*)?$')
RE_EXCEPT = re.compile(r'^\s*except\b.*:\s*(#.*)?$')
RE_FINALLY = re.compile(r'^\s*finally\s*:\s*(#.*)?$')
RE_CASE = re.compile(r'^\s*case\b.*:\s*(#.*)?$')
RE_USING = re.compile(r'^\s*using\b')
RE_LABEL = re.compile(r'^\s*label\s+[A-Za-z_]\w*\s*$|^\s*[A-Za-z_]\w*\s*:\s*$')
RE_END = re.compile(r'^\s*end\b')
RE_DEF_ANY = re.compile(r'^\s*def\b')
RE_WHOLE = re.compile(r'([A-Za-z_]\w*)\s*\(')
RE_STRING = re.compile(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'|`[^`]*`')
RE_TRIPLE = re.compile(r'"""(?:[^"]|"(?!""))*"""', re.S)
BLOCK_OPENERS = [
    (RE_BLOCK_DLLIMPORT, "dll_import"),
    (RE_BLOCK_DEF,       "def"),
    (RE_BLOCK_CLASS,     "class"),
    (RE_BLOCK_STRUCT,    "struct"),
    (RE_BLOCK_MATCH,     "match"),
    (RE_BLOCK_TRY,       "try"),
    (RE_BLOCK_WITH,      "with"),
    (RE_BLOCK_FOR,       "for"),
    (RE_BLOCK_WHILE,     "while"),
    (RE_BLOCK_IF,        "if"),
]


def strip_strings_and_comments(line):
    """去掉字符串字面量和注释，返回纯代码行（用于块分析）"""
    line = RE_TRIPLE.sub('""', line)
    line = RE_STRING.sub('""', line)
    line = re.sub(r'#.*$', '', line)
    line = re.sub(r'//.*$', '', line)
    return line.strip()
# ============ 分析 ============


def parse_defs(text):
    """收集函数定义: {名字: {line, col, detail, doc}}"""
    defs = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        # 跳过字符串/注释行
        code = strip_strings_and_comments(line)
        m = re.match(
            r'^def\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(->\s*[\w\[\]\s,]+?)?\s*(<-.*|:)?\s*$', code)
        if m:
            name = m.group(1)
            params = ", ".join(p.strip()
                               for p in m.group(2).split(",") if p.strip())
            ret = m.group(3).strip() if m.group(3) else ""
            sig = f"def {name}({params})" + \
                (f" -> {ret[2:].strip()}" if ret else "")
            doc = []
            # 文档注释：往上看 ///
            j = i - 1
            while j >= 0 and lines[j].strip().startswith("///"):
                doc.insert(0, lines[j].strip()[3:].strip())
                j -= 1
            if doc:
                doc = "\n".join(doc)
            else:
                doc = "（本文件定义的函数）"
            defs[name] = {"line": i, "col": line.index(name, line.index("def")),
                          "detail": sig, "doc": doc}
    return defs


def check_blocks(text):
    """块配对检查。块开: def/class/struct/dll_import/match/try/with/for/while/if
       不占块: elif/else/except/finally/case/using/label"""
    diags = []
    stack = []  # (类型, 行号, 起始列)
    for i, raw in enumerate(text.splitlines()):
        line = strip_strings_and_comments(raw)
        if not line:
            continue
        opened = None
        for regex, kind in BLOCK_OPENERS:
            m = regex.match(line)
            if m:
                opened = (kind, i, len(raw) - len(raw.lstrip()))
                break
        if opened:
            stack.append(opened)
            continue
        if (RE_ELIF.match(line) or RE_ELSE.match(line) or
            RE_EXCEPT.match(line) or RE_FINALLY.match(line) or
            RE_CASE.match(line) or RE_USING.match(line) or
                RE_LABEL.match(line)):
            continue
        if RE_END.match(line):
            if not stack:
                diags.append({
                    "range": _rng(i, raw.index("end"), i, raw.index("end") + 3),
                    "message": "多余的 end（没有可配对的开块语句）",
                    "severity": 4,
                })
            else:
                stack.pop()
    for kind, line, col in stack:
        diags.append({
            "range": _rng(line, col, line, col + len(kind)),
            "message": f"'{kind}' 块没有对应的 end",
            "severity": 1,
        })
    return diags


def check_unknown_returns(text):
    """return null / return unknown → Error"""
    diags = []
    for i, raw in enumerate(text.splitlines()):
        code = strip_strings_and_comments(raw)
        m = re.match(r'^return\s+(null|unknown)\s*$', code)
        if m:
            word = m.group(1)
            diags.append({
                "range": _rng(i, raw.index(word), i, raw.index(word) + len(word)),
                "message": f"return {word} 是错误（{word} 不能作为返回值）",
                "severity": 1,
            })
    return diags


def check_goto_label(text):
    """goto 引用了不存在的 label → Warning"""
    diags = []
    labels = set()
    lines = [strip_strings_and_comments(l) for l in text.splitlines()]
    for line in lines:
        m = re.match(r'^label\s+([A-Za-z_]\w*)', line)
        if m:
            labels.add(m.group(1))
        m2 = re.match(r'^([A-Za-z_]\w*)\s*:\s*$', line)
        if m2 and m2.group(1) not in ("def", "if", "else", "elif", "while",
                                      "try", "except", "finally", "for",
                                      "match", "case", "with", "class",
                                      "struct", "dll_import"):
            labels.add(m2.group(1))
    for i, line in enumerate(lines):
        m = re.match(r'^goto\s+([A-Za-z_]\w*)', line)
        if m and m.group(1) not in labels:
            diags.append({
                "range": _rng(i, 5, i, 5 + len(m.group(1))),
                "message": f"goto 的标签 '{m.group(1)}' 不存在",
                "severity": 2,
            })
    return diags


def _rng(sl, sc, el, ec):
    return {"start": {"line": sl, "character": sc},
            "end": {"line": el, "character": ec}}
# ============ JSON-RPC ============


def send(msg):
    data = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(
        f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def recv():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line or line in (b"\r\n", b"\n"):
            break
        k, _, v = line.decode("ascii").partition(":")
        headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", 0))
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


# ============ 主循环 ============
docs = {}
definitions = {}


def analyze(uri):
    text = docs.get(uri, "")
    definitions[uri] = parse_defs(text)
    diags = check_blocks(text)
    diags += check_unknown_returns(text)
    diags += check_goto_label(text)
    send({
        "jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
        "params": {"uri": uri, "diagnostics": diags},
    })


def make_hover(name):
    if name in KEYWORD_DOC:
        return f"**{name}** — {KEYWORD_DOC[name]}"
    if name in TYPES:
        return f"**{name}** — 类型：{TYPES[name]}"
    if name in BUILTIN_FUNCS:
        return f"```ecl\n{BUILTIN_FUNCS[name]}\n```"
    return None


def handle(msg):
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "capabilities": {
                "textDocumentSync": 1,
                "hoverProvider": True,
                "completionProvider": {"triggerCharacters": ["(", "."]},
                "definitionProvider": True,
            },
            "serverInfo": {"name": "ecl-lsp", "version": "0.3.0"},
        }})
    elif method == "textDocument/didOpen":
        p = msg["params"]
        docs[p["textDocument"]["uri"]] = p["textDocument"]["text"]
        analyze(p["textDocument"]["uri"])
    elif method == "textDocument/didChange":
        p = msg["params"]
        docs[p["textDocument"]["uri"]] = p["contentChanges"][-1]["text"]
        analyze(p["textDocument"]["uri"])
    elif method == "textDocument/hover":
        p = msg["params"]
        uri = p["textDocument"]["uri"]
        word = word_at(docs.get(uri, ""),
                       p["position"]["line"], p["position"]["character"])
        if word:
            content = make_hover(word)
            if not content and word in definitions.get(uri, {}):
                d = definitions[uri][word]
                content = f"```ecl\n{d['detail']}\n```\n{d['doc']}"
            if content:
                send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "contents": {"kind": "markdown", "value": content}}})
                return
        send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
    elif method == "textDocument/completion":
        items = []
        for kw, doc in KEYWORD_DOC.items():
            if kw.startswith("<"):
                continue
            items.append({"label": kw, "kind": 14, "detail": doc})
        for t, desc in TYPES.items():
            items.append({"label": t, "kind": 7, "detail": f"类型：{desc}"})
        for f, doc in BUILTIN_FUNCS.items():
            sig = doc.split(" 打印")[0].split(" 转")[0].split(" 读")[0]
            items.append(
                {"label": f, "kind": 3, "detail": sig, "documentation": doc})
        for uri, defs in definitions.items():
            for name, d in defs.items():
                items.append({"label": name, "kind": 3,
                              "detail": d["detail"], "documentation": d["doc"]})
        send({"jsonrpc": "2.0", "id": msg["id"], "result": items})
    elif method == "textDocument/definition":
        p = msg["params"]
        uri = p["textDocument"]["uri"]
        word = word_at(docs.get(uri, ""),
                       p["position"]["line"], p["position"]["character"])
        if word and word in definitions.get(uri, {}):
            d = definitions[uri][word]
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "uri": uri, "range": _rng(d["line"], d["col"],
                                          d["line"], d["col"] + len(word))}})
        else:
            send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
    elif msg.get("id") is not None and "result" not in msg and "method" not in msg:
        send({"jsonrpc": "2.0", "id": msg["id"], "result": None})


def word_at(text, line, char):
    lines = text.splitlines()
    if line >= len(lines):
        return None
    s = lines[line]
    if char >= len(s):
        char = len(s) - 1
    if char < 0 or not (s[char].isalnum() or s[char] in "_@"):
        if char > 0 and (s[char - 1].isalnum() or s[char - 1] in "_@"):
            char -= 1
        else:
            return None
    start = end = char
    while start > 0 and (s[start - 1].isalnum() or s[start - 1] == "_"):
        start -= 1
    if s[start] == "@":
        start += 1
    while end < len(s) - 1 and (s[end + 1].isalnum() or s[end + 1] == "_"):
        end += 1
    return s[start:end + 1]


def main():
    while True:
        try:
            msg = recv()
        except Exception:
            break
        if msg.get("method") == "exit":
            break
        handle(msg)


if __name__ == "__main__":
    main()
