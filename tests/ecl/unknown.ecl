print("unknown == unknown:", unknown == unknown)
print("unknown == null:", unknown == null)
print("unknown == none:", unknown == none)

if undefined_var:
    print("truthy")
else:
    print("falsy")
end

try:
    print(undefined_var + 1)
except TypeError:
    print("unknown + 1 -> TypeError")
end

try:
    print(undefined_var < 1)
except TypeError:
    print("unknown < 1 -> TypeError")
end
