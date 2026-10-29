"""Windows clipboard worker and watchdog. No third-party packages or network."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from privacy import RULES, rejection_reason
from storage import new_entry, save_entry, recover_pending


def load_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    for key in ('output_dir', 'state_dir'):
        if not Path(config[key]).is_absolute():
            raise ValueError('Absolute output_dir and state_dir required')
    return config


def state_write(root, name, data):
    root.mkdir(parents=True, exist_ok=True)
    temp = root / (name + '.tmp')
    temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(temp, root / name)


def worker(config_path):
    from native import SingleInstance, read_snapshot, listener, user
    cfg = load_config(config_path)
    state = Path(cfg['state_dir'])
    lock_id = hashlib.sha256(str(state.resolve()).lower().encode()).hexdigest()[:24]
    guard = SingleInstance('Local\\WinCopyWorker-' + lock_id)
    if guard.duplicate:
        return
    Path(cfg['output_dir']).mkdir(parents=True, exist_ok=True)
    recover_pending(cfg['output_dir'])
    last_seq = user.GetClipboardSequenceNumber()  # Do not capture existing contents.
    pending_read = False
    pending = []
    last_digest = None
    last_accepted = 0.0
    stats = {'pid': os.getpid(), 'started_at': time.time(), 'accepted': 0,
             'skipped': 0, 'saved': 0, 'write_errors': 0, 'last_reason': 'starting'}

    def capture(hwnd):
        nonlocal last_seq, pending_read, last_digest, last_accepted
        try:
            if user.GetClipboardSequenceNumber() == last_seq:
                pending_read = False
                return
            seq, text, reason, source, title = read_snapshot(hwnd, RULES['max_bytes'])
            last_seq, pending_read = seq, False
            reason = reason or rejection_reason(text, source, title)
            if reason:
                stats['skipped'] += 1
                stats['last_reason'] = reason
                return
            digest = hashlib.sha256(text.encode('utf-8')).digest()
            if digest == last_digest and time.monotonic() - last_accepted < 30:
                stats['last_reason'] = 'recent-duplicate'
                return
            if len(pending) >= 100:
                stats['skipped'] += 1
                stats['last_reason'] = 'queue-full'
                return
            pending.append(new_entry(text, source))
            last_digest, last_accepted = digest, time.monotonic()
            stats['accepted'] += 1
            stats['last_reason'] = 'accepted'
        except BlockingIOError:
            pending_read = True
        except Exception:
            # Never log exception details: OS/errors can embed clipboard data.
            pending_read = False
            last_seq = user.GetClipboardSequenceNumber()
            stats['skipped'] += 1
            stats['last_reason'] = 'read-error'

    def tick(hwnd):
        try:
            if pending_read:
                capture(hwnd)
            while pending:
                try:
                    save_entry(cfg['output_dir'], pending[0])
                    pending.pop(0)
                    stats['saved'] += 1
                    stats['last_reason'] = 'saved'
                except OSError:
                    stats['write_errors'] += 1
                    stats['last_reason'] = 'write-retry'
                    break
            stats['pending'] = len(pending)
            stats['heartbeat'] = time.time()
            state_write(state, 'worker.json', stats)
        except Exception:
            # Watchdog detects a missing heartbeat and recovers a stuck worker.
            pass

    listener(capture, tick)


def supervise(config_path):
    from native import SingleInstance, ChildJob
    cfg = load_config(config_path)
    state = Path(cfg['state_dir'])
    lock_id = hashlib.sha256(str(state.resolve()).lower().encode()).hexdigest()[:24]
    guard = SingleInstance('Local\\WinCopySupervisor-' + lock_id)
    if guard.duplicate:
        return
    job = ChildJob()
    child = None
    restarts = 0
    try:
        while not (state / 'stop.flag').exists():
            if (state / 'paused.flag').exists():
                if child and child.poll() is None:
                    child.terminate()
                    child.wait(timeout=10)
                child = None
                state_write(state, 'supervisor.json', {'pid': os.getpid(), 'worker_pid': None,
                            'heartbeat': time.time(), 'restarts': restarts, 'paused': True})
                time.sleep(2)
                continue
            if child is None or child.poll() is not None:
                # The worker mutex also prevents duplicates after a supervisor crash.
                child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                    'worker', '--config', str(Path(config_path).resolve())],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                job.add(child)
                restarts += 1
                launched = time.time()
            try:
                record = json.loads((state / 'worker.json').read_text(encoding='utf-8'))
                alive = time.time() - record.get('heartbeat', 0) < 45
                if not alive and time.time() - launched > 45 and child.poll() is None:
                    child.kill()
                    child.wait(timeout=10)
            except (OSError, ValueError):
                if time.time() - launched > 45 and child.poll() is None:
                    child.kill()
                    child.wait(timeout=10)
            state_write(state, 'supervisor.json', {'pid': os.getpid(), 'worker_pid': child.pid,
                        'heartbeat': time.time(), 'restarts': restarts, 'paused': False})
            time.sleep(3)
    finally:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['worker', 'supervise', 'status', 'pause', 'resume', 'stop'])
    parser.add_argument('--config', default=str(Path.home() / 'Documents/WinCopy/config.json'))
    args = parser.parse_args()
    cfg = load_config(args.config)
    state = Path(cfg['state_dir'])
    state.mkdir(parents=True, exist_ok=True)
    if args.command == 'worker':
        worker(args.config)
    elif args.command == 'supervise':
        supervise(args.config)
    elif args.command == 'status':
        result = {'output_dir': cfg['output_dir'], 'paused': (state / 'paused.flag').exists(),
                  'stopped': (state / 'stop.flag').exists()}
        for name in ['worker', 'supervisor']:
            try:
                result[name] = json.loads((state / (name + '.json')).read_text(encoding='utf-8'))
                result[name]['healthy'] = time.time() - result[name].get('heartbeat', 0) < 15
            except (OSError, ValueError):
                result[name] = {'healthy': False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == 'pause':
        (state / 'paused.flag').touch()
    elif args.command == 'resume':
        (state / 'paused.flag').unlink(missing_ok=True)
    elif args.command == 'stop':
        (state / 'stop.flag').touch()


if __name__ == '__main__':
    main()
