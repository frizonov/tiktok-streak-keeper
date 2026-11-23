"""
Минималистичный графический интерфейс (CustomTkinter) + иконка в системном трее (pystray)
для TikTok Streak Keeper.

Цвета огоньков полностью повторяют логику мобильного TikTok:
- Если огонёк сегодня ещё НЕ горит с обеих сторон — огонёк СЕРЫЙ (даже если мы уже отправили).
- Если огонёк ГОРИТ сегодня (оба написали) — цвет зависит от длины серии:
  * 200+ дней: фиолетовый (Purple/Violet)
  * 100–199 дней: ярко-розовый (Hot Pink/Magenta)
  * 30–99 дней: красно-коралловый
  * <30 дней: классический оранжево-золотой
"""
from __future__ import annotations

import ctypes
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
from PIL import Image, ImageDraw, ImageTk

TASK_NAME = "TikTokStreakKeeper"
RUN_REG_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
APP_USER_MODEL_ID = "frizonov.TikTokStreakKeeper.Minimal.2"


def set_windows_app_id() -> None:
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Палитра огоньков по уровням серии (1:1 как в мобильном TikTok)
# --------------------------------------------------------------------------- #
def get_streak_tier_palette(
    lit_today: bool, streak_num: int | None
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], tuple[int, int, int, int], str]:
    """
    Возвращает (outer_rgba, inner_rgba, core_rgba, text_hex) для огонька:
    - Не горит сегодня -> серый огонёк (#7A8291)
    - Горит сегодня:
      * 200+ дн. -> фиолетовый (#B967FF)
      * 100..199 дн. -> ярко-розовый (#FF3E9D)
      * 30..99 дн. -> кораллово-красный (#FF5263)
      * <30 дн. -> оранжево-золотой (#FF7A30)
    """
    if not lit_today:
        return (
            (114, 121, 136, 255),
            (156, 163, 178, 255),
            (205, 210, 222, 255),
            "#828a99",
        )

    n = streak_num or 0
    if n >= 200:
        # Фиолетовый (серия 200+ дней)
        return (
            (157, 78, 221, 255),
            (212, 132, 255, 255),
            (248, 225, 255, 255),
            "#c77dff",
        )
    if n >= 100:
        # Ярко-розовый / маджента (серия 100–199 дней)
        return (
            (255, 38, 137, 255),
            (255, 118, 190, 255),
            (255, 228, 244, 255),
            "#ff4da6",
        )
    if n >= 30:
        # Красно-коралловый
        return (
            (255, 59, 92, 255),
            (255, 138, 101, 255),
            (255, 238, 225, 255),
            "#ff5e78",
        )
    # Оранжево-золотой (базовый активный огонёк)
    return (
        (255, 98, 36, 255),
        (255, 195, 18, 255),
        (255, 246, 210, 255),
        "#ff8f3d",
    )


def _draw_flame_rgba(
    size: int = 128,
    bg_color: tuple[int, int, int, int] | None = None,
    outer_color: tuple[int, int, int, int] = (255, 71, 87, 255),
    inner_color: tuple[int, int, int, int] = (255, 195, 18, 255),
    core_color: tuple[int, int, int, int] = (255, 245, 210, 255),
    badge_color: tuple[int, int, int, int] | None = None,
    radius_ratio: float = 0.26,
) -> Image.Image:
    """Векторная отрисовка огонька с 2x-суперсэмплингом."""
    ss = size * 2
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if bg_color is not None:
        pad = int(ss * 0.04)
        rad = int(ss * radius_ratio)
        d.rounded_rectangle(
            (pad, pad, ss - pad, ss - pad),
            radius=rad,
            fill=bg_color,
        )

    def pt(x: float, y: float) -> tuple[int, int]:
        return (int(ss * x), int(ss * y))

    # Силуэт огонька TikTok (плавная капля с двумя языками пламени)
    d.polygon(
        [
            pt(0.53, 0.09),
            pt(0.75, 0.35),
            pt(0.81, 0.59),
            pt(0.71, 0.83),
            pt(0.50, 0.90),
            pt(0.28, 0.83),
            pt(0.19, 0.58),
            pt(0.26, 0.36),
            pt(0.37, 0.45),
        ],
        fill=outer_color,
    )
    d.ellipse((int(ss * 0.21), int(ss * 0.42), int(ss * 0.79), int(ss * 0.90)), fill=outer_color)

    # Внутреннее пламя
    d.polygon(
        [
            pt(0.51, 0.28),
            pt(0.67, 0.50),
            pt(0.64, 0.77),
            pt(0.50, 0.84),
            pt(0.36, 0.77),
            pt(0.33, 0.50),
        ],
        fill=inner_color,
    )
    d.ellipse((int(ss * 0.33), int(ss * 0.52), int(ss * 0.67), int(ss * 0.86)), fill=inner_color)

    # Светлое ядро
    d.ellipse((int(ss * 0.42), int(ss * 0.64), int(ss * 0.58), int(ss * 0.84)), fill=core_color)

    if badge_color is not None:
        d.ellipse((int(ss * 0.65), int(ss * 0.65), int(ss * 0.96), int(ss * 0.96)), fill=(13, 15, 20, 255))
        d.ellipse((int(ss * 0.70), int(ss * 0.70), int(ss * 0.91), int(ss * 0.91)), fill=badge_color)

    return img.resize((size, size), Image.Resampling.LANCZOS)


