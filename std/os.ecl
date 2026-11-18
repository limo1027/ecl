# std/os.ecl
dll_import "kernel32.dll":
    def GetCommandLineA() -> cstr
end

public def get_args() -> list:
    cmd <- str(GetCommandLineA())
    parts <- cmd.split(" ")
    result <- []
    for p in parts:
        if len(p) > 0:
            result.append(p)
        end
    end
    return result
end