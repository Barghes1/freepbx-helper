import socket, sys, time

GOIP_IP = "185.191.56.153"
UDP_PORT = 5060
TCP_PORT = 5060
TIMEOUT = 5
RETRIES = 3

def sip_options_udp(ip, port=UDP_PORT, timeout=TIMEOUT, retries=RETRIES):
    msg = f"""OPTIONS sip:{ip} SIP/2.0
Via: SIP/2.0/UDP 0.0.0.0:5060;branch=z9hG4bKcheck;rport
From: <sip:probe@local>;tag=12345
To: <sip:{ip}>
Call-ID: probe-{int(time.time())}@local
CSeq: 1 OPTIONS
Max-Forwards: 70
Content-Length: 0

"""
    data = msg.encode("ascii", "ignore")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # фиксируем исходный порт 5060 — реже режут на приёмной стороне
        sock.bind(("", 5060))
    except OSError:
        # если порт 5060 занят — пусть выберется любой свободный
        pass
    sock.settimeout(timeout)

    last_err = None
    for i in range(1, retries+1):
        try:
            sock.sendto(data, (ip, port))
            resp, _ = sock.recvfrom(4096)
            text = resp.decode("ascii", "ignore")
            if "SIP/2.0 200" in text:
                print(f"[OK:UDP] Получен ответ 200 OK за попытку {i}")
                print("\n".join(text.splitlines()[:6]))
                return True
            else:
                print(f"[UDP] Ответ получен, но не 200 OK:\n{text[:200]}")
                return False
        except socket.timeout:
            print(f"[UDP] Таймаут (попытка {i}/{retries})")
            last_err = "timeout"
        except Exception as e:
            print(f"[UDP] Ошибка: {e}")
            last_err = str(e)
    print(f"[FAIL:UDP] Нет ответа по UDP/5060: {last_err}")
    return False
    sock.close()

def sip_tcp_probe(ip, port=TCP_PORT, timeout=TIMEOUT):
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.close()
        print("[OK:TCP] TCP 5060 доступен (возможно SIP по TCP)")
        return True
    except Exception as e:
        print(f"[TCP] Соединение не установлено: {e}")
        return False

if __name__ == "__main__":
    ok_udp = sip_options_udp(GOIP_IP)
    ok_tcp = sip_tcp_probe(GOIP_IP)
    if not ok_udp and not ok_tcp:
        print("\nИТОГ: устройство не отвечает с твоей сети.")
        print("Чаще всего причина — GoIP принимает SIP только от IP АТС или веб/UDP фильтруется провайдером/фаерволом.")
