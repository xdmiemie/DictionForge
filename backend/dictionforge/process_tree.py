"""Windows kernel job owns descendants even when their launcher exits first."""
import ctypes
from ctypes import wintypes
import time

class WindowsProcessTree:
    def __init__(self, process):
        from ctypes import c_size_t, c_ulonglong, c_longlong
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", c_longlong), ("PerJobUserTimeLimit", c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", c_size_t),
                        ("MaximumWorkingSetSize", c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                       "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO), ("ProcessMemoryLimit", c_size_t),
                        ("JobMemoryLimit", c_size_t), ("PeakProcessMemoryUsed", c_size_t), ("PeakJobMemoryUsed", c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise

    def close(self):
        if not self.handle:
            return
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [(name, wintypes.DWORD) for name in ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]
        try:
            self.kernel.TerminateJobObject(self.handle, 1)
            deadline = time.monotonic() + 15
            while True:
                info = Accounting()
                if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if info.ActiveProcesses == 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("进程树未退出，拒绝释放 GPU 租约")
                time.sleep(.05)
        finally:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
