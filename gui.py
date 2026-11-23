"""
Графический интерфейс (CustomTkinter) + иконка в системном трее Windows (pystray)
для TikTok Streak Keeper.

Позволяет:
- Постоянно держать программу в системном трее возле часов (без окон на панели задач).
- При наведении мыши на иконку в трее сразу видеть сводку: сработал ли скрипт сегодня
  и кому из друзей уже отправлено.
- По правому клику на иконку в трее видеть список всех друзей со статусом каждого огонька.
- По левому клику открывать полноценную тёмную панель управления: список друзей,
  у кого горит огонёк (оба писали), кому отправлено, кнопка ручной отправки,
  сканирование огоньков и живой лог.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from argparse import Namespace
from datetime import date, datetime
from pathlib import Path

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw

TASK_NAME = "TikTokStreakKeeper"
RUN_REG_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"


# --------------------------------------------------------------------------- #
# Генерация иконки огонька для системного трея (Pillow)
# --------------------------------------------------------------------------- #
def create_flame_icon(status: str = "done") -> Image.Image:
    """
    Рисует иконку 64x64 для системного трея:
    - status="done":   все огоньки на сегодня отправлены (яркий огонёк + зелёный индикатор)
    - status="pending": ещё не всем отправлено сегодня (оранжевый огонёк + жёлтый индикатор)
    - status="working": идёт отправка или сканирование (голубой индикатор)
    - status="error":   требуется внимание (красный индикатор)
    """
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Тёмная скруглённая подложка
    d.rounded_rectangle((2, 2, 62, 62), radius=16, fill=(20, 22, 30, 255), outline=(48, 52, 68, 255), width=2)

    # Внешний контур пламени
    outer_color = (255, 75, 43, 255) if status != "pending" else (255, 140, 0, 255)
    d.polygon(
        [
            (32, 8),
            (46, 24),
            (50, 38),
            (44, 52),
            (32, 56),
            (20, 52),
            (14, 38),
            (18, 26),
            (25, 31),
        ],
        fill=outer_color,
    )
    d.ellipse((16, 28, 48, 56), fill=outer_color)

    # Внутреннее яркое ядро пламени
    inner_color = (255, 195, 18, 255)
    d.polygon(
        [
            (32, 20),
            (41, 33),
            (39, 48),
            (32, 52),
            (25, 48),
            (23, 33),
        ],
        fill=inner_color,
    )
    d.ellipse((23, 34, 41, 53), fill=inner_color)

    # Светлая сердцевина
    d.ellipse((28, 41, 36, 51), fill=(255, 245, 200, 255))

    # Индикатор-точка статуса в правом нижнем углу
    badge_colors = {
        "done": (46, 213, 115, 255),     # зелёный — всё отправлено
        "pending": (255, 165, 2, 255),   # жёлтый — ожидает отправки
        "working": (30, 144, 255, 255),  # синий — выполняется сейчас
        "error": (255, 71, 87, 255),     # красный — ошибка
    }
    b_col = badge_colors.get(status, badge_colors["done"])
    d.ellipse((43, 43, 61, 61), fill=(20, 22, 30, 255))
    d.ellipse((46, 46, 58, 58), fill=b_col)

    return img


# --------------------------------------------------------------------------- #
# Вспомогательные функции состояния друзей и огоньков
# --------------------------------------------------------------------------- #
def get_friend_view_models(core) -> tuple[dict, dict, list[dict]]:
    """
    Собирает единый список друзей из config.json + кэша чатов из state.json.
    Для каждого друга возвращает словарь со всеми статусами на сегодня.
    """
    cfg = core.load_config(required=False)
    state = core.load_state()
    today = date.today().isoformat()
    last_sent = state.get("last_sent", {})
    chats_cache = state.get("chats", {})

    items: list[dict] = []
    for f in cfg.get("friends", []):
        u = core.norm_username(f.get("username", ""))
        if not u:
            continue
        c = chats_cache.get(u, {})
        is_fresh_cache = c.get("updated_date") == today

        sent_me = (last_sent.get(u) == today) or (is_fresh_cache and bool(c.get("sent_by_me_today")))
        sent_peer = is_fresh_cache and bool(c.get("sent_by_peer_today"))
        both_today = sent_me and sent_peer

        streak_num = c.get("streak_num")
        streak_style = c.get("streak_style")
        has_streak = bool(c.get("has_streak_interaction")) or bool(streak_num)

        if both_today:
            status_code = "lit"
            status_text = "Горит сегодня (оба писали)"
            short_tray = "🔥 горит (оба писали)"
            color = "#2ed573"
        elif sent_me:
            status_code = "sent"
            status_text = "Отправлено сегодня (ждём ответа)"
            short_tray = "✓ отправлено сегодня"
            color = "#1e90ff"
        elif sent_peer:
            status_code = "peer_waiting"
            status_text = "Друг писал сегодня — ждёт тебя!"
            short_tray = "⏳ ждёт твоего ответа!"
            color = "#ffa502"
        else:
            status_code = "pending"
            status_text = "Ещё не отправлено сегодня"
            short_tray = "⚪ не отправлено"
            color = "#a4b0be"

        if streak_num:
            if streak_style == "grey":
                streak_badge = f"⚪ {streak_num} дн. (потух)"
            else:
                streak_badge = f"🔥 {streak_num} дн."
        elif has_streak:
            streak_badge = "🔥 Огонёк активен"
        else:
            streak_badge = "💬 Диалог"

        msgs = f.get("messages") or f.get("message") or cfg.get("default_messages", ["🔥"])
        if isinstance(msgs, list):
            msg_str = " | ".join(str(x) for x in msgs)
        else:
            msg_str = str(msgs)

        items.append(
            {
                "username": u,
                "nickname": c.get("nickname") or "",
                "sent_me": sent_me,
                "sent_peer": sent_peer,
                "both_today": both_today,
                "status_code": status_code,
                "status_text": status_text,
                "short_tray": short_tray,
                "color": color,
                "streak_badge": streak_badge,
                "streak_num": streak_num,
                "last_sent_date": last_sent.get(u) or c.get("last_msg_date") or "—",
                "msg_str": msg_str,
            }
        )

    return cfg, state, items


def build_tray_tooltip(cfg: dict, state: dict, items: list[dict], busy_text: str = "") -> str:
    """Формирует текст всплывающей подсказки при наведении мыши на иконку в трее (до 127 символов в Windows)."""
    if busy_text:
        return f"🔥 TikTok Streak Keeper\n⚡ {busy_text}"[:127]

    total = len(items)
    if total == 0:
        return "🔥 TikTok Streak Keeper\nСписок друзей пуст — нажми, чтобы добавить"[:127]

    done_list = [i["username"] for i in items if i["sent_me"]]
    wait_list = [i["username"] for i in items if not i["sent_me"]]
    done_cnt = len(done_list)
    send_time = cfg.get("send_time", "12:00")

    lines = []
    if done_cnt == total:
        lines.append(f"🔥 TikTok Streak: {done_cnt}/{total} отпр. сегодня ✓")
    else:
        lines.append(f"⏳ TikTok Streak: {done_cnt}/{total} (след. {send_time})")

    if done_list:
        lines.append("✓ " + ", ".join(done_list))
    if wait_list:
        lines.append("⏳ " + ", ".join(wait_list))

    text = "\n".join(lines)
    return (text[:124] + "...") if len(text) > 127 else text


def is_autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    r = subprocess.run(
        ["reg", "query", RUN_REG_KEY, "/v", TASK_NAME],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return r.returncode == 0


# --------------------------------------------------------------------------- #
# Главное окно и контроллер системного трея
# --------------------------------------------------------------------------- #
class StreakTrayApp:
    def __init__(self, core, start_visible: bool = False):
        self.core = core
        self.start_visible = start_visible
        self.show_signal_path: Path = core.ROOT / ".show_gui"
        self.busy_text: str = ""
        self._busy_lock = threading.Lock()
        self._stopping = False
        self.tray_icon: pystray.Icon | None = None

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title("🔥 TikTok Streak Keeper")
        self.root.geometry("920x680")
        self.root.minsize(820, 580)
        self.root.configure(fg_color="#0f1117")

        # При нажатии на крестик — сворачиваем в системный трей, а не убиваем фоновый сервис
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self._build_ui()
        self.refresh_ui()

        if not start_visible:
            self.root.withdraw()

        # Запускаем иконку в системном трее
        self._start_tray_icon()

        # Запускаем фоновый поток авторассылки по расписанию
        self._daemon_thread = threading.Thread(target=self._background_daemon_loop, daemon=True)
        self._daemon_thread.start()

        # Периодический таймер обновления UI и проверки сигнала открытия окна (.show_gui)
        self.root.after(1000, self._periodic_tick)

    # ------------------------------------------------------------------ #
    # Системный трей (pystray)
    # ------------------------------------------------------------------ #
    def _make_friend_action(self, username: str):
        def _action(icon, item):
            self.root.after(0, lambda: self.trigger_send_only(username))
        return _action

    def _build_tray_menu(self) -> pystray.Menu:
        cfg, state, items = get_friend_view_models(self.core)
        done_cnt = sum(1 for i in items if i["sent_me"])
        total = len(items)

        header_label = (
            f"Статус сегодня: {done_cnt}/{total} отправлено ✓"
            if total and done_cnt == total
            else f"Статус сегодня: {done_cnt}/{total} (план в {cfg.get('send_time', '12:00')})"
        )

        friend_menu_items = []
        for it in items:
            u = it["username"]
            badge = f" ({it['streak_num']}д)" if it.get("streak_num") else ""
            label = f"@{u}{badge} — {it['short_tray']}"
            friend_menu_items.append(
                pystray.MenuItem(
                    label,
                    self._make_friend_action(u),
                )
            )

        if not friend_menu_items:
            friend_menu_items.append(pystray.MenuItem("Список друзей пуст", None, enabled=False))

        return pystray.Menu(
            pystray.MenuItem("🔥 Открыть TikTok Streak Keeper", self._on_tray_open, default=True),
            pystray.MenuItem(header_label, None, enabled=False),
            pystray.Menu.SEPARATOR,
            *friend_menu_items,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🚀 Отправить огоньки сейчас (кому не горит)", self._on_tray_send_now),
            pystray.MenuItem("🔍 Обновить статусы огоньков (Scan)", self._on_tray_scan),
            pystray.MenuItem("📄 Открыть лог работы", self._on_tray_open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("✕ Выйти полностью", self._on_tray_quit),
        )

    def _start_tray_icon(self) -> None:
        cfg, state, items = get_friend_view_models(self.core)
        total = len(items)
        done_cnt = sum(1 for i in items if i["sent_me"])
        icon_status = "done" if (total > 0 and done_cnt == total) else "pending"

        self.tray_icon = pystray.Icon(
            "TikTokStreakKeeper",
            create_flame_icon(icon_status),
            build_tray_tooltip(cfg, state, items, self.busy_text),
            menu=self._build_tray_menu(),
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def update_tray(self) -> None:
        if not self.tray_icon:
            return
        try:
            cfg, state, items = get_friend_view_models(self.core)
            total = len(items)
            done_cnt = sum(1 for i in items if i["sent_me"])
            if self.busy_text:
                icon_status = "working"
            elif total > 0 and done_cnt == total:
                icon_status = "done"
            else:
                icon_status = "pending"

            self.tray_icon.icon = create_flame_icon(icon_status)
            self.tray_icon.title = build_tray_tooltip(cfg, state, items, self.busy_text)
            self.tray_icon.menu = self._build_tray_menu()
        except Exception:
            pass

    def _on_tray_open(self, icon=None, item=None) -> None:
        self.root.after(0, self.show_window)

    def _on_tray_send_now(self, icon=None, item=None) -> None:
        self.root.after(0, lambda: self.trigger_send(force_all=False))

    def _on_tray_scan(self, icon=None, item=None) -> None:
        self.root.after(0, lambda: self.trigger_scan(open_import_modal=False))

    def _on_tray_open_log(self, icon=None, item=None) -> None:
        log_path = self.core.LOG_DIR / "streak.log"
        if log_path.exists() and os.name == "nt":
            os.startfile(log_path)  # type: ignore[attr-defined]

    def _on_tray_quit(self, icon=None, item=None) -> None:
        self.root.after(0, self.quit_app)

    # ------------------------------------------------------------------ #
    # Построение интерфейса (CustomTkinter)
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        # 1. Верхняя шапка
        top = ctk.CTkFrame(self.root, fg_color="#161922", corner_radius=14)
        top.pack(fill="x", padx=16, pady=(14, 8))

        title_col = ctk.CTkFrame(top, fg_color="transparent")
        title_col.pack(side="left", padx=16, pady=12)

        ctk.CTkLabel(
            title_col,
            text="🔥 TikTok Streak Keeper",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#ffffff",
        ).pack(anchor="w")

        self.subtitle_lbl = ctk.CTkLabel(
            title_col,
            text="Работает в системном трее • проверка расписания активна",
            font=ctk.CTkFont(size=12),
            text_color="#8b949e",
        )
        self.subtitle_lbl.pack(anchor="w")

        btn_row = ctk.CTkFrame(top, fg_color="transparent")
        btn_row.pack(side="right", padx=16, pady=12)

        self.scan_btn = ctk.CTkButton(
            btn_row,
            text="🔍 Обновить статусы (Scan)",
            fg_color="#262b38",
            hover_color="#32394a",
            text_color="#e6edf3",
            corner_radius=10,
            height=36,
            command=lambda: self.trigger_scan(open_import_modal=True),
        )
        self.scan_btn.pack(side="left", padx=(0, 8))

        self.send_btn = ctk.CTkButton(
            btn_row,
            text="🚀 Отправить сейчас",
            fg_color="#ff4757",
            hover_color="#ff6b81",
            text_color="#ffffff",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=10,
            height=36,
            command=lambda: self.trigger_send(force_all=False),
        )
        self.send_btn.pack(side="left")

        # 2. Карточки сводки (4 карточки в ряд)
        cards = ctk.CTkFrame(self.root, fg_color="transparent")
        cards.pack(fill="x", padx=16, pady=(0, 8))
        for col in range(4):
            cards.grid_columnconfigure(col, weight=1, uniform="cards")

        # Карточка 1: Отправлено сегодня
        c1 = ctk.CTkFrame(cards, fg_color="#161922", corner_radius=12)
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=2)
        ctk.CTkLabel(c1, text="ОТПРАВЛЕНО СЕГОДНЯ", font=ctk.CTkFont(size=11, weight="bold"), text_color="#8b949e").pack(
            anchor="w", padx=14, pady=(10, 2)
        )
        self.card_today_val = ctk.CTkLabel(c1, text="0 / 0", font=ctk.CTkFont(size=22, weight="bold"), text_color="#2ed573")
        self.card_today_val.pack(anchor="w", padx=14)
        self.card_today_sub = ctk.CTkLabel(c1, text="Ожидание...", font=ctk.CTkFont(size=11), text_color="#8b949e")
        self.card_today_sub.pack(anchor="w", padx=14, pady=(0, 10))

        # Карточка 2: Статус огоньков
        c2 = ctk.CTkFrame(cards, fg_color="#161922", corner_radius=12)
        c2.grid(row=0, column=1, sticky="nsew", padx=6, pady=2)
        ctk.CTkLabel(c2, text="ГОРЯТ СЕГОДНЯ", font=ctk.CTkFont(size=11, weight="bold"), text_color="#8b949e").pack(
            anchor="w", padx=14, pady=(10, 2)
        )
        self.card_lit_val = ctk.CTkLabel(c2, text="0", font=ctk.CTkFont(size=22, weight="bold"), text_color="#ffa502")
        self.card_lit_val.pack(anchor="w", padx=14)
        self.card_lit_sub = ctk.CTkLabel(c2, text="Нажми Scan для сверки чатов", font=ctk.CTkFont(size=11), text_color="#8b949e")
        self.card_lit_sub.pack(anchor="w", padx=14, pady=(0, 10))

        # Карточка 3: Время ежедневной отправки
        c3 = ctk.CTkFrame(cards, fg_color="#161922", corner_radius=12)
        c3.grid(row=0, column=2, sticky="nsew", padx=6, pady=2)
        ctk.CTkLabel(c3, text="ВРЕМЯ АВТООТПРАВКИ", font=ctk.CTkFont(size=11, weight="bold"), text_color="#8b949e").pack(
            anchor="w", padx=14, pady=(10, 4)
        )
        time_row = ctk.CTkFrame(c3, fg_color="transparent")
        time_row.pack(anchor="w", padx=14, pady=(0, 6))
        self.time_entry = ctk.CTkEntry(time_row, width=76, height=30, corner_radius=8, font=ctk.CTkFont(size=14, weight="bold"))
        self.time_entry.pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            time_row,
            text="Сохранить",
            width=80,
            height=30,
            corner_radius=8,
            fg_color="#262b38",
            hover_color="#32394a",
            command=self._save_send_time,
        ).pack(side="left")
        self.time_status_lbl = ctk.CTkLabel(c3, text="Каждый день в фоне", font=ctk.CTkFont(size=11), text_color="#8b949e")
        self.time_status_lbl.pack(anchor="w", padx=14, pady=(0, 8))

        # Карточка 4: Система и аккаунт
        c4 = ctk.CTkFrame(cards, fg_color="#161922", corner_radius=12)
        c4.grid(row=0, column=3, sticky="nsew", padx=(6, 0), pady=2)
        ctk.CTkLabel(c4, text="СИСТЕМА И АККАУНТ", font=ctk.CTkFont(size=11, weight="bold"), text_color="#8b949e").pack(
            anchor="w", padx=14, pady=(10, 4)
        )
        self.autostart_switch = ctk.CTkSwitch(
            c4,
            text="Автозапуск в трее",
            font=ctk.CTkFont(size=12),
            progress_color="#2ed573",
            command=self._toggle_autostart,
        )
        self.autostart_switch.pack(anchor="w", padx=14, pady=(0, 6))
        ctk.CTkButton(
            c4,
            text="🔑 Войти / Сменить TikTok",
            height=26,
            corner_radius=8,
            fg_color="#262b38",
            hover_color="#32394a",
            font=ctk.CTkFont(size=11),
            command=self.trigger_login,
        ).pack(anchor="w", padx=14, pady=(0, 8))

        # 3. Панель добавления друга + список друзей и статусов огоньков
        list_container = ctk.CTkFrame(self.root, fg_color="#161922", corner_radius=14)
        list_container.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        list_header = ctk.CTkFrame(list_container, fg_color="transparent")
        list_header.pack(fill="x", padx=16, pady=(12, 6))

        ctk.CTkLabel(
            list_header,
            text="Друзья и статус огоньков на сегодня",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#ffffff",
        ).pack(side="left")

        add_row = ctk.CTkFrame(list_header, fg_color="transparent")
        add_row.pack(side="right")

        self.add_user_entry = ctk.CTkEntry(
            add_row,
            placeholder_text="@username друга",
            width=170,
            height=30,
            corner_radius=8,
        )
        self.add_user_entry.pack(side="left", padx=(0, 6))
        self.add_user_entry.bind("<Return>", lambda _e: self._add_friend_from_entry())

        ctk.CTkButton(
            add_row,
            text="+ Добавить",
            width=95,
            height=30,
            corner_radius=8,
            fg_color="#2ed573",
            hover_color="#26af5f",
            text_color="#0f1117",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._add_friend_from_entry,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            add_row,
            text="Принудительно всем",
            width=140,
            height=30,
            corner_radius=8,
            fg_color="#262b38",
            hover_color="#32394a",
            font=ctk.CTkFont(size=11),
            command=lambda: self.trigger_send(force_all=True),
        ).pack(side="left")

        self.friends_scroll = ctk.CTkScrollableFrame(
            list_container,
            fg_color="#11131a",
            corner_radius=10,
        )
        self.friends_scroll.pack(fill="both", expand=True, padx=14, pady=(4, 12))

        # 4. Нижняя панель: живой лог и управление
        bottom = ctk.CTkFrame(self.root, fg_color="#161922", corner_radius=14, height=130)
        bottom.pack(fill="x", padx=16, pady=(0, 14))

        bot_hdr = ctk.CTkFrame(bottom, fg_color="transparent")
        bot_hdr.pack(fill="x", padx=14, pady=(8, 2))

        ctk.CTkLabel(
            bot_hdr,
            text="Последние события (logs/streak.log)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#8b949e",
        ).pack(side="left")

        ctk.CTkButton(
            bot_hdr,
            text="Свернуть в трей",
            width=120,
            height=24,
            corner_radius=6,
            fg_color="#262b38",
            hover_color="#32394a",
            font=ctk.CTkFont(size=11),
            command=self.hide_to_tray,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            bot_hdr,
            text="📄 Открыть лог-файл",
            width=130,
            height=24,
            corner_radius=6,
            fg_color="#262b38",
            hover_color="#32394a",
            font=ctk.CTkFont(size=11),
            command=self._on_tray_open_log,
        ).pack(side="right")

        self.log_box = ctk.CTkTextbox(
            bottom,
            height=82,
            fg_color="#0b0d12",
            text_color="#a4b0be",
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=8,
        )
        self.log_box.pack(fill="x", padx=14, pady=(2, 10))
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # Обновление данных на экране
    # ------------------------------------------------------------------ #
    def refresh_ui(self) -> None:
        cfg, state, items = get_friend_view_models(self.core)
        total = len(items)
        done_cnt = sum(1 for i in items if i["sent_me"])
        lit_cnt = sum(1 for i in items if i["both_today"])

        # Подзаголовок
        if self.busy_text:
            self.subtitle_lbl.configure(text=f"⚡ {self.busy_text}", text_color="#1e90ff")
            self.send_btn.configure(state="disabled")
            self.scan_btn.configure(state="disabled")
        else:
            last_run = state.get("last_run_at") or state.get("last_scan_at") or "ещё не запускался"
            self.subtitle_lbl.configure(
                text=f"В системном трее • Последняя активность: {last_run}",
                text_color="#8b949e",
            )
            self.send_btn.configure(state="normal")
            self.scan_btn.configure(state="normal")

        # Карточка 1: Отправлено сегодня
        self.card_today_val.configure(
            text=f"{done_cnt} / {total}",
            text_color="#2ed573" if (total > 0 and done_cnt == total) else "#ffa502",
        )
        if total == 0:
            self.card_today_sub.configure(text="Добавь друзей ниже")
        elif done_cnt == total:
            self.card_today_sub.configure(text="✓ Всем отправлено сегодня!", text_color="#2ed573")
        else:
            self.card_today_sub.configure(text=f"Ожидают отправки: {total - done_cnt}", text_color="#ffa502")

        # Карточка 2: Горят с обеих сторон
        self.card_lit_val.configure(
            text=f"{lit_cnt} / {total}",
            text_color="#2ed573" if (total > 0 and lit_cnt == total) else "#ffa502",
        )
        last_scan = state.get("last_scan_at")
        self.card_lit_sub.configure(
            text=f"Сверено: {last_scan.split(' ')[-1]}" if last_scan else "Нажми Scan для проверки чатов"
        )

        # Карточка 3: Время
        if not self.time_entry.focus_get():
            self.time_entry.delete(0, "end")
            self.time_entry.insert(0, cfg.get("send_time", "12:00"))

        # Карточка 4: Автозапуск
        if is_autostart_enabled():
            self.autostart_switch.select()
        else:
            self.autostart_switch.deselect()

        # Список друзей
        for child in self.friends_scroll.winfo_children():
            child.destroy()

        if not items:
            ctk.CTkLabel(
                self.friends_scroll,
                text="Список друзей пока пуст.\nНажми «🔍 Обновить статусы (Scan)», чтобы автоматически найти друзей с огоньками в TikTok!",
                font=ctk.CTkFont(size=13),
                text_color="#8b949e",
                justify="center",
            ).pack(pady=40)
        else:
            for idx, it in enumerate(items, 1):
                self._render_friend_row(idx, it)

        # Обновляем лог
        self._refresh_log_box()
        # Обновляем иконку и подсказку в трее
        self.update_tray()

    def _render_friend_row(self, idx: int, it: dict) -> None:
        row = ctk.CTkFrame(self.friends_scroll, fg_color="#181c26", corner_radius=10)
        row.pack(fill="x", padx=6, pady=4)

        # Цветной индикатор слева
        dot = ctk.CTkFrame(row, width=6, height=44, fg_color=it["color"], corner_radius=4)
        dot.pack(side="left", padx=(8, 10), pady=8)

        # Имя и никнейм
        info_col = ctk.CTkFrame(row, fg_color="transparent", width=210)
        info_col.pack(side="left", padx=(0, 10), pady=6)
        name_txt = f"{idx}. @{it['username']}"
        if it["nickname"]:
            name_txt += f"  ({it['nickname']})"
        ctk.CTkLabel(
            info_col,
            text=name_txt,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#ffffff",
        ).pack(anchor="w")

        ctk.CTkLabel(
            info_col,
            text=f"{it['streak_badge']}  •  Сообщение: {it['msg_str']}  •  Посл.: {it['last_sent_date']}",
            font=ctk.CTkFont(size=11),
            text_color="#8b949e",
        ).pack(anchor="w")

        # Кнопки действий справа
        act_col = ctk.CTkFrame(row, fg_color="transparent")
        act_col.pack(side="right", padx=10, pady=6)

        u = it["username"]
        ctk.CTkButton(
            act_col,
            text="✕",
            width=30,
            height=28,
            corner_radius=6,
            fg_color="#262b38",
            hover_color="#ff4757",
            text_color="#a4b0be",
            command=lambda username=u: self._remove_friend(username),
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            act_col,
            text="🚀 Отправить",
            width=100,
            height=28,
            corner_radius=6,
            fg_color="#262b38",
            hover_color="#32394a",
            font=ctk.CTkFont(size=11),
            command=lambda username=u: self.trigger_send_only(username),
        ).pack(side="right", padx=(6, 0))

        # Бейдж статуса на сегодня
        status_pill = ctk.CTkLabel(
            row,
            text=f"  {it['status_text']}  ",
            fg_color="#222736",
            text_color=it["color"],
            corner_radius=8,
            height=26,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        status_pill.pack(side="right", padx=8)

    def _refresh_log_box(self) -> None:
        log_path = self.core.LOG_DIR / "streak.log"
        if not log_path.exists():
            text = "Лог пока пуст."
        else:
            try:
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-10:]
                text = "\n".join(lines)
            except Exception:
                text = ""
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.insert("end", text)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # Действия пользователя в GUI
    # ------------------------------------------------------------------ #
    def _save_send_time(self) -> None:
        raw = self.time_entry.get().strip()
        try:
            val = datetime.strptime(raw, "%H:%M").strftime("%H:%M")
        except ValueError:
            self.time_status_lbl.configure(text="Формат ЧЧ:ММ (напр. 12:00)", text_color="#ff4757")
            return
        cfg = self.core.load_config(required=False)
        cfg.pop("selectors", None)
        cfg["send_time"] = val
        with self.core.CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self.time_status_lbl.configure(text=f"✓ Сохранено на {val}", text_color="#2ed573")
        self.refresh_ui()

    def _toggle_autostart(self) -> None:
        if os.name != "nt":
            return
        if self.autostart_switch.get() == 1:
            pyw = self.core.ROOT / ".venv" / "Scripts" / "pythonw.exe"
            script = self.core.ROOT / "streak.py"
            cmd = f'"{pyw}" "{script}" daemon'
            subprocess.run(
                ["reg", "add", RUN_REG_KEY, "/v", TASK_NAME, "/t", "REG_SZ", "/d", cmd, "/f"],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            subprocess.run(
                ["reg", "delete", RUN_REG_KEY, "/v", TASK_NAME, "/f"],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        self.refresh_ui()

    def _add_friend_from_entry(self) -> None:
        raw = self.add_user_entry.get().strip()
        if not raw:
            return
        cfg = self.core.load_config(required=False)
        cfg.pop("selectors", None)
        existing = {self.core.norm_username(f.get("username", "")) for f in cfg.get("friends", [])}
        added = False
        for part in raw.replace(",", " ").split():
            u = self.core.norm_username(part)
            if u and u not in existing:
                cfg.setdefault("friends", []).append({"username": u})
                existing.add(u)
                added = True
        if added:
            with self.core.CONFIG_PATH.open("w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        self.add_user_entry.delete(0, "end")
        self.refresh_ui()

    def _remove_friend(self, username: str) -> None:
        cfg = self.core.load_config(required=False)
        cfg.pop("selectors", None)
        cfg["friends"] = [
            f for f in cfg.get("friends", []) if self.core.norm_username(f.get("username", "")) != username
        ]
        with self.core.CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self.refresh_ui()

    # ------------------------------------------------------------------ #
    # Фоновые операции (Отправка, Сканирование, Логин)
    # ------------------------------------------------------------------ #
    def _run_in_bg(self, label: str, fn) -> None:
        if not self._busy_lock.acquire(blocking=False):
            return
        self.busy_text = label
        self.root.after(0, self.refresh_ui)

        def worker():
            try:
                fn()
            except Exception as e:
                self.core.log.error("Ошибка операции (%s): %s", label, e)
            finally:
                self.busy_text = ""
                self._busy_lock.release()
                self.root.after(0, self.refresh_ui)

        threading.Thread(target=worker, daemon=True).start()

    def trigger_send(self, force_all: bool = False) -> None:
        label = "Принудительная отправка всем..." if force_all else "Проверка и отправка огоньков..."
        self._run_in_bg(
            label,
            lambda: self.core.cmd_run(
                Namespace(force=True, dry_run=False, only=None, respect_chat_history=not force_all)
            ),
        )

    def trigger_send_only(self, username: str) -> None:
        self._run_in_bg(
            f"Отправка огонька @{username}...",
            lambda: self.core.cmd_run(
                Namespace(force=True, dry_run=False, only=[username], respect_chat_history=False)
            ),
        )

    def trigger_login(self) -> None:
        self._run_in_bg("Ожидание входа в TikTok в открытом браузере...", lambda: self.core.cmd_login(Namespace()))

    def trigger_scan(self, open_import_modal: bool = True) -> None:
        def do_scan():
            chats = self.core.scan_streaks()
            if open_import_modal and chats:
                self.root.after(0, lambda: self._show_scan_results_modal(chats))

        self._run_in_bg("Сканирую чаты и статусы огоньков в TikTok...", do_scan)

    def _show_scan_results_modal(self, chats: list[dict]) -> None:
        cfg = self.core.load_config(required=False)
        existing = {self.core.norm_username(f.get("username", "")) for f in cfg.get("friends", [])}
        valid = [c for c in chats if c.get("username")]
        if not valid:
            return

        win = ctk.CTkToplevel(self.root)
        win.title("🔍 Найденные диалоги и огоньки в TikTok")
        win.geometry("660x500")
        win.configure(fg_color="#0f1117")
        win.attributes("-topmost", True)

        ctk.CTkLabel(
            win,
            text="Статусы всех чатов обновлены! Отметь друзей, которых хочешь добавить в список:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#ffffff",
        ).pack(anchor="w", padx=16, pady=(14, 6))

        scroll = ctk.CTkScrollableFrame(win, fg_color="#161922", corner_radius=10)
        scroll.pack(fill="both", expand=True, padx=16, pady=6)

        checks: list[tuple[str, ctk.CTkCheckBox]] = []
        for c in valid:
            u = c["username"]
            status = self.core.format_streak_status(c)
            already = u in existing
            is_streak = bool(c.get("has_streak_interaction")) or bool(c.get("streak_num")) or c.get("days_since_last_msg", 99) <= 3

            row = ctk.CTkFrame(scroll, fg_color="#1c202c", corner_radius=8)
            row.pack(fill="x", padx=6, pady=3)

            cb = ctk.CTkCheckBox(
                row,
                text=f"@{u} ({c.get('nickname', '')})  —  {status}",
                font=ctk.CTkFont(size=12),
                text_color="#2ed573" if already else "#ffffff",
            )
            cb.pack(anchor="w", padx=10, pady=8)
            if already or is_streak:
                cb.select()
            if already:
                cb.configure(state="disabled")
            else:
                checks.append((u, cb))

        def apply_selected():
            cfg_now = self.core.load_config(required=False)
            cfg_now.pop("selectors", None)
            cur_set = {self.core.norm_username(f.get("username", "")) for f in cfg_now.get("friends", [])}
            for u, cb in checks:
                if cb.get() == 1 and u not in cur_set:
                    cfg_now.setdefault("friends", []).append({"username": u})
                    cur_set.add(u)
            with self.core.CONFIG_PATH.open("w", encoding="utf-8") as f:
                json.dump(cfg_now, f, ensure_ascii=False, indent=2)
            win.destroy()
            self.refresh_ui()

        btn_bar = ctk.CTkFrame(win, fg_color="transparent")
        btn_bar.pack(fill="x", padx=16, pady=12)

        ctk.CTkButton(
            btn_bar,
            text="Добавить отмеченных в список",
            fg_color="#2ed573",
            hover_color="#26af5f",
            text_color="#0f1117",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=apply_selected,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btn_bar,
            text="Закрыть",
            fg_color="#262b38",
            hover_color="#32394a",
            command=win.destroy,
        ).pack(side="right")

    # ------------------------------------------------------------------ #
    # Цикл фонового демона и управление окном
    # ------------------------------------------------------------------ #
    def _background_daemon_loop(self) -> None:
        while not self._stopping:
            rc = 0
            if not self._busy_lock.locked():
                try:
                    with self._busy_lock:
                        self.busy_text = "Фоновая проверка по расписанию..."
                        self.root.after(0, self.update_tray)
                        rc = self.core.cmd_run(
                            Namespace(force=False, dry_run=False, only=None, respect_chat_history=True)
                        )
                except SystemExit:
                    pass
                except Exception:
                    self.core.log.exception("Ошибка в цикле демона")
                finally:
                    self.busy_text = ""
                    self.root.after(0, self.refresh_ui)
            wait_sec = 1800 if rc == 2 else 300
            for _ in range(wait_sec):
                if self._stopping:
                    return
                time.sleep(1)

    def _periodic_tick(self) -> None:
        if self._stopping:
            return
        # Если пользователь запустил start.bat ещё раз, пока демон уже висит в трее — показываем окно
        if self.show_signal_path.exists():
            try:
                self.show_signal_path.unlink(missing_ok=True)
            except Exception:
                pass
            self.show_window()
        self.root.after(1500, self._periodic_tick)

    def show_window(self) -> None:
        self.refresh_ui()
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def hide_to_tray(self) -> None:
        self.root.withdraw()
        self.update_tray()

    def quit_app(self) -> None:
        self._stopping = True
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            self.core.DAEMON_PID_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        self.root.quit()
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


# --------------------------------------------------------------------------- #
# Точки входа из streak.py
# --------------------------------------------------------------------------- #
def run_daemon_with_tray(core, open_window: bool = False) -> int:
    """Запускает фоновый демон вместе с иконкой в трее и (опционально) открытым главным окном."""
    core.DAEMON_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    core.log.info("Фоновый сервис TikTok Streak Keeper с иконкой в трее запущен (PID %d)", os.getpid())
    try:
        app = StreakTrayApp(core, start_visible=open_window)
        return app.run()
    finally:
        core.DAEMON_PID_PATH.unlink(missing_ok=True)


def launch_gui_or_signal_tray(core) -> int:
    """
    Вызывается при запуске start.bat или `python streak.py gui`:
    - если фоновый демон уже висит в трее, подаёт ему сигнал (.show_gui) развернуть окно и выходит;
    - если ещё не запущен, запускает фоновый процесс pythonw.exe с треем и открытым окном.
    """
    pid_file = core.DAEMON_PID_PATH
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text(encoding="utf-8").strip())
            if old_pid != os.getpid() and core.is_pid_alive(old_pid):
                (core.ROOT / ".show_gui").write_text("1", encoding="utf-8")
                return 0
        except Exception:
            pass

    return run_daemon_with_tray(core, open_window=True)
