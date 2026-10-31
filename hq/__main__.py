import argparse
import os
from pathlib import Path
import webbrowser
from .config import load_config
from .collector import Collector
from .store import Store
from .server import make_server
from .adapters.claude_code import ClaudeCodeAdapter
from .adapters.codex import CodexAdapter
from .adapters.opencode import OpencodeAdapter
from .adapters.antigravity import AntigravityAdapter
from .adapters.gemini_cli import GeminiCliAdapter
from .adapters.provider_usage import ProviderUsageAdapter

def main(argv=None):
    root=Path(__file__).resolve().parent.parent
    default_db=Path(os.environ['LOCALAPPDATA'])/'agent-hq'/'hq.db' if os.name=='nt' and os.environ.get('LOCALAPPDATA') else Path.home()/'.local'/'share'/'agent-hq'/'hq.db'
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=7420); p.add_argument('--config',type=Path,default=root/'config'); p.add_argument('--db',type=Path,default=default_db); p.add_argument('--no-browser',action='store_true'); p.add_argument('--once',action='store_true'); args=p.parse_args(argv)
    cfg=load_config(args.config); args.db.parent.mkdir(parents=True,exist_ok=True); store=Store(args.db)
    adapters=[ClaudeCodeAdapter(),CodexAdapter(),OpencodeAdapter(),AntigravityAdapter(),GeminiCliAdapter(),ProviderUsageAdapter()]; collector=Collector(store,cfg,adapters)
    try:
        if args.once:
            collector.run_once(); health=collector.health()
            for name,state in health.items(): print(f"{name:16} {state['state']:8} warnings={state['warnings']} {state['error'] or ''}")
            return int(any(s['state']=='partial' for s in health.values()))
        server,token=make_server(store,collector,cfg,args.config,args.port,root/'web'/'dist'); collector.start()
        if not args.no_browser: webbrowser.open(f'http://127.0.0.1:{server.server_port}/')
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close(); collector.stop()
        return 0
    finally: store.close()

if __name__=='__main__': raise SystemExit(main())
