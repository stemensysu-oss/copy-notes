"""One immutable Markdown file per accepted capture; no raw JSON backup."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import uuid


def new_entry(text, source):
    return {'id': uuid.uuid4().hex, 'time': datetime.now().astimezone().isoformat(),
            'text': text, 'source': source}


def save_entry(output_dir, entry):
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(entry['time'])
    dest = root / (stamp.strftime('%Y-%m-%d_%H%M%S_') + entry['id'] + '.md')
    if dest.exists():
        return dest  # Same entry ID on retry: already committed.
    # Treat copied Markdown/HTML as source text, not executable markup.
    fence = '`' * max(3, max((len(m[0]) + 1 for m in re.finditer(r'`+', entry['text'])), default=3))
    document = ('---\n' + 'capture_id: ' + entry['id'] + '\n'
                + 'captured_at: ' + json.dumps(entry['time']) + '\n'
                + 'source_app: ' + json.dumps(entry['source'], ensure_ascii=False) + '\n'
                + 'type: clipboard-text\n---\n\n'
                + '# ' + stamp.strftime('%Y-%m-%d %H:%M:%S') + '\n\n'
                + fence + 'text\n' + entry['text'] + '\n' + fence + '\n')
    temp = dest.with_suffix('.pending')
    with temp.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(document)
        stream.flush()
        os.fsync(stream.fileno())
    # Windows rename is atomic and refuses to replace an existing destination.
    try:
        temp.rename(dest)
    except FileExistsError:
        temp.unlink(missing_ok=True)
    return dest


def recover_pending(output_dir):
    # Only our ID-shaped files, never arbitrary user files. Complete the rename
    # only when the final fence exists; incomplete files stay explicitly pending.
    for temp in Path(output_dir).glob('????-??-??_??????_*.pending'):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}_\d{6}_[a-f0-9]{32}', temp.stem):
            continue
        data = temp.read_text(encoding='utf-8')
        fence = re.search(r'^(`{3,})text$', data, re.M)
        if fence and data.endswith('\n' + fence[1] + '\n'):
            dest = temp.with_suffix('.md')
            if not dest.exists():
                temp.rename(dest)
