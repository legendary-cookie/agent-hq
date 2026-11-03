import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path


# Names the tools need to start and find their own settings; none carries a secret.
_ALLOWED = {'PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'TEMP', 'TMP', 'LANG',
            # Windows: Node and .cmd launchers need these to start and to find %APPDATA% settings.
            'APPDATA', 'LOCALAPPDATA', 'PATHEXT', 'COMSPEC', 'SYSTEMDRIVE', 'WINDIR',
            'PROGRAMDATA', 'PROGRAMFILES', 'USERNAME', 'HOMEDRIVE', 'HOMEPATH',
            # POSIX
            'USER', 'LOGNAME', 'SHELL', 'TERM', 'LC_ALL', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME'}
_SECRET = re.compile(r'KEY|TOKEN|SECRET|PASSWORD', re.IGNORECASE)


def _key(name: str) -> str:
    return name.upper() if os.name == 'nt' else name


def child_env(base: Mapping[str, str], extra: dict[str, str], pass_names: list[str]) -> dict[str, str]:
    allowed = {_key(name) for name in _ALLOWED | set(pass_names)}
    result = {name: value for name, value in base.items() if _key(name) in allowed}
    for name, value in extra.items():
        if _SECRET.search(name):
            raise ValueError('Secret-like extra environment variable is not allowed')
        if os.name == 'nt':
            result = {key: old for key, old in result.items() if _key(key) != _key(name)}
        result[name] = value
    return result


def resolve_exe(name: str, refused_dirs: list[Path]) -> Path | None:
    found = shutil.which(name)
    if found is None:
        return None
    path = Path(found).resolve()
    if path.parent == Path.cwd().resolve() or any(path.is_relative_to(directory.resolve()) for directory in refused_dirs):
        return None
    return path
