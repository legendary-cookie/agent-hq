import glob
import sqlite3
from pathlib import Path
from typing import Iterator, Protocol
from ..config import SourceCfg
from ..records import Batch
from ..store import Store

class Adapter(Protocol):
    name: str
    def poll(self, store: Store, cfg: SourceCfg) -> Batch: ...

def iter_files(cfg: SourceCfg, pattern: str) -> Iterator[Path]:
    for root in cfg.paths:
        for match in glob.iglob(str(root), recursive=True):
            path = Path(match)
            if path.is_file():
                yield path
            elif path.is_dir():
                yield from (p for p in path.glob(pattern) if p.is_file())

def ro_connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)

def preview(text: str | None) -> str | None:
    return None if text is None else ' '.join(text.split())[:200]
