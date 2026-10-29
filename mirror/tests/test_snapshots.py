"""Offline checks that run snapshots contain every evaluation dependency."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
import runner
from storage import readJSON, writeJSON


class SnapshotTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        source = ROOT / 'result/2026-10-28_16-50-18.540_KST'
        if not (source / 'config.json').is_file():
            self.skipTest('Saved result not included in this checkout.')
        with tempfile.TemporaryDirectory(prefix='mirror-snapshot-test-') as temp:
            folder = Path(temp)
            for name in ('dataset-snapshot.json', 'prompt-snapshot.txt',
                         'pricing-snapshot.json', 'source-dataset.json',
                         'solution-witnesses-snapshot.json',
                         'exclusivity-proof-snapshot.json',
                         'dataset-manifest-snapshot.json'):
                shutil.copyfile(source / name, folder / name)
            (folder / 'code-snapshot').mkdir()
            config = readJSON(source / 'config.json')
            config['code_hashes'] = {}
            for name in runner.runtimeFiles:
                code = ROOT / 'code' / name
                shutil.copyfile(code, folder / 'code-snapshot' / name)
                config['code_hashes'][name] = runner.hash(code.read_bytes())
            writeJSON(folder / 'config.json', config)
            yield folder

    def test_complete_snapshot_loads(self):
        with self.fixture() as folder:
            loaded = runner.loadRun(folder)
            self.assertEqual(set(loaded['config']['code_hashes']), set(runner.runtimeFiles))
            self.assertEqual(len(loaded['dataset']['problems']), 100)

    def test_missing_hash_entry_is_rejected(self):
        with self.fixture() as folder:
            config = readJSON(folder / 'config.json')
            del config['code_hashes']['serialization.py']
            writeJSON(folder / 'config.json', config)
            with self.assertRaisesRegex(ValueError, '스냅샷 목록'):
                runner.loadRun(folder)

    def test_changed_snapshot_is_rejected(self):
        with self.fixture() as folder:
            (folder / 'code-snapshot/optics.py').write_text('# changed\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '저장된 코드 스냅샷이 변경'):
                runner.loadRun(folder)

    def test_changed_current_code_is_rejected(self):
        with self.fixture() as folder:
            current = folder / 'current-code'
            shutil.copytree(folder / 'code-snapshot', current)
            (current / 'optics.py').write_text('# changed\n', encoding='utf-8')
            with patch.object(runner, 'CODE', current):
                with self.assertRaisesRegex(ValueError, '실행 코드가 변경'):
                    runner.loadRun(folder)


if __name__ == '__main__':
    unittest.main()
