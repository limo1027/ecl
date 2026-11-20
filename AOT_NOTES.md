# ECL 0.1.0 实现说明

本目录是 ECL（Easy Control Language）解释器 + AOT 原生编译器。

## 运行

```powershell
python ecl.py run example/general_test.ecl     # 解释执行
python ecl.py run example/dll_test.ecl         # dll_import 示例
python ecl.py run example/ask_dialog.ecl       # 是/否询问框
python ecl.py run example/file_dialog.ecl      # 文件选择框（纯 ECL + struct）
python ecl.py example/general_test.ecl         # 同上（旧写法）
python ecl.py                                  # REPL
python ecl.py compile example/simple.ecl --target asm
python ecl.py compile example/simple.ecl --target exe
python ecl.py compile example/simple.ecl --target dll
python ecl.py compile example/fs.ecl --target dll
python ecl.py compile example/fs.ecl --target dll --remove-output
python ecl.py run example/fs_usage.ecl
```

- `example/testdll.c` 是示例 DLL 的源码；`example/testdll.dll` 已随目录提供。
  重新编译：`python example/build_testdll.py`（需要 MSVC）。
- `example/test.ecl` 是 `--target dll` 的导出示例；`example/simple.ecl` 是加载它的宿主示例。
- `compile --target asm` 只需要项目目录内的 `uasm64.exe`。
- `compile --target exe/dll` 使用项目目录内的 `uasm64.exe` + `lld-link.exe` 完成汇编/链接；
  `runtime.c` 由项目目录内的 `clang-cl.exe` 编译（`cl.exe` 仅作为后备）。
  没有编译器时，如果已有 `runtime.obj`，会直接使用预编译对象。
- `compile --target dll` 生成无 CRT 的 `.dll`，只导出 `public def` 的顶层函数
  （C ABI 包装 + `.def`）；同时生成 `.lib` / `.exp`。
- `compile --remove-output` 在成功链接后删除本次生成的中间文件
  （`.asm` / `.obj` / `.def` / `.lib` / `.exp` / `.eclc`），只保留最终 `.exe` / `.dll`；
  `--target asm` 时保留 `.asm`，只清理编译缓存。
- AOT 运行时会动态加载 `ucrtbase.dll`（Windows 自带）里的 `_gcvt` / `strtod` / `sqrt`，
  用于生成和 Python `repr` 一致的“最短 round-trip”浮点字符串。
- `run --no-dll` / REPL 的 `--no-dll` 会禁止 `dll_import`。
- `.eclc` 缓存会检查源文件内容 hash，以及源文件、被 `import` 的模块与缓存的
  修改时间：任一源文件比缓存新就自动重编。
  `--used-cache` 强制使用现有缓存（即使 `.ecl` 已更新），
  `--no-cache` 完全跳过缓存；两者同时给出时以 `--no-cache` 为准。

## 测试

```powershell
python tests/run_tests.py
```

- `tests/test_*.py`：文档逐节覆盖（变量、类型、控制流、函数、OOP/魔法方法、
  match、with、using、解包、异常、内置函数、import、缓存）。
- `tests/ecl/*.ecl` + `.expected`：端到端脚本；`test_aot.py` 会把同一脚本
  分别用解释器和 AOT 原生 EXE 跑一遍，逐字节比对 stdout。
- `tests/test_dll.py`：需要 MSVC。会现场编译 `tests/native/testdll.c`，覆盖
  `struct` / `i32` / `dll_import` 的 `ptr` / `in` / `out` 参数，以及
  `--target dll` 的 C ABI 导出；找不到 MSVC 时跳过。
- 只要 `uasm64.exe`、`lld-link.exe` 和 `clang-cl.exe`（或已有 `runtime.obj`）存在，
  AOT 测试就会运行；没有这些工具时自动跳过。

## AOT 架构

`token_parse → ast_parse → checker → compiler(IR) → asm.py(汇编) → uasm64/lld-link`
配合 `runtime.c` 的动态值运行时（Value 标签 + BigInt + list/dict/class/instance +
异常 setjmp）。解释器路径是同一份 IR 由 `vm.py` 执行。

### struct / dll_import

- `struct` 在 ECL 里就是普通 `dict`；AOT 在 `.data` 里生成布局描述，由
  `runtime_dll_register_layouts` 按 C 对齐规则计算字段偏移。
- `dll_import` 在 AOT 中不参与链接：调用点生成
  `path|函数|返回类型|参数:修饰符` 描述串，运行时 `LoadLibraryW` + `GetProcAddress`，
  由 `setjmp.asm` 的 `ecl_invoke` 按 Windows x64 ABI（rcx/rdx/r8/r9 与 xmm0-3，
  第 5 个起走栈）完成动态调用。`str` 走 UTF-8 ↔ UTF-16 转换，`cstr` 直接传
  UTF-8 指针，`ptr`/`out` 结构体在调用后写回 dict。
