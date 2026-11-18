class res:
    def __enter__() -> none:
        print("enter")
        return none
    end
    def __leave__(any t, any v) -> bool:
        print("leave", t.exc_type)
        return false
    end
end

try:
    with res():
        raise ValueError("boom")
    end
except ValueError:
    print("caught")
end
print("after")
