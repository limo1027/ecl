import py
sour <- """add(3, 7)"""
cod <- @@py_py(sour)
print("3 + 7 =", cod.run())
sour <- """mul(18446744073709551615, 7)"""
cod <- @@py_py(sour)
print("18446744073709551615 * 7 =", cod.run())
sour <- """sub(3, 7)"""
cod <- @@py_py(sour)
print("3 - 7 =", cod.run())