def make_inline_flame_icon(lit_today: bool, streak_num: int | None, px: int = 18) -> ctk.CTkImage:
    """Маленький огонёк без фона для отображения рядом с числом дней серии (как в TikTok: 🔥412 / ⚪565)."""
    outer, inner, core, _ = get_streak_tier_palette(lit_today, streak_num)
    pil_img = _draw_flame_rgba(
        size=px * 2,
        bg_color=None,
        outer_color=outer,
        inner_color=inner,
        core_color=core,
        badge_color=None,
    )
    return ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(px, px))


def create_tray_flame_icon(status: str = "done") -> Image.Image:
    """Иконка 64x64 для системного трея Windows."""
    badge_colors = {
        "done": (51, 224, 133, 255),
        "pending": (255, 174, 43, 255),
        "working": (84, 168, 255, 255),
        "error": (255, 59, 92, 255),
    }
    if status == "pending":
        outer, inner, core, _ = get_streak_tier_palette(False, None)
    else:
        outer, inner, core, _ = get_streak_tier_palette(True, 250)
    return _draw_flame_rgba(
        size=64,
        bg_color=(17, 19, 26, 255),
        outer_color=outer,
        inner_color=inner,
        core_color=core,
        badge_color=badge_colors.get(status, badge_colors["done"]),
    )


