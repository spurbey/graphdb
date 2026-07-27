import re
from pathlib import Path

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def count(text: str) -> int:
        return len(_enc.encode(text))
except ImportError:
    def count(text: str) -> int:
        return len(text) // 4

def count_file(path: str | Path) -> int:
    return count(Path(path).read_text(encoding="utf-8", errors="replace"))

def count_lines(lines: list[str]) -> int:
    return count("".join(lines))

def count_files(glob_pattern: str, root: str | Path) -> int:
    total = 0
    for p in Path(root).rglob(glob_pattern):
        if p.is_file():
            total += count_file(p)
    return total
