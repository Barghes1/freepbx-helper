import re
from typing import List, Tuple


def clean_url(url: str) -> str:
    """
    Убирает протокол и слэши в конце для красивого отображения.
    http://1.2.3.4/  ->  1.2.3.4
    """
    return re.sub(r"^https?://", "", url).rstrip("/")


def equip_start(eq: int) -> int:
    """
    Расчёт стартового EXT для базы оборудования:
      1 -> 101, 2 -> 201, 3 -> 301, 4 -> 401, 10 -> 1001
    """
    return eq * 100 + 1


def parse_targets(s: str) -> List[str]:
    """
    Разбирает строку с номерами/диапазонами в список EXT.
    Пример: "401 402 410-418" -> ["401","402","410","411",...,"418"]
    """
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
    """
    Находит первые count свободных EXT, начиная со start,
    исключая те, что уже есть в existing.
    """
    taken = set(map(int, existing))
    res, cur = [], start
    while len(res) < count:
        if cur not in taken:
            res.append(str(cur))
        cur += 1
    return res


def format_list(ip: str, pairs: List[Tuple[str, str]]) -> str:
    """
    Формирует список EXT/паролей в текстовом виде.
    """
    if not pairs:
        return f"{ip}\n\n(пусто)"
    lines = [ip, ""]
    lines += [f"{ext} {pw}" for ext, pw in pairs]
    return "\n".join(lines)
