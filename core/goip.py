import base64
import logging
import re
import time
from http.client import RemoteDisconnected
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import (
    ConnectTimeout,
    ConnectionError as ReqConnectionError,
    ReadTimeout,
)
from urllib3.exceptions import ProtocolError
from urllib3.util.retry import Retry


log = logging.getLogger(__name__)


class GoipStatus:
    READY = "ready"
    UNAUTHORIZED = "unauthorized"
    ERROR = "error"


class GoIP:
    def __init__(self, base_url: str, login: str, password: str, verify: bool = False, timeout: int = 8):
        self.base_url = base_url.rstrip("/")
        self.login = login
        self.password = password
        self.verify = verify
        self.timeout = timeout
        self.last_ok_at: float = 0.0

    @property
    def status_url(self) -> str:
        parsed = urlparse(self.base_url)
        if parsed.path and parsed.path.lower().endswith(".html"):
            return self.base_url
        return urljoin(self.base_url + "/", "default/en_US/status.html")

    # ---------- internals ----------
    def _auth_header_variants(self) -> list[str]:
        """
        Возвращает список возможных значений заголовка Authorization (utf-8, затем latin-1).
        Нужно из-за символа '№' в пароле.
        """
        out = []
        for enc in ("utf-8", "latin-1"):
            try:
                raw = f"{self.login}:{self.password}".encode(enc, "strict")
                out.append("Basic " + base64.b64encode(raw).decode("ascii"))
            except Exception:
                pass
        return out

    def _session(self) -> requests.Session:
        """
        Сессия без keep-alive (будем слать Connection: close),
        без прокси из окружения, с мягкими ретраями на 502/503/504.
        """
        s = requests.Session()
        s.trust_env = False  # игнорировать прокси переменные окружения

        retry = Retry(
            total=2,
            backoff_factor=0.6,
            status_forcelist=(502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=4, pool_block=False)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        return s

    def _common_headers(self, referer: Optional[str] = None) -> dict:
        h = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8,ru;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "close",
        }
        if referer:
            h["Referer"] = referer
        return h

    # ---------- optional radmin warm-up ----------
    @staticmethod
    def warmup_radmin(radmin_url: str, login: str, password: str,
                      verify: bool = False, timeout: int = 5) -> tuple[bool, str]:
        """
        Короткий 'разогрев' Радмина: пробуем обратиться с Basic-Auth (utf-8/latin-1),
        затем без авторизации. Цель — чтобы Радмин зафиксировал внешний IP и
        пропустил далее к GoIP.
        """
        def _auth_variants(u, p):
            res = []
            for enc in ("utf-8", "latin-1"):
                try:
                    raw = f"{u}:{p}".encode(enc, "strict")
                    res.append("Basic " + base64.b64encode(raw).decode("ascii"))
                except Exception:
                    pass
            return res

        headers_base = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "close",
        }
        try:
            with requests.Session() as s:
                s.trust_env = False
                # 1) Пара попыток с Basic-Auth
                for ah in _auth_variants(login, password):
                    h = {**headers_base, "Authorization": ah}
                    try:
                        r = s.get(radmin_url, headers=h, timeout=(3, timeout),
                                  verify=verify, allow_redirects=True)
                        return True, f"Radmin warmup HTTP {r.status_code}"
                    except requests.RequestException:
                        continue
                # 2) Без авторизации — на всякий случай
                try:
                    r = s.get(radmin_url, headers=headers_base, timeout=(3, timeout),
                              verify=verify, allow_redirects=True)
                    return True, f"Radmin warmup (no-auth) HTTP {r.status_code}"
                except requests.RequestException as e2:
                    return False, f"Radmin warmup fail: {e2}"
        except Exception as e:
            return False, f"Radmin warmup error: {e}"

    # ---------- public API ----------
    def check_status(self) -> Tuple[str, str, int]:
        """
        Проверяет доступность /status.html. Возвращает (статус, сообщение, http_code).
        Делает короткий «разогрев» каталога, один мягкий ретрай при обрыве.
        """
        auths = self._auth_header_variants()
        if not auths:
            return GoipStatus.ERROR, "Не удалось закодировать логин/пароль (utf-8/latin-1).", 0

        url = self.status_url
        connect_to = 5
        read_to = max(self.timeout, 15)

        last_exc = None
        with self._session() as s:
            for ah in auths:
                headers = {**self._common_headers(referer=url), "Authorization": ah}
                try:
                    # «Разогрев» некоторых прошивок: дернуть корень и папку языка
                    root = url.split("/default/")[0] + "/"
                    s.get(root, headers=headers, timeout=(3, 5), verify=self.verify, allow_redirects=False)
                    s.get(root + "default/en_US/", headers=headers, timeout=(3, 5), verify=self.verify, allow_redirects=False)
                except requests.RequestException:
                    pass  # прогрев опционален

                for _ in range(2):  # одна повторная попытка при обрыве
                    try:
                        r = s.get(url, headers=headers, timeout=(connect_to, read_to),
                                  verify=self.verify, allow_redirects=False)
                        code = r.status_code
                        if code in (401, 403):
                            break  # пробуем следующую кодировку
                        if code != 200:
                            return GoipStatus.ERROR, f"Неожиданный код ответа: {code}", code

                        body = (r.text or "").lower()
                        if any(m in body for m in ("goip", "status", "imei", "signal", "module", "gsm")):
                            self.last_ok_at = time.time()
                            return GoipStatus.READY, "GOIP готова (страница статуса доступна).", 200
                        return GoipStatus.ERROR, "HTTP 200, но не похоже на статус GOIP.", 200

                    except (ReadTimeout, ConnectTimeout, ReqConnectionError, ProtocolError, RemoteDisconnected, ConnectionResetError) as e:
                        last_exc = e
                        time.sleep(0.35)

        return GoipStatus.ERROR, f"Сетевой сбой: {last_exc}", 0

    def ata_in_url(self) -> str:
        """
        URL страницы настроек входящих (ata_in_setting).
        """
        p = urlparse(self.base_url)
        root = f"{p.scheme}://{p.netloc}"
        parts = [seg for seg in p.path.strip("/").split("/") if seg]
        lang_prefix = "default/en_US"
        if len(parts) >= 2:
            lang_prefix = "/".join(parts[:2])
        return f"{root}/{lang_prefix}/config.html?type=ata_in_setting"

    def set_incoming_enabled(self, slot: int, enabled: bool) -> Tuple[bool, str]:
        """
        Безопасный способ: читаем форму, копируем значения всех 32 каналов,
        меняем только нужный слот, шлём все поля, затем валидируем изменившийся флаг.
        """
        if slot < 1 or slot > 32:
            return False, "Слот вне диапазона 1..32"

        url = self.ata_in_url()
        connect_to = 5
        read_to = max(self.timeout, 25)

        with self._session() as s:
            for ah in self._auth_header_variants():
                h_base = {**self._common_headers(referer=url), "Authorization": ah}
                try:
                    # 1) GET формы
                    gr = s.get(url, headers=h_base, timeout=(connect_to, read_to),
                               verify=self.verify, allow_redirects=True)
                    if gr.status_code in (401, 403):
                        continue  # пробуем другой вариант кодировки
                    if gr.status_code != 200:
                        return False, f"Не удалось открыть форму (HTTP {gr.status_code})"
                    html = gr.text

                    # 2) Собираем значения всех 32 слотов
                    form_data = {
                        "user_noinput_t": "60",
                        "cid_fw_mode": "1",
                        "submit": "Save",
                        "line_fw_conf_tab": f"line{slot}_fw_conf",  # активная вкладка = наш слот
                    }
                    for i in range(1, 33):
                        # radio on/off
                        form_data[f"line{i}_fw_to_voip"] = (
                            "on" if re.search(
                                rf'name="line{i}_fw_to_voip"\s+value="on"[^>]*checked',
                                html, flags=re.I
                            ) else "off"
                        )
                        # alias
                        m = re.search(rf'name="line{i}_fw_num_to_voip"[^>]*value="([^"]*)"', html, flags=re.I)
                        form_data[f"line{i}_fw_num_to_voip"] = m.group(1) if m else ""
                        # cw
                        m = re.search(rf'name="line{i}_gsm_cw"[^>]*value="([^"]*)"', html, flags=re.I)
                        form_data[f"line{i}_gsm_cw"] = m.group(1) if m else "0"
                        # group mode
                        m = re.search(rf'name="line{i}_gsm_group_mode"[^>]*value="([^"]*)"', html, flags=re.I)
                        form_data[f"line{i}_gsm_group_mode"] = m.group(1) if m else "DISABLE"
                        # fw mode
                        m = re.search(rf'name="line{i}_gsm_fw_mode"[^>]*value="([^"]*)"', html, flags=re.I)
                        form_data[f"line{i}_gsm_fw_mode"] = m.group(1) if m else "0"
                        # blacklist (checkbox)
                        form_data[f"line{i}_auto_blacklist_in_enable"] = (
                            "on" if re.search(
                                rf'name="line{i}_auto_blacklist_in_enable"[^>]*checked',
                                html, flags=re.I
                            ) else "off"
                        )

                    # 3) Меняем только нужный слот
                    if enabled:
                        form_data[f"line{slot}_fw_to_voip"] = "on"
                        if not form_data.get(f"line{slot}_fw_num_to_voip"):
                            form_data[f"line{slot}_fw_num_to_voip"] = f"sim{slot}"
                    else:
                        form_data[f"line{slot}_fw_to_voip"] = "off"
                        form_data[f"line{slot}_fw_num_to_voip"] = ""

                    # 4) POST всей формы
                    h_post = {**h_base, "Content-Type": "application/x-www-form-urlencoded"}
                    pr = s.post(url, headers=h_post, data=form_data,
                                timeout=(connect_to, read_to), verify=self.verify, allow_redirects=True)
                    if pr.status_code not in (200, 302):
                        return False, f"HTTP {pr.status_code}: устройство не приняло изменения."

                    # 5) Верификация — повторный GET и поиск checked у on-радиокнопки
                    vr = s.get(url, headers=h_base, timeout=(connect_to, read_to),
                               verify=self.verify, allow_redirects=True)
                    if vr.status_code != 200:
                        return False, f"Сохранение прошло, но проверка не удалась (HTTP {vr.status_code})."

                    pat = re.compile(
                        rf'<input\b[^>]*name="line{slot}_fw_to_voip"[^>]*value="on"[^>]*>',
                        flags=re.I
                    )
                    checked = False
                    for m in pat.finditer(vr.text):
                        if re.search(r'\bchecked\b', m.group(0), flags=re.I):
                            checked = True
                            break

                    if enabled and not checked:
                        return False, "Не удалось включить: после сохранения флаг всё ещё OFF."
                    if not enabled and checked:
                        return False, "Не удалось отключить: после сохранения флаг всё ещё ON."

                    return True, f"{'Включены' if enabled else 'Отключены'} входящие для слота {slot}."

                except (ReadTimeout, ConnectTimeout, ReqConnectionError, ProtocolError, RemoteDisconnected, ConnectionResetError) as e:
                    # мягкая пауза и попробуем другую кодировку базик-аута (если есть)
                    log.warning("GOIP network hiccup on set_incoming_enabled: %s", e)
                    time.sleep(0.35)

        return False, "Сетевая ошибка при попытке применить изменения (GOIP рвёт соединение)."
