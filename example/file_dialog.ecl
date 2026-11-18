# ==========================================
# 纯 ECL 文件选择框（不封装 DLL，直接调用 comdlg32 / kernel32）
# 运行：python main.py run example/file_dialog.ecl
# ==========================================

# OPENFILENAMEW（Windows x64，152 字节）
# i32 pad 用于补齐 C 结构体的对齐空洞；
# nFileOffset + nFileExtension 两个 WORD 合成一个 i32。
struct OPENFILENAMEW:
    i32 lStructSize
    i32 pad0
    ptr hwndOwner
    ptr hInstance
    ptr lpstrFilter
    ptr lpstrCustomFilter
    i32 nMaxCustFilter
    i32 nFilterIndex
    ptr lpstrFile
    i32 nMaxFile
    i32 pad1
    ptr lpstrFileTitle
    i32 nMaxFileTitle
    i32 pad2
    ptr lpstrInitialDir
    str lpstrTitle
    i32 Flags
    i32 nFileOffsetExt
    ptr lpstrDefExt
    int lCustData
    ptr lpfnHook
    ptr lpTemplateName
    ptr pvReserved
    i32 dwReserved
    i32 FlagsEx
end

dll_import "comdlg32.dll":
    def GetOpenFileNameW(in OPENFILENAMEW ofn) -> i32
end

dll_import "kernel32.dll":
    def VirtualAlloc(int addr, int size, i32 alloc_type, i32 protect) -> int
    def lstrcpyW(int dest, str src) -> none
    def lstrcpynW(int dest, int src, i32 n) -> str
end

# lpstrFile 必须指向一块可写的宽字符缓冲区。
# VirtualAlloc(MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE) 返回清零内存。
wbuf <- VirtualAlloc(0, 2048, 0x3000, 0x04)
rbuf <- VirtualAlloc(0, 2048, 0x3000, 0x04)
if wbuf == 0 or rbuf == 0:
    print("内存分配失败")
    exit(1)
end

# 初始文件名为空；VirtualAlloc 已经是清零内存，这里只是显式写入 NUL。
lstrcpyW(wbuf, "")

ofn <- {
    "lStructSize": 152,
    "pad0": 0,
    "hwndOwner": 0,
    "hInstance": 0,
    "lpstrFilter": 0,
    "lpstrCustomFilter": 0,
    "nMaxCustFilter": 0,
    "nFilterIndex": 0,
    "lpstrFile": wbuf,
    "nMaxFile": 1024,
    "pad1": 0,
    "lpstrFileTitle": 0,
    "nMaxFileTitle": 0,
    "pad2": 0,
    "lpstrInitialDir": 0,
    "lpstrTitle": "选择一个文件",
    "Flags": 0x00081808,
    "nFileOffsetExt": 0,
    "lpstrDefExt": 0,
    "lCustData": 0,
    "lpfnHook": 0,
    "lpTemplateName": 0,
    "pvReserved": 0,
    "dwReserved": 0,
    "FlagsEx": 0
}

ret <- GetOpenFileNameW(ofn)
if ret == 0:
    print("未选择文件")
else:
    # 把宽字符缓冲区复制到另一块缓冲区，并作为 str 返回
    path <- lstrcpynW(rbuf, wbuf, 1024)
    print("选择的文件：")
    print(path)
end
