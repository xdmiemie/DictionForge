import ctypes
import json
import os
import threading
import time
import pytest
from dictionforge.audio import atomic_json

@pytest.mark.skipif(os.name != 'nt', reason='Windows handle sharing semantics')
def test_atomic_json_retries_real_windows_reader_handle(tmp_path):
    path = tmp_path/'state.json'
    atomic_json(path, {'value':'old'})
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    kernel.CreateFileW.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.CreateFileW(str(path),0x80000000,1,None,3,0,None)
    assert handle != ctypes.c_void_p(-1).value
    def release():
        time.sleep(.15)
        kernel.CloseHandle(handle)
    thread=threading.Thread(target=release)
    thread.start()
    atomic_json(path, {'value':'new'})
    thread.join()
    assert json.loads(path.read_text())=={'value':'new'}
    assert not list(tmp_path.glob('*.tmp'))
