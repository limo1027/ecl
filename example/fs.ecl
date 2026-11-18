# ==========================================
# example/fs.ecl — 纯 ECL 文件系统库
#
# 只调用 Windows 自带 DLL，不需要任何自写辅助 DLL：
#   kernel32: CreateFileW / ReadFile / WriteFile / FindFirstFileW ...
#
# 约定：
#   - 对外路径参数是 cstr（UTF-8 字节串）；内部调用 W 版 API，
#     dll_import 声明为 wstr，运行时会自动 UTF-8 -> UTF-16
#   - 缓冲区用 new_buffer(n) 创建，&buf 取裸地址
#   - 从 C 缓冲区读回字符串用 lstrcpynA/W + str()
#
# 导出：
#   read_text / write_text / append_text
#   exists / is_dir / file_size
#   delete_file / rename_file / create_dir / delete_dir
#   list_dir
# ==========================================

dll_import "kernel32.dll":
    def CreateFileW(wstr path, i32 access, i32 share, ptr sa,
                    i32 disposition, i32 attrs, int template) -> int
    def ReadFile(int handle, ptr buffer, i32 to_read, ptr read_count,
                 ptr overlapped) -> i32
    def WriteFile(int handle, ptr buffer, i32 to_write, ptr written,
                  ptr overlapped) -> i32
    def SetFilePointer(int handle, i32 low, ptr high, i32 method) -> i32
    def GetFileSize(int handle, ptr high) -> i32
    def CloseHandle(int handle) -> i32
    def DeleteFileW(wstr path) -> i32
    def MoveFileW(wstr from_path, wstr to_path) -> i32
    def CreateDirectoryW(wstr path, ptr attrs) -> i32
    def RemoveDirectoryW(wstr path) -> i32
    def GetFileAttributesW(wstr path) -> i32
    def FindFirstFileW(wstr pattern, ptr find_data) -> int
    def FindNextFileW(int handle, ptr find_data) -> i32
    def FindClose(int handle) -> i32
    def lstrcpynA(ptr dest, cstr src, i32 count) -> cstr
    def lstrcpynW(ptr dest, ptr src, i32 count) -> str
end

const GENERIC_READ <- 0x80000000
const GENERIC_WRITE <- 0x40000000
const OPEN_EXISTING <- 3
const CREATE_ALWAYS <- 2
const OPEN_ALWAYS <- 4
const FILE_ATTRIBUTE_NORMAL <- 0x80
const FILE_ATTRIBUTE_DIRECTORY <- 0x10
const INVALID_HANDLE_VALUE <- -1
const INVALID_FILE_ATTRIBUTES <- -1
const FILE_END <- 2
# WIN32_FIND_DATAW 里 cFileName 的字节偏移
const FIND_DATA_NAME_OFFSET <- 44

# ---------------------------------------------------------------
# 读取整个文件（UTF-8 字节原样返回）
# ---------------------------------------------------------------
public def read_text(cstr path) -> str:
    handle <- CreateFileW(path, GENERIC_READ, 0x1, 0, OPEN_EXISTING,
                          FILE_ATTRIBUTE_NORMAL, 0)
    if handle == INVALID_HANDLE_VALUE:
        return ""
    end

    high <- new_buffer(8)
    size <- int(GetFileSize(handle, &high))
    if size <= 0:
        CloseHandle(handle)
        free(high)
        return ""
    end

    data <- new_buffer(size + 2)
    read_count <- new_buffer(8)
    ok <- ReadFile(handle, &data, size, &read_count, 0)
    CloseHandle(handle)
    free(read_count)
    free(high)
    if ok == 0:
        free(data)
        return ""
    end

    # 把 C 缓冲区里的 UTF-8 字节复制成 ECL str。
    # lstrcpynA 返回 cstr，runtime_dll_call 会立即复制成 VAL_STR，
    # 所以 holder 可以在返回前释放。
    holder <- new_buffer(size + 2)
    text <- str(lstrcpynA(&holder, &data, size + 1))
    free(data)
    free(holder)
    return text
