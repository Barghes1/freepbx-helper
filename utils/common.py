import re
from typing import List, Tuple
import hashlib
import string
from typing import Optional
import secrets

PAGE_SIZE = 25

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

def _slice_pairs(pairs, page: int, page_size: int = PAGE_SIZE):
    total = len(pairs)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    start = page * page_size
    end = start + page_size
    return pairs[start:end], page, pages

def _profile_key(base_url: str, client_id: str) -> str:
    return hashlib.sha1(f"{base_url}|{client_id}".encode()).hexdigest()[:10]

def _gen_secret(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "._-@"
    return "".join(secrets.choice(alphabet) for _ in range(length))

def _ext_to_slot(ext: str) -> Optional[int]:
    try:
        n = int(ext)
        s = n % 100
        return s if 1 <= s <= 32 else None
    except Exception:
        return None