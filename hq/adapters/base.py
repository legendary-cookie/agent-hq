import os
import fnmatch
import sqlite3
from pathlib import Path
from typing import Iterator, Protocol
from ..config import SourceCfg, is_forbidden
from ..records import Batch
from ..store import Store

class Adapter(Protocol):
    name: str
    def poll(self, store: Store, cfg: SourceCfg) -> Batch: ...

def _expand(path: Path) -> Iterator[Path]:
    parts = path.parts
    base = Path(path.anchor) if path.anchor else Path('.')
    parts = parts[1:] if path.anchor else parts
    def visit(parent, index):
        if is_forbidden(parent): return
        if index == len(parts):
            if parent.exists(): yield parent
            return
        part = parts[index]
        if part == '**':
            yield from visit(parent, index + 1)
            if parent.is_dir():
                with os.scandir(parent) as entries:
                    for entry in entries:
                        child = Path(entry.path)
                        if not entry.name.startswith('.') and not is_forbidden(child) and entry.is_dir(follow_symlinks=False):
                            yield from visit(child, index)
        elif any(c in part for c in '*?['):
            if parent.is_dir():
                with os.scandir(parent) as entries:
                    for entry in entries:
                        if (part.startswith('.') or not entry.name.startswith('.')) and fnmatch.fnmatchcase(entry.name, part):
                            yield from visit(Path(entry.path), index + 1)
        else:
            yield from visit(parent / part, index + 1)
    yield from visit(base, 0)

def iter_files(cfg: SourceCfg, pattern: str) -> Iterator[Path]:
    for root in cfg.paths:
        for path in _expand(root):
            if is_forbidden(path): continue
            if path.is_file():
                yield path
            elif path.is_dir():
                for directory, dirs, files in os.walk(path):
                    dirs[:] = [d for d in dirs if not is_forbidden(Path(directory) / d)]
                    for name in files:
                        candidate = Path(directory) / name
                        rel = candidate.relative_to(path).as_posix()
                        if (fnmatch.fnmatchcase(rel, pattern) or (pattern.startswith('**/') and fnmatch.fnmatchcase(rel, pattern[3:]))) and not is_forbidden(candidate) and candidate.is_file():
                            yield candidate

def ro_connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)

def preview(text: str | None) -> str | None:
    return None if text is None else ' '.join(text.split())[:200]
