from pathlib import Path
from typing import Iterator

try:
    from compression import zstd
except ImportError:
    zstd = None

available = zstd is not None
_MAGIC = b'\x28\xb5\x2f\xfd'


def iter_frames(data: bytes) -> Iterator[bytes]:
    if not available:
        raise RuntimeError('compression.zstd is unavailable')
    if not data:
        return
    starts = []
    offset = 0
    while True:
        offset = data.find(_MAGIC, offset)
        if offset < 0:
            break
        starts.append(offset)
        offset += len(_MAGIC)
    if not starts or starts[0] != 0:
        raise ValueError('invalid zstd frame header')
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(data)
        try:
            yield zstd.decompress(data[start:end])
        except (zstd.ZstdError, EOFError) as exc:
            if index + 1 < len(starts):
                raise ValueError('invalid zstd frame') from exc


def read_text(path: Path) -> str:
    return b''.join(iter_frames(path.read_bytes())).decode('utf-8', errors='replace')
