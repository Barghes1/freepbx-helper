# core/goip.py
import time
import logging
from typing import Tuple
from urllib.parse import urlparse, urljoin
import base64
import requests
import re

log = logging.getLogger(__name__)

class GoipStatus:
    READY = "ready"
    UNAUTHORIZED = "unauthorized"
    ERROR = "error"

class GoIP:
    """
    Лёгкий клиент для работы с веб-мордой GOIP.
    Поддерживает базовую авторизацию (Basic-Auth).
    """
    def __init__(self, base_url: str, login: str, password: str, verify: bool = False, timeout: int = 8):
        self.base_url = base_url.rstrip("/")
        self.login = login
        self.password = password
        self.verify = verify
        self.timeout = timeout
        self.last_ok_at: float = 0.0

    @property
    def status_url(self) -> str:
        # Обычно статус доступен по /default/en_US/status.html
        parsed = urlparse(self.base_url)
        if parsed.path and parsed.path.lower().endswith(".html"):
            return self.base_url
        return urljoin(self.base_url + "/", "default/en_US/status.html")

    def _auth_header_value(self) -> str:
        for enc in ("utf-8", "latin-1"):
            try:
                raw = f"{self.login}:{self.password}".encode(enc, "strict")
                return "Basic " + base64.b64encode(raw).decode("ascii")
            except Exception:
                continue
        raise RuntimeError("Не удалось закодировать логин/пароль")

    def check_status(self) -> Tuple[str, str, int]:
        headers = {"Authorization": self._auth_header_value()}
        try:
            r = requests.get(self.status_url, headers=headers, timeout=self.timeout, verify=self.verify)
        except requests.RequestException as e:
            return GoipStatus.ERROR, f"Сетевой сбой: {e}", 0

        if r.status_code in (401, 403):
            return GoipStatus.UNAUTHORIZED, f"HTTP {r.status_code} — проверь логин/пароль.", r.status_code
        if r.status_code != 200:
            return GoipStatus.ERROR, f"Неожиданный код ответа: {r.status_code}", r.status_code

        body = (r.text or "").lower()
        if any(m in body for m in ("goip", "status", "imei", "signal", "gsm")):
            self.last_ok_at = time.time()
            return GoipStatus.READY, "GOIP готова (страница статуса доступна).", 200
        return GoipStatus.ERROR, "HTTP 200, но не похоже на статус GOIP.", 200

    def ata_in_url(self) -> str:
        """
        Страница настроек входящих (ata_in_setting).
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
        Безопасный способ: читаем всю форму, меняем только один слот, шлём полный набор всех 32 слотов.
        """
        if slot < 1 or slot > 32:
            return False, "Слот вне диапазона 1..32"

        url = self.ata_in_url()
        headers_base = {
            "Authorization": self._auth_header_value(),
            "Cache-Control": "no-cache",
        }
        headers_form = {
            **headers_base,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": url,
        }

        try:
            # 1. GET страницы формы
            gr = requests.get(url, headers=headers_base, timeout=(5, 20),
                              verify=self.verify, allow_redirects=True)
            if gr.status_code != 200:
                return False, f"Не удалось открыть форму (HTTP {gr.status_code})"
            html = gr.text

            # 2. Собираем все значения для 32 каналов
            form_data = {
                "user_noinput_t": "60",
                "cid_fw_mode": "1",
                "submit": "Save",
                "line_fw_conf_tab": f"line{slot}_fw_conf",  # активная вкладка
            }

            for i in range(1, 33):
                # fw_to_voip: radio (on/off)
                if re.search(rf'name="line{i}_fw_to_voip"\s+value="on"[^>]*checked', html, flags=re.I):
                    form_data[f"line{i}_fw_to_voip"] = "on"
                else:
                    form_data[f"line{i}_fw_to_voip"] = "off"

                # fw_num_to_voip (alias)
                m = re.search(rf'name="line{i}_fw_num_to_voip"[^>]*value="([^"]*)"', html, flags=re.I)
                form_data[f"line{i}_fw_num_to_voip"] = m.group(1) if m else ""

                # gsm_cw
                m = re.search(rf'name="line{i}_gsm_cw"[^>]*value="([^"]*)"', html, flags=re.I)
                form_data[f"line{i}_gsm_cw"] = m.group(1) if m else "0"

                # gsm_group_mode
                m = re.search(rf'name="line{i}_gsm_group_mode"[^>]*value="([^"]*)"', html, flags=re.I)
                form_data[f"line{i}_gsm_group_mode"] = m.group(1) if m else "DISABLE"

                # gsm_fw_mode
                m = re.search(rf'name="line{i}_gsm_fw_mode"[^>]*value="([^"]*)"', html, flags=re.I)
                form_data[f"line{i}_gsm_fw_mode"] = m.group(1) if m else "0"

                # auto_blacklist_in_enable
                if re.search(rf'name="line{i}_auto_blacklist_in_enable"[^>]*checked', html, flags=re.I):
                    form_data[f"line{i}_auto_blacklist_in_enable"] = "on"
                else:
                    form_data[f"line{i}_auto_blacklist_in_enable"] = "off"

            # 3. Меняем только наш слот
            if enabled:
                form_data[f"line{slot}_fw_to_voip"] = "on"
                form_data[f"line{slot}_fw_num_to_voip"] = f"sim{slot}"
            else:
                form_data[f"line{slot}_fw_to_voip"] = "off"
                form_data[f"line{slot}_fw_num_to_voip"] = ""

            # 4. POST всей формы
            pr = requests.post(url, headers=headers_form, data=form_data,
                              timeout=(5, 25), verify=self.verify, allow_redirects=True)
            if pr.status_code not in (200, 302):
                return False, f"HTTP {pr.status_code}: устройство не приняло изменения."

            return True, f"{'Включены' if enabled else 'Отключены'} входящие для слота {slot}."

        except requests.RequestException as e:
            return False, f"Сетевая ошибка: {e}"



