import re
import paramiko
from typing import Optional, Tuple, List, Dict
import io
import paramiko

class SSHExecError(Exception):
    pass


def _ssh_run(host: str, username: str, password: str, command: str, port: int = 22, timeout: int = 10) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            auth_timeout=timeout,
        )
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode(errors="ignore")
        err = stderr.read().decode(errors="ignore")
        if err and not out:
            raise SSHExecError(err.strip()[:400])
        return out
    finally:
        try:
            client.close()
        except Exception:
            pass


def _normalize_ssh_host(raw: str) -> Tuple[str, int]:
    s = raw.strip()
    s = re.sub(r'^\s*ssh://', '', s, flags=re.I)
    s = re.sub(r'^\s*https?://', '', s, flags=re.I)
    s = s.strip().rstrip('/')
    if ':' in s:
        host, port_str = s.rsplit(':', 1)
        try:
            return host.strip(), int(port_str)
        except Exception:
            return s, 22
    return s, 22

# ---------- Разбор вывода PJSIP ----------

_CONTACT_RE = re.compile(
    r"Contact:\s+\S+/sip:[^@\s]+@(?P<ip>\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?\b",
    re.IGNORECASE,
)
_MATCH_RE = re.compile(
    r"Match:\s+(?P<ip>\d{1,3}(?:\.\d{1,3}){3})(?:/\d+)?",
    re.IGNORECASE,
)


def parse_ips_from_endpoint(text: str) -> List[str]:
    seen = set()
    out: List[str] = []
    for m in _CONTACT_RE.finditer(text):
        ip = m.group("ip")
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    for m in _MATCH_RE.finditer(text):
        ip = m.group("ip")
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def fetch_goip_ips_via_ssh(
    host: str,
    username: str,
    password: str,
    endpoint_primary: str = "goip32sell",
    endpoint_incoming: str = "goip32sell_incoming",
    port: int = 22,
    timeout: int = 10,
) -> Tuple[Optional[str], List[str]]:
    h, p = _normalize_ssh_host(host)
    if port:
        p = port

    cmds = [
        f'asterisk -rx "pjsip show endpoint {endpoint_primary}"',
        f'asterisk -rx "pjsip show endpoint {endpoint_incoming}"',
        f'asterisk -rx "pjsip show contacts" | grep -i {endpoint_primary} || true',
    ]

    ips: List[str] = []
    for cmd in cmds:
        try:
            out = _ssh_run(h, username, password, cmd, port=p, timeout=timeout)
        except Exception:
            continue
        if not out:
            continue
        found = parse_ips_from_endpoint(out)
        if not found and "contacts" in cmd.lower():
            found = [m.group("ip") for m in _CONTACT_RE.finditer(out)]
        for ip in found:
            if ip not in ips:
                ips.append(ip)
        if ips:
            break

    best = ips[0] if ips else None
    return best, ips


def fetch_pjsip_endpoints_via_ssh(
    host: str, username: str, password: str, port: Optional[int] = None, timeout: int = 10
) -> List[str]:
    h, p = _normalize_ssh_host(host)
    if port:
        p = port
    out = _ssh_run(
        h, username, password, 'asterisk -rx "pjsip show endpoints"', port=p, timeout=timeout
    )
    names = []
    for line in out.splitlines():
        m = re.search(r'^\s*Endpoint:\s+(\S+)', line, flags=re.I)
        if m:
            names.append(m.group(1))
    return names


def fetch_endpoint_raw_via_ssh(
    host: str, username: str, password: str, endpoint: str, port: Optional[int] = None, timeout: int = 10
) -> str:
    h, p = _normalize_ssh_host(host)
    if port:
        p = port
    cmd = f'asterisk -rx "pjsip show endpoint {endpoint}"'
    return _ssh_run(h, username, password, cmd, port=p, timeout=timeout)

def _sftp_open_ro(sftp: paramiko.SFTPClient, path: str) -> Optional[io.BytesIO]:
    try:
        with sftp.file(path, "rb") as f:
            return io.BytesIO(f.read())
    except FileNotFoundError:
        return None
    

