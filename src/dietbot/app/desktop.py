"""Uruchamianie aplikacji: lokalny serwer (uvicorn na 127.0.0.1) + okno pywebview, opcjonalnie bot Telegram."""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import socket
import threading
import time
import webbrowser
from pathlib import Path

from .. import __version__

log = logging.getLogger("dietbot.desktop")
# stały port okna aplikacji: pamięć strony (odhaczone zakupy, wybrany dzień) jest przypisana do adresu z portem,
# więc losowy port przy każdym starcie kasowałby ją; gdy port jest zajęty przez inny program — bierzemy dowolny wolny
PREFERRED_PORT = 8765
CLOSE_WAIT_S = 15.0  # tyle czekamy po zamknięciu okna, aż przerwane zadanie zauważy przerwanie
_instance_mutex = None  # uchwyt nazwanego muteksu Windows — trzymany do końca procesu


def bind_server_socket(host: str = "127.0.0.1", port: int | None = None) -> socket.socket:
    """Gniazdo nasłuchujące: podany port, a bez niego stały port aplikacji z zapasem w postaci dowolnego wolnego."""
    for candidate in ([port] if port is not None else [PREFERRED_PORT, 0]):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":  # na Windows SO_REUSEADDR pozwoliłoby wejść na port zajęty przez inny program
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        elif hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((host, candidate))
            return sock
        except OSError:
            sock.close()
            if candidate == 0:
                raise
    raise OSError("brak wolnego portu")