def ensure_app_ico(log_dir: Path) -> Path:
    log_dir.mkdir(exist_ok=True)
    ico_path = log_dir / "app_icon_v2.ico"
    try:
        outer, inner, core, _ = get_streak_tier_palette(True, 250)
        base = _draw_flame_rgba(
            size=256,
            bg_color=(17, 19, 26, 255),
            outer_color=outer,
            inner_color=inner,
            core_color=core,
            badge_color=None,
            radius_ratio=0.25,
        )
        base.save(
            ico_path,
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
    except Exception:
        pass
    return ico_path


# --------------------------------------------------------------------------- #
# Сборка данных друзей
# --------------------------------------------------------------------------- #
def get_friend_view_models(core) -> tuple[dict, dict, list[dict]]:
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
        _, _, _, flame_hex = get_streak_tier_palette(both_today, streak_num)

        if both_today:
            status_text = "Огонёк горит • оба написали"
            status_color = "#33e085"
            short_tray = "🔥 горит сегодня"
        elif sent_me:
            status_text = "Отправлено сегодня • ждём ответа"
            status_color = "#6e7787"
            short_tray = "⚪ отправлено (ждём друга)"
        elif sent_peer:
            status_text = "Друг написал • ждёт твоего ответа"
            status_color = "#ffae2b"
            short_tray = "⏳ ждёт твоего ответа"
        else:
            status_text = "Сегодня ещё не отправлено"
            status_color = "#555d6b"
            short_tray = "⚪ не отправлено"

        items.append(
            {
                "username": u,
                "nickname": c.get("nickname") or u,
                "sent_me": sent_me,
                "sent_peer": sent_peer,
                "both_today": both_today,
                "streak_num": streak_num,
                "last_msg_ts": int(c.get("last_msg_ts") or 0),
                "chat_idx": int(c.get("idx") if c.get("idx") is not None else 999),
                "flame_hex": flame_hex,
                "status_text": status_text,
                "status_color": status_color,
                "short_tray": short_tray,
            }
        )

    return cfg, state, items


def build_tray_tooltip(cfg: dict, state: dict, items: list[dict], busy_text: str = "") -> str:
    if busy_text:
        return f"TikTok Streak\n{busy_text}"[:127]

    total = len(items)
    if total == 0:
        return "TikTok Streak\nСписок друзей пуст"[:127]

    done_cnt = sum(1 for i in items if i["sent_me"])
    lit_list = [i["username"] for i in items if i["both_today"]]
    wait_list = [i["username"] for i in items if not i["both_today"]]

    lines = [f"TikTok Streak • Отпр: {done_cnt}/{total} • Горят: {len(lit_list)}/{total}"]
    if lit_list:
        lines.append("🔥 " + ", ".join(lit_list))
    if wait_list:
        lines.append("⚪ " + ", ".join(wait_list))

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


def is_profile_logged_in(core) -> bool:
    """Проверяет, сохранён ли профиль браузера с сессией TikTok."""
    d = core.PROFILE_DIR
    return d.exists() and any(d.iterdir())


def sort_friend_items(items: list[dict], sort_mode: str) -> list[dict]:
    """Сортирует список друзей по выбранному критерию."""
    if sort_mode == "Недавние":
        return sorted(
            items,
            key=lambda it: (-it["last_msg_ts"], it["chat_idx"], it["nickname"].lower()),
        )
    if sort_mode == "По серии":
        return sorted(
            items,
            key=lambda it: (-(it["streak_num"] or 0), it["nickname"].lower(), it["username"]),
        )
    if sort_mode == "По имени":
        return sorted(
            items,
            key=lambda it: (it["nickname"].lower(), it["username"]),
        )
    if sort_mode == "Не горят":
        return sorted(
            items,
            key=lambda it: (1 if it["both_today"] else 0, -(it["streak_num"] or 0), it["nickname"].lower()),
        )
    return items


# --------------------------------------------------------------------------- #
# Минималистичное окно и трей
# --------------------------------------------------------------------------- #
class StreakTrayApp:
    def __init__(self, core, start_visible: bool = False):
        set_windows_app_id()
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
        self.root.title("TikTok Streak")
        self.root.geometry("560x540")
        self.root.minsize(500, 460)
        self.root.configure(fg_color="#0b0c10")

        self.ico_path = ensure_app_ico(self.core.LOG_DIR)
        self._apply_window_icon()
        self.root.after(250, self._apply_window_icon)
        self.root.after(600, self._apply_window_icon)

        self._brand_flame = make_inline_flame_icon(True, 250, px=22)

        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self._build_ui()
        self.refresh_ui()

        if not start_visible:
            self.root.withdraw()

        self._start_tray_icon()

        self._daemon_thread = threading.Thread(target=self._background_daemon_loop, daemon=True)
        self._daemon_thread.start()

        self.root.after(1000, self._periodic_tick)

    def _apply_window_icon(self) -> None:
        try:
            if self.ico_path.exists():
                self.root.iconbitmap(default=str(self.ico_path))
                self.root.iconbitmap(str(self.ico_path))
            outer, inner, core, _ = get_streak_tier_palette(True, 250)
            pil_icon = _draw_flame_rgba(
                size=64,
                bg_color=(17, 19, 26, 255),
                outer_color=outer,
                inner_color=inner,
                core_color=core,
            )
            self._tk_photo_icon = ImageTk.PhotoImage(pil_icon)
            self.root.wm_iconphoto(True, self._tk_photo_icon)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Системный трей
    # ------------------------------------------------------------------ #
    def _make_friend_action(self, username: str):
        def _action(icon, item):
            self.root.after(0, lambda: self.trigger_send_only(username))
        return _action

    def _build_tray_menu(self) -> pystray.Menu:
        cfg, state, items = get_friend_view_models(self.core)
        done_cnt = sum(1 for i in items if i["sent_me"])
        lit_cnt = sum(1 for i in items if i["both_today"])
        total = len(items)

        header_label = (
            f"Отправлено: {done_cnt}/{total} • Горят: {lit_cnt}/{total}"
            if total
            else "Список друзей пуст"
        )

        friend_menu_items = []
        for it in items:
            u = it["username"]
            num_str = f" {it['streak_num']}" if it.get("streak_num") else ""
            label = f"{it['nickname']} (@{u}){num_str} — {it['short_tray']}"
            friend_menu_items.append(pystray.MenuItem(label, self._make_friend_action(u)))

        if not friend_menu_items:
            friend_menu_items.append(pystray.MenuItem("Нет добавленных друзей", None, enabled=False))

        return pystray.Menu(
            pystray.MenuItem("Открыть TikTok Streak", self._on_tray_open, default=True),
            pystray.MenuItem(header_label, None, enabled=False),
            pystray.Menu.SEPARATOR,
            *friend_menu_items,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Отправить сейчас", self._on_tray_send_now),
            pystray.MenuItem("Обновить статусы (Scan)", self._on_tray_scan),
            pystray.MenuItem("Открыть лог", self._on_tray_open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self._on_tray_quit),
        )

    def _start_tray_icon(self) -> None:
        cfg, state, items = get_friend_view_models(self.core)
        total = len(items)
        done_cnt = sum(1 for i in items if i["sent_me"])
        icon_status = "done" if (total > 0 and done_cnt == total) else "pending"

        self.tray_icon = pystray.Icon(
            "TikTokStreakKeeper",
            create_tray_flame_icon(icon_status),
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

            self.tray_icon.icon = create_tray_flame_icon(icon_status)
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
    # Минималистичный интерфейс (в стиле Входящих TikTok)
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        # 1. Компактная шапка
        top = ctk.CTkFrame(self.root, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 10))

        left_hdr = ctk.CTkFrame(top, fg_color="transparent")
        left_hdr.pack(side="left")

        title_line = ctk.CTkFrame(left_hdr, fg_color="transparent")
        title_line.pack(anchor="w")

        ctk.CTkLabel(title_line, text="", image=self._brand_flame).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            title_line,
            text="TikTok Streak",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color="#f5f7fa",
        ).pack(side="left")

        self.summary_lbl = ctk.CTkLabel(
            left_hdr,
            text="Отправлено: 0/0  •  Горят: 0/0",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#6e7787",
        )
        self.summary_lbl.pack(anchor="w", pady=(2, 0))

        right_hdr = ctk.CTkFrame(top, fg_color="transparent")
        right_hdr.pack(side="right")

        self.scan_btn = ctk.CTkButton(
            right_hdr,
            text="Обновить",
            width=88,
            height=32,
            corner_radius=8,
            fg_color="#161922",
            hover_color="#202532",
            text_color="#c9d1d9",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=lambda: self.trigger_scan(open_import_modal=False),
        )
        self.scan_btn.pack(side="left", padx=(0, 8))

        self.send_btn = ctk.CTkButton(
            right_hdr,
            text="Отправить",
            width=98,
            height=32,
            corner_radius=8,
            fg_color="#ff2e63",
            hover_color="#ff4777",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=lambda: self.trigger_send(force_all=False),
        )
        self.send_btn.pack(side="left")

        # 2. Тонкая панель сортировки над списком
        sort_bar = ctk.CTkFrame(self.root, fg_color="transparent")
        sort_bar.pack(fill="x", padx=20, pady=(0, 6))

        ctk.CTkLabel(
            sort_bar,
            text="Сортировка:",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#6e7787",
        ).pack(side="left", padx=(0, 8))

        self.sort_seg = ctk.CTkSegmentedButton(
            sort_bar,
            values=["Недавние", "По серии", "По имени", "Не горят"],
            height=24,
            corner_radius=7,
            fg_color="#12141c",
            selected_color="#232838",
            selected_hover_color="#2d3448",
            unselected_color="#12141c",
            unselected_hover_color="#1a1d28",
            text_color="#c9d1d9",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._on_sort_change,
        )
        self.sort_seg.pack(side="left")

        # 3. Чистый список друзей (занимает всё основное пространство)
        self.friends_scroll = ctk.CTkScrollableFrame(
            self.root,
            fg_color="#0b0c10",
            corner_radius=0,
        )
        self.friends_scroll.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        # 3. Нижняя минималистичная полоса добавления и настроек
        bottom = ctk.CTkFrame(self.root, fg_color="#12141c", corner_radius=12)
        bottom.pack(fill="x", padx=18, pady=(0, 14))

        # Строка 1 нижней панели: добавление друга
        add_bar = ctk.CTkFrame(bottom, fg_color="transparent")
        add_bar.pack(fill="x", padx=12, pady=(10, 6))

        self.add_user_entry = ctk.CTkEntry(
            add_bar,
            placeholder_text="+ @username друга...",
            height=28,
            corner_radius=7,
            fg_color="#0b0c10",
            border_width=1,
            border_color="#1e2230",
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.add_user_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.add_user_entry.bind("<Return>", lambda _e: self._add_friend_from_entry())

        ctk.CTkButton(
            add_bar,
            text="Добавить",
            width=78,
            height=28,
            corner_radius=7,
            fg_color="#1c202c",
            hover_color="#272d3d",
            text_color="#d9e2f2",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._add_friend_from_entry,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            add_bar,
            text="Найти в TikTok",
            width=105,
            height=28,
            corner_radius=7,
            fg_color="#1c202c",
            hover_color="#272d3d",
            text_color="#9aa5b8",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=lambda: self.trigger_scan(open_import_modal=True),
        ).pack(side="left")

        # Строка 2 нижней панели: расписание, автозапуск, вход, лог
        settings_bar = ctk.CTkFrame(bottom, fg_color="transparent")
        settings_bar.pack(fill="x", padx=12, pady=(2, 10))

        ctk.CTkLabel(
            settings_bar,
            text="Автоотправка в",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b95a7",
        ).pack(side="left", padx=(0, 6))

        self.time_entry = ctk.CTkEntry(
            settings_bar,
            width=58,
            height=24,
            corner_radius=6,
            fg_color="#0b0c10",
            border_width=1,
            border_color="#252a3a",
            text_color="#f0f3f8",
            justify="center",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
        )
        self.time_entry.pack(side="left", padx=(0, 4))
        self.time_entry.bind("<Return>", lambda _e: self._save_send_time())
        self.time_entry.bind("<FocusOut>", lambda _e: self._save_send_time())

        ctk.CTkButton(
            settings_bar,
            text="Ок",
            width=34,
            height=24,
            corner_radius=6,
            fg_color="#1c202c",
            hover_color="#272d3d",
            text_color="#9aa5b8",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            command=self._save_send_time,
        ).pack(side="left", padx=(0, 14))

        self.autostart_switch = ctk.CTkSwitch(
            settings_bar,
            text="Автозапуск",
            width=36,
            height=18,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b95a7",
            progress_color="#33e085",
            command=self._toggle_autostart,
        )
        self.autostart_switch.pack(side="left")

        ctk.CTkButton(
            settings_bar,
            text="Лог",
            width=46,
            height=24,
            corner_radius=6,
            fg_color="transparent",
            hover_color="#1c202c",
            text_color="#6e7787",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._on_tray_open_log,
        ).pack(side="right")

        self.account_btn = ctk.CTkButton(
            settings_bar,
            text="● Вход выполнен",
            width=118,
            height=24,
            corner_radius=6,
            fg_color="#13221c",
            hover_color="#192e25",
            text_color="#33e085",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self.trigger_login,
        )
        self.account_btn.pack(side="right", padx=(0, 6))

    # ------------------------------------------------------------------ #
    # Обновление списка
    # ------------------------------------------------------------------ #
    def refresh_ui(self) -> None:
        cfg, state, items = get_friend_view_models(self.core)
        total = len(items)
        done_cnt = sum(1 for i in items if i["sent_me"])
        lit_cnt = sum(1 for i in items if i["both_today"])

        if self.busy_text:
            self.summary_lbl.configure(text=self.busy_text, text_color="#54a8ff")
            self.send_btn.configure(state="disabled")
            self.scan_btn.configure(state="disabled")
        else:
            self.summary_lbl.configure(
                text=f"Отправлено сегодня: {done_cnt}/{total}   •   Горят: {lit_cnt}/{total}",
                text_color="#7d8594",
            )
            self.send_btn.configure(state="normal")
            self.scan_btn.configure(state="normal")

        if self.root.focus_get() != getattr(self.time_entry, "_entry", None):
            self.time_entry.delete(0, "end")
            self.time_entry.insert(0, cfg.get("send_time", "12:00"))

        if is_autostart_enabled():
            self.autostart_switch.select()
        else:
            self.autostart_switch.deselect()

        if is_profile_logged_in(self.core):
            self.account_btn.configure(
                text="● Вход выполнен",
                fg_color="#13221c",
                hover_color="#192e25",
                text_color="#33e085",
            )
        else:
            self.account_btn.configure(
                text="● Войти в TikTok",
                fg_color="#2a161b",
                hover_color="#3b1d25",
                text_color="#ff5e78",
            )

        sort_mode = cfg.get("sort_by", "Недавние")
        if sort_mode not in ("Недавние", "По серии", "По имени", "Не горят"):
            sort_mode = "Недавние"
        self.sort_seg.set(sort_mode)
        sorted_items = sort_friend_items(items, sort_mode)

        for child in self.friends_scroll.winfo_children():
            child.destroy()

        if not sorted_items:
            ctk.CTkLabel(
                self.friends_scroll,
                text="Список друзей пуст.\nНажми «Найти в TikTok» внизу или добавь @username.",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color="#555d6b",
                justify="center",
            ).pack(pady=60)
        else:
            for it in sorted_items:
                self._render_friend_row(it)

        self.update_tray()

    def _render_friend_row(self, it: dict) -> None:
        row = ctk.CTkFrame(
            self.friends_scroll,
            fg_color="#12141c",
            corner_radius=11,
        )
        row.pack(fill="x", padx=4, pady=3)

        # Левая колонка: Ник + Иконка огонька (серая если не горит, цветная по уровню серии если горит!) + Число дней
        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=14, pady=9)

        top_line = ctk.CTkFrame(info, fg_color="transparent")
        top_line.pack(anchor="w")

        ctk.CTkLabel(
            top_line,
            text=it["nickname"],
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#f0f3f8",
        ).pack(side="left")

        if it["nickname"].lower() != it["username"].lower():
            ctk.CTkLabel(
                top_line,
                text=f"  @{it['username']}",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color="#596170",
            ).pack(side="left")

        # Огонёк + количество дней серии прямо возле имени (как в мобильном TikTok)
        flame_img = make_inline_flame_icon(it["both_today"], it["streak_num"], px=17)
        flame_lbl = ctk.CTkLabel(top_line, text="", image=flame_img, cursor="hand2")
        flame_lbl.pack(side="left", padx=(8, 2))

        num_text = str(it["streak_num"]) if it["streak_num"] else "—"
        num_lbl = ctk.CTkLabel(
            top_line,
            text=num_text,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=it["flame_hex"],
            cursor="hand2",
        )
        num_lbl.pack(side="left")

        # Клик по числу серии позволяет быстро подправить количество дней, если нужно
        u = it["username"]
        cur_num = it["streak_num"]
        flame_lbl.bind("<Button-1>", lambda _e, user=u, n=cur_num: self._prompt_streak_num(user, n))
        num_lbl.bind("<Button-1>", lambda _e, user=u, n=cur_num: self._prompt_streak_num(user, n))

        # Подпись статуса под именем
        ctk.CTkLabel(
            info,
            text=it["status_text"],
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=it["status_color"],
        ).pack(anchor="w", pady=(1, 0))

        # Правая часть: минималистичные кнопки
        ctk.CTkButton(
            row,
            text="×",
            width=26,
            height=26,
            corner_radius=6,
            fg_color="transparent",
            hover_color="#261822",
            text_color="#555d6b",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            command=lambda username=u: self._remove_friend(username),
        ).pack(side="right", padx=(2, 10))

        ctk.CTkButton(
            row,
            text="Отправить",
            width=80,
            height=26,
            corner_radius=6,
            fg_color="#1a1d28",
            hover_color="#252a3a",
            text_color="#9aa5b8",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=lambda username=u: self.trigger_send_only(username),
        ).pack(side="right", padx=2)

    def _prompt_streak_num(self, username: str, current_num: int | None) -> None:
        dlg = ctk.CTkInputDialog(
            text=f"Укажи текущее количество дней огонька с @{username}:",
            title="Дни серии",
        )
        val = dlg.get_input()
        if val is None:
            return
        val = val.strip()
        if not val.isdigit():
            return
        st = self.core.load_state()
        entry = st.setdefault("chats", {}).setdefault(username, {"username": username})
        entry["streak_num"] = int(val)
        entry["streak_num_date"] = date.today().isoformat()
        self.core.save_state(st)
        self.refresh_ui()

    # ------------------------------------------------------------------ #
    # Настройки и управление списком
    # ------------------------------------------------------------------ #
    def _on_sort_change(self, value: str) -> None:
        cfg = self.core.load_config(required=False)
        cfg.pop("selectors", None)
        cfg["sort_by"] = value
        with self.core.CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self.refresh_ui()

    def _save_send_time(self) -> None:
        raw = self.time_entry.get().strip()
        try:
            val = datetime.strptime(raw, "%H:%M").strftime("%H:%M")
        except ValueError:
            return
        cfg = self.core.load_config(required=False)
        cfg.pop("selectors", None)
        if cfg.get("send_time") != val:
            cfg["send_time"] = val
            with self.core.CONFIG_PATH.open("w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self.update_tray()

    def _toggle_autostart(self) -> None:
        if os.name != "nt":
            return
        if self.autostart_switch.get() == 1:
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}" daemon'
            else:
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
    # Фоновые задачи
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
                self.core.log.error("Ошибка (%s): %s", label, e)
            finally:
                self.busy_text = ""
                self._busy_lock.release()
                self.root.after(0, self.refresh_ui)

        threading.Thread(target=worker, daemon=True).start()

    def trigger_send(self, force_all: bool = False) -> None:
        self._run_in_bg(
            "Отправка огоньков...",
            lambda: self.core.cmd_run(
                Namespace(force=True, dry_run=False, only=None, respect_chat_history=not force_all)
            ),
        )

    def trigger_send_only(self, username: str) -> None:
        self._run_in_bg(
            f"Отправка @{username}...",
            lambda: self.core.cmd_run(
                Namespace(force=True, dry_run=False, only=[username], respect_chat_history=False)
            ),
        )

    def trigger_login(self) -> None:
        self._run_in_bg("Вход в TikTok в браузере...", lambda: self.core.cmd_login(Namespace()))

    def trigger_scan(self, open_import_modal: bool = True) -> None:
        def do_scan():
            chats = self.core.scan_streaks()
            if open_import_modal and chats:
                self.root.after(0, lambda: self._show_scan_results_modal(chats))

        self._run_in_bg("Обновление чатов из TikTok...", do_scan)

    def _show_scan_results_modal(self, chats: list[dict]) -> None:
        cfg = self.core.load_config(required=False)
        existing = {self.core.norm_username(f.get("username", "")) for f in cfg.get("friends", [])}
        valid = [c for c in chats if c.get("username")]
        if not valid:
            return

        win = ctk.CTkToplevel(self.root)
        win.title("Чаты TikTok")
        win.geometry("520x460")
        win.configure(fg_color="#0b0c10")
        win.attributes("-topmost", True)

        ctk.CTkLabel(
            win,
            text="Выбери друзей для добавления в список:",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#f0f3f8",
        ).pack(anchor="w", padx=16, pady=(14, 6))

        scroll = ctk.CTkScrollableFrame(win, fg_color="#12141c", corner_radius=10)
        scroll.pack(fill="both", expand=True, padx=16, pady=6)

        checks: list[tuple[str, ctk.CTkCheckBox]] = []
        for c in valid:
            u = c["username"]
            already = u in existing
            is_streak = bool(c.get("has_streak_interaction")) or bool(c.get("streak_num")) or c.get("days_since_last_msg", 99) <= 3
            num_txt = f" • {c['streak_num']} дн." if c.get("streak_num") else ""

            row = ctk.CTkFrame(scroll, fg_color="#171a24", corner_radius=8)
            row.pack(fill="x", padx=4, pady=3)

            cb = ctk.CTkCheckBox(
                row,
                text=f"{c.get('nickname', u)} (@{u}){num_txt}",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color="#33e085" if already else "#f0f3f8",
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
            text="Добавить",
            width=100,
            fg_color="#33e085",
            hover_color="#28bd6e",
            text_color="#0b0c10",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=apply_selected,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btn_bar,
            text="Закрыть",
            width=80,
            fg_color="#1c202c",
            hover_color="#272d3d",
            command=win.destroy,
        ).pack(side="right")

    # ------------------------------------------------------------------ #
    # Фоновый цикл демона и трей
    # ------------------------------------------------------------------ #
    def _background_daemon_loop(self) -> None:
        while not self._stopping:
            rc = 0
            if not self._busy_lock.locked():
                try:
                    with self._busy_lock:
                        self.busy_text = "Фоновая проверка..."
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
        self._apply_window_icon()
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


def run_daemon_with_tray(core, open_window: bool = False) -> int:
    set_windows_app_id()
    core.DAEMON_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    core.log.info("Фоновый сервис TikTok Streak Keeper с иконкой в трее запущен (PID %d)", os.getpid())
    try:
        app = StreakTrayApp(core, start_visible=open_window)
        return app.run()
    finally:
        core.DAEMON_PID_PATH.unlink(missing_ok=True)


def launch_gui_or_signal_tray(core) -> int:
    set_windows_app_id()
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
