from pathlib import Path

from hq.run.env import resolve_exe
from .agy import AgyLauncher
from .claude import ClaudeLauncher
from .codex import CodexLauncher
from .dsh import DshLauncher
from .gemini import GeminiLauncher
from .hermes import HermesLauncher
from .kimi import KimiLauncher
from .opencode import OpencodeLauncher

REGISTRY = {launcher.name: launcher for launcher in (
    ClaudeLauncher(), CodexLauncher(), AgyLauncher(), OpencodeLauncher(),
    KimiLauncher(), GeminiLauncher(), DshLauncher(), HermesLauncher())}


def available(refused_dirs: list[Path]) -> dict[str, set[str]]:
    return {name: launcher.levels() for name, launcher in REGISTRY.items()
            if resolve_exe('wsl' if name == 'codex' else launcher.exe, refused_dirs)}
