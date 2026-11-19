---
name: Bug 报告
about: 报告解释器 / AOT 编译 / 缓存 / DLL 互操作的问题
title: "[Bug] 一句话描述问题"
labels: bug
assignees: ''
---
## 环境信息
- ECL 版本或 commit: <!-- 例如 v0.1.0 / a1b2c3d -->
- 运行方式: 
  - [ ] 解释器(python ecl.py run)
  - [ ] REPL
  - [ ] AOT 编译产物(exe)
  - [ ] AOT 编译产物(dll，由宿主调用)
  - [ ] dump / load 缓存操作
- 操作系统: <!-- 例如 Windows 11 23H2 / Windows Server 2022 -->
- Python 版本(解释模式): <!-- 例如 3.11.8 -->
- AOT 工具链(如相关): <!-- 例如 uasm64 + lld-link，或 MSVC ml64/cl/link -->
## 复现步骤
```ecl
# test.ecl —— 最小可复现代码
# 请尽量精简到十几行以内，去掉与问题无关的部分
def f(x) -> int:
    return x + 1
end
print(f(1))
```
复现命令: 
```bash
python ecl.py run test.ecl --verbose
```
## 期望结果

<!-- 正确输出应该是什么 -->

## 实际结果

<!-- 实际输出 / 错误信息，完整粘贴，不要截图 -->

## 关键信息

- [ ] 解释器模式下能复现
- [ ] AOT 模式下能复现(compile --target exe 后运行产物)
- [ ] 只有 AOT 模式下有问题，解释器正常
- [ ] 只有解释器模式下有问题，AOT 正常
- [ ] 涉及缓存(删除 .eclc 后问题消失/出现)
- [ ] 涉及 dll_import / struct 布局
- [ ] 涉及 DLL 导出(--target dll)

💡 如果两条执行路径行为不一致，请在标题注明「仅 AOT」或「仅解释器」，这类
问题优先级最高。

## AOT 模式补充(仅 AOT 问题时填写)

是否愿意附上下列中间产物？

- [ ] 生成的 .asm 片段(出错函数附近即可)

- [ ] dump test.aot.eclc --flat 的输出

- [ ] 汇编器/链接器的完整报错

## 其他说明

<!-- 可能的原因猜测、regression 出现的版本、workaround 等 -->