def start_server(service, host: str = "127.0.0.1", port: int | None = None, token: str | None = None):
    """Serwer API w wątku w tle. Zwraca (serwer, wątek, port) po tym, jak zaczął przyjmować połączenia.
    `token` — tajny token sesji wymagany w nagłówku każdego żądania API (inne programy i konta na komputerze nie
    odczytają czatu, nie zmienią celów ani nie uruchomią płatnych zadań)."""
    import uvicorn

    from .api import create_app

    sock = bind_server_socket(host, port)
    port = sock.getsockname()[1]
    config = uvicorn.Config(create_app(service, session_token=token), host=host, port=port, log_level="warning",
                            access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name="dietbot-serwer", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("Nie udało się uruchomić lokalnego serwera aplikacji.")
        time.sleep(0.05)
    return server, thread, port


def start_telegram_in_background(service) -> threading.Thread | None:
    """Bot Telegram w osobnym wątku (własna pętla asyncio), jeśli w .env jest token."""
    if not os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        return None
    import asyncio

    from .telegram_bot import run_bot

    def runner() -> None:
        try:
            asyncio.run(run_bot(service))
        except Exception:  # noqa: BLE001 — bot nie może zamknąć aplikacji
            log.exception("Bot Telegram zatrzymał się z błędem")

    thread = threading.Thread(target=runner, name="dietbot-telegram", daemon=True)
    thread.start()
    return thread


def _other_instance(lock: Path) -> str | None:
    """Adres działającej już kopii aplikacji (plik blokady + odpowiedź serwera) albo None."""
    import json
    import urllib.error
    import urllib.request

    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{int(info['port'])}/"
        with urllib.request.urlopen(url + "api/status", timeout=2) as resp:  # noqa: S310 — tylko localhost
            return url if resp.status == 200 else None
    except urllib.error.HTTPError as exc:  # serwer Porcji z tokenem sesji odpowiada 401 — ale działa
        return url if exc.code == 401 else None
    except Exception:  # noqa: BLE001 — brak pliku, martwy proces, zajęty port innej aplikacji
        return None


def _acquire_instance_mutex(paths) -> bool:
    """Jedna kopia aplikacji na katalog danych — nazwany mutex Windows (atomowy, zwalniany przy wyjściu procesu).
    Sam plik blokady był sprawdzany przez kilka sekund wczytywania danych, więc dwa szybkie uruchomienia dawały dwie
    kopie na jednej bazie (audyt 09.10.2026, N5)."""
    global _instance_mutex
    if os.name != "nt":
        return True
    import ctypes
    import hashlib

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    name = "Local\\Porcja-" + hashlib.sha1(str(Path(paths.root).resolve()).lower().encode("utf-8")).hexdigest()[:16]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True  # nie udało się utworzyć muteksu — nie blokujemy startu
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _instance_mutex = handle
    return True


def _confirm_close(service) -> bool:
    """Zamknięcie okna w trakcie zadania: pytamy, a po zgodzie przerywamy zadania (bez zapisu niedokończonego planu)."""
    if not service.jobs.busy():
        return True
    text = ("Porcja jeszcze układa albo zmienia plan.\n\nPrzerwać i zamknąć? Niedokończony plan nie zostanie "
            "zapisany, a model AI nie będzie już wywoływany.")
    try:
        import ctypes

        answer = ctypes.windll.user32.MessageBoxW(None, text, "Porcja", 0x4 | 0x20 | 0x40000)  # TAK/NIE, pytanie, na wierzchu
    except Exception:  # noqa: BLE001 — poza Windows: przerywamy bez pytania
        answer = 6
    if answer != 6:  # IDYES
        return False
    service.jobs.cancel_all()
    return True


def _message_box(text: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "Porcja", 0x40)
    except Exception:  # noqa: BLE001 — poza Windows po prostu wypisujemy
        print(text)


def run_desktop(with_telegram: bool = True) -> None:
    import json

    from ..storage.paths import Paths
    from .service import DietService

    paths = Paths.discover()
    storage = paths.out / "okno"
    storage.mkdir(parents=True, exist_ok=True)
    lock = storage / "instancja.json"
    # druga kopia okna nie może używać tego samego profilu WebView2 ani bazy (sprawdzamy przed wczytaniem danych)
    if not _acquire_instance_mutex(paths):
        running = _other_instance(lock)
        _message_box("Porcja jest już otwarta." + (f" Jeśli nie widzisz okna, adres aplikacji to {running}"
                                                   if running else " Poczekaj chwilę, aż poprzednie okno się zamknie."))
        return
    service = DietService(paths)
    token = secrets.token_urlsafe(24)
    server, thread, port = start_server(service, token=token)
    url = f"http://127.0.0.1:{port}/"
    window_url = f"{url}?t={token}"  # strona zapamiętuje token i usuwa go z paska adresu
    lock.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")
    log.info("Serwer aplikacji: %s", url)
    print(f"DietBot: {url}", flush=True)
    if with_telegram:
        start_telegram_in_background(service)
    try:
        try:
            import webview
        except ImportError:
            log.warning("Brak pywebview — otwieram aplikację w przeglądarce")
            webbrowser.open(window_url)
            thread.join()
            return
        window = webview.create_window(f"Porcja {__version__} — dobrze mieć plan", window_url, width=1360, height=900,
                                       min_size=(960, 640), text_select=True, background_color="#FAF9F5")
        window.events.closing += lambda: _confirm_close(service)  # False = zostajemy w oknie
        webview.start(private_mode=False, storage_path=str(storage))  # pamięć okna (np. odhaczone zakupy) przetrwa restart
    finally:
        server.should_exit = True
        stopped = service.jobs.shutdown(wait_s=CLOSE_WAIT_S)
        with contextlib.suppress(OSError):
            lock.unlink()
        if not stopped:
            # zadanie czeka na odpowiedź modelu — nie zapisze już planu (przerwanie sprawdzane przed zapisem),
            # a ukryty proces nie może dalej działać i płacić
            log.warning("Zadanie nie zakończyło się w %.0f s po zamknięciu okna — kończę proces", CLOSE_WAIT_S)
            logging.shutdown()
            os._exit(0)


def run_server_only(port: int = 8765, open_browser: bool = True, with_telegram: bool = True) -> None:
    from .service import DietService

    service = DietService()
    # DIETBOT_TOKEN — stały token tylko do pracy deweloperskiej (podgląd, testy ręczne); domyślnie losowy na sesję
    token = os.environ.get("DIETBOT_TOKEN", "").strip() or secrets.token_urlsafe(24)
    try:
        server, thread, port = start_server(service, port=port, token=token)
    except OSError as exc:
        raise SystemExit(f"Port {port} jest zajęty (inna kopia DietBota albo inny program) — użyj --port. ({exc})") from None
    url = f"http://127.0.0.1:{port}/?t={token}"
    print(f"Porcja działa: {url}  (adres zawiera token sesji; Ctrl+C kończy)", flush=True)
    log.info("Serwer aplikacji (tryb przeglądarki): %s", url)
    if with_telegram:
        start_telegram_in_background(service)
    if open_browser:
        webbrowser.open(url)
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        server.should_exit = True
        thread.join(5)


def main() -> None:
    """Punkt wejścia okna bez konsoli (dietbot-okno.exe / DietBot.exe). Dziennik: wyniki/dietbot.log."""
    import sys
    from logging.handlers import RotatingFileHandler

    from ..storage.paths import Paths

    handlers: list[logging.Handler] = []
    try:
        out = Paths.discover().out
        out.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(out / "dietbot.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
        if sys.stdout is None or sys.stderr is None:  # okno bez konsoli: print() i błędy do pliku
            stream = open(out / "dietbot_konsola.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
            sys.stdout = sys.stdout or stream
            sys.stderr = sys.stderr or stream
    except FileNotFoundError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers or None)
    try:
        run_desktop()
    except Exception as exc:
        logging.getLogger("dietbot").exception("Aplikacja zakończyła się błędem")
        # okno bez konsoli: bez tego program po prostu by zniknął
        _message_box(f"Porcja nie mogła wystartować:\n{exc}\n\nSzczegóły w pliku wyniki\\dietbot.log.")
        raise
