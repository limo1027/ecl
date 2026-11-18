struct POINT:
    i32 x
    i32 y
end

dll_import "user32.dll":
    def GetCursorPos(POINT* pt) -> i32
end

p <- POINT()
if GetCursorPos(&p) != 0:
    print("鼠标坐标:", p.x, p.y)
end