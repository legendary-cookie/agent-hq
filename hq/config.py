import fnmatch
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from .pricebook import PriceBook

@dataclass
class SourceCfg:
    enabled: bool
    paths: list[Path]

@dataclass
class Profile:
    id: str
    tool: str
    config_dir: str
    account_id: str

    def __post_init__(self):
        if is_forbidden(Path(self.config_dir).expanduser()):
            raise ForbiddenPathError("Forbidden profile path")

@dataclass
class Account:
    id: str
    provider: str
    label: str
    plan: str
    monthly_price_usd: float | None
    billing_day: int
    match: list[dict]
    cap_tokens: dict[str, int]
    profiles: list[Profile] = field(default_factory=list)

    def __post_init__(self):
        self.profiles = [Profile(**{**p, 'account_id': self.id}) if isinstance(p, dict) else p for p in self.profiles]

@dataclass
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float

@dataclass
class Config:
    sources: dict[str, SourceCfg]
    accounts: list[Account]
    prices: dict[str, Price]
    alerts: dict
    default_skin: str
    recommend_task_kind: str
    relabel: list[tuple[Path, str]] = field(default_factory=list)
    zylos_previews: bool = False
    deepseek_alert: float = 5.0
    pricebook: PriceBook = field(init=False, compare=False)

    def __post_init__(self):
        self.pricebook = PriceBook(self.prices, {}, {})

class ForbiddenPathError(ValueError):
    pass

_FORBIDDEN = {'secrets', 'auth.json', 'oauth_creds.json', 'credentials', 'google_accounts.json', '.env', '.credentials.json'}

def is_forbidden(path: Path) -> bool:
    resolved = path.resolve(strict=False)
    parts = (*resolved.parts, *PureWindowsPath(str(resolved)).parts,
             *path.parts, *PureWindowsPath(str(path)).parts)
    return any(part.split(':', 1)[0].rstrip(' .').lower() in _FORBIDDEN for part in parts)

def _path(value):
    path = Path(os.path.expanduser(value))
    if is_forbidden(path):
        raise ForbiddenPathError(f'Forbidden source path: {value}')
    return path

def load_config(config_dir: Path) -> Config:
    def read(name):
        return json.loads((config_dir / name).read_text(encoding='utf-8'))
    sources = read('sources.json')
    # accounts.json is personal (plans, prices) and gitignored; a fresh clone starts from the example.
    accounts_path, example_path = config_dir / 'accounts.json', config_dir / 'accounts.example.json'
    if not accounts_path.exists() and example_path.exists():
        accounts_path.write_text(example_path.read_text(encoding='utf-8'), encoding='utf-8')
    accounts = read('accounts.json')
    prices = read('prices.json')
    cfg = Config({k: SourceCfg(v['enabled'], [_path(p) for p in v['paths']]) for k, v in sources['sources'].items()},
                  [Account(**a) for a in accounts], {k: Price(**v) for k, v in prices.items()},
                  sources.get('alerts', {'thresholds': [80, 95], 'toast': False}),
                  sources.get('default_skin', 'isometric'), sources.get('recommend_task_kind', 'coding'),
                  [(_path(rule['under']), rule['harness']) for rule in sources.get('relabel', [])],
                  sources.get('zylos_previews', False), sources.get('deepseek_alert', 5.0))
    snapshot_path = config_dir / 'prices.litellm.json'
    if not snapshot_path.exists():
        snapshot_path = Path(__file__).resolve().parent.parent / 'config' / 'prices.litellm.json'
    if snapshot_path.exists():
        snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
        cfg.pricebook = PriceBook(cfg.prices, {k: Price(**v) for k, v in snapshot['models'].items()}, snapshot['aliases'])
    return cfg

def save_config(config_dir: Path, cfg: Config) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    sources = {'sources': {k: {'enabled': v.enabled, 'paths': [str(p) for p in v.paths]} for k, v in cfg.sources.items()},
               'alerts': cfg.alerts, 'default_skin': cfg.default_skin, 'recommend_task_kind': cfg.recommend_task_kind,
               'relabel': [{'under': str(path), 'harness': harness} for path, harness in cfg.relabel],
               'zylos_previews': cfg.zylos_previews, 'deepseek_alert': cfg.deepseek_alert}
    accounts_path = config_dir / 'accounts.json'
    existing = json.loads(accounts_path.read_text(encoding='utf-8')) if accounts_path.exists() else []
    existing_profiles = {a['id']: a['profiles'] for a in existing if 'profiles' in a}
    accounts = []
    for account in cfg.accounts:
        data = asdict(account)
        data.pop('profiles')
        if account.id in existing_profiles:
            data['profiles'] = existing_profiles[account.id]
        accounts.append(data)
    for name, data in [('sources.json', sources), ('accounts.json', accounts), ('prices.json', {k: asdict(v) for k, v in cfg.prices.items()})]:
        (config_dir / name).write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')

def match_account(cfg, harness, provider, model) -> str:
    values = {'harness': harness, 'provider': provider, 'model': model}
    for account in cfg.accounts:
        for rule in account.match:
            if all(k in values and values[k] is not None and fnmatch.fnmatchcase(values[k], v) for k, v in rule.items()):
                return account.id
    return 'unassigned'
