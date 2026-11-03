"""Resolve supported npm batch shims without executing a command interpreter."""
import re
import shutil
from pathlib import Path


_BATCH = {'.cmd', '.bat'}
_SAFE_ARG = re.compile(r'[A-Za-z0-9._:/=\\@+\-]*', re.ASCII)
_EXE = re.compile(r'"%dp0%[\\/](.+?\.exe)"\s+%\*', re.IGNORECASE)
_JS = re.compile(r'"%_prog%"\s+"%dp0%[\\/](.+?\.js)"\s+%\*', re.IGNORECASE)


def resolve_command(exe_path: Path) -> list[str]:
    if exe_path.suffix.lower() not in _BATCH:
        return [str(exe_path)]
    try:
        lines = exe_path.read_text(encoding='utf-8-sig').splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError('Cannot read batch shim') from exc
    line = next((line.strip() for line in reversed(lines) if line.strip()), '')
    exe_match = _EXE.fullmatch(line)
    js_match = _JS.fullmatch(line)
    match = exe_match or js_match
    if not match:
        raise ValueError('Unsupported batch shim')
    relative = match.group(1).replace('\\', '/')
    # Reject Windows absolute paths and substitutions even on non-Windows hosts.
    if re.search(r'[:%]', relative) or relative.startswith('/'):
        raise ValueError('Invalid shim target')
    root = exe_path.parent.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError('Shim target must exist inside the shim directory')
    if exe_match:
        return [str(target)]
    local_node = root / 'node.exe'
    if local_node.is_file():
        node = str(local_node)
    else:
        node = shutil.which('node')
        if not node:
            raise ValueError('Node executable not found')
    return [node, str(target)]


def check_batch_args(argv: list[str]):
    if any(_SAFE_ARG.fullmatch(arg) is None for arg in argv[1:]):
        raise ValueError('Unsafe batch argument')


def final_argv(launch, resolved_exe: Path) -> list[str]:
    if launch.argv[0] == 'wsl':
        return list(launch.argv)
    argv = resolve_command(resolved_exe) + launch.argv[1:]
    if Path(argv[0]).suffix.lower() in _BATCH:
        check_batch_args(argv)
    return argv
