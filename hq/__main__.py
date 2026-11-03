import argparse
import os
import re
import secrets
from pathlib import Path
import webbrowser
from .config import load_config
from .collector import Collector
from .remote.collector import RemoteCollector
from .remote.config import load_hosts
from .store import Store
from .server import make_server
from .run.service import RunService
from .limits import recommend_bridge
from .adapters.claude_code import ClaudeCodeAdapter
from .adapters.codex import CodexAdapter
from .adapters.opencode import OpencodeAdapter
from .adapters.antigravity import AntigravityAdapter
from .adapters.gemini_cli import GeminiCliAdapter
from .adapters.provider_usage import ProviderUsageAdapter
from .adapters.kimi import KimiAdapter
from .adapters.dsh import DshAdapter
from .adapters.zylos import ZylosAdapter
from .adapters.deepseek_balance import DeepseekBalanceAdapter
from .adapters.importer import ImporterAdapter


def build_adapters(cfg, db_dir):
    """Every reader, in one list shared by --once and the server."""
    return [
        ClaudeCodeAdapter(),
        CodexAdapter(),
        OpencodeAdapter(),
        AntigravityAdapter(),
        GeminiCliAdapter(),
        ProviderUsageAdapter(),
        KimiAdapter(),
        DshAdapter(),
        ZylosAdapter(cfg),
        DeepseekBalanceAdapter(),
        ImporterAdapter(db_dir / 'tools'),
    ]

def load_token(token_path, rotate=False):
    try:
        token = token_path.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        token = ''
    if rotate or not re.fullmatch(r'[A-Za-z0-9_-]{32,}', token):
        token = secrets.token_urlsafe(32)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as token_file:
        if os.name != 'nt':
            os.fchmod(token_file.fileno(), 0o600)
        token_file.write(token)
    return token


def main(argv=None):
    root=Path(__file__).resolve().parent.parent
    default_db=Path(os.environ['LOCALAPPDATA'])/'agent-hq'/'hq.db' if os.name=='nt' and os.environ.get('LOCALAPPDATA') else Path.home()/'.local'/'share'/'agent-hq'/'hq.db'
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=7420); p.add_argument('--config',type=Path,default=root/'config'); p.add_argument('--db',type=Path,default=default_db); p.add_argument('--no-browser',action='store_true'); p.add_argument('--rotate-token',action='store_true'); p.add_argument('--once',action='store_true'); args=p.parse_args(argv)
    cfg=load_config(args.config); args.db.parent.mkdir(parents=True,exist_ok=True); store=Store(args.db)
    adapters=build_adapters(cfg,args.db.parent); collector=Collector(store,cfg,adapters)
    hosts, warnings = load_hosts(args.config)
    for warning in warnings: print(f'hosts: {warning}')
    remote = RemoteCollector(store, cfg, hosts, args.db.parent, build_adapters)
    try:
        if args.once:
            collector.run_once(); remote.run_once(); health=collector.health()
            for name,state in health.items(): print(f"{name:16} {state['state']:8} warnings={state['warnings']} {state['error'] or ''}")
            for name,state in remote.health().items(): print(f"{name:16} {state['state']:8} {state['error'] or ''}")
            return int(any(s['state']=='partial' for s in list(health.values()) + list(remote.health().values())))
        token = load_token(args.db.parent / 'token', rotate=args.rotate_token)
        source=cfg.sources.get('provider_usage')
        pu_dir=Path(source.paths[0]) if source and source.enabled and source.paths else None
        if pu_dir and pu_dir.name == 'usage.db': pu_dir=pu_dir.parent
        service=RunService(store,cfg,args.config,args.db.parent,lambda kind: recommend_bridge(pu_dir,kind) if pu_dir else None,pu=pu_dir)
        service.runner.recover()
        try:
            server,token=make_server(store,collector,cfg,args.config,args.port,root/'web'/'dist',token=token,run_service=service,remote_collector=remote); collector.start(); remote.start()
        except BaseException:
            service.runner.shutdown()
            raise
        try:
            url=f'http://127.0.0.1:{server.server_port}/#code={server.bootstrap_code}'
            if args.no_browser: print(url,flush=True)
            else: webbrowser.open(url)
            server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close(); collector.stop(); remote.stop(); service.runner.shutdown()
        return 0
    finally: store.close()

if __name__=='__main__': raise SystemExit(main())
