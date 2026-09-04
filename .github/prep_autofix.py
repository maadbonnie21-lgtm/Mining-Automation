"""One-shot source repair for the P0 RuneLite PREP branch.

This file is removed by the companion workflow after verification succeeds.
"""

from pathlib import Path

path = Path("src/mining_automation/validation/_runelite_prep_win32.py")
text = path.read_text(encoding="utf-8")

old = '    return ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]\n'
new = '    return ctypes.WinDLL("user32", use_last_error=True)\n'
if old in text:
    text = text.replace(old, new, 1)
elif new not in text:
    raise SystemExit("WinDLL typing anchor missing")

old = '''        ctypes.set_last_error(0)
        value = int(getter(hwnd, index))
    else:
        getter32 = user32.GetWindowLongW
        getter32.restype = wintypes.LONG
        getter32.argtypes = [wintypes.HWND, ctypes.c_int]
        ctypes.set_last_error(0)
        value = int(getter32(hwnd, index))
    error = ctypes.get_last_error()
    if value == 0 and error:
        raise OSError(error, "GetWindowLong failed for RuneLite PREP")
    return value
'''
new = '''        value = int(getter(hwnd, index))
    else:
        getter32 = user32.GetWindowLongW
        getter32.restype = wintypes.LONG
        getter32.argtypes = [wintypes.HWND, ctypes.c_int]
        value = int(getter32(hwnd, index))
    if value == 0:
        raise OSError("GetWindowLong returned zero for RuneLite PREP")
    return value
'''
if old in text:
    text = text.replace(old, new, 1)
elif new not in text:
    raise SystemExit("GetWindowLong typing anchor missing")

path.write_text(text, encoding="utf-8")