def _ssh_run_mysql_single(host: str, username: str, password: str, sql: str,
                          port: int = 22, timeout: int = 10) -> str:
    """
    Выполнить один SQL-запрос на FreePBX-хосте через mysql CLI.
    Берём AMPDB-креды из /etc/freepbx.conf (поддержка ' и ").
    """
    import shlex
    bash = r"""
AMPDBUSER=$(awk -F"['\"]" '/AMPDBUSER/{print $4}' /etc/freepbx.conf)
AMPDBPASS=$(awk -F"['\"]" '/AMPDBPASS/{print $4}' /etc/freepbx.conf)
AMPDBNAME=$(awk -F"['\"]" '/AMPDBNAME/{print $4}' /etc/freepbx.conf)
if [ -z "$AMPDBUSER" ] || [ -z "$AMPDBPASS" ] || [ -z "$AMPDBNAME" ]; then
  echo "AMPDB vars not found" >&2
  exit 2
fi
mysql -N -B --user="$AMPDBUSER" --password="$AMPDBPASS" "$AMPDBNAME" -e %s
""" % (shlex.quote(sql),)
    return _ssh_run(host, username, password, bash, port=port, timeout=timeout)


def set_incoming_trunk_sip_server_via_ssh(host: str, username: str, password: str,
                                          trunk_name: str = "goip32sell_incoming",
                                          new_ip: str = "1.2.3.4",
                                          port: int = 22, timeout: int = 10) -> dict:
    """
    1) Находим id транка по trunk_name/sv_trunk_name в таблице pjsip.
    2) Узнаём существующий формат значения sip_server.
    3) Обновляем sip_server значением new_ip (у вас — чистый IP).
    4) Делаем fwconsole reload.
    Возвращает краткий отчёт.
    """
    # 1) Найти id по trunk_name (если нет — по sv_trunk_name)
    sql_find_id = (
        "SELECT id FROM pjsip "
        f"WHERE (keyword='trunk_name' OR keyword='sv_trunk_name') AND data='{trunk_name}' "
        "ORDER BY (keyword='trunk_name') DESC LIMIT 1;"
    )
    out = _ssh_run_mysql_single(host, username, password, sql_find_id, port=port, timeout=timeout).strip()
    if not out:
        raise SSHExecError(f"Не найден id для транка '{trunk_name}' в pjsip.")
    trunk_id = out.splitlines()[0].strip()

    # 2) Текущая строка sip_server (чтобы показать старое значение/проверить формат)
    sql_cur = f"SELECT data FROM pjsip WHERE id={trunk_id} AND keyword='sip_server' LIMIT 1;"
    cur_val = _ssh_run_mysql_single(host, username, password, sql_cur, port=port, timeout=timeout).strip()

    # 3) Обновить (в вашей схеме sip_server — это просто IP)
    sql_upd = f"UPDATE pjsip SET data='{new_ip}' WHERE id={trunk_id} AND keyword='sip_server';"
    _ssh_run_mysql_single(host, username, password, sql_upd, port=port, timeout=timeout)

    # 4) Применить конфиг
    _ssh_run(host, username, password, "fwconsole reload", port=port, timeout=timeout)

    # 5) Проверка/возврат нового значения
    new_val = _ssh_run_mysql_single(host, username, password, sql_cur, port=port, timeout=timeout).strip()
    return {
        "trunk_id": trunk_id,
        "trunk_name": trunk_name,
        "old_value": cur_val or "<empty>",
        "new_value": new_val or "<empty>",
    }


def _sql_escape(val: str) -> str:
    return val.replace("\\", "\\\\").replace("'", "\\'")

