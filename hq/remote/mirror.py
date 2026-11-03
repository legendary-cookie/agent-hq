import base64
import binascii
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
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


def _target(root, host, name):
    if not isinstance(name, str) or not name or '\\' in name or '\0' in name:
        raise ValueError('invalid path')
    path = PurePosixPath(name)
    windows = PureWindowsPath(name)
    if path.is_absolute() or windows.drive or windows.root or '..' in path.parts:
        raise ValueError('invalid path')
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
    result.more = bool(answer.get('more', False))
    errors = answer.get('errors', [])
    if isinstance(errors, list):
        result.errors.extend(str(error) for error in errors)
    metadata = _metadata(root)
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
            if result.bytes + len(data) > 20000000 or offset + len(data) > size or (kind != 'append' and offset != 0):
                raise ValueError('invalid data size')
            if kind == 'replace' and len(data) != size:
                raise ValueError('incomplete replacement')
            current = target.stat().st_size if target.exists() else 0
            pending = metadata.get(name) is None and name in metadata
            if kind == 'append' and offset != current and not (pending and offset == 0):
                metadata[name] = None
                result.resets += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if kind != 'append' or pending:
                _atomic(target, data)
            else:
                with target.open('ab') as stream:
                    stream.write(data)
            metadata[name] = mtime
            result.written += 1
            result.bytes += len(data)
        except (ValueError, TypeError, OSError, binascii.Error) as exc:
            result.errors.append(str(exc))
    if metadata:
        try:
            _atomic(root / '.hqmeta.json', json.dumps(metadata).encode('utf-8'))
        except OSError as exc:
            result.errors.append(str(exc))
    return result


def cursors_for(root: Path, host) -> dict:
    cursors = {}
    for name, mtime in _metadata(root).items():
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
