# ==========================================
# 弹出一个“是 / 否”询问框（Windows user32.dll）
# 运行：python main.py run example/ask_dialog.ecl
# ==========================================

dll_import "user32.dll":
    # UINT MessageBoxW(HWND, LPCWSTR, LPCWSTR, UINT)
    def MessageBoxW(int hwnd, str text, str caption, i32 flags) -> i32
end

# MB_YESNO(0x4) | MB_ICONQUESTION(0x20) | MB_TOPMOST(0x40000)
const MB_YESNO_QUESTION <- 0x00040024

def ask_yes_no(str question, str title) -> bool:
    answer <- MessageBoxW(0, question, title, MB_YESNO_QUESTION)
    return answer == 6   # IDYES
end

if ask_yes_no("要打开 ECL 示例吗？", "ECL 询问"):
    print("你选择了：是")
else:
    print("你选择了：否")
end
