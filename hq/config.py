import fnmatch
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath

@dataclass
class SourceCfg:
    enabled: bool
    paths: list[Path]

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

class ForbiddenPathError(ValueError):
    pass

_FORBIDDEN = {'secrets', 'auth.json', 'oauth_creds.json', 'credentials', 'google_accounts.json', '.env'}

def _path(value):
    parts = (*Path(value).parts, *PureWindowsPath(value).parts)
    if any(part.lower() in _FORBIDDEN for part in parts):
        raise ForbiddenPathError(f'Forbidden source path: {value}')
    return Path(os.path.expanduser(value))

def load_config(config_dir: Path) -> Config:
    def read(name):
        return json.loads((config_dir / name).read_text(encoding='utf-8'))
    sources = read('sources.json')
    accounts = read('accounts.json')
    prices = read('prices.json')
    return Config({k: SourceCfg(v['enabled'], [_path(p) for p in v['paths']]) for k, v in sources['sources'].items()},
                  [Account(**a) for a in accounts], {k: Price(**v) for k, v in prices.items()},
                  sources.get('alerts', {'thresholds': [80, 95], 'toast': False}),
                  sources.get('default_skin', 'isometric'), sources.get('recommend_task_kind', 'coding'))

def save_config(config_dir: Path, cfg: Config) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    sources = {'sources': {k: {'enabled': v.enabled, 'paths': [str(p) for p in v.paths]} for k, v in cfg.sources.items()},
               'alerts': cfg.alerts, 'default_skin': cfg.default_skin, 'recommend_task_kind': cfg.recommend_task_kind}
    for name, data in [('sources.json', sources), ('accounts.json', [asdict(a) for a in cfg.accounts]), ('prices.json', {k: asdict(v) for k, v in cfg.prices.items()})]:
        (config_dir / name).write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')

def match_account(cfg, harness, provider, model) -> str:
    values = {'harness': harness, 'provider': provider, 'model': model}
    for account in cfg.accounts:
        for rule in account.match:
            if all(k in values and values[k] is not None and fnmatch.fnmatchcase(values[k], v) for k, v in rule.items()):
                return account.id
    return 'unassigned'
