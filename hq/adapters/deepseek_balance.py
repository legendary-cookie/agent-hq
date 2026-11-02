"""Fetch DeepSeek API balances without exposing credentials in errors."""
import json
import math
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from ..records import Batch


def load_key(db_dir: Path) -> str | None:
    key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    if key:
        return key
    # This explicitly designated credential is not a source-data path.
    try:
        return (db_dir / 'secrets' / 'deepseek_api_key').read_text(encoding='utf-8').strip() or None
    except FileNotFoundError:
        return None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, 'redirect refused', headers, fp)


def fetch_balance(key: str, url: str = 'https://api.deepseek.com/user/balance', timeout: float = 10) -> list[dict]:
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1')):
            raise ValueError('insecure URL')
        request = Request(url)
        request.add_unredirected_header('Authorization', 'Bearer ' + key)
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            payload = json.load(response)
        infos = payload['balance_infos']
        if not isinstance(infos, list):
            raise ValueError('invalid balance response')
        result = []
        for item in infos:
            currency = item['currency']
            values = [float(item[k]) for k in ('total_balance', 'granted_balance', 'topped_up_balance')]
            if not isinstance(currency, str) or not currency or not all(math.isfinite(v) for v in values):
                raise ValueError('invalid balance response')
            result.append(dict(zip(('currency', 'total', 'granted', 'topped_up'), (currency, *values))))
        return result
    except (HTTPError, URLError, OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise RuntimeError('DeepSeek balance request failed or returned invalid data') from None


class DeepseekBalanceAdapter:
    name = 'deepseek_balance'

    def __init__(self, url='https://api.deepseek.com/user/balance'):
        self.url = url
        self.last_request = None
        self.last_error = None

    def poll(self, store, cfg=None):
        key = load_key(store.path.parent)
        now = time.time()
        if not key:
            self.last_error = None
            return Batch()
        if self.last_request is not None and now - self.last_request < 900:
            if self.last_error:
                raise RuntimeError(self.last_error)
            return Batch()
        self.last_request = now
        try:
            balances = fetch_balance(key, self.url)
        except RuntimeError as exc:
            self.last_error = str(exc)
            raise
        self.last_error = None
        with store._lock, store._writer as conn:
            conn.executemany('INSERT OR REPLACE INTO balances(account_id,currency,total,granted,topped_up,recorded_at) VALUES (?,?,?,?,?,?)',
                             [('deepseek-api', b['currency'], b['total'], b['granted'], b['topped_up'], now) for b in balances])
        return Batch()
