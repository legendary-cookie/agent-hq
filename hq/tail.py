import os
from pathlib import Path

def read_new_lines(path: Path, cursor: tuple[int, float] | None) -> tuple[list[str], tuple[int, float]]:
    with path.open('rb') as stream:
        stat = os.fstat(stream.fileno())
        offset = cursor[0] if cursor else 0
        if stat.st_size < offset:
            offset = 0
        stream.seek(offset)
        chunks = stream.read().split(b'\n')
        complete = chunks[:-1]
        offset += sum(len(line) + 1 for line in complete)
        return [line.decode('utf-8', errors='replace') for line in complete], (offset, stat.st_mtime)
