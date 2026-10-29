"""Opt-in live Windows test. Uses only synthetic fixtures; saves outside the repo.
Stops the test watcher before restoring the prior clipboard IDataObject.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'windows'))
from native import user, kernel, signature


def wait_until(fn, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.2)
    raise AssertionError('Condition timed out')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work-dir', required=True)
    args = parser.parse_args()
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=False)
    state, captures = work / 'state', work / 'captures'
    state.mkdir()
    captures.mkdir()
    config = work / 'config.json'
    config.write_text(json.dumps({'output_dir': str(captures), 'state_dir': str(state)}), encoding='utf-8')
    ole = C.OleDLL('ole32')
    ole.OleInitialize(None)
    ole.OleGetClipboard.argtypes = [C.POINTER(C.c_void_p)]
    ole.OleSetClipboard.argtypes = [C.c_void_p]
    original = C.c_void_p()
    ole.OleGetClipboard(C.byref(original))
    signature(user, 'EmptyClipboard', W.BOOL)
    signature(user, 'SetClipboardData', W.HANDLE, W.UINT, W.HANDLE)
    signature(kernel, 'GlobalAlloc', W.HANDLE, W.UINT, C.c_size_t)
    hwnd = user.CreateWindowExW(0, 'STATIC', 'WinCopy fixture', 0, 0, 0, 0, 0, W.HWND(-3), None, None, None)
    if not hwnd:
        raise C.WinError(C.get_last_error())
    last_seq = None
    supervisor = None
    evidence = {}

    def publish(text, format_name=None, value=b'\x00\x00\x00\x00'):
        nonlocal last_seq
        wait_until(lambda: user.OpenClipboard(hwnd))
        try:
            user.EmptyClipboard()
            formats = [(13, (text + '\0').encode('utf-16-le'))]
            if format_name:
                formats.append((user.RegisterClipboardFormatW(format_name), value))
            for fmt, raw in formats:
                handle = kernel.GlobalAlloc(0x42, len(raw))
                pointer = kernel.GlobalLock(handle)
                C.memmove(pointer, raw, len(raw))
                kernel.GlobalUnlock(handle)
                if not user.SetClipboardData(fmt, handle):
                    raise C.WinError(C.get_last_error())
        finally:
            user.CloseClipboard()
        last_seq = user.GetClipboardSequenceNumber()

    def record(name='worker'):
        try:
            return json.loads((state / (name + '.json')).read_text(encoding='utf-8'))
        except (ValueError, OSError):
            return {}

    try:
        supervisor = subprocess.Popen([sys.executable, str(ROOT / 'windows/win_copy.py'),
            'supervise', '--config', str(config)], creationflags=subprocess.CREATE_NO_WINDOW)
        first = wait_until(lambda: record().get('pid'))
        first_text = '这是一条公开的剪贴板功能测试记录，用于验证中文与表情符号可以完整保存。😀'
        publish(first_text)
        wait_until(lambda: record().get('saved') == 1)
        first_file = next(captures.glob('*.md'))
        assert first_text in first_file.read_text(encoding='utf-8')
        evidence['utf8_clipboard_to_markdown'] = True
        publish(first_text)
        time.sleep(1)
        assert len(list(captures.glob('*.md'))) == 1
        evidence['duplicate_suppressed'] = True
        blocked = [
            ('synthetic_reader', None),
            ('Synthetic!Password42', None),
            ('这里是一段足够长的文字，用户名：synthetic_user。这整段都不应该存到文件。', None),
            ('这是一段本来可以保存的普通文字，用于测试受保护剪贴板格式会不会被正确忽略。', 'ExcludeClipboardContentFromMonitorProcessing'),
            ('这是一段本来可以保存的普通文字，用于测试历史记录禁用标志能否阻止内容被保存。', 'CanIncludeInClipboardHistory'),
            ('这是一段本来可以保存的普通文字，用于测试云端同步禁用标志能否阻止内容被保存。', 'CanUploadToCloudClipboard')]
        for text, fmt in blocked:
            before = record().get('skipped', 0)
            publish(text, fmt)
            wait_until(lambda: record().get('skipped', 0) > before)
        assert len(list(captures.glob('*.md'))) == 1
        # Status files must not leak rejected contents either.
        metadata = ''.join(p.read_text(encoding='utf-8') for p in state.glob('*.json'))
        assert all(text not in metadata for text, _ in blocked)
        evidence['six_sensitive_or_protected_samples_excluded'] = True
        subprocess.run(['taskkill', '/PID', str(first), '/F'], capture_output=True, check=True)
        second = wait_until(lambda: record().get('pid') if record().get('pid') != first else None)
        publish('这是采集进程重启之后的另一条公开测试记录，用于确认恢复后依然能够正常保存文字。')
        wait_until(lambda: len(list(captures.glob('*.md'))) == 2)
        evidence['worker_restart'] = {'old_pid': first, 'new_pid': second, 'capture_after_restart': True}
        (state / 'paused.flag').touch()
        wait_until(lambda: record('supervisor').get('paused'))
        publish('这是一段暂停采集期间的公开测试文字，恢复后也不应把已有剪贴板内容补录进去。')
        time.sleep(1)
        assert len(list(captures.glob('*.md'))) == 2
        (state / 'paused.flag').unlink()
        wait_until(lambda: record().get('pid') not in (None, first, second))
        time.sleep(1)
        assert len(list(captures.glob('*.md'))) == 2
        evidence['pause_and_skip_existing_on_resume'] = True
    finally:
        (state / 'stop.flag').touch()
        if supervisor:
            try:
                supervisor.wait(timeout=10)
            except subprocess.TimeoutExpired:
                supervisor.kill()
                supervisor.wait()
        # Do not replace a new clipboard item the user copied during the test.
        if last_seq is not None and user.GetClipboardSequenceNumber() == last_seq and original:
            evidence['prior_clipboard_restore_requested'] = False
            for attempt in range(5):
                try:
                    ole.OleSetClipboard(original)
                    ole.OleFlushClipboard()
                    evidence['prior_clipboard_restore_requested'] = True
                    break
                except OSError:
                    time.sleep(.2)
        ole.OleUninitialize()
    (work / 'result.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
