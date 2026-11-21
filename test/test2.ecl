print("---- 边界回归：本轮修复点 ----")

# 1. 重载 + 默认参数 + *args 混合（对应 define 无条件发射的修复）
def pick():
    return 0
end
def pick(x):
    return x
end
def pick(x, y = 10):
    return x + y
end
def collect(*args):
    n <- 0
    for a in args:
        n <- n + a
    end
    return n
end
r1 <- pick()
r2 <- pick(5)
r3 <- pick(1, 2)
r5 <- collect(1, 2)
r6 <- collect(1, 2, 3, 4)
if r1 == 0 and r2 == 5 and r3 == 3 and r5 == 3 and r6 == 10:
    print("[PASS] 重载+默认值+varargs")
else:
    print("[FAIL] 重载+默认值+varargs")
    print(r1) 
    print(r2)
    print(r3) 
    print(r5) 
    print(r6)
end

# 2. with 返回资源被 body 修改（对应 store_share 的修复）
ledger <- []
class Ledger:
    def __enter__():
        ledger.clear()
        ledger.append("open")
        return ledger
    end
    def __leave__(etype, evalue):
        ledger.append("close")
        return False
    end
end
with Ledger() as lg:
    lg.append("tx1")
    lg.append("tx2")
end
ok2 <- (ledger == ["open", "tx1", "tx2", "close"])
if ok2:
    print("[PASS] with 资源引用绑定")
else:
    print("[FAIL] with 资源引用绑定")
    print(ledger)
end

# 2b. with 异常路径：leave 拿到异常且 body 的修改留在资源上
ledger.clear()
try:
    with Ledger() as lg:
        lg.append("boom")
        raise ValueError("fail")
    end
except:
    print("caught")
else:
    print("unreachable")   # 这行不应执行
end
out_ <- 100
# 3. 闭包捕获（global_set 共享语义）
def make_counter():
    n <- 0
    def inc():
        last.n <- last.n + 1
        global.out_ <- 200
        return last.n
    end
    return inc
end
c1 <- make_counter()
c2 <- make_counter()
a <- c1()
a <- c1()
b <- c2()
if a == 2 and b == 1 and out_ == 200:
    print("[PASS] 闭包独立捕获")
else:
    print("[FAIL] 闭包独立捕获")
    print(a) 
    print(b)
end