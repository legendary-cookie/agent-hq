import json
import tempfile
import unittest
from pathlib import Path
from hq.config import Account, Config, SourceCfg, Price, ForbiddenPathError, load_config, save_config, match_account

class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.cfg = Config({'codex': SourceCfg(True, [Path('~/.codex/sessions')])}, [], {}, {'thresholds': [80, 95], 'toast': False}, 'isometric', 'coding')

    def test_expands_home_and_globs(self):
        save_config(self.path, self.cfg)
        self.assertEqual(load_config(self.path).sources['codex'].paths, [Path.home() / '.codex/sessions'])

    def test_forbidden_path_rejected(self):
        for path in ['~/.hermes/secrets/x', '~/.gemini/oauth_creds.json', 'C:/x/Auth.JSON', '/x/credentials/a', '/x/google_accounts.json', '/x/.env']:
            with self.subTest(path=path):
                self.cfg.sources['codex'].paths = [Path(path)]
                save_config(self.path, self.cfg)
                with self.assertRaises(ForbiddenPathError): load_config(self.path)

    def test_match_account_first_rule_wins(self):
        self.cfg.accounts = [Account('first','x','x','x',None,1,[{'harness':'codex'}],{}), Account('second','x','x','x',None,1,[{'provider':'openai'}],{})]
        self.assertEqual(match_account(self.cfg, 'codex', 'openai', 'gpt-6-luna'), 'first')

    def test_match_account_model_glob(self):
        self.cfg.accounts = [Account('a','x','x','x',None,1,[{'harness':'antigravity','model':'gemini-4-argon*'}],{})]
        self.assertEqual(match_account(self.cfg, 'antigravity', 'google', 'gemini-4-argon-high'), 'a')

    def test_unassigned_when_no_rule(self):
        self.assertEqual(match_account(self.cfg, 'x', 'y', None), 'unassigned')

    def test_save_roundtrip(self):
        self.cfg.sources['codex'].paths = [self.path / '*.jsonl']
        self.cfg.prices = {'model': Price(3, 15, .3, 3.75)}
        save_config(self.path, self.cfg)
        self.assertEqual(load_config(self.path), self.cfg)


class FirstRunAccountsTests(unittest.TestCase):
    """A fresh clone has no config/accounts.json (it is personal and gitignored)."""
    def test_missing_accounts_json_is_created_from_example(self):
        import shutil, tempfile
        from hq.config import load_config
        src = Path(__file__).resolve().parent.parent / 'config'
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp)
            for name in ('sources.json', 'prices.json', 'accounts.example.json'):
                shutil.copy(src / name, cfg_dir / name)
            cfg = load_config(cfg_dir)
            self.assertTrue((cfg_dir / 'accounts.json').exists())
            self.assertTrue(cfg.accounts)
            self.assertTrue(all(a.monthly_price_usd is None for a in cfg.accounts))

    def test_example_has_no_personal_plans(self):
        example = json.loads((Path(__file__).resolve().parent.parent / 'config' / 'accounts.example.json').read_text())
        self.assertNotIn('ninerouter', {a['id'] for a in example})
