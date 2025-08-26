import re
from typing import List, Tuple

def clean_url(url: str) -> str:
    return re.sub(r"^https?://", "", url).rstrip("/")

def equip_start(eq: int) -> int:
    return eq * 100 + 1

def parse_targets(s: str) -> List[str]:
    out: List[str] = []
    for tok in s.strip().split():
        if "-" in tok:
            a, b = tok.split("-", 1)
            for n in range(int(a), int(b) + 1):
                out.append(str(n))
        else:
            out.append(tok)
    return sorted(set(out), key=lambda x: int(x))


def next_free(existing: List[str], start: int, count: int) -> List[str]:
    taken = set(map(int, existing))
    res, cur = [], start
    while len(res) < count:
        if cur not in taken:
            res.append(str(cur))
        cur += 1
    return res

def format_list(ip: str, pairs: List[Tuple[str, str]]) -> str:
    if not pairs:
        return f"{ip}\n\n(пусто)"
    lines = [ip, ""]
    lines += [f"{ext} {pw}" for ext, pw in pairs]
    return "\n".join(lines)
