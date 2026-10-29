"""No model calls. Deliberately over-filter; never persist rejected text."""
import json
import re
import unicodedata
from pathlib import Path

RULES = json.loads((Path(__file__).resolve().parents[1] / 'privacy-rules.json').read_text(encoding='utf-8'))
PATTERNS = [re.compile(p, re.I) for p in RULES['patterns']]


def rejection_reason(text, source='', title='', concealed=False, require_source=True):
    text = re.sub(r'[\u200b-\u200f\u202a-\u202e\u2060\ufeff]', '', unicodedata.normalize('NFKC', text))
    if concealed:
        return 'protected-format'
    if require_source and not source:
        return 'unknown-source'
    if any(s in source.lower() for s in RULES['blocked_sources']):
        return 'blocked-source'
    if any(s in title.lower() for s in RULES['blocked_titles']):
        return 'sensitive-window'
    if not text.strip():
        return 'empty'
    if len(text.encode('utf-8')) > RULES['max_bytes']:
        return 'oversized'
    if any(p.search(text) for p in PATTERNS):
        return 'credential-pattern'
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    if any(re.fullmatch(r'[\w.@+\-/\\=!:;#$%^&*()]+', s, flags=re.ASCII) or re.fullmatch(r'[\u3400-\u9fff]{1,8}', s) for s in lines):
        return 'identifier-like'
    if len(text.strip()) < RULES['min_characters']:
        return 'short-text'
    if not re.search(r'[。！？.!?]', text):
        return 'not-prose'
    if not re.search(r'[\u3400-\u9fff]', text) and len(text.split()) < 5:
        return 'not-prose'
    return None
