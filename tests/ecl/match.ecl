# match：FALLTHROUGH / 守卫 / break
define const FALLTHROUGH
int x <- 2
match x:
    case 1:
        print("one")
    case 2:
        print("two")
    case 3:
        print("three")
end

int y <- 3
match y:
    case int if y > 5:
        print("big")
    case int if y == 3:
        print("three")
        break
    case _:
        print("other")
end
