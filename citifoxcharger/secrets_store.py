"""Windows per-user DPAPI. No plaintext credential files or bundled credentials."""
import ctypes
import os
from ctypes import wintypes
from .config import atomic_write


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data, decrypt=False):
    if os.name != "nt":
        raise ValueError("Persistent key storage is only available on Windows.")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptProtectData.restype = wintypes.BOOL
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    # CRYPTPROTECT_UI_FORBIDDEN. No machine-wide protection flag: bound to this Windows user.
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    description = None if decrypt else "CitiFoxCharger MySkoda API key"
    if not function(ctypes.byref(source), description, None, None, None, 1, ctypes.byref(target)):
        raise ValueError("Windows could not encrypt or decrypt the key.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


class SecretStore:
    def __init__(self, directory):
        self.path = directory / "skoda-key.dpapi"
        self.key = ""
        self.persisted = False
        self.warning = None
        if self.path.exists():
            try:
                self.key = dpapi(self.path.read_bytes(), decrypt=True).decode("utf-8")
                self.persisted = True
            except (ValueError, UnicodeError):
                self.warning = "Please enter the saved API key again; encryption is bound to the Windows user."

    def set(self, key, persist=False):
        if not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ValueError("Invalid API key. Enter it without whitespace.")
        if persist:
            atomic_write(self.path, dpapi(key.encode("utf-8")))
        else:
            self.path.unlink(missing_ok=True)
        self.key, self.persisted, self.warning = key, persist, None

    def clear(self):
        self.path.unlink(missing_ok=True)
        self.key, self.persisted, self.warning = "", False, None

    def status(self):
        return {"configured": bool(self.key), "persisted": self.persisted,
                "persistence_available": os.name == "nt", "warning": self.warning}
