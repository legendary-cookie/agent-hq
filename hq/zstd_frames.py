"""Read logs written as a series of independent zstd frames (one per flush).

Frames are walked by the decompressor itself, never by searching for the
zstd magic number: compressed or raw-stored payloads can contain those four
bytes by chance. A frame that does not reach its end is a torn tail (the
writer is mid-flush) and is skipped; it is read whole on a later pass. A
frame that is corrupt before the end of the data raises ValueError.
"""
from pathlib import Path
from typing import Iterator

try:
    from compression import zstd
except ImportError:  # Python < 3.14
    zstd = None

available = zstd is not None
_MAGIC = b'\x28\xb5\x2f\xfd'
_MAX_PROBES = 64


def _complete_frame_follows(rest: bytes) -> bool:
    """True when a whole frame starts somewhere after the first byte of `rest`.
    Used only to tell a torn tail (nothing complete follows) from a corrupt frame
    in the middle of the log (a complete frame follows it)."""
    offset = rest.find(_MAGIC, 1)
    probes = 0
    while offset >= 0 and probes < _MAX_PROBES:
        decompressor = zstd.ZstdDecompressor()
        try:
            decompressor.decompress(rest[offset:])
            if decompressor.eof:
                return True
        except zstd.ZstdError:
            pass
        probes += 1
        offset = rest.find(_MAGIC, offset + 1)
    return False


def iter_frames(data: bytes) -> Iterator[bytes]:
    if not available:
        raise RuntimeError('compression.zstd is unavailable')
    rest = data
    while rest:
        decompressor = zstd.ZstdDecompressor()
        try:
            out = decompressor.decompress(rest)
        except zstd.ZstdError as exc:
            raise ValueError('invalid zstd frame') from exc
        if not decompressor.eof:
            if _complete_frame_follows(rest):
                raise ValueError('invalid zstd frame before the end of the log')
            return  # torn tail: incomplete last frame
        yield out
        rest = decompressor.unused_data


def read_text(path: Path) -> str:
    return b''.join(iter_frames(path.read_bytes())).decode('utf-8', errors='replace')
