"""ECL struct 编译期布局计算（C 对齐规则）。

字段类型：
    bool / i32 / float      4 字节
    i64 / int / double      8 字节
    str / cstr / wstr / ptr 8 字节
    嵌套 struct             按内层大小/对齐
    T*                      8 字节指针
"""

SCALAR_SIZE = {
    'bool': 4, 'i32': 4, 'float': 4,
    'i64': 8, 'int': 8, 'double': 8,
    'str': 8, 'cstr': 8, 'wstr': 8, 'ptr': 8,
}


def _size_align(type_name, layouts, stack=()):
    if type_name in layouts:
        if type_name in stack:
            raise ValueError(f"struct 递归嵌套: {type_name}")
        return layouts[type_name]['size'], layouts[type_name]['align']
    if type_name.endswith('*'):
        return 8, 8
    if type_name in SCALAR_SIZE:
        n = SCALAR_SIZE[type_name]
        return n, n
    # 未知类型按指针处理
    return 8, 8


def compute_layouts(structs):
    """structs: name -> [(type, field)]，返回 name -> layout。"""
    layouts = {}
    remaining = dict(structs)
    # 多轮解析，允许任意声明顺序 / 嵌套
    for _ in range(len(structs) + 1):
        progressed = False
        for name, fields in list(remaining.items()):
            offsets = []
            offset = 0
            max_align = 1
            ok = True
            for ftype, fname in fields:
                if ftype in structs and ftype not in layouts:
                    ok = False
                    break
                size, align = _size_align(ftype, layouts)
                if align < 1:
                    align = 1
                offset = (offset + align - 1) // align * align
                offsets.append((ftype, fname, offset, size))
                offset += size
                max_align = max(max_align, align)
            if not ok:
                continue
            size = (offset + max_align - 1) // max_align * max_align
            layouts[name] = {
                'size': size,
                'align': max_align,
                'fields': offsets,
            }
            del remaining[name]
            progressed = True
        if not remaining or not progressed:
            break
    # 无法解析的（循环）给 0
    for name in remaining:
        layouts[name] = {'size': 0, 'align': 1, 'fields': []}
    return layouts
