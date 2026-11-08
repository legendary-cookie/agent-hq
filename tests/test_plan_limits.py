import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hq.remote import plan_limits, scanner


class PlanLimitTests(unittest.TestCase):
    def test_window_names(self):
        self.assertEqual(plan_limits.window_of('five_hour'), ('5h', '*'))
        self.assertEqual(plan_limits.window_of('seven_day'), ('week', '*'))
        self.assertEqual(plan_limits.window_of('seven_day_opus'), ('week', 'opus'))
        self.assertIsNone(plan_limits.window_of('monthly_spend'))

    def test_answer_becomes_measured_limits(self):
        batch = plan_limits.to_batch({'found': True, 'captured_at': 100.0, 'windows': {'five_hour': [23.5, 2000], 'seven_day': [41.2, 9000], 'odd': [1, 2]}})
        self.assertEqual(sorted((l.window, l.used_percent, l.resets_at, l.recorded_at, l.source) for l in batch.limits),
                         [('5h', 23.5, 2000, 100.0, 'measured'), ('week', 41.2, 9000, 100.0, 'measured')])

    def test_bad_answers_are_ignored(self):
        for answer in (None, {'found': False}, {'found': True}, {'found': True, 'captured_at': 1, 'windows': {'five_hour': [150, 1]}}):
            self.assertEqual(plan_limits.to_batch(answer).limits, [])

    def test_scanner_reads_only_numbers_from_the_probe_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / '.claude').mkdir()
            (home / '.claude' / 'hq-limits.json').write_text(json.dumps({'captured_at': 5, 'rate_limits': {
                'five_hour': {'used_percentage': 10, 'resets_at': 99, 'extra': 'x'}, 'seven_day': {'used_percentage': 'bad'}}}))
            self.assertEqual(scanner.claude_limits(home), {'found': True, 'captured_at': 5, 'windows': {'five_hour': [10.0, 99]}})
            with mock.patch.object(Path, 'home', return_value=home):
                result = scanner.scan({'sources': ['claude_limits']})
            self.assertEqual(result['claude_limits']['windows'], {'five_hour': [10.0, 99]})

    def test_scanner_without_a_probe_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(scanner.claude_limits(Path(tmp)), {'found': False})


class ProbeInstallTests(unittest.TestCase):
    def load(self, home):
        import importlib.util
        spec = importlib.util.spec_from_file_location('probe', Path(__file__).resolve().parents[1] / 'tools' / 'limits-probe.py')
        with mock.patch.object(Path, 'home', return_value=home):
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module

    def test_install_keeps_the_old_status_line_and_remove_restores_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / '.claude').mkdir()
            old = {'theme': 'dark', 'statusLine': {'type': 'command', 'command': 'echo hi'}}
            (home / '.claude' / 'settings.json').write_text(json.dumps(old))
            probe = self.load(home)
            probe.install()
            probe.install()  # Twice is safe.
            data = json.loads((home / '.claude' / 'settings.json').read_text())
            self.assertEqual(data['statusLine']['command'], probe.COMMAND)
            self.assertEqual(data['theme'], 'dark')
            self.assertEqual(json.loads((home / '.claude' / 'hq-limits-previous.json').read_text())['command'], 'echo hi')
            probe.remove()
            self.assertEqual(json.loads((home / '.claude' / 'settings.json').read_text()), old)
            self.assertFalse((home / '.claude' / 'hq-limits-statusline.py').exists())

    def test_install_and_remove_with_no_status_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            probe = self.load(home)
            probe.install()
            probe.remove()
            self.assertEqual(json.loads((home / '.claude' / 'settings.json').read_text()), {})


if __name__ == '__main__':
    unittest.main()
