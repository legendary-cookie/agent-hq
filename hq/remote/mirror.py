import base64
import binascii
import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from hq.config import is_forbidden
from hq.remote.config import SOURCES


@dataclass
class MirrorResult:
    written: int
    bytes: int
    resets: int
    more: bool
    errors: list[str]
    notes: list[str] = field(default_factory=list)  # reported by the scanner, e.g. a skipped big database


def _target(root, host, name):
    if not isinstance(name, str) or not name or '\\' in name or '\0' in name:
        raise ValueError('invalid path')
    path = PurePosixPath(name)
    windows = PureWindowsPath(name)
    if path.is_absolute() or windows.drive or windows.root or '..' in path.parts:
        raise ValueError('invalid path')
    if any(':' in part or part.endswith(('.', ' ')) or
           re.fullmatch(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part, re.IGNORECASE)
           for part in path.parts):
        raise ValueError('invalid Windows path component')
    allowed = [PurePosixPath(p) for source in host.sources for p in SOURCES.get(source, ())]
    if not any(path == base or base in path.parents for base in allowed):
        raise ValueError('path outside requested sources')
    target = root.joinpath(*path.parts)
    if is_forbidden(target) or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('forbidden path')
    return target


def _metadata(root):
    try:
        data = json.loads((root / '.hqmeta.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            name = stream.name
            stream.write(data)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def apply_answer(answer: dict, host, root: Path) -> MirrorResult:
    result = MirrorResult(0, 0, 0, False, [])
    if not isinstance(answer, dict) or not isinstance(answer.get('files', []), list):
        result.errors.append('invalid answer')
        return result
    if len(answer.get('files', [])) > 2000:
        result.errors.append('too many file entries')
        return result
    result.more = bool(answer.get('more', False))
    errors = answer.get('errors', [])
    if isinstance(errors, list):
        result.notes.extend(str(error)[:300] for error in errors[:50])
    raw_metadata = _metadata(root)
    metadata = {}
    aliases = {}
    def remember(name):
        parts = PurePosixPath(name).parts
        for i in range(1, len(parts) + 1):
            component = '/'.join(parts[:i])
            key = component.casefold()
            if key in aliases and aliases[key] != component:
                raise ValueError('case-insensitive path collision')
            aliases[key] = component
    for key, value in raw_metadata.items():
        name = value.get('path', key) if isinstance(value, dict) else key
        mtime = value.get('mtime') if isinstance(value, dict) else value
        try:
            _target(root, host, name)
            remember(name)
            metadata[name.casefold()] = {'path': name, 'mtime': mtime}
        except (ValueError, TypeError):
            result.errors.append('invalid or colliding metadata path')
    existing = set(metadata)
    if root.exists():
        for folder, dirs, files in os.walk(root, followlinks=False):
            for filename in files:
                path = Path(folder) / filename
                if path == root / '.hqmeta.json': continue
                name = path.relative_to(root).as_posix()
                existing.add(name.casefold())
                try: remember(name)
                except ValueError: result.errors.append('case-insensitive path collision')
    dirty = False
    db_bytes = 0
    for entry in answer.get('files', []):
        try:
            if not isinstance(entry, dict):
                raise ValueError('invalid entry')
            name = entry.get('path')
            target = _target(root, host, name)
            kind = entry.get('kind')
            offset = entry.get('offset')
            mtime = entry.get('mtime')
            size = entry.get('size')
            if kind not in ('append', 'reset', 'replace') or type(offset) is not int or offset < 0:
                raise ValueError('invalid kind or offset')
            if type(size) is not int or size < 0 or not isinstance(mtime, (int, float)) or isinstance(mtime, bool) or not math.isfinite(mtime):
                raise ValueError('invalid size or mtime')
            data = base64.b64decode(entry.get('data_b64', ''), validate=True)
            if not data and not (kind in ('reset', 'replace') and size == 0):
                continue
            key = name.casefold()
            remember(name)
            if key not in existing and len(existing) >= 20000:
                raise ValueError('mirror file count limit exceeded')
            is_db = kind == 'replace' and name.lower().endswith('.db')
            budget_used = db_bytes if is_db else result.bytes - db_bytes
            if budget_used + len(data) > (30000000 if is_db else 20000000) or offset + len(data) > size or (kind != 'append' and offset != 0):
                raise ValueError('invalid data size')
            if kind == 'replace' and len(data) != size:
                raise ValueError('incomplete replacement')
            current = target.stat().st_size if target.exists() else 0
            pending = key in metadata and metadata[key]['mtime'] is None
            if kind == 'append' and offset != current and not (pending and offset == 0):
                metadata[key] = {'path': name, 'mtime': None}
                existing.add(key)
                dirty = True
                result.resets += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if kind != 'append' or pending:
                _atomic(target, data)
            else:
                with target.open('ab') as stream:
                    stream.write(data)
            metadata[key] = {'path': name, 'mtime': mtime}
            existing.add(key)
            dirty = True
            result.written += 1
            result.bytes += len(data)
            if is_db:
                db_bytes += len(data)
        except (ValueError, TypeError, OSError, binascii.Error) as exc:
            result.errors.append(str(exc))
    if dirty:
        try:
            _atomic(root / '.hqmeta.json', json.dumps(metadata).encode('utf-8'))
        except OSError as exc:
            result.errors.append(str(exc))
    return result


def cursors_for(root: Path, host) -> dict:
    cursors = {}
    for key, value in _metadata(root).items():
        name = value.get('path', key) if isinstance(value, dict) else key
        mtime = value.get('mtime') if isinstance(value, dict) else value
        if mtime is None:
            continue
        try:
            path = _target(root, host, name)
            if path.is_file():
                cursors[name] = [path.stat().st_size, mtime]
        except (ValueError, OSError):
            continue
    return cursors


def prune_hosts(remote_root: Path, keep: set[str]):
    if not remote_root.exists():
        return
    for path in remote_root.iterdir():
        if path.name not in keep and path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
