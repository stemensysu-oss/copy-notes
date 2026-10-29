import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'windows'))
from privacy import rejection_reason
from storage import new_entry, save_entry, recover_pending, migrate_legacy


class PrivacyTests(unittest.TestCase):
    def test_shared_cases(self):
        cases = json.loads((ROOT / 'tests/privacy-cases.json').read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(case=case['name']):
                ctx = {'source': 'reader.exe', **case.get('context', {})}
                self.assertEqual(rejection_reason(case['text'], **ctx) is None, case['allowed'])

    def test_real_byte_limit(self):
        self.assertEqual(rejection_reason('普通文本。' * 20000, source='reader.exe'), 'oversized')


class StorageTests(unittest.TestCase):
    def test_utf8_and_retry_preserve_one_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = '中文与 emoji 测试。😀\n```\n原始内容必须保留。'
            entry = new_entry(text, 'reader.exe')
            path = save_entry(tmp, entry)
            before = path.read_bytes()
            self.assertEqual(save_entry(tmp, entry), path)
            self.assertEqual(path.read_bytes(), before)
            self.assertIn(text, before.decode('utf-8'))
            self.assertEqual(len(list(Path(tmp).glob('*.md'))), 1)

    def test_same_day_appends_and_midnight_rotates(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = new_entry('第一条内容。', 'reader.exe')
            first['time'] = '2026-10-29T09:00:00+08:00'
            path = save_entry(tmp, first)
            prefix = path.read_bytes()
            second = new_entry('第二条内容。', 'reader.exe')
            second['time'] = '2026-10-29T23:59:59+08:00'
            self.assertEqual(save_entry(tmp, second), path)
            self.assertTrue(path.read_bytes().startswith(prefix))
            self.assertEqual(path.name, '2026-10-29.md')
            self.assertEqual(path.read_text(encoding='utf-8').count('<!-- capture-id:'), 2)
            next_day = new_entry('跨日内容。', 'reader.exe')
            next_day['time'] = '2026-10-30T00:00:01+08:00'
            self.assertEqual(save_entry(tmp, next_day).name, '2026-10-30.md')
            self.assertEqual(len(list(Path(tmp).glob('*.md'))), 2)

    def test_recover_interrupted_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = new_entry('这是中文与表情的中断写入测试。😀', 'reader.exe')
            def partial(dest, data):
                dest.write_bytes(data[:5])
                raise PermissionError('synthetic')
            with patch('storage._append_bytes', side_effect=partial):
                with self.assertRaises(PermissionError):
                    save_entry(tmp, entry)
            recover_pending(tmp)
            path = save_entry(tmp, entry)
            content = path.read_text(encoding='utf-8')
            self.assertIn(entry['text'], content)
            self.assertEqual(content.count('<!-- capture-id:'), 1)
            self.assertFalse((Path(tmp) / '.win-copy-state/transaction.json').exists())

    def test_recover_after_successful_append_before_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = new_entry('追加成功但清理事务时崩溃，不应重复写入。', 'reader.exe')
            with patch.object(Path, 'unlink', side_effect=PermissionError('synthetic')):
                with self.assertRaises(PermissionError):
                    save_entry(tmp, entry)
            recover_pending(tmp)
            path = save_entry(tmp, entry)
            self.assertEqual(path.read_text(encoding='utf-8').count('<!-- capture-id:'), 1)

    def test_conflicting_edit_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = new_entry('中断后的人工修改不能被自动覆盖。', 'reader.exe')
            with patch('storage._append_bytes', side_effect=PermissionError('synthetic')):
                with self.assertRaises(PermissionError):
                    save_entry(tmp, entry)
            path = Path(tmp) / (entry['time'][:10] + '.md')
            path.write_text('人工修改', encoding='utf-8')
            with self.assertRaises(OSError):
                recover_pending(tmp)
            self.assertEqual(path.read_text(encoding='utf-8'), '人工修改')

    def test_migration_preserves_old_bytes_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = ('---\ncapture_id: ' + 'a'*32 + '\ncaptured_at: "2026-10-29T09:00:00+08:00"\n'
                        + 'source_app: "reader.exe"\ntype: clipboard-text\n---\n\n# old\n\n'
                        + '```text\n旧版本的原始文字。😀\n```\n')
            old = Path(tmp) / ('2026-10-29_090000_' + 'a'*32 + '.md')
            old.write_text(original, encoding='utf-8', newline='\n')
            before = old.read_bytes()
            self.assertEqual(migrate_legacy(tmp), {'migrated': 1, 'skipped': 0})
            self.assertEqual((Path(tmp) / '.win-copy-legacy' / (old.name + '.bak')).read_bytes(), before)
            self.assertFalse(old.exists())
            self.assertIn('旧版本的原始文字。😀', (Path(tmp)/'2026-10-29.md').read_text(encoding='utf-8'))
            self.assertEqual(migrate_legacy(tmp), {'migrated': 0, 'skipped': 0})


if __name__ == '__main__':
    unittest.main()
