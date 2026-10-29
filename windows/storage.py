"""One Markdown journal per local date, with recoverable, idempotent appends."""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid


def new_entry(text, source):
    return {'id': uuid.uuid4().hex, 'time': datetime.now().astimezone().isoformat(),
            'text': text, 'source': source}


def _metadata(root):
    return root / '.win-copy-state'


@contextmanager
def _locked(root):
    root.mkdir(parents=True, exist_ok=True)
    state = _metadata(root)
    state.mkdir(exist_ok=True)
    with (state / 'writer.lock').open('a+b') as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        deadline = time.monotonic() + 10
        while True:
            try:
                lock.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _block(entry):
    if not re.fullmatch(r'[a-f0-9]{32}', entry['id']):
        raise ValueError('Invalid capture ID')
    stamp = datetime.fromisoformat(entry['time'])
    fence = '`' * max(3, max((len(m[0]) + 1 for m in re.finditer(r'`+', entry['text'])), default=3))
    return ('\n\n<!-- capture-id: ' + entry['id'] + ' -->\n'
            + '## ' + stamp.strftime('%H:%M:%S') + '\n\n'
            + '采集时间：' + entry['time'] + '  \n'
            + '来源应用：' + json.dumps(entry['source'], ensure_ascii=False) + '\n\n'
            + fence + 'text\n' + entry['text'] + '\n' + fence + '\n')


def _append_bytes(dest, data):
    with dest.open('ab') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _finish_transaction(root):
    journal = _metadata(root) / 'transaction.json'
    if not journal.exists():
        return
    txn = json.loads(journal.read_text(encoding='utf-8'))
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.md', txn['file']):
        raise OSError('Invalid journal destination')
    dest = root / txn['file']
    before = dest.read_bytes() if dest.exists() else b''
    offset = txn['offset']
    if offset < 0 or len(before) < offset or hashlib.sha256(before[:offset]).hexdigest() != txn['prefix_sha256']:
        raise OSError('Daily file changed; transaction preserved for review')
    payload = txn['payload'].encode('utf-8')
    tail = before[offset:]
    if tail.startswith(payload):
        pass  # Append succeeded before an interrupted cleanup.
    elif payload.startswith(tail):
        _append_bytes(dest, payload[len(tail):])  # Also repairs partial UTF-8.
    else:
        raise OSError('Daily file changed; transaction preserved for review')
    journal.unlink()


def save_entry(output_dir, entry):
    root = Path(output_dir)
    stamp = datetime.fromisoformat(entry['time'])
    dest = root / (stamp.strftime('%Y-%m-%d') + '.md')
    block = _block(entry)
    with _locked(root):
        _finish_transaction(root)
        before = dest.read_bytes() if dest.exists() else b''
        marker = ('\n<!-- capture-id: ' + entry['id'] + ' -->\n').encode('ascii')
        if marker in before:
            return dest
        payload = (('# ' + stamp.strftime('%Y-%m-%d') + ' 复制记录\n') if not before else '') + block
        txn = {'file': dest.name, 'offset': len(before),
               'prefix_sha256': hashlib.sha256(before).hexdigest(), 'payload': payload}
        journal = _metadata(root) / 'transaction.json'
        temp = journal.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8', newline='\n') as stream:
            json.dump(txn, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, journal)
        _finish_transaction(root)
    return dest


def recover_pending(output_dir):
    with _locked(Path(output_dir)):
        _finish_transaction(Path(output_dir))


def read_legacy(path):
    """Read only the old per-capture format, without logging its content."""
    data = path.read_text(encoding='utf-8')
    header = re.match(r'\A---\n(.*?)\n---\n', data, re.S)
    if not header:
        raise ValueError('Not a legacy capture')
    fields = dict(line.split(': ', 1) for line in header[1].splitlines() if ': ' in line)
    body = re.search(r'^(`{3,})text\n', data[header.end():], re.M)
    if not body:
        raise ValueError('Missing literal text block')
    start = header.end() + body.end()
    suffix = '\n' + body[1] + '\n'
    if not data.endswith(suffix):
        raise ValueError('Incomplete legacy capture')
    entry = {'id': fields['capture_id'], 'time': json.loads(fields['captured_at']),
             'source': json.loads(fields['source_app']), 'text': data[start:-len(suffix)]}
    if entry['id'] not in path.stem:
        raise ValueError('Legacy ID mismatch')
    _block(entry)
    return entry


def migrate_legacy(output_dir):
    """Merge old captures, retaining byte-identical .bak copies. Pause first."""
    root = Path(output_dir).resolve()
    backup = root / '.win-copy-legacy'
    migrated, skipped = 0, 0
    for source in sorted(root.glob('????-??-??_??????_*.md')):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}_\d{6}_[a-f0-9]{32}\.md', source.name):
            continue
        if not source.resolve().is_relative_to(root):
            raise OSError('Source leaves output directory')
        try:
            entry = read_legacy(source)
        except (ValueError, KeyError):
            skipped += 1
            continue
        target = backup / (source.name + '.bak')
        if not target.resolve().is_relative_to(root):
            raise OSError('Backup leaves output directory')
        if target.exists():
            skipped += 1
            continue
        save_entry(root, entry)
        backup.mkdir(exist_ok=True)
        source.rename(target)
        migrated += 1
    return {'migrated': migrated, 'skipped': skipped}