end

# ---------------------------------------------------------------
# 覆盖写入
# ---------------------------------------------------------------
public def write_text(cstr path, str content) -> bool:
    return _write_file(path, content, CREATE_ALWAYS)
end

# ---------------------------------------------------------------
# 追加写入
# ---------------------------------------------------------------
public def append_text(cstr path, str content) -> bool:
    return _write_file(path, content, OPEN_ALWAYS)
end

def _write_file(cstr path, str content, int disposition) -> bool:
    handle <- CreateFileW(path, GENERIC_WRITE, 0x0, 0, disposition,
                          FILE_ATTRIBUTE_NORMAL, 0)
    if handle == INVALID_HANDLE_VALUE:
        return false
    end

    if disposition == OPEN_ALWAYS:
        SetFilePointer(handle, 0, 0, FILE_END)
    end

    n <- len(content)
    if n <= 0:
        CloseHandle(handle)
        return true
    end

    data <- new_buffer(n + 2)
    written <- new_buffer(8)
    lstrcpynA(&data, content, n + 1)
    ok <- WriteFile(handle, &data, n, &written, 0)
    CloseHandle(handle)
    free(data)
    free(written)
    return ok != 0
end

# ---------------------------------------------------------------
# 文件信息
# ---------------------------------------------------------------
public def exists(cstr path) -> bool:
    return GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES
end

public def is_dir(cstr path) -> bool:
    attrs <- GetFileAttributesW(path)
    if attrs == INVALID_FILE_ATTRIBUTES:
        return false
    end
    return (attrs & FILE_ATTRIBUTE_DIRECTORY) != 0
end

public def file_size(cstr path) -> int:
    handle <- CreateFileW(path, GENERIC_READ, 0x1, 0, OPEN_EXISTING,
                          FILE_ATTRIBUTE_NORMAL, 0)
    if handle == INVALID_HANDLE_VALUE:
        return -1
    end
    high <- new_buffer(8)
    size <- int(GetFileSize(handle, &high))
    CloseHandle(handle)
    free(high)
    return size
end

# ---------------------------------------------------------------
# 删除 / 重命名 / 目录
# ---------------------------------------------------------------
public def delete_file(cstr path) -> bool:
    return DeleteFileW(path) != 0
end

public def rename_file(cstr old_path, cstr new_path) -> bool:
    return MoveFileW(old_path, new_path) != 0
end

public def create_dir(cstr path) -> bool:
    return CreateDirectoryW(path, 0) != 0
end

public def delete_dir(cstr path) -> bool:
    return RemoveDirectoryW(path) != 0
end

# ---------------------------------------------------------------
# 列出目录（跳过 . 和 ..），返回以 "\n" 分隔的文件名串
# ---------------------------------------------------------------
public def list_dir(cstr path) -> str:
    pattern <- path
    n <- len(pattern)
    if n > 0 and pattern[n - 1] != "\\":
        if pattern[n - 1] != "/":
            pattern <- pattern + "\\"
        end
    end
    pattern <- pattern + "*"

    find_data <- new_buffer(1024)
    if find_data == 0:
        return ""
    end

    handle <- FindFirstFileW(pattern, &find_data)
    if handle == INVALID_HANDLE_VALUE:
        free(find_data)
        return ""
    end

    result <- ""
    while true:
        name_ptr <- &find_data + FIND_DATA_NAME_OFFSET
        holder <- new_buffer(1024)
        name <- lstrcpynW(&holder, name_ptr, 260)
        free(holder)
        if len(name) > 0 and name != "." and name != "..":
            if len(result) > 0:
                result <- result + "\n"
            end
            result <- result + name
        end
        if FindNextFileW(handle, &find_data) == 0:
            break
        end
    end

    FindClose(handle)
    free(find_data)
    return result
end
