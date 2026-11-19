
---
name: Pull Request
about: 提交代码改动
---

## 改动说明

(可输入说明)

## 改动范围

- [ ] 词法(token_parse.py)
- [ ] 语法(ast_parse.py)
- [ ] 检查器(checker.py)
- [ ] 编译器(compiler.py)
- [ ] 优化器(optimizer.py)
- [ ] VM / 解释器(vm.py / interpreter.py)
- [ ] AOT 后端(asm.py / runtime.c / setjmp.asm)
- [ ] 缓存(dl.py)
- [ ] CLI(ecl.py)
- [ ] 文档(语法库.txt / README)

## 自查清单

- [ ] 解释器模式下测试通过
- [ ] AOT 模式下行为一致(同一份 .ecl，两条路径输出一致)
- [ ] 改了 IR 指令形状时已将 dl.py 的 IR_VERSION +1
- [ ] 涉及语法变更时已同步更新 语法库.txt
- [ ] 未定义变量的警告行为不受影响
- [ ] 新增 struct 字段类型 / dll_import 类型时已同步 checker、struct_layout 和 runtime.c 的布局规则
- [ ] .eclc 缓存在改动后能正确失效重编(或明确说明缓存兼容)
  
## 测试

运行方式及结果: 
```bash
python ecl.py run test.ecl     # 实际输出
python ecl.py compile test.ecl --target exe && test.exe   # 实际输出
```
新增测试用例(如有): 
```ecl
# 测试代码
```

## 相关 Issue

Closes #