#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""A small cross-platform GUI for the tested webOS 26 SlopBro flow.

The wizard deliberately keeps the two decisions that only the owner can make
on the television: accepting SSAP pairing and confirming that Homebrew Channel
shows "Root OK". Everything else is performed locally and logged.
"""

from __future__ import print_function

import argparse
import json
import os
import queue
import socket
import sys
import threading
import time
from urllib.parse import urlparse
from urllib.request import urlopen

import slopbro


APP_NAME = "webOS Root Wizard"
VOICE_APP = "com.webos.app.voiceweb"
DEVELOPER_MODE_APP = "com.palmdts.devmode"
HOMEBREW_APP = "org.webosbrew.hbchannel"
COMPAT_FILES = [
    "run-webos26-compatible.html",
    "package-webos26.json",
    "services.json",
    "exploit-webos26.js",
    "autoroot-webos26.sh",
]
DISCOVERY_TARGETS = (
    "urn:lge-com:service:webos-second-screen:1",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
)


def bundle_root():
    """Return the checkout root or PyInstaller's extracted data directory."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def app_data_dir():
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def valid_ipv4(value):
    try:
        socket.inet_aton(value)
    except OSError:
        return False
    return value.count(".") == 3


def parse_ssdp_location(payload):
    """Return a response LOCATION host, or None for an unrelated response."""
    if isinstance(payload, bytes):
        text = payload.decode("latin-1", "replace")
    else:
        text = payload
    headers = {}
    for line in text.replace("\r\n", "\n").split("\n")[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    location = headers.get("location")
    if not location:
        return None
    host = urlparse(location).hostname
    return host if host and valid_ipv4(host) else None


def discover_tvs(timeout=3.5):
    """Discover likely LG TVs through SSDP without scanning the LAN."""
    found = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.35)
    try:
        for target in DISCOVERY_TARGETS:
            request = (
                "M-SEARCH * HTTP/1.1\r\n"
                "HOST: 239.255.255.250:1900\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 2\r\n"
                "ST: %s\r\n\r\n" % target
            ).encode("ascii")
            sock.sendto(request, ("239.255.255.250", 1900))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, address = sock.recvfrom(65535)
            except socket.timeout:
                continue
            host = parse_ssdp_location(data) or address[0]
            if valid_ipv4(host):
                found.add(host)
    finally:
        sock.close()

    # A webOS television normally exposes secure SSAP on TCP 3001. Filtering
    # avoids presenting unrelated UPnP renderers as TVs.
    verified = []
    for host in sorted(found):
        try:
            connection = socket.create_connection((host, slopbro.PORT_TLS), 0.8)
            connection.close()
            verified.append(host)
        except OSError:
            pass
    return verified


class WizardError(RuntimeError):
    pass


