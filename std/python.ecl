# ==========================================
# std/python.ecl — 极简 Python 解析器（ECL 实现）
#
# 只支持最小子集：
#   def name(a, b):
#       return <表达式>
#
# python_parse 返回带 .run(...args) 的对象。
# ==========================================

class PyCode:
    def __init__(str source, str entry) -> none:
        self.source <- source
        self.entry <- entry
    end

    def run(*args) -> any:
        g <- code(self.source).run()
        fn <- g.get(self.entry)
        return fn(*args)
    end
end

def python_parse(str text) -> any:
    entry <- ""
    args <- ""
    body <- ""
    state <- 0

    for raw in text.split("\n"):
        line <- raw.strip()
        if line.find("def ") == 0:
            pieces <- line.split("(")
            if len(pieces) >= 2:
                head <- pieces[0]
                names <- head.split("def ")
                if len(names) >= 2:
                    entry <- names[1].strip()
                end
                tail <- pieces[1].split(")")
                if len(tail) >= 1:
                    args <- tail[0].strip()
                end
                state <- 1
            end
        elif state == 1 and line.find("return ") == 0:
            pieces <- line.split("return ")
            if len(pieces) >= 2:
                body <- pieces[1].strip()
                state <- 2
            end
        end
    end

    if len(entry) == 0 or len(body) == 0:
        return none
    end

    source <- "def " + entry + "(" + args + ") -> any:\n"
    source <- source + "    return " + body + "\nend\n"
    return PyCode(source, entry)
end
