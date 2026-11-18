class py:
    def __exec__(str code):
        self.tokens <- []
        # mul(a, b)
        code <- code.replace(" ", "")
        code <- code.replace("(", " ")
        code <- code.replace(")", " ")
        code <- code.replace(",", " ")
        self.tokens <- code.split()
        return self
    end

    def run() -> int:
        if self.tokens[0] == "mul":
            return int(self.tokens[1]) * int(self.tokens[2])
        elif self.tokens[0] == "add":
            return int(self.tokens[1]) + int(self.tokens[2])
        elif self.tokens[0] == "sub":
            return int(self.tokens[1]) - int(self.tokens[2])
        end

    end
end