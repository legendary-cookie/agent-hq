import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from hq.run.config import Project, RunConfig
from hq.run.request import parse_request, request_hash
from hq.run.nonce import NonceBook


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name).resolve()
        self.projects = [Project('project', self.path)]
        self.cfg = RunConfig()
        self.data = dict(tool='tool', model='model', project='project', prompt='hello', level='edit', guard_mode='block', profile=None, time_limit_min=30)
        self.tools = {'tool': {'edit'}}

    def parse(self, **changes):
        return parse_request({**self.data, **changes}, self.cfg, self.projects, self.tools)

    def test_unknown_tool_refused(self):
        with self.assertRaises(ValueError): self.parse(tool='unknown')

    def test_level_not_offered_refused(self):
        with self.assertRaises(ValueError): self.parse(level='full_auto')

    def test_bad_model_refused(self):
        for model in ('gpt 6', 'a;b', 'a' * 81, '--dangerously-skip-permissions'):
            with self.subTest(model=model), self.assertRaises(ValueError): self.parse(model=model)

    def test_prompt_too_long_refused(self):
        with self.assertRaises(ValueError): self.parse(prompt='a' * 100001)

    def test_empty_prompt_refused(self):
        with self.assertRaises(ValueError): self.parse(prompt='')

    def test_time_limit_bounds(self):
        for value in (0, 241):
            with self.subTest(value=value), self.assertRaises(ValueError): self.parse(time_limit_min=value)
        self.assertEqual(self.parse(time_limit_min=None).time_limit_min, 30)

    def test_project_must_be_allowlisted_by_name(self):
        with self.assertRaises(ValueError): self.parse(project=str(self.path))

    def test_junction_outside_allowlist_refused(self):
        original = self.path / 'original'
        outside = self.path / 'outside'
        original.mkdir(); outside.mkdir()
        self.projects = [Project('project', original.resolve())]
        original.rmdir()
        try:
            original.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f'Cannot create directory symlink: {exc}')
        with self.assertRaises(ValueError): self.parse()

    def test_guard_mode_values(self):
        for mode in ('warn', 'block', 'auto'): self.assertEqual(self.parse(guard_mode=mode).guard_mode, mode)
        with self.assertRaises(ValueError): self.parse(guard_mode='other')

    def test_nonce_single_use(self):
        book = NonceBook(); req = self.parse(); nonce, _ = book.issue(req)
        self.assertTrue(book.redeem(nonce, req))
        self.assertFalse(book.redeem(nonce, req))

    def test_nonce_expires(self):
        now = [0]; book = NonceBook(clock=lambda: now[0]); req = self.parse(); nonce, _ = book.issue(req)
        now[0] = 61
        self.assertFalse(book.redeem(nonce, req))

    def test_nonce_bound_to_request(self):
        book = NonceBook(); req = self.parse(); nonce, _ = book.issue(req)
        self.assertFalse(book.redeem(nonce, replace(req, prompt='different')))
        self.assertFalse(book.redeem(nonce, req))

    def test_hash_stable_key_order(self):
        self.assertEqual(request_hash(self.parse()), request_hash(parse_request(dict(reversed(list(self.data.items()))), self.cfg, self.projects, self.tools)))
