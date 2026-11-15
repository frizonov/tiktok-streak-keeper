#!/usr/bin/env python3
"""
TikTok Streak Keeper — раз в сутки отправляет сообщение выбранным друзьям
в TikTok, чтобы не потерять огонёк.

Работает через настоящий браузер (Playwright) с отдельным сохранённым
профилем: ты один раз входишь в аккаунт руками, дальше скрипт использует
эту сессию полностью в фоне (без видимых окон).

Команды:
    python streak.py                  интерактивное меню (или двойной клик по start.bat)
    python streak.py login            открыть браузер и войти в TikTok
    python streak.py check            проверить, что сессия жива
    python streak.py scan             найти активные чаты/огоньки и проверить статус на сегодня
    python streak.py run              отправить тем, кому ещё не отправлено сегодня
    python streak.py run --force      отправить прямо сейчас, игнорируя время и историю
    python streak.py run --dry-run    напечатать сообщение, но не отправлять
    python streak.py run --only nick  только одному пользователю
    python streak.py daemon           тихий фоновый процесс (ждёт времени отправки и шлёт раз в сутки)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import subprocess
import sys
import time
from datetime import date, datetime
from datetime import time as dtime
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
PROFILE_DIR = ROOT / "profile"
LOG_DIR = ROOT / "logs"
LOCK_PATH = ROOT / ".streak.lock"
DAEMON_PID_PATH = ROOT / ".daemon.pid"

BASE_URL = "https://www.tiktok.com"
APP_NAME = "TikTok Streak Keeper"

# Селекторы перебираются по порядку, берётся первый видимый элемент.
# Если TikTok поменяет вёрстку — их можно переопределить в config.json ("selectors").
DEFAULT_SELECTORS: dict[str, list[str]] = {
    "message_button": [
        '[data-e2e="message-button"]',
        'button:has-text("Message")',
        'button:has-text("Сообщение")',
        'button:has-text("Написать")',
    ],
    "message_input": [
        '[data-e2e="dm-new-chatbox"] div.public-DraftEditor-content[contenteditable="true"]',
        '[data-e2e="message-input-area"] div.public-DraftEditor-content[contenteditable="true"]',
        'div.public-DraftEditor-content[contenteditable="true"]',
    ],
    "chat_list_items": [
        '[data-e2e="dm-new-conversation-list"] [class*="--DivItemWrapper"]',
        '[data-e2e="chat-list-item"]',
        '[class*="--DivItemWrapper"]',
    ],
    "chat_header_username": [
        '[data-e2e="dm-new-chatbox"] [class*="--SpanHeaderUserId"]',
        '[data-e2e="dm-new-chatbox"] [class*="--PUniqueId"]',
        '[data-e2e="chat-uniqueid"]',
        '[class*="--SpanHeaderUserId"]',
        '[class*="--PUniqueId"]',
    ],
    "chat_header_nickname": [
        '[data-e2e="dm-new-chatbox"] [class*="--PHeaderDisplayName"]',
        '[data-e2e="chat-nickname"]',
    ],
    "captcha": [
        "#captcha-verify-container",
        "#captcha_container",
        "#tiktok-verify-ele",
        '[id*="captcha"]',
        'iframe[src*="captcha"]',
        'div[class*="captcha-verify"]',
        'div[class*="secsdk-captcha"]',
        'div[class*="verify-wrap"]',
    ],
}

DEFAULT_CONFIG = {
    "send_time": "12:00",
    "headless": False,
    "hide_window": True,
    "browser_channel": "msedge",
    "captcha_wait_sec": 300,
    "smart_skip_if_sent_today": True,
    "auto_send_active_streaks": False,
    "active_streak_days_window": 3,
    "delay_between_friends_sec": [4, 9],
    "default_messages": ["🔥"],
    "friends": [],
    "selectors": {},
}

log = logging.getLogger("streak")


# --------------------------------------------------------------------------- #
# Ошибки
# --------------------------------------------------------------------------- #
class StreakError(Exception):
    """Ошибка отправки конкретному пользователю — пропускаем его и идём дальше."""


class NeedLogin(Exception):
    """Сессия протухла — без участия человека дальше нельзя."""


class CaptchaDetected(Exception):
    """TikTok показал проверку и её не прошли за отведённое время."""


class AlreadyRunning(Exception):
    pass


# --------------------------------------------------------------------------- #
# Утилиты
# --------------------------------------------------------------------------- #
def setup_logging(verbose: bool) -> None:
    if log.handlers:
        return
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = RotatingFileHandler(
        LOG_DIR / "streak.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)

    # под pythonw.exe stderr отсутствует
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        log.addHandler(console)

    log.setLevel(logging.DEBUG if verbose else logging.INFO)


def notify(title: str, message: str) -> None:
    """Всплывающее уведомление Windows (на других ОС — только в лог)."""
    log.info("Уведомление: %s — %s", title, message)
    if os.name != "nt":
        return

    def esc(s: str) -> str:
        return s.replace("'", "''")

    app_id = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode('{esc(title)}')) > $null
$x.Item(1).AppendChild($t.CreateTextNode('{esc(message)}')) > $null
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=20,
            check=False,
            capture_output=True,
        )
    except Exception as e:
        log.debug("Не удалось показать уведомление: %s", e)


def norm_username(username: str) -> str:
    return username.strip().lstrip("@").lower()


def parse_time(value: str) -> dtime:
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        raise SystemExit(f'send_time должен быть в формате "ЧЧ:ММ", получено: {value!r}')


def load_config(required: bool = True) -> dict:
    if not CONFIG_PATH.exists():
        if required:
            raise SystemExit(
                "Нет config.json. Скопируй config.example.json в config.json и заполни его."
            )
        return dict(DEFAULT_CONFIG)

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        user_cfg = json.load(f)

    cfg = {**DEFAULT_CONFIG, **user_cfg}
    selectors = {k: list(v) for k, v in DEFAULT_SELECTORS.items()}
    for key, value in (user_cfg.get("selectors") or {}).items():
        selectors[key] = [value] if isinstance(value, str) else list(value)
    cfg["selectors"] = selectors
    parse_time(cfg["send_time"])  # валидация
    return cfg


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            with STATE_PATH.open("r", encoding="utf-8") as f:
                state = json.load(f)
                state.setdefault("last_sent", {})
                return state
        except (json.JSONDecodeError, OSError):
            log.warning("state.json повреждён — начинаю с чистой истории")
    return {"last_sent": {}}


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(STATE_PATH)


def pick_message(friend: dict, cfg: dict) -> str:
    options = friend.get("messages") or friend.get("message") or cfg["default_messages"]
    if isinstance(options, str):
        options = [options]
    return random.choice(options)


class Lock:
    """Не даёт запуститься двум копиям отправки одновременно."""

    STALE_AFTER_SEC = 30 * 60

    def __enter__(self) -> "Lock":
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() - LOCK_PATH.stat().st_mtime > self.STALE_AFTER_SEC:
                LOCK_PATH.unlink(missing_ok=True)
                return self.__enter__()
            raise AlreadyRunning()
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc) -> None:
        LOCK_PATH.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Браузер и позиционирование окна
# --------------------------------------------------------------------------- #
def reset_saved_window_placement() -> None:
    """Сбрасывает сохранённые в профиле Chromium координаты (-32000 или второй монитор)."""
    prefs_path = PROFILE_DIR / "Default" / "Preferences"
    if not prefs_path.exists():
        return
    try:
        data = json.loads(prefs_path.read_text(encoding="utf-8"))
        bp = data.setdefault("browser", {}).setdefault("window_placement", {})
        bp.update(
            {
                "left": 80,
                "top": 60,
                "right": 1360,
                "bottom": 900,
                "maximized": False,
                "minimized": False,
            }
        )
        prefs_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.debug("Не удалось сбросить координаты окна в Preferences: %s", e)


def set_window_bounds(page, state: str = "normal", left: int = 80, top: int = 60, width: int = 1280, height: int = 840) -> None:
    """Перемещает/разворачивает/сворачивает видимое окно через Chrome DevTools Protocol."""
    try:
        cdp = page.context.new_cdp_session(page)
        target = cdp.send("Browser.getWindowForTarget")
        win_id = target["windowId"]
        if state == "minimized":
            cdp.send("Browser.setWindowBounds", {"windowId": win_id, "bounds": {"windowState": "minimized"}})
        else:
            cdp.send("Browser.setWindowBounds", {"windowId": win_id, "bounds": {"windowState": "normal"}})
            cdp.send(
                "Browser.setWindowBounds",
                {
                    "windowId": win_id,
                    "bounds": {"left": left, "top": top, "width": width, "height": height, "windowState": "normal"},
                },
            )
        cdp.detach()
    except Exception as e:
        log.debug("CDP setWindowBounds не сработал: %s", e)


def bring_window_to_front(page) -> None:
    """Гарантированно выводит видимое окно браузера на основной монитор и поверх остальных."""
    set_window_bounds(page, state="normal", left=80, top=60, width=1280, height=840)
    try:
        page.bring_to_front()
    except Exception:
        pass


def launch_browser(p, cfg: dict, headless: bool, hide_window: bool):
    """
    Запускает браузер с постоянным профилем.
    Если hide_window=True (по умолчанию), используется режим --headless=new
    с полной маскировкой под обычный видимый браузер (без флага HeadlessChrome и webdriver),
    чтобы TikTok не сбрасывал сессию при фоновых проверках.
    """
    reset_saved_window_placement()
    invisible = bool(hide_window or headless)
    args = [
        "--disable-notifications",
        "--disable-blink-features=AutomationControlled",
        "--window-position=80,60",
        "--window-size=1280,840",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]
    if invisible:
        args.append("--headless=new")

    kwargs = dict(
        user_data_dir=str(PROFILE_DIR),
        headless=False,  # Передаём False, так как новый невидимый режим включается через --headless=new
        args=args,
        ignore_default_args=["--no-sandbox", "--enable-automation"],
        viewport={"width": 1240, "height": 760},
    )
    channel = (cfg.get("browser_channel") or "").strip()
    ctx = None
    if channel:
        for attempt in range(1, 5):
            try:
                ctx = p.chromium.launch_persistent_context(channel=channel, **kwargs)
                break
            except Exception as e:
                err_str = str(e)
                # Ни в коем случае не открываем профиль Edge встроенным Chromium при блокировке профиля —
                # это ломает шифрование куки и сбрасывает сессию TikTok!
                if "ProcessSingleton" in err_str or "Lock file" in err_str or attempt < 4:
                    log.debug("Профиль браузера занят (попытка %d/4), жду 3 сек...", attempt)
                    time.sleep(3)
                    if attempt == 4:
                        raise AlreadyRunning()
                else:
                    log.warning("Не удалось запустить браузер %r (%s), использую встроенный Chromium", channel, e)
                    ctx = p.chromium.launch_persistent_context(**kwargs)
                    break
    else:
        ctx = p.chromium.launch_persistent_context(**kwargs)

    ctx._is_invisible = invisible  # type: ignore[attr-defined]
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()

    # Убираем маркер HeadlessChrome из User-Agent в режиме --headless=new,
    # чтобы для сервера TikTok фоновый запуск ничем не отличался от обычного окна
    if invisible:
        try:
            raw_ua = pg.evaluate("() => navigator.userAgent")
            if "HeadlessChrome/" in raw_ua:
                clean_ua = raw_ua.replace("HeadlessChrome/", "Chrome/")
                cdp = ctx.new_cdp_session(pg)
                cdp.send("Emulation.setUserAgentOverride", {"userAgent": clean_ua})
                cdp.detach()
        except Exception as e:
            log.debug("Не удалось переопределить User-Agent: %s", e)
    else:
        bring_window_to_front(pg)
    return ctx


def find_first(page, selectors: list[str], timeout: float = 10.0):
    """Первый видимый элемент из списка селекторов или None."""
    deadline = time.monotonic() + timeout
    while True:
        for selector in selectors:
            loc = page.locator(selector).first
            try:
                if loc.is_visible():
                    return loc
            except Exception:
                pass
        if time.monotonic() > deadline:
            return None
        page.wait_for_timeout(300)


def is_element_inert(locator) -> bool:
    """Проверяет, не заблокирован ли сам элемент атрибутом inert."""
    try:
        return bool(
            locator.evaluate(
                """el => {
                    for (let n = el; n; n = n.parentElement) {
                        if (n.inert || n.getAttribute('inert') !== null) return true;
                    }
                    return false;
                }"""
            )
        )
    except Exception:
        return False


def check_blockers(page, selectors: dict, wait_sec: int = 300, hide_after: bool = False, target_locator=None) -> None:
    """Проверяет, не разлогинило ли и нет ли капчи/модалки. Если есть — открывает окно и ждёт решения вручную."""
    need_auth = "/login" in page.url or not has_session(page.context)
    has_captcha = bool(find_first(page, selectors["captcha"], timeout=0))
    has_inert = is_element_inert(target_locator) if target_locator is not None else False

    if not need_auth and not has_captcha and not has_inert:
        return

    # Если браузер был запущен в полностью невидимом режиме (--headless=new),
    # сигнализируем, чтобы внешний код перезапустил его с видимым окном на экране
    if getattr(page.context, "_is_invisible", False) or wait_sec <= 0:
        if need_auth:
            raise NeedLogin()
        raise CaptchaDetected()

    if need_auth:
        log.warning("Сессия TikTok требует входа! Вывожу окно браузера на экран и жду входа (%d сек)...", wait_sec)
        msg = "Сессия TikTok слетела! Войди в аккаунт в открывшемся окне браузера — я сразу отправлю огоньки."
    else:
        log.warning("Окно заблокировано капчей или всплывающим окном! Вывожу браузер на экран и жду (%d сек)...", wait_sec)
        msg = "В TikTok вылезла капча или окно! Реши/закрой его в браузере — я продолжу сам."

    bring_window_to_front(page)
    if os.name == "nt":
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
    notify(APP_NAME, msg)

    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        page.wait_for_timeout(1000)
        still_need_auth = "/login" in page.url or not has_session(page.context)
        still_captcha = bool(find_first(page, selectors["captcha"], timeout=0))
        still_inert = is_element_inert(target_locator) if target_locator is not None else False
        if not still_need_auth and not still_captcha and not still_inert:
            log.info("Вход/проверка успешно пройдены! Продолжаю работу...")
            if "/messages" not in page.url:
                goto(page, f"{BASE_URL}/messages")
                page.wait_for_timeout(2000)
            return

    if "/login" in page.url or not has_session(page.context):
        raise NeedLogin()
    raise CaptchaDetected()


def has_session(ctx) -> bool:
    return any(c["name"] == "sessionid" and c["value"] for c in ctx.cookies(BASE_URL))


def goto(page, url: str, attempts: int = 3) -> None:
    """page.goto с повтором при сетевых сбоях."""
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded")
            return
        except Exception as e:
            if attempt == attempts:
                raise StreakError(f"не удалось открыть {url}: {str(e).splitlines()[0]}")
            log.debug("Не открылась %s (%s), повтор %d", url, e, attempt)
            page.wait_for_timeout(3000 * attempt)


def dismiss_overlays(page) -> None:
    """Убирает баннер cookies TikTok, который перекрывает поле ввода чата и перехватывает клики."""
    try:
        page.evaluate(
            """() => {
                document.querySelectorAll('tiktok-cookie-banner, #tiktok-cookie-banner-config').forEach(el => el.remove());
            }"""
        )
    except Exception:
        pass


def ensure_logged_in(ctx, page, selectors: dict, cfg: dict | None = None) -> None:
    wait_sec = (cfg or {}).get("captcha_wait_sec", 300)
    hide_after = bool((cfg or {}).get("hide_window", True))
    goto(page, f"{BASE_URL}/messages")
    page.wait_for_timeout(2000)
    dismiss_overlays(page)
    check_blockers(page, selectors, wait_sec=wait_sec, hide_after=hide_after)
    if not has_session(ctx):
        raise NeedLogin()

    # Дожидаемся загрузки элементов списка чатов (чтобы исчез скелетон)
    first_item = find_first(page, selectors.get("chat_list_items", []), timeout=30.0)
    if first_item is None:
        log.debug("Список чатов долго грузится — перезагружаю /messages...")
        goto(page, f"{BASE_URL}/messages")
        page.wait_for_timeout(2000)
        dismiss_overlays(page)
        check_blockers(page, selectors, wait_sec=wait_sec, hide_after=hide_after)
        find_first(page, selectors.get("chat_list_items", []), timeout=25.0)

    # Дожидаемся подгрузки профилей собеседников (uniqueId) в React-хранилище TikTok
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        meta = inspect_conversations_metadata(page)
        if any(m.get("username") for m in meta):
            break
        page.wait_for_timeout(400)

    dismiss_overlays(page)
    log.info("Сессия активна")


# --------------------------------------------------------------------------- #
# Чтение метаданных чатов и огоньков из IM SDK (React state)
# --------------------------------------------------------------------------- #
def inspect_conversations_metadata(page) -> list[dict]:
    """
    Мгновенно (без кликов по чатам) извлекает из React Fiber списка чатов:
    - idx: индекс элемента в списке
    - username: @uniqueId собеседника
    - nickname: отображаемое имя собеседника
    - sent_by_me_today: отправлял ли ТЫ сообщение этому человеку сегодня (по локальному времени)
    - sent_by_peer_today: писал ли собеседник сегодня
    - both_active_today: оба обменялись сообщениями сегодня (огонёк за сегодня точно горит)
    - last_msg_date: дата последнего сообщения (YYYY-MM-DD в локальном часовом поясе)
    - days_since_last_msg: сколько дней назад было последнее сообщение
    - has_streak_interaction: были ли в последних сообщениях специальные действия огонька (spark_back, nudge, streak badge)
    - streak_num: число дней огонька из плашки (если есть в кэше)
    - streak_style: 'color' | 'grey' | None
    """
    raw_items = page.evaluate(
        """() => {
            const wrappers = document.querySelectorAll('[data-e2e="dm-new-conversation-list"] [class*="--DivItemWrapper"]');
            if (!wrappers.length) return [];

            let getUserFn = null;
            const w0 = wrappers[0];
            const k0 = Object.keys(w0).find(k => k.startsWith('__reactFiber$'));
            if (k0) {
                for (let n = w0[k0], d = 0; n && d < 12; d++, n = n.return) {
                    for (let h = n.memoizedState, hi = 0; h && hi < 40; hi++, h = h.next) {
                        const ms = h.memoizedState;
                        const cand = Array.isArray(ms) ? ms[0] : ms;
                        if (cand && typeof cand.getUser === 'function') {
                            getUserFn = cand.getUser;
                            break;
                        }
                    }
                    if (getUserFn) break;
                }
            }

            return Array.from(wrappers).map((w, idx) => {
                const domNick = w.querySelector('[class*="--PInfoNickname2"]')?.textContent?.trim() || '';
                const k = Object.keys(w).find(k => k.startsWith('__reactFiber$'));
                let conv = null;
                if (k) {
                    for (let n = w[k], d = 0; n && d < 10; d++, n = n.return) {
                        for (let h = n.memoizedState, hi = 0; h && hi < 40; hi++, h = h.next) {
                            const cand = h.memoizedState?.current;
                            if (cand && cand.instance && cand.instance.coreInfo) {
                                conv = cand;
                                break;
                            }
                        }
                        if (conv) break;
                    }
                }
                if (!conv) return { idx, domNick, found: false };

                const peerUid = String(conv.toParticipantUserId || '');
                const u = (getUserFn && peerUid) ? getUserFn(peerUid) : null;
                const convId = String(conv.id || conv.conversationId || '');
                // Пропускаем чат с самим собой (избранное)
                const parts = convId.split(':');
                const isSelfChat = parts.length >= 4 && parts[2] === parts[3];

                const msgs = [];
                const seen = new Set();
                function collect(obj, depth) {
                    if (!obj || typeof obj !== 'object' || depth > 5 || seen.has(obj)) return;
                    seen.add(obj);
                    if (obj.createdAt && (obj.sender || obj.content)) {
                        const ts = (obj.createdAt instanceof Date)
                            ? obj.createdAt.getTime()
                            : Date.parse(String(obj.createdAt));
                        msgs.push({
                            ts: Number.isFinite(ts) ? ts : 0,
                            sender: String(obj.sender || ''),
                            ext: obj.ext || obj.instance?.ext || {},
                            template: obj.content?.template || null
                        });
                        return;
                    }
                    if (Array.isArray(obj)) {
                        for (const item of obj) collect(item, depth + 1);
                    } else {
                        for (const [kk, vv] of Object.entries(obj)) {
                            if (kk.startsWith('_') || kk === 'parent' || kk === 'api') continue;
                            collect(vv, depth + 1);
                        }
                    }
                }
                collect(conv.instance.segments, 0);
                collect(conv.firstMessage, 0);
                collect(conv.lastMessage, 0);
                collect(conv.lastVisibleMessage, 0);

                return {
                    idx,
                    found: true,
                    isSelfChat,
                    peerUid,
                    uniqueId: u?.uniqueId || '',
                    nickname: u?.nickname || domNick,
                    msgs
                };
            });
        }"""
    )

    today_local = date.today()
    today_iso = today_local.isoformat()
    saved_last_sent = load_state().get("last_sent", {})
    results: list[dict] = []

    for item in raw_items or []:
        if not item.get("found") or item.get("isSelfChat"):
            continue

        peer_uid = item.get("peerUid") or ""
        username = norm_username(item.get("uniqueId") or "")
        nickname = (item.get("nickname") or "").strip()

        sent_by_me_today = saved_last_sent.get(username) == today_iso
        sent_by_peer_today = False
        has_streak_interaction = False
        streak_num: int | None = None
        streak_style: str | None = None
        latest_dt: datetime | None = None

        for m in item.get("msgs") or []:
            ext = m.get("ext") or {}
            src = str(ext.get("a:src") or "")
            light = str(ext.get("a:light_interaction_name") or "")
            if any(k in (src + " " + light) for k in ("spark", "nudge", "streak")):
                has_streak_interaction = True

            for tpl in m.get("template") or []:
                extra = (tpl or {}).get("extra") or {}
                if "streak_badge_streak_num" in extra:
                    has_streak_interaction = True
                    try:
                        streak_num = int(extra["streak_badge_streak_num"])
                    except ValueError:
                        pass
                    streak_style = extra.get("streak_badge_style")

            ts_ms = m.get("ts") or 0
            sender = str(m.get("sender") or "")
            if sender in ("", "1", "0") or not ts_ms:
                continue

            try:
                dt = datetime.fromtimestamp(ts_ms / 1000.0)
            except Exception:
                continue

            if latest_dt is None or dt > latest_dt:
                latest_dt = dt

            if dt.date() == today_local:
                if sender == peer_uid:
                    sent_by_peer_today = True
                else:
                    sent_by_me_today = True

        last_msg_date = latest_dt.date().isoformat() if latest_dt else None
        days_since = (today_local - latest_dt.date()).days if latest_dt else 999

        results.append(
            {
                "idx": item["idx"],
                "username": username,
                "nickname": nickname,
                "sent_by_me_today": sent_by_me_today,
                "sent_by_peer_today": sent_by_peer_today,
                "both_active_today": sent_by_me_today and sent_by_peer_today,
                "last_msg_date": last_msg_date,
                "days_since_last_msg": days_since,
                "has_streak_interaction": has_streak_interaction,
                "streak_num": streak_num,
                "streak_style": streak_style,
            }
        )

    return results


def input_text(box) -> str:
    try:
        return box.inner_text().strip()
    except Exception:
        return ""


def type_and_send(chat, selectors: dict, username: str, text: str, dry_run: bool, cfg: dict) -> None:
    wait_sec = cfg.get("captcha_wait_sec", 300)
    hide_after = bool(cfg.get("hide_window", True))
    dismiss_overlays(chat)
    check_blockers(chat, selectors, wait_sec=wait_sec, hide_after=hide_after)

    box = find_first(chat, selectors["message_input"], timeout=20)
    if box is None:
        dismiss_overlays(chat)
        check_blockers(chat, selectors, wait_sec=wait_sec, hide_after=hide_after)
        box = find_first(chat, selectors["message_input"], timeout=10)
    if box is None:
        raise StreakError("поле ввода сообщения не найдено")

    dismiss_overlays(chat)
    check_blockers(chat, selectors, wait_sec=wait_sec, hide_after=hide_after, target_locator=box)

    try:
        box.click(timeout=5000)
    except Exception:
        dismiss_overlays(chat)
        check_blockers(chat, selectors, wait_sec=wait_sec, hide_after=hide_after, target_locator=box)
        box.click(timeout=10000)

    # Контрольная проверка прямо перед вводом: в окне чата точно открыт нужный собеседник
    opened_user = active_chat_username(chat, selectors)
    if opened_user and opened_user != username:
        raise StreakError(f"защита от промаха: открыт чат @{opened_user} вместо @{username}, отправка отменена")

    chat.keyboard.insert_text(text)
    chat.wait_for_timeout(500)
    if not input_text(box):
        log.debug("insert_text не сработал, печатаю посимвольно")
        box.press_sequentially(text, delay=60)
        chat.wait_for_timeout(500)
    if not input_text(box):
        raise StreakError("не удалось ввести текст в поле")

    if dry_run:
        log.info("[dry-run] @%s: введено %r, не отправляю", username, text)
        chat.keyboard.press("Control+A")
        chat.keyboard.press("Backspace")
        return

    chat.keyboard.press("Enter")

    deadline = time.monotonic() + 10
    while input_text(box):
        if time.monotonic() > deadline:
            raise StreakError("после Enter поле не очистилось — сообщение, похоже, не отправлено")
        chat.wait_for_timeout(300)
    chat.wait_for_timeout(1000)


def active_chat_username(page, selectors: dict) -> str:
    """
    Точно определяет @uniqueId открытого диалога напрямую из React Fiber контейнера
    [data-e2e="dm-new-chatbox"] (по toParticipantUserId -> getUser(uid).uniqueId),
    не путаясь на репостах видео и аватарках внутри переписки.
    """
    try:
        uid_name = page.evaluate(
            """() => {
                const box = document.querySelector('[data-e2e="dm-new-chatbox"]');
                if (!box) return '';

                // 1. Достаём getUser(uid) из React-хранилища
                let getUserFn = null;
                const w0 = document.querySelector('[data-e2e="dm-new-conversation-list"] [class*="--DivItemWrapper"]') || box;
                const k0 = Object.keys(w0).find(k => k.startsWith('__reactFiber$'));
                if (k0) {
                    for (let n = w0[k0], d = 0; n && d < 16; d++, n = n.return) {
                        for (let h = n.memoizedState, hi = 0; h && hi < 40; hi++, h = h.next) {
                            const ms = h.memoizedState;
                            const cand = Array.isArray(ms) ? ms[0] : ms;
                            if (cand && typeof cand.getUser === 'function') {
                                getUserFn = cand.getUser;
                                break;
                            }
                        }
                        if (getUserFn) break;
                    }
                }

                // 2. Ищем активный conversation объект в React Fiber самого окна чата
                const kb = Object.keys(box).find(k => k.startsWith('__reactFiber$'));
                if (kb && getUserFn) {
                    for (let n = box[kb], d = 0; n && d < 25; d++, n = n.return) {
                        for (let h = n.memoizedState, hi = 0; h && hi < 35; hi++, h = h.next) {
                            const ms = h.memoizedState;
                            const cand = ms?.current || (Array.isArray(ms) ? ms[0] : ms);
                            if (cand && typeof cand === 'object' && cand.toParticipantUserId) {
                                const u = getUserFn(String(cand.toParticipantUserId));
                                if (u && u.uniqueId) return String(u.uniqueId);
                            }
                        }
                    }
                }

                // 3. Резерв: карточка @uniqueId в шапке диалога (не ссылки из сообщений!)
                const pUid = box.querySelector('[class*="--PUniqueId"], [class*="--SpanHeaderUserId"]');
                if (pUid && pUid.textContent) return pUid.textContent.trim();
                return '';
            }"""
        )
        if uid_name:
            return norm_username(uid_name)
    except Exception:
        pass
    return ""


def open_chat_from_messages_list(page, username: str, selectors: dict, cfg: dict, meta_by_user: dict[str, dict] | None = None) -> bool:
    """
    Открывает чат с @username на странице /messages строго по актуальному индексу
    из React Fiber (после каждой отправки порядок чатов меняется, поэтому индекс
    пересчитывается вживую перед кликом).
    """
    wait_sec = cfg.get("captcha_wait_sec", 300)
    hide_after = bool(cfg.get("hide_window", True))
    if "/messages" not in page.url:
        goto(page, f"{BASE_URL}/messages")

    dismiss_overlays(page)
    check_blockers(page, selectors, wait_sec=wait_sec, hide_after=hide_after)

    first_item = find_first(page, selectors.get("chat_list_items", []), timeout=25.0)
    if first_item is None:
        check_blockers(page, selectors, wait_sec=wait_sec, hide_after=hide_after)
        first_item = find_first(page, selectors.get("chat_list_items", []), timeout=15.0)
    if first_item is None:
        log.warning("Список чатов на /messages не загрузился")
        return False

    if active_chat_username(page, selectors) == username:
        return True

    item_loc = page.locator('[data-e2e="dm-new-conversation-list"] [class*="--DivItemWrapper"]')
    if item_loc.count() == 0:
        for sel in selectors.get("chat_list_items", []):
            loc = page.locator(sel)
            if loc.count() > 0:
                item_loc = loc
                break

    # Всегда запрашиваем СВЕЖИЕ метаданные списка чатов прямо перед кликом,
    # так как после отправки предыдущему другу его чат прыгает на 1-е место и сдвигает индексы!
    for scroll_try in range(4):
        live_meta = inspect_conversations_metadata(page)
        live_by_user = {c["username"]: c for c in live_meta if c.get("username")}
        if meta_by_user is not None:
            meta_by_user.clear()
            meta_by_user.update(live_by_user)

        if username in live_by_user:
            target_idx = live_by_user[username]["idx"]
            if target_idx < item_loc.count():
                try:
                    item = item_loc.nth(target_idx)
                    item.scroll_into_view_if_needed(timeout=3000)
                    dismiss_overlays(page)
                    check_blockers(page, selectors, wait_sec=wait_sec, hide_after=hide_after, target_locator=item)
                    item.click(timeout=5000)
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        if active_chat_username(page, selectors) == username:
                            log.info("Чат с @%s открыт (#%d в списке)", username, target_idx + 1)
                            return True
                        page.wait_for_timeout(200)
                except Exception as e:
                    log.debug("Клик по чату @%s (#%d) не сработал: %s", username, target_idx + 1, e)

        # Если друг ниже в списке — прокручиваем список чатов вниз, чтобы подгрузить следующие
        cnt = item_loc.count()
        if cnt > 0 and scroll_try < 3:
            try:
                item_loc.nth(cnt - 1).scroll_into_view_if_needed(timeout=2000)
                page.wait_for_timeout(800)
            except Exception:
                break

    seen = [c["username"] for c in inspect_conversations_metadata(page) if c.get("username")]
    log.warning(
        "Чат с @%s не найден в списке чатов (найдены: %s)",
        username,
        ", ".join("@" + u for u in seen) if seen else "нет",
    )
    return False


def send_to(
    ctx,
    page,
    username: str,
    text: str,
    selectors: dict,
    dry_run: bool,
    cfg: dict,
    meta_by_user: dict[str, dict] | None = None,
    skip_if_already_sent: bool = False,
) -> bool:
    """Возвращает True, если сообщение было отправлено, или False, если при открытии чата обнаружилось, что сегодня уже писали."""
    if open_chat_from_messages_list(page, username, selectors, cfg, meta_by_user=meta_by_user):
        if skip_if_already_sent and not dry_run:
            page.wait_for_timeout(600)
            fresh = {c["username"]: c for c in inspect_conversations_metadata(page) if c.get("username")}
            if fresh.get(username, {}).get("sent_by_me_today"):
                log.info("Пропускаю @%s — внутри открытого чата видно, что ты уже писал сегодня", username)
                return False
        type_and_send(page, selectors, username, text, dry_run, cfg)
        return True

    raise StreakError(
        f"чат с @{username} не найден в списке сообщений на tiktok.com/messages "
        "(напиши этому человеку хотя бы одно сообщение вручную, чтобы диалог появился в списке)"
    )


# --------------------------------------------------------------------------- #
# Команды
# --------------------------------------------------------------------------- #
def cmd_login(args) -> int:
    from playwright.sync_api import sync_playwright

    cfg = load_config(required=False)
    print("Откроется окно браузера на основном экране. Войди в TikTok, затем просто закрой окно.")
    with sync_playwright() as p:
        ctx = launch_browser(p, cfg, headless=False, hide_window=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        bring_window_to_front(page)
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        ctx.wait_for_event("close", timeout=0)
    print("Профиль сохранён. Проверь сессию: python streak.py check")
    return 0


def cmd_check(args) -> int:
    from playwright.sync_api import sync_playwright

    cfg = load_config(required=False)
    with sync_playwright() as p:
        ctx = launch_browser(p, cfg, headless=cfg["headless"], hide_window=cfg["hide_window"])
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            ensure_logged_in(ctx, page, cfg["selectors"], cfg)
        except NeedLogin:
            log.error("Не авторизован. Запусти: python streak.py login")
            return 2
        except CaptchaDetected:
            log.error("Капча не была пройдена вовремя. Попробуй снова.")
            return 2
        finally:
            ctx.close()
    log.info("Всё ок")
    return 0


def scan_streaks(cfg: dict | None = None) -> list[dict]:
    """Открывает /messages и возвращает список всех диалогов с метаданными огоньков и активности."""
    from playwright.sync_api import sync_playwright

    full_cfg = load_config(required=False)
    if cfg:
        full_cfg.update({k: v for k, v in cfg.items() if k != "selectors"})
    with sync_playwright() as p:
        ctx = launch_browser(p, full_cfg, headless=full_cfg["headless"], hide_window=full_cfg["hide_window"])
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            ensure_logged_in(ctx, page, full_cfg["selectors"], full_cfg)
            return inspect_conversations_metadata(page)
        except CaptchaDetected:
            if full_cfg.get("hide_window", True):
                ctx.close()
                log.info("В фоновом режиме появилась капча — открываю видимое окно для решения...")
                ctx = launch_browser(p, full_cfg, headless=False, hide_window=False)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                ensure_logged_in(ctx, page, full_cfg["selectors"], full_cfg)
                return inspect_conversations_metadata(page)
            raise
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def format_streak_status(chat: dict) -> str:
    parts = []
    if chat.get("streak_num"):
        if chat.get("streak_style") == "grey":
            parts.append(f"⚪ серия {chat['streak_num']} дн. (потух)")
        else:
            parts.append(f"🔥 серия {chat['streak_num']} дн.")
    elif chat.get("has_streak_interaction"):
        parts.append("🔥 огонёк/искра")

    if chat.get("both_active_today"):
        parts.append("сегодня горит (оба писали)")
    elif chat.get("sent_by_me_today"):
        parts.append("ты уже писал сегодня (ждём ответа)")
    elif chat.get("sent_by_peer_today"):
        parts.append("друг писал сегодня — ждёт твоего ответа!")
    elif chat.get("last_msg_date"):
        d = chat["days_since_last_msg"]
        if d == 1:
            parts.append("последнее сообщение вчера — пора писать!")
        else:
            parts.append(f"последнее сообщение {chat['last_msg_date']} ({d} дн. назад)")
    return " | ".join(parts)


def cmd_scan(args) -> int:
    cfg = load_config(required=False)
    chats = scan_streaks(cfg)
    if not chats:
        log.warning("Чаты не найдены")
        return 1

    print("\nНайденные диалоги в TikTok:")
    for c in chats:
        if not c["username"]:
            continue
        status = format_streak_status(c)
        need = "⏳ НУЖНО ОТПРАВИТЬ" if not c["sent_by_me_today"] and c["days_since_last_msg"] <= cfg.get("active_streak_days_window", 3) else "✅ ок"
        print(f"  @{c['username']:<18} ({c['nickname']}) — {status} [{need}]")
    return 0


def _run_session(p, cfg: dict, state: dict, today: str, friends: list[dict], args, hide_window: bool) -> list[str]:
    """Выполняет один сеанс проверки и отправки в браузере. Возвращает список неудачных ников."""
    selectors = cfg["selectors"]
    auto_detect = bool(cfg.get("auto_send_active_streaks", False))
    failed: list[str] = []

    ctx = launch_browser(p, cfg, headless=cfg["headless"], hide_window=hide_window)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ensure_logged_in(ctx, page, selectors, cfg)

        chats_meta = inspect_conversations_metadata(page)
        meta_by_user = {c["username"]: c for c in chats_meta if c.get("username")}

        friends_by_user = {norm_username(f["username"]): dict(f) for f in friends}
        if auto_detect and not args.only:
            for c in chats_meta:
                u = c.get("username")
                if not u or u in friends_by_user:
                    continue
                if c.get("has_streak_interaction") or c.get("streak_num"):
                    friends_by_user[u] = {"username": u}
                    log.info("Автоопределён активный огонёк: @%s (%s)", u, format_streak_status(c))

        smart_skip = bool(cfg.get("smart_skip_if_sent_today", True))
        pending: list[dict] = []
        for u, f in friends_by_user.items():
            meta = meta_by_user.get(u)
            if not args.force and not args.dry_run:
                if state["last_sent"].get(u) == today:
                    continue
                if smart_skip and meta and meta.get("sent_by_me_today"):
                    log.info("Пропускаю @%s — ты уже отправлял ему сообщение сегодня (видно в чате TikTok)", u)
                    state["last_sent"][u] = today
                    save_state(state)
                    continue
            elif smart_skip and not args.dry_run and getattr(args, "respect_chat_history", False):
                if state["last_sent"].get(u) == today or (meta and meta.get("sent_by_me_today")):
                    log.info("Пропускаю @%s — огонёк на сегодня уже отправлен", u)
                    state["last_sent"][u] = today
                    save_state(state)
                    continue
            pending.append(f)

        if not pending:
            log.info("Всем друзьям с огоньками сегодня уже отправлено сообщение!")
            return []

        log.info("Отправка: %s", ", ".join("@" + norm_username(f["username"]) for f in pending))

        allow_skip_inside_chat = smart_skip and (not args.force or getattr(args, "respect_chat_history", False))
        for i, friend in enumerate(pending):
            username = norm_username(friend["username"])
            text = pick_message(friend, cfg)
            try:
                sent_now = send_to(
                    ctx,
                    page,
                    username,
                    text,
                    selectors,
                    args.dry_run,
                    cfg,
                    meta_by_user=meta_by_user,
                    skip_if_already_sent=allow_skip_inside_chat,
                )
                if not args.dry_run:
                    state["last_sent"][username] = today
                    save_state(state)
                    if sent_now:
                        log.info("✓ @%s: %s", username, text)
            except (NeedLogin, CaptchaDetected):
                raise
            except Exception as e:
                if not has_session(ctx):
                    log.error("✗ @%s: TikTok завершил сессию во время работы", username)
                    raise NeedLogin()
                log.error("✗ @%s: %s", username, e)
                log.debug("Подробности", exc_info=True)
                failed.append(username)

            if i < len(pending) - 1:
                lo, hi = cfg["delay_between_friends_sec"]
                page.wait_for_timeout(random.uniform(lo, hi) * 1000)
        return failed
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def cmd_run(args) -> int:
    cfg = load_config()
    state = load_state()
    today = date.today().isoformat()

    auto_detect = bool(cfg.get("auto_send_active_streaks", False))
    friends = [f for f in cfg["friends"] if f.get("username")]
    if args.only:
        only = {norm_username(u) for u in args.only}
        friends = [f for f in friends if norm_username(f["username"]) in only]

    if not friends and not auto_detect:
        log.warning("Список друзей пуст — добавь друзей в меню или включи автопоиск огоньков")
        return 0

    if not args.force and datetime.now().time() < parse_time(cfg["send_time"]):
        log.debug("Ещё рано: отправка в %s", cfg["send_time"])
        return 0

    if not auto_detect:
        pending_cfg = [
            f for f in friends
            if args.force or args.dry_run
            or state["last_sent"].get(norm_username(f["username"])) != today
        ]
        if not pending_cfg:
            log.debug("Сегодня уже всё отправлено")
            return 0

    from playwright.sync_api import sync_playwright

    try:
        with Lock():
            with sync_playwright() as p:
                hide_win = bool(cfg.get("hide_window", True))
                try:
                    failed = _run_session(p, cfg, state, today, friends, args, hide_window=hide_win)
                except (CaptchaDetected, NeedLogin):
                    if hide_win:
                        log.warning("В фоновом режиме потребовался вход или капча — открываю видимое окно на экране...")
                        failed = _run_session(p, cfg, state, today, friends, args, hide_window=False)
                    else:
                        raise

            if failed:
                notify(APP_NAME, "Не отправлено: " + ", ".join("@" + u for u in failed) + ". Повторю позже.")
                return 1
            return 0

    except AlreadyRunning:
        log.info("Уже запущен другой экземпляр — выхожу")
        return 0
    except NeedLogin:
        log.error("Сессия TikTok истекла")
        notify(APP_NAME, "Сессия TikTok истекла. Запусти: python streak.py login")
        return 2
    except CaptchaDetected:
        log.error("TikTok показал проверку (капчу), время ожидания истекло")
        notify(APP_NAME, "Капча не была пройдена за отведённое время. Повторю позже.")
        return 2


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (r.stdout or "").lower()
        return "python" in out and f'"{pid}"' in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cmd_daemon(args) -> int:
    """
    Тихий фоновый процесс (запускается через pythonw.exe без единого окна):
    висит в фоне и раз в 5 минут проверяет, не пора ли отправить огоньки.
    """
    if DAEMON_PID_PATH.exists():
        try:
            old_pid = int(DAEMON_PID_PATH.read_text(encoding="utf-8").strip())
            if old_pid != os.getpid() and is_pid_alive(old_pid):
                log.info("Фоновый демон уже работает (PID %d) — выхожу", old_pid)
                return 0
        except Exception:
            pass

    DAEMON_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    log.info("Фоновый демон TikTok Streak Keeper запущен (PID %d)", os.getpid())
    try:
        while True:
            rc = 0
            try:
                rc = cmd_run(argparse.Namespace(force=False, dry_run=False, only=None, respect_chat_history=True))
            except SystemExit:
                pass
            except Exception:
                log.exception("Ошибка в цикле демона")
            # Если окно входа/капчи было проигнорировано (пользователя нет у ПК) — ждём 30 минут, иначе 5 минут
            time.sleep(1800 if rc == 2 else 300)
    finally:
        DAEMON_PID_PATH.unlink(missing_ok=True)


def main() -> int:
    # Без аргументов — интерактивное меню
    if len(sys.argv) == 1:
        setup_logging(verbose=False)
        import menu
        try:
            menu.run(sys.modules[__name__])
        except (KeyboardInterrupt, EOFError):
            pass
        return 0

    parser = argparse.ArgumentParser(prog="streak", description="Поддерживает огоньки в TikTok.")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="открыть браузер и войти в TikTok")
    sub.add_parser("check", help="проверить, что сессия жива")
    sub.add_parser("scan", help="найти активные чаты/огоньки и показать статус на сегодня")
    sub.add_parser("daemon", help="тихий фоновый сервис (без окон)")
    run = sub.add_parser("run", help="отправить сообщения (если пора и ещё не отправлено)")
    run.add_argument("--force", action="store_true", help="игнорировать время и историю отправок")
    run.add_argument("--dry-run", action="store_true", help="ввести сообщение, но не отправлять")
    run.add_argument("--only", nargs="+", metavar="USERNAME", help="только указанным пользователям")

    args = parser.parse_args()
    setup_logging(args.verbose)

    commands = {
        "login": cmd_login,
        "check": cmd_check,
        "scan": cmd_scan,
        "daemon": cmd_daemon,
        "run": cmd_run,
    }
    try:
        return commands[args.command](args)
    except SystemExit:
        raise
    except Exception:
        log.exception("Непредвиденная ошибка")
        notify(APP_NAME, "Непредвиденная ошибка, подробности в logs/streak.log")
        return 1


if __name__ == "__main__":
    sys.exit(main())