- `--target dll` 为每个 `public def` 生成 `ecl_export_<name>` 包装：把 C ABI
  参数转成 ECL `Value`，调用原函数，再按返回类型转换（`str` → `wchar_t*`）。
  `.def` 把导出名映射到包装符号；`private def` 不导出。
- DLL 用 `/NOENTRY` 链接，没有 DllMain；导出包装在第一次被调用时执行一次 `main`，
  完成常量、全局变量、函数定义和 struct 布局注册，因此导出函数中的常量/全局变量
  与解释器行为一致。
- `example/fs.ecl` 是纯 ECL 文件系统库（`CreateFileW`/`FindFirstFileW` 等），
  `example/fs_usage.ecl` 是它的使用/测试脚本；`tests/test_fs.py` 会编译成 DLL
  并在解释器与 AOT 下逐字节比对输出。
- struct 返回值不属于 C ABI 允许的导出/导入返回类型（检查器会报错）。

## 已知差异（与解释器不完全一致的地方）

1. **double 精度**：解释器用 `decimal.Decimal`（34 位十进制，近似 binary128）；
   AOT 的 double 是 C `double`（binary64）。因此对 double 做运行期运算时，
   末位可能不同（例如 `0.1d + 0.2d` 解释器为 `0.3`，AOT 可能为
   `0.30000000000000004`）。常量表达式在编译期折叠时两边一致。
2. **任意精度整数**：AOT 已支持 BigInt 的 `+ - * /`、比较、幂（指数较小时）；
   `//` 和 `%` 在 BigInt 上会抛 `NotImplementedError`。普通数值的 `int` 仍是
   64 位；`int("...")` 支持十进制、`0x` / `0o` / `0b` 前缀、正负号和下划线，
   超出 int64 时生成 BigInt，`i64(...)` / `-> i64` 返回时再按 64 位回绕。
3. **动态 code()**：静态反引号代码块 / `code("字面量")` 会在 AOT 中编译成隐藏函数，
   `run()` 返回变量字典，行为与解释器一致；`code(变量)` 这种运行期字符串无法在
   AOT 编译，会抛 `NotImplementedError`。
4. **闭包 `last.x`**：AOT 用“函数名 + 变量名”的全局捕获槽实现，普通嵌套函数
   （文档示例）行为一致；递归函数中捕获同一变量会共享槽（解释器是每帧独立）。
5. **random / id / hash**：本身依赖实现/随机种子，两边数值不保证相同。
6. **原生异常规则**：`return` 位于 try 体内时 `finally` 不会执行（解释器与 AOT
   行为一致，但和文档“finally 无论是否报错都执行”仍有差距）。
7. **内存模型（ECL 0.1.0）**：
   - `a <- b` 是深拷贝（嵌套 list/dict/struct/instance 一起复制）；
   - `ref` 已废弃，检查器直接报错；
   - `new_buffer(n)` 创建裸字节缓冲区，`buf[i]` 读写单字节，`&buf` 取地址；
   - `free(x)` 物理释放该槽位指向的堆内存并置 `null`；
   - 无 GC、无引用计数；进程退出时 `runtime_shutdown()` 统一释放。
8. **struct 真类型**：
   - `struct P: ... end` + `P()` 构造，字段 `.x` 读写，`sizeof(P)` 编译期常量；
   - 字段按 C 对齐布局，`&p` 得到连续内存地址，可直接传给 `P*` 的 dll_import；
   - `str` 字段为 UTF-8 `char*`，`wstr` 字段为 UTF-16 `wchar_t*`。
9. **指针与 wstr**：
   - `T*` 作为 dll_import 参数类型时按裸地址传递；
   - `w"..."` 是 UTF-16 字面量，`type()` 返回 `wstr`。
10. **@@ 外部语言执行**：
    ```ecl
    import py

    code <- """
    def add(a, b):
        return a + b
    """

    ctx <- @@py_py(code)      # 脱糖为 py_py().__exec__(code)
    print(ctx.run(3, 5))
    ```
    `@@` 只是语法糖：解析成「实例化扁平类 + 调用 `__exec__`」的普通 AST，
    与手写 `py_py().__exec__(code)` 完全等价，因此解释器和 AOT 走同一套 IR。
    检查器会校验目标类存在且实现了 `__exec__`；没有 import 或类不存在时报错。
    目标类返回一个带 `.run(...)` 的上下文对象，外部语言的实际解析完全由 ECL
    代码自己完成，编译器不认识 Python/Lua/JS 语法。目标类的实现有两种常见写法：
    纯 ECL 解析（可 AOT），或内部用动态 `code()` 编译（解释器模式）。
    `example/py.ecl` 是仓库里的最小示例。

