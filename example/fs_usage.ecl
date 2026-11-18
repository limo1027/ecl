# ==========================================
# example/fs_usage.ecl — fs.ecl 的测试/演示
#
# 编译 fs.dll：
#   python main.py compile example/fs.ecl --target dll
#
# 运行：
#   python main.py run example/fs_usage.ecl
# ==========================================

dll_import "C:/Users/Lq/Desktop/myprojects/NUD重制版/example/fs.dll":
    def read_text(cstr path) -> str
    def write_text(cstr path, str content) -> bool
    def append_text(cstr path, str content) -> bool
    def exists(cstr path) -> bool
    def is_dir(cstr path) -> bool
    def file_size(cstr path) -> int
    def delete_file(cstr path) -> bool
    def rename_file(cstr old_path, cstr new_path) -> bool
    def create_dir(cstr path) -> bool
    def delete_dir(cstr path) -> bool
    def list_dir(cstr path) -> str
end

print("=== 当前目录下的文件 ===")
names <- list_dir(".")
for name in names.split("\n"):
    if len(name) > 0:
        print("  ", name)
    end
end
print("=== 列表结束 ===")