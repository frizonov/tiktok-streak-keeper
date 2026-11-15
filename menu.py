"""Интерактивное меню TikTok Streak Keeper — чтобы не править config.json руками."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from argparse import Namespace
from datetime import date, datetime

TASK_NAME = "TikTokStreakKeeper"
RUN_REG_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"

BOLD, DIM, GREEN, RED, YELLOW, CYAN, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"
)

BROWSER_LABELS = {
    "msedge": "Microsoft Edge",
    "chrome": "Google Chrome",
    "": "Chromium (встроенный)",
}


def run(core) -> None:
    """core — модуль streak.py (передаётся из него самого)."""
    Menu(core).loop()


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" {DIM}[{default}]{RESET}" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value if value else (default or "")


def confirm(prompt: str) -> bool:
    return ask(f"{prompt} (д/н)").lower() in ("д", "да", "y", "yes", "l")


def pause() -> None:
    input(f"\n{DIM}Нажми Enter, чтобы продолжить...{RESET}")


def split_messages(raw: str) -> list[str]:
    return [m.strip() for m in raw.split("|") if m.strip()]


class Menu:
    def __init__(self, core):
        self.core = core
        if os.name == "nt":
            os.system("")  # включает ANSI-цвета в консоли Windows

    # ------------------------------------------------------------------ #
    # config.json
    # ------------------------------------------------------------------ #
    def load(self) -> dict:
        path = self.core.CONFIG_PATH
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            base = {k: v for k, v in self.core.DEFAULT_CONFIG.items() if k != "selectors"}
            return {**base, **data}
        cfg = {k: v for k, v in self.core.DEFAULT_CONFIG.items() if k != "selectors"}
        cfg["friends"] = []
        return cfg

    def save(self, cfg: dict) -> None:
        with self.core.CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ #
    # Статус
    # ------------------------------------------------------------------ #
    def daemon_running(self) -> bool:
        pid_file = self.core.DAEMON_PID_PATH
        if not pid_file.exists():
            return False
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            return self.core.is_pid_alive(pid)
        except Exception:
            return False

    def autostart_enabled(self) -> bool:
        if os.name != "nt":
            return False
        r1 = subprocess.run(
            ["reg", "query", RUN_REG_KEY, "/v", TASK_NAME],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if r1.returncode == 0:
            return True
        r2 = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r2.returncode == 0

    def profile_exists(self) -> bool:
        d = self.core.PROFILE_DIR
        return d.exists() and any(d.iterdir())

    def sent_today(self) -> set[str]:
        today = date.today().isoformat()
        state = self.core.load_state()
        return {u for u, d in state["last_sent"].items() if d == today}

    def header(self, cfg: dict) -> None:
        os.system("cls" if os.name == "nt" else "clear")
        sent = self.sent_today()
        friends = cfg.get("friends", [])
        done = sum(1 for f in friends if self.core.norm_username(f["username"]) in sent)

        login = f"{GREEN}профиль сохранён{RESET}" if self.profile_exists() else f"{RED}не выполнен{RESET}"
        if self.autostart_enabled():
            bg = "работает в фоне" if self.daemon_running() else "включён"
            auto = f"{GREEN}{bg} (без окон){RESET}"
        else:
            auto = f"{YELLOW}выключен{RESET}"

        window = (
            f"{GREEN}скрыто (100% в фоне, без окон){RESET}"
            if cfg.get("hide_window", True)
            else f"{YELLOW}видимое окно на экране{RESET}"
        )
        channel = cfg.get("browser_channel", "msedge") or ""
        browser_name = BROWSER_LABELS.get(channel, channel)
        auto_streak = (
            f"{GREEN}вкл (сам находит активные огоньки){RESET}"
            if cfg.get("auto_send_active_streaks", False)
            else f"{DIM}только по списку друзей{RESET}"
        )

        print(f"{BOLD}🔥 TikTok Streak Keeper{RESET}")
        print(f"{DIM}{'─' * 58}{RESET}")
        print(f"  Аккаунт TikTok:    {login}")
        print(f"  Друзей в списке:   {BOLD}{len(friends)}{RESET} {DIM}(отправлено сегодня: {done}){RESET}")
        print(f"  Автопоиск чатов:   {auto_streak}")
        print(f"  Время отправки:    {CYAN}{cfg.get('send_time', '12:00')}{RESET}")
        print(f"  Фоновый сервис:    {auto}")
        print(f"  Режим браузера:    {browser_name} — {window}")
        print(f"{DIM}{'─' * 58}{RESET}")

    # ------------------------------------------------------------------ #
    # Главное меню (аккуратно сгруппировано и отсортировано 1..11)
    # ------------------------------------------------------------------ #
    def loop(self) -> None:
        sections = [
            (
                "Огоньки и отправка",
                [
                    ("1", "🔍 Найти друзей с огоньками в TikTok", self.scan_and_import),
                    ("2", "👥 Список друзей (добавить / удалить / сообщения)", self.friends_menu),
                    ("3", "🚀 Отправить сейчас (тем, у кого не горит сегодня)", self.send_unlit_now),
                    ("4", "🧪 Тестовый прогон (введёт текст без отправки)", self.dry_run),
                ],
            ),
            (
                "Автоматизация и настройки",
                [
                    ("5", "⚡ Включить / выключить фоновый автозапуск", self.toggle_autostart),
                    ("6", "⏰ Изменить время ежедневной отправки", self.set_time),
                    ("7", "💬 Изменить сообщения по умолчанию", self.set_default_messages),
                    ("8", "🤖 Автопоиск огоньков (всем активным / только списку)", self.toggle_auto_streaks),
                ],
            ),
            (
                "Аккаунт и система",
                [
                    ("9", "🔑 Войти в аккаунт TikTok (или сменить аккаунт)", self.login),
                    ("10", "👻 Режим окна браузера (скрыто в фоне / показывать)", self.toggle_window),
                    ("11", "🌐 Сменить браузер (Edge / Chrome / Chromium)", self.switch_browser),
                    ("12", "📄 Открыть лог работы", self.open_log),
                ],
            ),
        ]

        dispatch = {key: fn for _, items in sections for key, _, fn in items}

        while True:
            cfg = self.load()
            self.header(cfg)
            for section_title, items in sections:
                print(f"\n  {CYAN}{BOLD}{section_title}:{RESET}")
                for key, title, _ in items:
                    print(f"    {BOLD}{key:>2}{RESET}. {title}")
            print(f"\n    {BOLD} 0{RESET}. Выход")

            choice = input("\nВыбери номер: ").strip().lower()
            if choice == "0":
                return
            if choice not in dispatch:
                continue
            try:
                dispatch[choice]()
            except KeyboardInterrupt:
                print("\nОтменено.")
                pause()
            except SystemExit as e:
                if e.code not in (None, 0):
                    print(f"{RED}{e.code}{RESET}")
                pause()
            except Exception as e:
                print(f"{RED}Ошибка: {e}{RESET}")
                pause()

    # ------------------------------------------------------------------ #
    # Сканирование огоньков и автоимпорт друзей
    # ------------------------------------------------------------------ #
    def scan_and_import(self) -> None:
        cfg = self.load()
        self.save(cfg)
        print("\nСканирую твои чаты на tiktok.com/messages (в фоне)...")
        chats = self.core.scan_streaks(cfg)
        valid = [c for c in chats if c.get("username")]
        if not valid:
            print(f"{YELLOW}Не удалось найти диалоги с юзернеймами.{RESET}")
            pause()
            return

        existing = {self.core.norm_username(f["username"]) for f in cfg.get("friends", [])}
        window_days = int(cfg.get("active_streak_days_window", 3))

        print(f"\n{BOLD}Найденные диалоги в TikTok:{RESET}\n")
        candidates: list[dict] = []
        for i, c in enumerate(valid, 1):
            u = c["username"]
            in_list = f"{CYAN}[в списке]{RESET}" if u in existing else ""
            status = self.core.format_streak_status(c)
            is_active = c["days_since_last_msg"] <= window_days or c["has_streak_interaction"]
            if c["sent_by_me_today"]:
                badge = f"{GREEN}✓ сегодня уже отправлено{RESET}"
            elif is_active:
                badge = f"{YELLOW}⏳ НУЖНО ОТПРАВИТЬ СЕГОДНЯ{RESET}"
            else:
                badge = f"{DIM}давно не переписывались{RESET}"

            print(f"  {i:2d}. @{u:<18} ({c['nickname']}) {in_list}")
            print(f"      {status}  →  {badge}")
            if is_active:
                candidates.append(c)

        print(
            f"\n  {BOLD}a{RESET}. Добавить все активные огоньки/чаты ({len(candidates)} шт.) в список друзей\n"
            f"  {BOLD}номера{RESET}. Добавить выбранные номера через пробел (например: 1 2 5)\n"
            f"  {BOLD}Enter{RESET}. Назад"
        )
        raw = input("\nВыбери: ").strip().lower()
        if not raw:
            return

        to_add: list[str] = []
        if raw in ("a", "ф"):
            to_add = [c["username"] for c in candidates]
        else:
            for part in raw.replace(",", " ").split():
                if part.isdigit() and 1 <= int(part) <= len(valid):
                    to_add.append(valid[int(part) - 1]["username"])

        added = []
        for u in to_add:
            if u not in existing:
                cfg.setdefault("friends", []).append({"username": u})
                existing.add(u)
                added.append(u)

        if added:
            self.save(cfg)
            print(f"\n{GREEN}Добавлены в список друзей: {', '.join('@' + u for u in added)}{RESET}")
        else:
            print(f"\n{DIM}Все выбранные друзья уже были в списке.{RESET}")
        pause()

    # ------------------------------------------------------------------ #
    # Друзья
    # ------------------------------------------------------------------ #
    def print_friends(self, cfg: dict) -> None:
        friends = cfg.get("friends", [])
        if not friends:
            print(f"  {DIM}Список пуст{RESET}")
            return
        sent = self.sent_today()
        default = " | ".join(cfg.get("default_messages", ["🔥"]))
        for i, f in enumerate(friends, 1):
            u = self.core.norm_username(f["username"])
            msgs = f.get("messages") or f.get("message")
            if isinstance(msgs, list):
                msgs = " | ".join(msgs)
            mark = f"{GREEN}✓{RESET}" if u in sent else " "
            text = msgs if msgs else f"{DIM}по умолчанию: {default}{RESET}"
            print(f"  {mark} {i:2d}. @{u:<18} — {text}")

    def pick_friend(self, cfg: dict) -> int | None:
        raw = ask("Номер друга")
        if raw.isdigit() and 1 <= int(raw) <= len(cfg.get("friends", [])):
            return int(raw) - 1
        print(f"{RED}Нет такого номера{RESET}")
        return None

    def friends_menu(self) -> None:
        while True:
            cfg = self.load()
            os.system("cls" if os.name == "nt" else "clear")
            print(f"{BOLD}👥 Список друзей{RESET}  {DIM}(✓ — уже отправлено сегодня){RESET}\n")
            self.print_friends(cfg)
            print(
                f"\n  {BOLD}1{RESET}. Добавить вручную\n"
                f"  {BOLD}2{RESET}. Найти друзей с огоньками в TikTok\n"
                f"  {BOLD}3{RESET}. Изменить сообщение другу\n"
                f"  {BOLD}4{RESET}. Удалить друга\n"
                f"  {BOLD}0{RESET}. Назад"
            )
            choice = input("\nВыбери: ").strip().lower()

            if choice in ("0", ""):
                return
            if choice in ("1", "a", "ф"):
                self.add_friends(cfg)
            elif choice in ("2", "s", "ы"):
                self.scan_and_import()
            elif choice in ("3", "e", "у") and cfg.get("friends"):
                idx = self.pick_friend(cfg)
                if idx is not None:
                    self.edit_messages(cfg, idx)
            elif choice in ("4", "d", "в") and cfg.get("friends"):
                idx = self.pick_friend(cfg)
                if idx is not None:
                    removed = cfg["friends"].pop(idx)
                    self.save(cfg)
                    print(f"Удалён @{self.core.norm_username(removed['username'])}")
                    pause()

    def add_friends(self, cfg: dict) -> None:
        print(f"\n{DIM}Юзернейм можно с @ или без. Несколько — через пробел или запятую.{RESET}")
        raw = ask("Юзернейм(ы)")
        names = [self.core.norm_username(n) for n in raw.replace(",", " ").split() if n.strip("@ ")]
        if not names:
            return

        existing = {self.core.norm_username(f["username"]) for f in cfg.get("friends", [])}
        new = [n for n in names if n not in existing]
        for n in set(names) - set(new):
            print(f"{YELLOW}@{n} уже в списке{RESET}")
        if not new:
            pause()
            return

        print(f"\n{DIM}Сообщение для них. Несколько вариантов — через |, будет выбираться случайный.")
        print(f"Пусто — использовать сообщения по умолчанию.{RESET}")
        msgs = split_messages(ask("Сообщение"))

        for n in new:
            entry = {"username": n}
            if msgs:
                entry["messages"] = msgs
            cfg.setdefault("friends", []).append(entry)
        self.save(cfg)
        print(f"{GREEN}Добавлено: {', '.join('@' + n for n in new)}{RESET}")
        pause()

    def edit_messages(self, cfg: dict, idx: int) -> None:
        friend = cfg["friends"][idx]
        current = friend.get("messages") or friend.get("message") or []
        if isinstance(current, str):
            current = [current]
        print(f"\n{DIM}Через | для нескольких вариантов. Введи - чтобы вернуть сообщения по умолчанию.{RESET}")
        raw = ask("Сообщение", " | ".join(current) if current else None)
        friend.pop("message", None)
        friend.pop("messages", None)
        if raw != "-":
            msgs = split_messages(raw)
            if msgs:
                friend["messages"] = msgs
        self.save(cfg)
        print(f"{GREEN}Сохранено{RESET}")
        pause()

    # ------------------------------------------------------------------ #
    # Настройки
    # ------------------------------------------------------------------ #
    def set_time(self) -> None:
        cfg = self.load()
        raw = ask("Время отправки ЧЧ:ММ", cfg.get("send_time", "12:00"))
        try:
            value = datetime.strptime(raw, "%H:%M").strftime("%H:%M")
        except ValueError:
            print(f"{RED}Неверный формат, нужно например 18:30{RESET}")
            pause()
            return
        cfg["send_time"] = value
        self.save(cfg)
        print(f"{GREEN}Время отправки: {value}{RESET}")
        if self.autostart_enabled():
            print("Обновляю фоновый автозапуск...")
            self.install_task(value)
        pause()

    def set_default_messages(self) -> None:
        cfg = self.load()
        current = " | ".join(cfg.get("default_messages", ["🔥"]))
        print(f"{DIM}Используются для друзей без своего сообщения. Несколько — через |.{RESET}")
        msgs = split_messages(ask("Сообщения", current))
        if msgs:
            cfg["default_messages"] = msgs
            self.save(cfg)
            print(f"{GREEN}Сохранено{RESET}")
        pause()

    def toggle_window(self) -> None:
        cfg = self.load()
        cfg["hide_window"] = not cfg.get("hide_window", True)
        self.save(cfg)

    def toggle_auto_streaks(self) -> None:
        cfg = self.load()
        cfg["auto_send_active_streaks"] = not cfg.get("auto_send_active_streaks", False)
        self.save(cfg)

    def switch_browser(self) -> None:
        cfg = self.load()
        order = ["msedge", "chrome", ""]
        cur = cfg.get("browser_channel", "msedge") or ""
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "msedge"
        cfg["browser_channel"] = nxt
        self.save(cfg)

    # ------------------------------------------------------------------ #
    # Действия с TikTok
    # ------------------------------------------------------------------ #
    def login(self) -> None:
        self.core.cmd_login(Namespace())
        pause()

    def choose_targets(self, cfg: dict) -> list[str] | None | bool:
        """Список ников, None — всем, False — отмена."""
        if not cfg.get("friends") and not cfg.get("auto_send_active_streaks", False):
            print(f"{YELLOW}Сначала добавь друзей (пункт 1 или 2){RESET}")
            pause()
            return False
        if cfg.get("friends"):
            self.print_friends(cfg)
            raw = ask("\nНомер друга или Enter — всем")
            if not raw:
                return None
            if raw.isdigit() and 1 <= int(raw) <= len(cfg["friends"]):
                return [cfg["friends"][int(raw) - 1]["username"]]
            print(f"{RED}Нет такого номера{RESET}")
            pause()
            return False
        return None

    def dry_run(self) -> None:
        cfg = self.load()
        self.save(cfg)
        targets = self.choose_targets(cfg)
        if targets is False:
            return
        code = self.core.cmd_run(Namespace(force=True, dry_run=True, only=targets, respect_chat_history=False))
        print(f"\n{GREEN}Тест прошёл{RESET}" if code == 0 else f"\n{RED}Есть ошибки, см. сообщения выше{RESET}")
        pause()

    def send_unlit_now(self) -> None:
        cfg = self.load()
        self.save(cfg)
        targets = self.choose_targets(cfg)
        if targets is False:
            return
        print(
            f"\n  {BOLD}1{RESET}. Отправить только тем, у кого ещё НЕ горит сегодня (рекомендуется)\n"
            f"  {BOLD}2{RESET}. Отправить принудительно (даже если ты уже писал сегодня)\n"
            f"  {BOLD}0{RESET}. Отмена"
        )
        mode = ask("Выбери режим", "1")
        if mode == "0":
            return
        respect = mode != "2"
        code = self.core.cmd_run(Namespace(force=True, dry_run=False, only=targets, respect_chat_history=respect))
        print(f"\n{GREEN}Готово{RESET}" if code == 0 else f"\n{RED}Есть ошибки, см. сообщения выше{RESET}")
        pause()

    # ------------------------------------------------------------------ #
    # Фоновый автозапуск (как сервис — без окон)
    # ------------------------------------------------------------------ #
    def install_task(self, send_time: str) -> bool:
        script = self.core.ROOT / "install_task.ps1"
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Time", send_time],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print(f"{GREEN}Фоновый автозапуск включён!{RESET}")
            print(f"{DIM}Сервис запущен в фоне без окон (как zapret): стартует вместе с Windows и шлёт огоньки в {send_time}.{RESET}")
            return True
        print(f"{RED}Не удалось включить автозапуск:{RESET}\n{r.stdout}{r.stderr}")
        return False

    def toggle_autostart(self) -> None:
        if os.name != "nt":
            print("Автозапуск сделан только для Windows.")
            pause()
            return
        if self.autostart_enabled():
            if confirm("Выключить фоновый автозапуск и остановить сервис?"):
                script = self.core.ROOT / "uninstall_task.ps1"
                subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                    capture_output=True,
                )
                print(f"{YELLOW}Фоновый автозапуск выключен{RESET}")
                pause()
            return

        cfg = self.load()
        if not cfg.get("friends") and not cfg.get("auto_send_active_streaks", False):
            print(f"{YELLOW}Сначала добавь друзей (пункт 1 или 2) или включи автопоиск огоньков (пункт 8){RESET}")
        elif not self.profile_exists():
            print(f"{YELLOW}Сначала войди в TikTok (пункт 9){RESET}")
        else:
            # Для фонового сервиса сразу включаем скрытый режим браузера
            cfg["hide_window"] = True
            self.save(cfg)
            self.install_task(cfg.get("send_time", "12:00"))
        pause()

    def open_log(self) -> None:
        path = self.core.LOG_DIR / "streak.log"
        if not path.exists():
            print("Лог пока пуст.")
            pause()
            return
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            print(path.read_text(encoding="utf-8")[-5000:])
            pause()
