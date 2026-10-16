"""Windows power requests for an active charging session; never keep the display on."""
import ctypes
import os
import threading
from ctypes import wintypes

SYSTEM_REQUIRED, EXECUTION_REQUIRED = 1, 3


class DetailedReason(ctypes.Structure):
    _fields_ = [("module", wintypes.HMODULE), ("reason_id", wintypes.ULONG),
                ("count", wintypes.ULONG), ("strings", ctypes.POINTER(wintypes.LPWSTR))]


class Reason(ctypes.Union):
    _fields_ = [("detailed", DetailedReason), ("simple", wintypes.LPWSTR)]


class ReasonContext(ctypes.Structure):
    _fields_ = [("version", wintypes.ULONG), ("flags", wintypes.DWORD), ("reason", Reason)]


class SystemPowerStatus(ctypes.Structure):
    _fields_ = [("ac", wintypes.BYTE), ("battery", wintypes.BYTE),
                ("percent", wintypes.BYTE), ("flags", wintypes.BYTE),
                ("remaining", wintypes.DWORD), ("full", wintypes.DWORD)]


class WindowsPowerRequest:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.PowerCreateRequest.argtypes = [ctypes.POINTER(ReasonContext)]
        self.api.PowerCreateRequest.restype = wintypes.HANDLE
        for name in ("PowerSetRequest", "PowerClearRequest"):
            function = getattr(self.api, name)
            function.argtypes = [wintypes.HANDLE, ctypes.c_int]
            function.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.api.GetSystemPowerStatus.argtypes = [ctypes.POINTER(SystemPowerStatus)]
        self.api.GetSystemPowerStatus.restype = wintypes.BOOL

    def create(self):
        reason = ReasonContext()
        reason.version, reason.flags = 0, 1  # POWER_REQUEST_CONTEXT_VERSION / SIMPLE_STRING
        reason.reason.simple = "CitiFoxCharger überwacht das Ladeziel. Der Bildschirm darf ausgehen."
        handle = self.api.PowerCreateRequest(ctypes.byref(reason))
        if handle in (None, ctypes.c_void_p(-1).value):
            raise OSError("Windows-Energieanforderung fehlgeschlagen.")
        return handle

    def set(self, handle, request):
        if not self.api.PowerSetRequest(handle, request):
            raise OSError("Windows-Energieanforderung fehlgeschlagen.")

    def clear(self, handle, request):
        self.api.PowerClearRequest(handle, request)

    def close(self, handle):
        self.api.CloseHandle(handle)

    def on_ac_power(self):
        status = SystemPowerStatus()
        if not self.api.GetSystemPowerStatus(ctypes.byref(status)) or status.ac == 255:
            return None
        return status.ac == 1


class PowerGuard:
    def __init__(self, backend=None):
        self.backend = backend
        self.handle, self.requests, self.warning = None, [], None
        self.lock = threading.Lock()
        self.closed = False
        if backend is None and os.name == "nt":
            try:
                self.backend = WindowsPowerRequest()
            except (OSError, AttributeError):
                self.warning = "Windows-Wachhaltefunktion nicht verfügbar. Energiesparen bitte selbst verhindern."

    def _release(self):
        if self.handle is not None:
            try:
                for request in reversed(self.requests):
                    self.backend.clear(self.handle, request)
            finally:
                self.backend.close(self.handle)
                self.handle, self.requests = None, []

    def update(self, active):
        with self.lock:
            if not active:
                self._release()
            elif not self.closed and self.backend and self.handle is None:
                try:
                    self.handle = self.backend.create()
                    for request in (SYSTEM_REQUIRED, EXECUTION_REQUIRED):
                        self.backend.set(self.handle, request)
                        self.requests.append(request)
                    self.warning = None
                except OSError:
                    self._release()
                    self.warning = "Windows konnte das Wachhalten nicht bestätigen. Energiesparen bitte selbst verhindern."

    def state(self):
        with self.lock:
            ac = self.backend.on_ac_power() if self.backend else None
            return {"supported": self.backend is not None, "requested": bool(self.requests),
                    "on_ac_power": ac, "warning": self.warning}

    def close(self):
        with self.lock:
            self.closed = True
            self._release()