def set_extension_chansip_secret_via_ssh(
    host: str,
    username: str,
    password: str,
    extension: str,
    new_secret: str,
    *,
    port: int = 22,
    timeout: int = 10,
    do_reload: bool = True,  # ← добавили
) -> dict:
    """
    Меняет пароль у EXT для chan_sip:
      sip: id='<ext>', keyword='secret'
    md5_cred не трогаем (если заполнен, клиент может использовать его).
    Если do_reload=False — не делает fwconsole reload (удобно для батча).
    """
    h, p = _normalize_ssh_host(host)
    if port:
        p = port

    if not re.fullmatch(r"[A-Za-z0-9._\-@]+", new_secret):
        raise SSHExecError("Недопустимый формат пароля (разрешены буквы/цифры/._-@)")
    esc = _sql_escape(new_secret)
    ext = str(int(extension))

    # старое значение (для отчёта)
    sql_read = f"SELECT data FROM sip WHERE id='{ext}' AND keyword='secret' LIMIT 1;"
    cur = _ssh_run_mysql_single(h, username, password, sql_read, port=p, timeout=timeout).strip()
    old_value = cur.splitlines()[0].strip() if cur else ""

    # md5_cred присутствует?
    sql_md5 = f"SELECT data FROM sip WHERE id='{ext}' AND keyword='md5_cred' LIMIT 1;"
    md5 = _ssh_run_mysql_single(h, username, password, sql_md5, port=p, timeout=timeout).strip()
    md5_present = bool(md5.splitlines()[0].strip()) if md5 else False

    # пишем/обновляем secret
    sql_write = (
        f"INSERT INTO sip (id,keyword,data) VALUES ('{ext}','secret','{esc}') "
        "ON DUPLICATE KEY UPDATE data=VALUES(data);"
    )
    _ssh_run_mysql_single(h, username, password, sql_write, port=p, timeout=timeout)

    # применяем конфиг (по желанию)
    if do_reload:
        _ssh_run(h, username, password, "fwconsole reload", port=p, timeout=max(timeout, 30))

    return {
        "ext": ext,
        "old_value": old_value or "<empty>",
        "new_value": new_secret,
        "tech": "chan_sip",
        "md5_present": md5_present,
    }
    
# ВНИЗ ФАЙЛА core/asterisk.py (рядом с set_incoming_trunk_sip_server_via_ssh и set_extension_chansip_secret_via_ssh)
def _parse_range(s: str) -> list[str]:
    """
    '001-032' -> ['001','002',...,'032']
    допускает также одиночное '007'
    """
    s = s.strip()
    if "-" in s:
        a, b = s.split("-", 1)
        a = a.strip(); b = b.strip()
        w = max(len(a), len(b))
        ia, ib = int(a), int(b)
        if ia > ib:
            ia, ib = ib, ia
        return [str(i).zfill(w) for i in range(ia, ib + 1)]
    else:
        return [s]


