class Optimizer:
    """
    ECL 中间表示 (IR) 优化器。
    包含：常量折叠、死代码消除。
    """

    def __init__(self):
        self.passes = [
            self.constant_folding,
            self.dead_code_elimination,
        ]

    def optimize(self, ir):
        for _ in range(2):
            for pass_fn in self.passes:
                ir = pass_fn(ir)
        return ir

    # ----------------------------------------------------------------
    # Pass 1: 常量折叠 (Constant Folding)
    # ----------------------------------------------------------------
    def constant_folding(self, ir):
        new_ir = []
        const_map = {}

        math_ops = {
            'add': lambda a, b: a + b,
            'sub': lambda a, b: a - b,
            'mul': lambda a, b: a * b,
            'div': lambda a, b: a / b,
            'floordiv': lambda a, b: a // b,
            'mod': lambda a, b: a % b,
            'pow': lambda a, b: a ** b,
        }
        cmp_ops = {
            'eq': lambda a, b: a == b,
            'ne': lambda a, b: a != b,
            'lt': lambda a, b: a < b,
            'gt': lambda a, b: a > b,
            'le': lambda a, b: a <= b,
            'ge': lambda a, b: a >= b,
        }

        def clear_args(op):
            """把 op 里所有字符串参数从 const_map 移除"""
            for arg in op[1:]:
                if isinstance(arg, str):
                    const_map.pop(arg, None)
                elif isinstance(arg, (list, tuple)):
                    for sub in arg:
                        if isinstance(sub, str):
                            const_map.pop(sub, None)
                elif isinstance(arg, dict):
                    for sub in arg.values():
                        if isinstance(sub, str):
                            const_map.pop(sub, None)

        for op in ir:
            kind = op[0]

            # ★ 控制流汇合点：label 之后常量表不可靠，必须清空
            if kind == 'label':
                const_map.clear()
                new_ir.append(op)
                continue

            # 跳转目标也是汇合点：goto / if_goto / if_not_goto / jz / jnz
            # 保守起见，遇到跳转就清空（跳转后的代码可能是另一个分支的汇合点）
            if kind in ('goto', 'if_goto', 'if_not_goto', 'jz', 'jnz'):
                clear_args(op)
                new_ir.append(op)
                continue

            if kind == 'const':
                _, dest, value = op
                const_map[dest] = value
                new_ir.append(op)
                continue

            elif kind in math_ops or kind in cmp_ops:
                _, dest, left, right = op
                if left in const_map and right in const_map:
                    try:
                        if kind in math_ops:
                            result = math_ops[kind](
                                const_map[left], const_map[right])
                        else:
                            result = cmp_ops[kind](
                                const_map[left], const_map[right])
                        new_ir.append(('const', dest, result))
                        const_map[dest] = result
                        continue
                    except Exception:
                        pass
                const_map.pop(left, None)
                const_map.pop(right, None)
                new_ir.append(op)
                continue

            elif kind == 'assign':
                _, dest, src = op
                if src in const_map:
                    new_ir.append(('const', dest, const_map[src]))
                    const_map[dest] = const_map[src]
                    continue
                else:
                    const_map.pop(src, None)
                    new_ir.append(op)
                    continue

            else:
                clear_args(op)
                new_ir.append(op)

        return new_ir

    # ----------------------------------------------------------------
    # Pass 2: 死代码消除 (Dead Code Elimination)
    # ----------------------------------------------------------------
    def dead_code_elimination(self, ir):
        """
        只对函数体做 DCE，避免影响主程序（主程序里有 print 和 store）。
        """
        new_ir = []

        for op in ir:
            if op[0] in ('func', 'method_func'):
                if op[0] == 'func':
                    _, name, sub_code, params, varargs, kwargs, ret_type, defaults = op
                    tag = 'func'
                else:
                    _, cls_name, method_name, sub_code, params, varargs, kwargs, ret_type, defaults = op
                    name = f"{cls_name}.{method_name}"
                    tag = 'method_func'

                # 1. 收集所有被使用的临时变量（递归扫描嵌套容器）
                def collect(arg):
                    if isinstance(arg, str):
                        used_vars.add(arg)
                    elif isinstance(arg, (list, tuple)):
                        for sub_arg in arg:
                            collect(sub_arg)
                    elif isinstance(arg, dict):
                        for sub_arg in arg.values():
                            collect(sub_arg)

                used_vars = set()
                for instr in sub_code:
                    for arg in instr[1:]:
                        collect(arg)

                # 2. 过滤掉没用的纯计算指令
                new_sub_code = []
                droppable = ('const', 'assign', 'add', 'sub', 'mul', 'div',
                             'floordiv', 'mod', 'pow', 'eq', 'ne',
                             'lt', 'gt', 'le', 'ge', 'bor', 'band',
                             'xor', 'lshift', 'rshift', 'neg', 'invert',
                             'not', 'new_list', 'new_dict', 'new_code',
                             'new_code_var', 'get_item', 'get_attr',
                             'get_attr_opt', 'to_tuple', 'to_bi',
                             'convert_type', 'call', 'call_spread',
                             'builtin_call', 'match_eq', 'match_multi',
                             'is_type')
                for instr in sub_code:
                    kind = instr[0]
                    if kind in droppable:
                        dest = instr[1]
                        if dest not in used_vars:
                            continue
                    new_sub_code.append(instr)

                if tag == 'func':
                    new_ir.append(('func', name, new_sub_code,
                                  params, varargs, kwargs, ret_type, defaults))
                else:
                    new_ir.append(('method_func', cls_name, method_name,
                                  new_sub_code, params, varargs, kwargs, ret_type, defaults))
            else:
                new_ir.append(op)

        return new_ir