class RootEngine(object):
    """Network/root orchestration kept separate from the Tk interface."""

    def __init__(self, tv_ip, local_ip, logger, ask_user, status):
        self.tv_ip = tv_ip
        self.local_ip_override = local_ip or None
        self.log = logger
        self.ask_user = ask_user
        self.status = status
        self.data_dir = app_data_dir()
        self.wwwroot = os.path.join(bundle_root(), "wwwroot")

    @property
    def key_file(self):
        return os.path.join(self.data_dir, "%s.key" % self.tv_ip)

    def load_key(self):
        try:
            with open(self.key_file, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""

    def save_key(self, key):
        with open(self.key_file, "w", encoding="utf-8") as handle:
            handle.write(key)
        try:
            os.chmod(self.key_file, 0o600)
        except OSError:
            pass

    def connect(self):
        self.log("Подключаюсь к телевизору по защищённому локальному каналу…")
        source = (self.local_ip, 0)
        stored_key = self.load_key()
        for attempt in range(2):
            ws = slopbro.WebSocket.connect(
                self.tv_ip,
                slopbro.PORT_TLS,
                secure=True,
                source_address=source,
            )
            client = slopbro.SSAPClient(ws)
            if not stored_key:
                self.status("Подтвердите подключение на телевизоре")
                self.log("На экране ТВ нажмите «Разрешить» при запросе подключения.")
            try:
                new_key = client.register(stored_key, slopbro.DEFAULT_MANIFEST)
                break
            except Exception:
                client.close()
                if not stored_key or attempt:
                    raise
                self.log("Сохранённый ключ устарел; запрашиваю новое сопряжение.")
                stored_key = ""
                try:
                    os.unlink(self.key_file)
                except OSError:
                    pass
        if new_key and new_key != stored_key:
            self.save_key(new_key)
            self.log(
                "Сопряжение подтверждено; ключ сохранён только на этом компьютере."
            )
        else:
            self.log("Сопряжение подтверждено.")
        return client

    @property
    def local_ip(self):
        return slopbro.guess_local_ip(self.tv_ip, self.local_ip_override)

    def list_app_ids(self, client):
        response = client.request(
            "ssap://com.webos.applicationManager/listApps", {}, timeout=10.0
        )
        if response.get("type") == "error":
            raise WizardError(
                "Телевизор не разрешил получить список приложений: %s"
                % response.get("error", "неизвестная ошибка")
            )
        apps = (response.get("payload") or {}).get("apps") or []
        return set(
            app.get("id") for app in apps if isinstance(app, dict) and app.get("id")
        )

    def check_prerequisites(self):
        self.status("Проверяю телевизор")
        client = self.connect()
        try:
            app_ids = self.list_app_ids(client)
        finally:
            client.close()
        if VOICE_APP not in app_ids:
            raise WizardError(
                "На телевизоре не найден системный Voice Assistant (voiceweb). "
                "Этот мастер не может безопасно продолжить."
            )
        if DEVELOPER_MODE_APP in app_ids:
            raise WizardError(
                "На телевизоре установлен LG Developer Mode. Удалите это приложение "
                "обычным способом и снова нажмите «Начать root»."
            )
        if HOMEBREW_APP in app_ids:
            raise WizardError(
                "Homebrew Channel уже установлен. Сначала откройте его и проверьте "
                "статус Root. Если Root OK отсутствует, не перезагружайте ТВ и "
                "сохраните журнал для диагностики вместо повторного root."
            )
        self.log("Voice Assistant найден; Developer Mode не установлен.")

    def validate_assets(self, names):
        missing = [
            name
            for name in names
            if not os.path.isfile(os.path.join(self.wwwroot, name))
        ]
        if missing:
            raise WizardError("В сборке отсутствуют файлы: %s" % ", ".join(missing))

    def start_server(self, names):
        self.validate_assets(names)
        tracker = slopbro.RequestedFilesTracker(names)
        server = slopbro.start_http_server(
            self.wwwroot,
            tracker,
            names,
            allow_embedded_assets=False,
            allow_filesystem_fallback=True,
            bind_host="0.0.0.0",
            preferred_port=8080,
        )
        return server, tracker

    @staticmethod
    def stop_server(server):
        if server is None:
            return
        try:
            server.shutdown()
        finally:
            server.server_close()

    def primary_attempt(self):
        names = slopbro.required_files()
        server = None
        client = None
        try:
            server, tracker = self.start_server(names)
            page_url = slopbro.build_self_hosted_url(
                self.tv_ip,
                server.server_port,
                debug=True,
                local_ip_override=self.local_ip,
            )
            self.log("Основной загрузчик готов: %s" % page_url)
            client = self.connect()
            slopbro.launch_app(client, VOICE_APP, {"URL": page_url})
            self.log("Основной загрузчик открыт на телевизоре.")
            client.close()
            client = None
            self.status("Передаю файлы — не выключайте ТВ")
            if tracker.wait_for_all(120):
                self.log("Телевизор получил все файлы основного способа.")
                return True
            self.log(
                "Основной способ не завершился; автоматически включаю совместимый."
            )
            self.log("Не получены: %s" % ", ".join(tracker.missing_files()))
            return False
        finally:
            if client is not None:
                client.close()
            self.stop_server(server)

    def compatibility_attempt(self, wait_seconds):
        server = None
        client = None
        try:
            server, tracker = self.start_server(COMPAT_FILES)
            page_url = "http://%s:%d/run-webos26-compatible.html?attempt=%d" % (
                self.local_ip,
                server.server_port,
                int(time.time() * 1000),
            )
            self.log("Совместимый загрузчик готов: %s" % page_url)
            client = self.connect()
            # The tested webOS 26 firmware is more reliable when voiceweb is
            # activated before its URL is changed.
            slopbro.launch_app(client, VOICE_APP, {})
            time.sleep(1.0)
            slopbro.launch_app(client, VOICE_APP, {"URL": page_url})
            client.close()
            client = None
            self.status("Совместимый способ передаёт файлы")
            if tracker.wait_for_all(wait_seconds):
                self.log("Телевизор получил все файлы совместимого способа.")
                return True
            self.log("Не получены: %s" % ", ".join(tracker.missing_files()))
            return False
        finally:
            if client is not None:
                client.close()
            self.stop_server(server)

    def wait_for_homebrew(self, seconds=70):
        self.status("Жду установки Homebrew Channel")
        deadline = time.time() + seconds
        while time.time() < deadline:
            client = None
            try:
                client = self.connect()
                app_ids = self.list_app_ids(client)
                if HOMEBREW_APP in app_ids:
                    self.log("Homebrew Channel появился в списке приложений.")
                    slopbro.launch_app(client, HOMEBREW_APP, {})
                    return True
            except Exception as exc:
                self.log("Ожидание Homebrew: %s" % exc)
            finally:
                if client is not None:
                    client.close()
            time.sleep(5)
        return False

    def run(self):
        # Route selection works with VPN enabled: the kernel chooses the local
        # interface that actually reaches this TV, and that address is also used
        # as the source of SSAP connections.
        self.log("IP телевизора: %s" % self.tv_ip)
        self.log("Локальный IP компьютера: %s" % self.local_ip)
        self.check_prerequisites()

        self.status("Запускаю основной способ")
        transferred = self.primary_attempt()
        if not transferred:
            self.status("Переключаюсь на совместимый способ")
            transferred = self.compatibility_attempt(40)
            if not transferred:
                proceed = self.ask_user(
                    "Нужно открыть Voice Assistant",
                    "На телевизоре зажмите кнопку микрофона/AI, откройте панель "
                    "Voice Assistant и оставьте её на экране. Затем нажмите "
                    "«Продолжить».",
                )
                if not proceed:
                    raise WizardError("Остановлено пользователем до повторной попытки.")
                transferred = self.compatibility_attempt(100)

        if not transferred:
            raise WizardError(
                "Телевизор не запросил все файлы. Не перезагружайте ТВ; сохраните "
                "журнал и повторите после открытия Voice Assistant."
            )

        self.log("Установщик запущен. Жду появления Homebrew Channel…")
        if not self.wait_for_homebrew():
            raise WizardError(
                "Homebrew Channel не появился за отведённое время. Не перезагружайте "
                "телевизор и сохраните журнал для диагностики."
            )

        root_ok = self.ask_user(
            "Проверьте Root OK",
            "Homebrew Channel открыт. В его настройках найдите статус Root. "
            "Нажмите «Продолжить» только если там зелёный статус Root OK.\n\n"
            "Если Root OK нет, нажмите «Отмена» и НЕ перезагружайте телевизор.",
        )
        if not root_ok:
            raise WizardError(
                "Homebrew установлен, но Root OK не подтверждён. Не перезагружайте ТВ."
            )
        self.status("Готово — Root OK подтверждён")
        self.log(
            "Готово. После Root OK телевизор можно перезагрузить из Homebrew Channel."
        )


def self_test():
    root = bundle_root()
    wwwroot = os.path.join(root, "wwwroot")
    names = list(dict.fromkeys(slopbro.required_files() + COMPAT_FILES))
    missing = [
        name for name in names if not os.path.isfile(os.path.join(wwwroot, name))
    ]
    if missing:
        print("[error] Missing assets: %s" % ", ".join(missing))
        return 1

    # Validate the SSDP parser and every JSON asset.
    sample = "HTTP/1.1 200 OK\r\nLOCATION: http://192.168.1.11:3000/device.xml\r\n\r\n"
    if parse_ssdp_location(sample) != "192.168.1.11":
        print("[error] SSDP parser self-test failed")
        return 1
    for name in names:
        if name.endswith(".json"):
            with open(os.path.join(wwwroot, name), "r", encoding="utf-8") as handle:
                json.load(handle)

    # Serve and fetch every bundled payload only on loopback. No TV is touched.
    tracker = slopbro.RequestedFilesTracker(names)
    server = slopbro.start_http_server(
        wwwroot,
        tracker,
        names,
        allow_embedded_assets=False,
        allow_filesystem_fallback=True,
        bind_host="127.0.0.1",
        preferred_port=0,
    )
    try:
        for name in names:
            with urlopen(
                "http://127.0.0.1:%d/%s" % (server.server_port, name), timeout=3
            ) as response:
                if response.status != 200 or not response.read():
                    raise RuntimeError("asset did not load: %s" % name)
        if not tracker.wait_for_all(1):
            raise RuntimeError("not all assets were tracked")
    finally:
        server.shutdown()
        server.server_close()
    print("[ok] GUI engine self-test passed; no TV was contacted")
    return 0


def run_gui(default_tv_ip=""):
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk

    class WizardApp(object):
        def __init__(self, root):
            self.root = root
            self.root.title(APP_NAME)
            self.root.geometry("760x610")
            self.root.minsize(680, 520)
            self.worker = None
            self.messages = queue.Queue()
            self.log_path = os.path.join(app_data_dir(), "wizard.log")

            frame = ttk.Frame(root, padding=22)
            frame.pack(fill="both", expand=True)
            ttk.Label(
                frame, text="webOS 26 Root Wizard", font=("TkDefaultFont", 22, "bold")
            ).pack(anchor="w")
            ttk.Label(
                frame,
                text="Автоматический мастер для проверенного SlopBro-сценария webOS 26",
            ).pack(anchor="w", pady=(3, 18))

            address = ttk.LabelFrame(frame, text="Телевизор", padding=12)
            address.pack(fill="x")
            row = ttk.Frame(address)
            row.pack(fill="x")
            ttk.Label(row, text="IP телевизора:").pack(side="left")
            self.tv_ip = tk.StringVar(value=default_tv_ip)
            self.ip_entry = ttk.Entry(row, textvariable=self.tv_ip, width=22)
            self.ip_entry.pack(side="left", padx=(8, 8), fill="x", expand=True)
            self.find_button = ttk.Button(
                row, text="Найти автоматически", command=self.find_tv
            )
            self.find_button.pack(side="left")

            advanced = ttk.Frame(address)
            advanced.pack(fill="x", pady=(10, 0))
            ttk.Label(advanced, text="IP компьютера (необязательно):").pack(side="left")
            self.local_ip = tk.StringVar()
            ttk.Entry(advanced, textvariable=self.local_ip, width=22).pack(
                side="left", padx=8
            )
            ttk.Label(advanced, text="оставьте пустым для автоопределения").pack(
                side="left"
            )

            self.status_var = tk.StringVar(value="Готов к запуску")
            ttk.Label(
                frame, textvariable=self.status_var, font=("TkDefaultFont", 12, "bold")
            ).pack(anchor="w", pady=(18, 6))
            self.progress = ttk.Progressbar(frame, mode="indeterminate")
            self.progress.pack(fill="x")

            self.log_box = scrolledtext.ScrolledText(
                frame, height=15, wrap="word", state="disabled"
            )
            self.log_box.pack(fill="both", expand=True, pady=(12, 10))

            controls = ttk.Frame(frame)
            controls.pack(fill="x")
            self.start_button = ttk.Button(
                controls, text="Начать root", command=self.start_root
            )
            self.start_button.pack(side="right")
            ttk.Button(
                controls, text="Открыть папку журнала", command=self.open_log_folder
            ).pack(side="left")

            ttk.Label(
                frame,
                text=(
                    "Потребуется только разрешить сопряжение на ТВ и подтвердить "
                    "зелёный Root OK."
                ),
            ).pack(anchor="w", pady=(12, 0))
            self.root.after(100, self.drain_messages)
            self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        def enqueue(self, kind, value):
            self.messages.put((kind, value))

        def drain_messages(self):
            try:
                while True:
                    kind, value = self.messages.get_nowait()
                    if kind == "log":
                        timestamp = time.strftime("%H:%M:%S")
                        line = "[%s] %s\n" % (timestamp, value)
                        self.log_box.configure(state="normal")
                        self.log_box.insert("end", line)
                        self.log_box.see("end")
                        self.log_box.configure(state="disabled")
                        try:
                            with open(self.log_path, "a", encoding="utf-8") as handle:
                                handle.write(line)
                        except OSError:
                            pass
                    elif kind == "status":
                        self.status_var.set(value)
                    elif kind == "busy":
                        self.set_busy(value)
                    elif kind == "error":
                        messagebox.showerror("Мастер остановлен", value)
                    elif kind == "success":
                        messagebox.showinfo("Готово", value)
                    elif kind == "set_ip":
                        self.tv_ip.set(value)
                    elif kind == "ask":
                        title, message, event, result = value
                        result["value"] = messagebox.askokcancel(
                            title, message, default="ok"
                        )
                        event.set()
            except queue.Empty:
                pass
            self.root.after(100, self.drain_messages)

        def set_busy(self, busy):
            state = "disabled" if busy else "normal"
            self.start_button.configure(state=state)
            self.find_button.configure(state=state)
            self.ip_entry.configure(state=state)
            if busy:
                self.progress.start(12)
            else:
                self.progress.stop()

        def ask_from_worker(self, title, message):
            event = threading.Event()
            result = {"value": False}
            self.enqueue("ask", (title, message, event, result))
            event.wait()
            return result["value"]

        def find_tv(self):
            self.set_busy(True)
            self.status_var.set("Ищу LG TV в локальной сети…")

            def work():
                try:
                    televisions = discover_tvs()
                    if not televisions:
                        self.enqueue(
                            "error",
                            "Телевизор не найден автоматически. "
                            "Введите его IP вручную.",
                        )
                        self.enqueue("status", "Введите IP телевизора")
                    elif len(televisions) == 1:
                        self.enqueue("set_ip", televisions[0])
                        self.enqueue("log", "Найден телевизор: %s" % televisions[0])
                        self.enqueue("status", "Телевизор найден")
                    else:
                        self.enqueue("set_ip", televisions[0])
                        self.enqueue(
                            "log", "Найдены устройства: %s" % ", ".join(televisions)
                        )
                        self.enqueue("status", "Найдено несколько ТВ; выбран первый")
                except Exception as exc:
                    self.enqueue("error", "Не удалось выполнить поиск: %s" % exc)
                finally:
                    self.enqueue("busy", False)

            self.worker = threading.Thread(target=work, daemon=True)
            self.worker.start()

        def start_root(self):
            tv_ip = self.tv_ip.get().strip()
            local_ip = self.local_ip.get().strip()
            if not valid_ipv4(tv_ip):
                messagebox.showerror(
                    "Неверный адрес", "Введите корректный IPv4 телевизора."
                )
                return
            if local_ip and not valid_ipv4(local_ip):
                messagebox.showerror(
                    "Неверный адрес",
                    "IP компьютера должен быть корректным IPv4 или пустым.",
                )
                return
            accepted = messagebox.askokcancel(
                "Экспериментальный root",
                "Мастер предназначен для вашего собственного LG webOS 26. "
                "Метод проверен на OLED77G6RLA, прошивке 43.11.78; на других "
                "прошивках результат не гарантируется.\n\nНе выключайте и не "
                "перезагружайте ТВ до зелёного Root OK. Продолжить?",
            )
            if not accepted:
                return
            self.set_busy(True)
            self.status_var.set("Начинаю проверку")
            self.enqueue("log", "--- Новая попытка ---")

            def work():
                previous_log = slopbro.log
                slopbro.log = lambda text: self.enqueue("log", text)
                try:
                    engine = RootEngine(
                        tv_ip,
                        local_ip,
                        lambda text: self.enqueue("log", text),
                        self.ask_from_worker,
                        lambda text: self.enqueue("status", text),
                    )
                    engine.run()
                    self.enqueue(
                        "success",
                        "Root OK подтверждён. Теперь телевизор можно перезагрузить "
                        "через Homebrew Channel.",
                    )
                except Exception as exc:
                    self.enqueue("status", "Остановлено — проверьте журнал")
                    self.enqueue("log", "ОШИБКА: %s" % exc)
                    self.enqueue("error", str(exc))
                finally:
                    slopbro.log = previous_log
                    self.enqueue("busy", False)

            self.worker = threading.Thread(target=work, daemon=True)
            self.worker.start()

        def open_log_folder(self):
            folder = app_data_dir()
            try:
                if sys.platform == "darwin":
                    import subprocess

                    subprocess.Popen(["open", folder])
                elif os.name == "nt":
                    os.startfile(folder)
                else:
                    import subprocess

                    subprocess.Popen(["xdg-open", folder])
            except Exception as exc:
                messagebox.showerror("Не удалось открыть папку", str(exc))

        def on_close(self):
            if self.worker is not None and self.worker.is_alive():
                if not messagebox.askyesno(
                    "Мастер ещё работает",
                    "Закрытие прервёт локальный мастер. "
                    "Телевизор не выключайте. Закрыть?",
                ):
                    return
            self.root.destroy()

    root = tk.Tk()
    WizardApp(root)
    root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="validate bundled assets without contacting a TV",
    )
    parser.add_argument(
        "--tv-ip", default="", help="pre-fill the television IPv4 address"
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    run_gui(args.tv_ip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
