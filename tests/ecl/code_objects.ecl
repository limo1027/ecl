print("code")
code c <- `print("hi from code")`
c.run()
int captured <- 100
code c2 <- `print("captured =", captured)`
c2.run()
global g <- `int z <- 5
str msg <- "done"`.run()
print(g.z)
print(g.msg)
print(g.keys())