def create_outbound_route_with_ranges_via_ssh(
    host: str,
    username: str,
    password: str,
    *,
    route_name: str,
    # Диапазоны
    prepend_range: str,         # напр. '001-032'
    callerid_range: str = None, # если None — берём тот же диапазон, что и prepend_range
    # Паттерны пачек:
    pattern_first: str = "X.",     # первая пачка — с плюсиком в конце prepend
    pattern_second: str = "XXXX",  # вторая пачка — без плюсика
    # Транки (по имени), опционально
    trunk_names: list[str] | None = None,
    # Прочее
    port: int = 22,
    timeout: int = 15,
) -> dict:
    """
    Создаёт outbound route и добавляет два набора dial patterns:
      1) prepend='NNN+'  pattern=pattern_first,  callerid=NNN
      2) prepend='NNN'   pattern=pattern_second, callerid=NNN
    prefix='' (пусто). Транки (если заданы) привязываются по имени в порядке.
    Делает fwconsole reload в конце.
    """
    h, p = _normalize_ssh_host(host)
    if port:
        p = port

    nums = _parse_range(prepend_range)
    cids = _parse_range(callerid_range) if callerid_range else nums
    if len(cids) != len(nums):
        raise SSHExecError("Длины диапазонов prepend_range и callerid_range не совпадают.")

    # --- ШАГ 1. Получить/создать route_id (устраняем проблемы сравнения по collation) ---
    sql_get_or_create = f"""
SET NAMES utf8mb3;
SET collation_connection = 'utf8mb3_general_ci';

SET @rname := '{_sql_escape(route_name)}';
SET @rid := (SELECT route_id FROM outbound_routes WHERE BINARY name = BINARY @rname LIMIT 1);

-- если нет — создать
INSERT INTO outbound_routes (name)
SELECT @rname FROM DUAL
WHERE @rid IS NULL;

-- зафиксировать rid после возможной вставки
SET @rid := COALESCE(@rid, LAST_INSERT_ID());

-- гарантировать присутствие в outbound_route_sequence (добавить в конец, если нет)
INSERT INTO outbound_route_sequence (route_id, seq)
SELECT @rid, COALESCE((SELECT MAX(seq)+1 FROM outbound_route_sequence), 0)
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM outbound_route_sequence WHERE route_id=@rid);

-- вернуть rid и имя
SELECT @rid AS route_id, @rname AS route_name;
""".strip()

    res = _ssh_run_mysql_single(h, username, password, sql_get_or_create, port=p, timeout=timeout).strip()

    # вытащим route_id
    route_id = None
    for line in res.splitlines():
        parts = [x.strip() for x in line.split("\t")]
        if len(parts) >= 2 and parts[0].isdigit():
            route_id = int(parts[0])
            break
    if route_id is None:
        raise SSHExecError("Не удалось получить route_id после вставки outbound_routes.")

    # --- ШАГ 2. Вставить dial patterns уже с подставленным числовым route_id ---
    # таблица: (route_id, match_pattern_prefix, match_pattern_pass, match_cid, prepend_digits)
    values = []
    esc_p1 = _sql_escape(pattern_first)
    esc_p2 = _sql_escape(pattern_second)
    for n, cid in zip(nums, cids):
        esc_cid = _sql_escape(cid)
        esc_n   = _sql_escape(n)
        # пачка 1: с плюсиком
        values.append(f"({route_id},'', '{esc_p1}', '{esc_cid}', '{esc_n}+' )")
        # пачка 2: без плюсика
        values.append(f"({route_id},'', '{esc_p2}', '{esc_cid}', '{esc_n}'  )")

    # В ряде сборок на эту таблицу навешан составной PK (все 5 полей) — подстрахуемся INSERT IGNORE.
    # Разобьём на чанк-и, чтобы не переполнить максимальную длину пакета (хотя 64 записи и так ок).
    CHUNK = 200
    for i in range(0, len(values), CHUNK):
        chunk_sql = ",\n  ".join(values[i:i+CHUNK])
        sql_ins = (
            "INSERT IGNORE INTO outbound_route_patterns "
            "(route_id, match_pattern_prefix, match_pattern_pass, match_cid, prepend_digits) VALUES\n  "
            + chunk_sql + ";"
        )
        _ssh_run_mysql_single(h, username, password, sql_ins, port=p, timeout=timeout)

    # --- ШАГ 3. Привязать транки, если заданы ---
    if trunk_names:
        trunk_names_esc = [f"'{_sql_escape(t)}'" for t in trunk_names]
        trunks_sql = f"""
DROP TEMPORARY TABLE IF EXISTS tmp_trunks;
CREATE TEMPORARY TABLE tmp_trunks AS
SELECT trunkid, name FROM trunks WHERE name IN ({", ".join(trunk_names_esc)});

INSERT INTO outbound_route_trunks (route_id, trunk_id, seq)
SELECT {route_id}, t.trunkid, FIELD(t.name, {", ".join(trunk_names_esc)}) - 1
FROM tmp_trunks t
ORDER BY FIELD(t.name, {", ".join(trunk_names_esc)});
""".strip()
        _ssh_run_mysql_single(h, username, password, trunks_sql, port=p, timeout=timeout)

    # --- ШАГ 4. Применить конфиг ---
    _ssh_run(h, username, password, "fwconsole reload", port=p, timeout=max(timeout, 30))

    return {
        "route_id": str(route_id),
        "route_name": route_name,
        "patterns_created": len(values),
        "trunks_bound": trunk_names or [],
    }
