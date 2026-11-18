# ==========================================
# 纯 ECL 文件操作 (直接调 kernel32.dll)
# ==========================================

dll_import "kernel32.dll":
    def CreateFileW(str lpFileName, i32 dwDesiredAccess, i32 dwShareMode, ptr lpSecurityAttributes, i32 dwCreationDisposition, i32 dwFlagsAndAttributes, int hTemplateFile) -> int
    def ReadFile(int hFile, ptr lpBuffer, i32 nNumberOfBytesToRead, ptr lpNumberOfBytesRead, ptr lpOverlapped) -> i32
    def WriteFile(int hFile, ptr lpBuffer, i32 nNumberOfBytesToWrite, ptr lpNumberOfBytesWritten, ptr lpOverlapped) -> i32
    def CloseHandle(int hObject) -> i32
    def DeleteFileW(str lpFileName) -> i32
    def FindFirstFileW(str lpFileName, ptr lpFindFileData) -> int
    def FindNextFileW(int hFindFile, ptr lpFindFileData) -> i32
    def FindClose(int hFindFile) -> i32
    def VirtualAlloc(int lpAddress, int dwSize, i32 flAllocationType, i32 flProtect) -> int
    def lstrcpynW(int dest, int src, i32 n) -> none
end

# 常量
const GENERIC_READ <- 0x80000000
const GENERIC_WRITE <- 0x40000000
const OPEN_EXISTING <- 3
const CREATE_ALWAYS <- 2
const FILE_ATTRIBUTE_NORMAL <- 0x80
const INVALID_HANDLE_VALUE <- -1

# 读文件
def read_file(str path) -> str:
    h <- CreateFileW(path, GENERIC_READ, 0, 0, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, 0)
    if h == INVALID_HANDLE_VALUE:
        return ""
    end
    
    # 读 64KB
    size <- 65536
    buf <- VirtualAlloc(0, size + 2, 0x3000, 0x04)
    bytes_read <- VirtualAlloc(0, 8, 0x3000, 0x04)
    
    ReadFile(h, buf, size, bytes_read, 0)
    CloseHandle(h)
    
    # 用 lstrcpynW 把拿到的指针转成 ECL 的 str
    rbuf <- VirtualAlloc(0, 65536, 0x3000, 0x04)
    lstrcpynW(rbuf, buf, 65536) 
    
    # 假设 ECL 支持 str(ptr) 或自动读取以 \0 结尾的字符串
    return str(rbuf)
end

# 写文件
def write_file(str path, str content) -> bool:
    h <- CreateFileW(path, GENERIC_WRITE, 0, 0, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, 0)
    if h == INVALID_HANDLE_VALUE:
        return false
    end
    
    written <- VirtualAlloc(0, 8, 0x3000, 0x04)
    ret <- WriteFile(h, content, len(content), written, 0)
    CloseHandle(h)
    return ret != 0
end

# 删文件
def delete_file(str path) -> bool:
    return DeleteFileW(path) != 0
end

# 列目录
struct WIN32_FIND_DATAW:
    i32 dwFileAttributes
    i32 pad1
    i64 ftCreationTime
    i64 ftLastAccessTime
    i64 ftLastWriteTime
    i32 nFileSizeHigh
    i32 nFileSizeLow
    i32 dwReserved0
    i32 dwReserved1
    ptr cFileName # 偏移量 44
    ptr cAlternateFileName
end

def list_dir(str path) -> none:
    search_path <- path + "\\*"
    data <- VirtualAlloc(0, 600, 0x3000, 0x04)
    hFind <- FindFirstFileW(search_path, data)
    
    if hFind == INVALID_HANDLE_VALUE:
        return
    end
    
    label loop
        # 由于 ECL 目前可能不支持直接取结构体偏移，我们用 rbuf 和 lstrcpynW 截取
        # 把 data 转成 dict 读取（假设你有自动解析 struct 的功能）
        # 这里直接打印目录内容
        file_title <- VirtualAlloc(0, 520, 0x3000, 0x04)
        # 偏移 44 取 cFileName
        file_ptr <- data + 44 
        lstrcpynW(file_title, file_ptr, 260)
        print(" -", str(file_title))
        
        if FindNextFileW(hFind, data) == 0:
            break
        end
        goto loop
    end
    
    FindClose(hFind)
end

# 测试
print("=== ECL 纯原生文件操作 ===")

test_file <- "example/file_ops_test.txt"

write_file(test_file, "Hello ECL! 这是直接操作 Kernel32.dll 写入的。")

content <- read_file(test_file)
print("文件内容:", content)

print("目录列表:")
list_dir("example")

delete_file(test_file)
print("文件已删除。")