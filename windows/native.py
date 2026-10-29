"""Small Win32 clipboard listener. Windows only, Python standard library only."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path

user = C.WinDLL('user32', use_last_error=True)
kernel = C.WinDLL('kernel32', use_last_error=True)
LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class WNDCLASS(C.Structure):
    _fields_ = [('style', W.UINT), ('proc', WNDPROC), ('clsExtra', C.c_int),
                ('winExtra', C.c_int), ('instance', W.HINSTANCE), ('icon', W.HICON),
                ('cursor', W.HANDLE), ('background', W.HBRUSH),
                ('menuName', W.LPCWSTR), ('className', W.LPCWSTR)]


def signature(dll, name, restype, *args):
    f = getattr(dll, name)
    f.restype, f.argtypes = restype, list(args)
    return f


signature(user, 'OpenClipboard', W.BOOL, W.HWND)
signature(user, 'CloseClipboard', W.BOOL)
signature(user, 'GetClipboardData', W.HANDLE, W.UINT)
signature(user, 'IsClipboardFormatAvailable', W.BOOL, W.UINT)
signature(user, 'RegisterClipboardFormatW', W.UINT, W.LPCWSTR)
signature(user, 'GetClipboardSequenceNumber', W.DWORD)
signature(user, 'GetClipboardOwner', W.HWND)
signature(user, 'GetForegroundWindow', W.HWND)
signature(user, 'GetWindowThreadProcessId', W.DWORD, W.HWND, C.POINTER(W.DWORD))
signature(user, 'GetWindowTextW', C.c_int, W.HWND, W.LPWSTR, C.c_int)
signature(user, 'RegisterClassW', W.ATOM, C.POINTER(WNDCLASS))
signature(user, 'CreateWindowExW', W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
          C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID)
signature(user, 'DefWindowProcW', LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
signature(user, 'AddClipboardFormatListener', W.BOOL, W.HWND)
signature(user, 'RemoveClipboardFormatListener', W.BOOL, W.HWND)
signature(user, 'SetTimer', C.c_size_t, W.HWND, C.c_size_t, W.UINT, W.LPVOID)
signature(user, 'KillTimer', W.BOOL, W.HWND, C.c_size_t)
signature(user, 'GetMessageW', W.BOOL, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
signature(user, 'TranslateMessage', W.BOOL, C.POINTER(W.MSG))
signature(user, 'DispatchMessageW', LRESULT, C.POINTER(W.MSG))
signature(kernel, 'GetModuleHandleW', W.HMODULE, W.LPCWSTR)
signature(kernel, 'OpenProcess', W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
signature(kernel, 'QueryFullProcessImageNameW', W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD))
signature(kernel, 'CloseHandle', W.BOOL, W.HANDLE)
signature(kernel, 'GlobalLock', W.LPVOID, W.HANDLE)
signature(kernel, 'GlobalUnlock', W.BOOL, W.HANDLE)
signature(kernel, 'GlobalSize', C.c_size_t, W.HANDLE)
signature(kernel, 'CreateMutexW', W.HANDLE, W.LPVOID, W.BOOL, W.LPCWSTR)
signature(kernel, 'CreateJobObjectW', W.HANDLE, W.LPVOID, W.LPCWSTR)
signature(kernel, 'SetInformationJobObject', W.BOOL, W.HANDLE, C.c_int, W.LPVOID, W.DWORD)
signature(kernel, 'AssignProcessToJobObject', W.BOOL, W.HANDLE, W.HANDLE)


class JOB_BASIC(C.Structure):
    _fields_ = [('processTime', C.c_int64), ('jobTime', C.c_int64), ('flags', W.DWORD),
                ('minWorking', C.c_size_t), ('maxWorking', C.c_size_t), ('activeLimit', W.DWORD),
                ('affinity', C.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]


class JOB_LIMITS(C.Structure):
    _fields_ = [('basic', JOB_BASIC), ('ioCounters', C.c_uint64 * 6),
                ('processMemory', C.c_size_t), ('jobMemory', C.c_size_t),
                ('peakProcessMemory', C.c_size_t), ('peakJobMemory', C.c_size_t)]


class ChildJob:
    def __init__(self):
        self.handle = kernel.CreateJobObjectW(None, None)
        limits = JOB_LIMITS()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; supervisor death kills child.
        if not self.handle or not kernel.SetInformationJobObject(self.handle, 9, C.byref(limits), C.sizeof(limits)):
            raise C.WinError(C.get_last_error())

    def add(self, child):
        if not kernel.AssignProcessToJobObject(self.handle, W.HANDLE(int(child._handle))):
            child.terminate()
            raise C.WinError(C.get_last_error())


class SingleInstance:
    def __init__(self, name):
        self.handle = kernel.CreateMutexW(None, False, name)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        self.duplicate = C.get_last_error() == 183


def window_info(hwnd):
    if not hwnd:
        return '', ''
    pid = W.DWORD()
    user.GetWindowThreadProcessId(hwnd, C.byref(pid))
    title = C.create_unicode_buffer(2048)
    user.GetWindowTextW(hwnd, title, len(title))
    proc = kernel.OpenProcess(0x1000, False, pid.value)
    if not proc:
        return '', title.value
    try:
        size = W.DWORD(32768)
        path = C.create_unicode_buffer(size.value)
        if kernel.QueryFullProcessImageNameW(proc, 0, path, C.byref(size)):
            return Path(path.value).name.lower(), title.value
        return '', title.value
    finally:
        kernel.CloseHandle(proc)


def bytes_for_format(fmt, limit):
    handle = user.GetClipboardData(fmt)
    if not handle:
        return None
    size = kernel.GlobalSize(handle)
    if not size or size > limit:
        return None
    ptr = kernel.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return C.string_at(ptr, size)
    finally:
        kernel.GlobalUnlock(handle)


EXCLUDE_NAMES = ['ExcludeClipboardContentFromMonitorProcessing', 'Clipboard Viewer Ignore',
                 'org.nspasteboard.ConcealedType', 'org.nspasteboard.TransientType']
EXCLUDES = [user.RegisterClipboardFormatW(s) for s in EXCLUDE_NAMES]
FLAGS = [user.RegisterClipboardFormatW(s) for s in
         ['CanIncludeInClipboardHistory', 'CanUploadToCloudClipboard']]


def read_snapshot(hwnd, max_bytes):
    if not user.OpenClipboard(hwnd):
        raise BlockingIOError('clipboard-busy')
    try:
        seq = user.GetClipboardSequenceNumber()
        # Check protection BEFORE reading CF_UNICODETEXT.
        for fmt in EXCLUDES:
            if user.IsClipboardFormatAvailable(fmt):
                return seq, None, 'protected-format', '', ''
        for fmt in FLAGS:
            if user.IsClipboardFormatAvailable(fmt):
                raw = bytes_for_format(fmt, 64)
                if raw is None or len(raw) < 4 or int.from_bytes(raw[:4], 'little') == 0:
                    return seq, None, 'protected-format', '', ''
        if not user.IsClipboardFormatAvailable(13):
            return seq, None, 'not-text', '', ''
        owner, owner_title = window_info(user.GetClipboardOwner())
        foreground, foreground_title = window_info(user.GetForegroundWindow())
        # Owner is evidence of who supplied the clipboard; foreground is only an
        # additional privacy veto and is never persisted as claimed provenance.
        from privacy import RULES
        if not owner or not foreground:
            return seq, None, 'unknown-source', '', ''
        if any(s in (owner + ' ' + foreground) for s in RULES['blocked_sources']):
            return seq, None, 'blocked-source', '', ''
        if any(s in (owner_title + ' ' + foreground_title).lower() for s in RULES['blocked_titles']):
            return seq, None, 'sensitive-window', '', ''
        raw = bytes_for_format(13, max_bytes * 2 + 2)
        if raw is None:
            return seq, None, 'oversized-or-unreadable', '', ''
        text = raw.decode('utf-16-le', errors='strict').split('\0', 1)[0]
        return seq, text, None, owner, owner_title
    finally:
        user.CloseClipboard()


def listener(callback, tick):
    @WNDPROC
    def dispatch(hwnd, message, wp, lp):
        if message == 0x031D:  # WM_CLIPBOARDUPDATE
            callback(hwnd)
            return 0
        if message == 0x0113:
            tick(hwnd)
            return 0
        return user.DefWindowProcW(hwnd, message, wp, lp)
    instance = kernel.GetModuleHandleW(None)
    wc = WNDCLASS()
    wc.proc, wc.instance, wc.className = dispatch, instance, 'WinCopyListener'
    if not user.RegisterClassW(C.byref(wc)):
        raise C.WinError(C.get_last_error())
    hwnd = user.CreateWindowExW(0, wc.className, 'WinCopy', 0, 0, 0, 0, 0,
                               W.HWND(-3), None, instance, None)
    if not hwnd or not user.AddClipboardFormatListener(hwnd):
        raise C.WinError(C.get_last_error())
    user.SetTimer(hwnd, 1, 500, None)
    tick(hwnd)
    msg = W.MSG()
    while True:
        result = user.GetMessageW(C.byref(msg), None, 0, 0)
        if result == -1:
            raise C.WinError(C.get_last_error())
        if result == 0:
            break
        user.TranslateMessage(C.byref(msg))
        user.DispatchMessageW(C.byref(msg))
