"""Read logs written as a series of independent zstd frames (one per flush).

Frames are walked by the decompressor itself, never by searching for the
zstd magic number: compressed or raw-stored payloads can contain those four
bytes by chance. A frame that does not reach its end is a torn tail (the
writer is mid-flush) and is skipped; it is read whole on a later pass. A
damaged frame followed by a complete frame is skipped and reported.
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


def _complete_frame_follows(rest: bytes) -> int | None:
    """Return the offset of a complete frame after the first byte, if any."""
    offset = rest.find(_MAGIC, 1)
    probes = 0
    while offset >= 0 and probes < _MAX_PROBES:
        decompressor = zstd.ZstdDecompressor()
        try:
            decompressor.decompress(rest[offset:])
            if decompressor.eof:
                return offset
        except zstd.ZstdError:
            pass
        probes += 1
        offset = rest.find(_MAGIC, offset + 1)
    return None


def iter_frames(data: bytes, on_skip=None) -> Iterator[bytes]:
    if not available:
        raise RuntimeError('compression.zstd is unavailable')
    rest = data
    while rest:
        decompressor = zstd.ZstdDecompressor()
        try:
            out = decompressor.decompress(rest)
        except zstd.ZstdError as exc:
            offset = _complete_frame_follows(rest)
            if offset is None:
                raise ValueError('invalid zstd frame') from exc
            if on_skip:
                on_skip()
            rest = rest[offset:]
            continue
        if not decompressor.eof:
            offset = _complete_frame_follows(rest)
            if offset is None:
                return  # torn tail: incomplete last frame
            if on_skip:
                on_skip()
            rest = rest[offset:]
            continue
        yield out
        rest = decompressor.unused_data


def read_text(path: Path, on_skip=None) -> str:
    return b''.join(iter_frames(path.read_bytes(), on_skip=on_skip)).decode('utf-8', errors='replace')
