import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'windows'))
from privacy import rejection_reason
from storage import new_entry, save_entry, recover_pending


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

    def test_recover_completed_write_after_rename_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = new_entry('这是一段普通测试素材，用来验证文件写好后重命名失败的恢复。', 'reader.exe')
            with patch.object(Path, 'rename', side_effect=PermissionError('synthetic')):
                with self.assertRaises(PermissionError):
                    save_entry(tmp, entry)
            self.assertEqual(len(list(Path(tmp).glob('*.pending'))), 1)
            recover_pending(tmp)
            self.assertEqual(len(list(Path(tmp).glob('*.md'))), 1)
            self.assertEqual(len(list(Path(tmp).glob('*.pending'))), 0)

    def test_incomplete_file_is_not_claimed_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / ('2026-01-01_120000_' + 'a' * 32 + '.pending')
            pending.write_text('incomplete', encoding='utf-8')
            recover_pending(tmp)
            self.assertTrue(pending.exists())
            self.assertFalse(list(Path(tmp).glob('*.md')))


if __name__ == '__main__':
    unittest.main()
