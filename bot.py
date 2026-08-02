import asyncio
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import date
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import aiohttp

import cookies

BASE_DIR = Path(__file__).resolve().parent
LIVE_URL = "https://api.live.bilibili.com/room/v1/Room/get_info"
SEND_URL = "https://api.live.bilibili.com/msg/send"
MONITOR_INTERVAL = 30.0
MAX_BACKOFF = 300.0


@dataclass(frozen=True)
class Room:
    room_id: int
    msg: str
    interval: float


@dataclass(frozen=True)
class NotificationConfig:
    enabled: bool = False
    cooldown: float = 1800.0
    notify_recovery: bool = True
    daily_limit: int = 5


class ServerChanNotifier:
    def __init__(
        self,
        config: NotificationConfig,
        state_path: Path | None = None,
    ) -> None:
        self.enabled = config.enabled
        self.cooldown = config.cooldown
        self.notify_recovery = config.notify_recovery
        self.daily_limit = config.daily_limit
        self.sendkey = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
        self.state_path = state_path or (BASE_DIR / ".serverchan_state.json")
        self.push_lock = asyncio.Lock()
        self.quota_date = date.today().isoformat()
        self.quota_count = 0
        self.quota_warning_date: str | None = None
        self.last_notified: dict[tuple[int, str], float] = {}
        self.active_errors: dict[int, tuple[str, str]] = {}
        if self.enabled and not self.sendkey:
            raise ValueError("已启用 Server酱，但未设置环境变量 SERVERCHAN_SENDKEY")
        self.load_quota_state()

    def load_quota_state(self) -> None:
        try:
            with self.state_path.open("r", encoding="utf-8") as file:
                state = json.load(file)
            if state.get("date") == self.quota_date:
                count = state.get("count", 0)
                if isinstance(count, int) and count >= 0:
                    self.quota_count = count
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            logging.warning("无法读取 Server酱额度状态，将从 0 开始计数：%s", exc)

    def refresh_quota_date(self) -> None:
        today = date.today().isoformat()
        if today != self.quota_date:
            self.quota_date = today
            self.quota_count = 0
            self.quota_warning_date = None

    def save_quota_state(self) -> None:
        temporary_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        try:
            temporary_path.write_text(
                json.dumps(
                    {"date": self.quota_date, "count": self.quota_count},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            temporary_path.replace(self.state_path)
        except OSError as exc:
            logging.error("无法保存 Server酱额度状态：%s", exc)

    def endpoint(self) -> str:
        # Server酱同时支持 SCT 和 sctp 开头的新格式 SendKey。
        match = re.fullmatch(r"sctp(\d+)t.+", self.sendkey)
        if match:
            return f"https://{match.group(1)}.push.ft07.com/send/{self.sendkey}.send"
        return f"https://sctapi.ftqq.com/{self.sendkey}.send"

    async def push(
        self, session: aiohttp.ClientSession, title: str, description: str
    ) -> bool:
        if not self.enabled:
            return False
        # 锁覆盖额度检查和请求，避免多个房间并发突破每日上限。
        async with self.push_lock:
            self.refresh_quota_date()
            if self.quota_count >= self.daily_limit:
                if self.quota_warning_date != self.quota_date:
                    logging.warning(
                        "Server酱今日推送已达到上限 %s 条，后续通知仅记录日志",
                        self.daily_limit,
                    )
                    self.quota_warning_date = self.quota_date
                return False
            try:
                async with session.post(
                    self.endpoint(),
                    data={"title": title.replace("\n", " "), "desp": description},
                ) as response:
                    result = await response.json(content_type=None)
                    if response.status == 200 and result.get("code") == 0:
                        self.quota_count += 1
                        self.save_quota_state()
                        logging.info(
                            "Server酱通知发送成功（今日 %s/%s）",
                            self.quota_count,
                            self.daily_limit,
                        )
                        return True
                    logging.error(
                        "Server酱通知发送失败：HTTP %s, code=%s",
                        response.status,
                        result.get("code"),
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                # 不输出异常文本，避免某些客户端异常把包含 SendKey 的 URL 写入日志。
                logging.error("Server酱通知请求失败：%s", type(exc).__name__)
        return False

    async def report_api_error(
        self,
        session: aiohttp.ClientSession,
        room_id: int,
        http_status: int,
        code: object,
        message: object,
    ) -> None:
        if not self.enabled:
            return
        code_text = str(code)
        message_text = str(message or "无错误信息")
        fingerprint = (room_id, code_text)
        now = time.monotonic()
        self.active_errors[room_id] = (code_text, message_text)
        last = self.last_notified.get(fingerprint)
        if last is not None and now - last < self.cooldown:
            return
        description = (
            f"## Bilibili 弹幕接口异常\n\n"
            f"- 房间：`{room_id}`\n"
            f"- HTTP 状态：`{http_status}`\n"
            f"- API code：`{code_text}`\n"
            f"- 信息：{message_text}\n\n"
            "可能是 Cookie 过期、账号状态异常或接口风控，请检查服务器日志和 Cookie。"
        )
        if await self.push(session, f"弹幕 Bot 告警：房间 {room_id}", description):
            self.last_notified[fingerprint] = now

    async def report_recovery(
        self, session: aiohttp.ClientSession, room_id: int
    ) -> None:
        previous = self.active_errors.pop(room_id, None)
        if not self.enabled or not self.notify_recovery or previous is None:
            return
        code, message = previous
        description = (
            f"房间 `{room_id}` 的弹幕接口已恢复正常。\n\n"
            f"上一次错误：`code={code}`，{message}"
        )
        if await self.push(session, f"弹幕 Bot 已恢复：房间 {room_id}", description):
            self.last_notified.pop((room_id, code), None)


def configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = TimedRotatingFileHandler(
        BASE_DIR / "danmaku.log", when="midnight", backupCount=7, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logging.basicConfig(
        level=logging.INFO, handlers=[file_handler, console_handler], force=True
    )


def load_config() -> tuple[float, list[Room], NotificationConfig]:
    path = BASE_DIR / "danmaku_cfg.json"
    try:
        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取配置文件 {path}: {exc}") from exc

    global_interval = raw.get("global_interval")
    if not isinstance(global_interval, (int, float)) or global_interval <= 0:
        raise ValueError("global_interval 必须是正数")
    raw_rooms = raw.get("rooms")
    if not isinstance(raw_rooms, list):
        raise ValueError("rooms 必须是数组")

    rooms: list[Room] = []
    seen: set[int] = set()
    for index, item in enumerate(raw_rooms):
        if not isinstance(item, dict):
            raise ValueError(f"rooms[{index}] 必须是对象")
        if not item.get("enable", False):
            continue
        room_id, msg, interval = item.get("room_id"), item.get("msg"), item.get("interval")
        if not isinstance(room_id, int) or isinstance(room_id, bool) or room_id <= 0:
            raise ValueError(f"rooms[{index}].room_id 必须是正整数")
        if room_id in seen:
            raise ValueError(f"房间 {room_id} 重复配置")
        if not isinstance(msg, str) or not msg.strip():
            raise ValueError(f"rooms[{index}].msg 不能为空")
        if not isinstance(interval, (int, float)) or interval <= 0:
            raise ValueError(f"rooms[{index}].interval 必须是正数")
        seen.add(room_id)
        rooms.append(Room(room_id, msg.strip(), float(interval)))
    if not rooms:
        raise ValueError("没有启用的房间")
    notification_raw = raw.get("notifications", {})
    if not isinstance(notification_raw, dict):
        raise ValueError("notifications 必须是对象")
    enabled = notification_raw.get("enabled", False)
    cooldown = notification_raw.get("cooldown", 1800)
    notify_recovery = notification_raw.get("notify_recovery", True)
    daily_limit = notification_raw.get("daily_limit", 5)
    if not isinstance(enabled, bool) or not isinstance(notify_recovery, bool):
        raise ValueError("notifications.enabled 和 notify_recovery 必须是布尔值")
    if not isinstance(cooldown, (int, float)) or cooldown < 0:
        raise ValueError("notifications.cooldown 必须是非负数")
    if not isinstance(daily_limit, int) or isinstance(daily_limit, bool) or daily_limit <= 0:
        raise ValueError("notifications.daily_limit 必须是正整数")
    notification = NotificationConfig(
        enabled, float(cooldown), notify_recovery, daily_limit
    )
    return float(global_interval), rooms, notification


def load_auth() -> tuple[str, str]:
    path = BASE_DIR / "cookies.json"
    values = cookies.get_cookies(path)
    missing = [name for name in ("bili_jct", "SESSDATA") if not values.get(name)]
    if missing:
        raise ValueError(f"cookies.json 缺少必要 Cookie: {', '.join(missing)}")
    header = cookies.parse_cookies(path, allowed_names={"bili_jct", "SESSDATA"})
    return values["bili_jct"], header


class Bot:
    def __init__(
        self, global_interval: float, csrf: str, notifier: ServerChanNotifier
    ) -> None:
        self.global_interval = global_interval
        self.csrf = csrf
        self.send_lock = asyncio.Lock()
        self.last_attempt = 0.0
        self.notifier = notifier

    async def is_live(self, session: aiohttp.ClientSession, room_id: int) -> bool | None:
        """查询失败返回 None，避免把临时网络故障误判为下播。"""
        try:
            async with session.get(LIVE_URL, params={"id": room_id}) as response:
                if response.status != 200:
                    logging.warning("[%s] 状态查询返回 HTTP %s", room_id, response.status)
                    return None
                result = await response.json(content_type=None)
                if result.get("code") != 0:
                    logging.warning("[%s] 状态查询失败，code=%s", room_id, result.get("code"))
                    return None
                status = result.get("data", {}).get("live_status")
                return status == 1 if status is not None else None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            logging.warning("[%s] 状态查询失败：%s", room_id, exc)
            return None

    async def reserve_slot(self, room_id: int) -> None:
        async with self.send_lock:
            wait = self.global_interval - (time.monotonic() - self.last_attempt)
            if wait > 0:
                logging.info("[%s] 全局限流，等待 %.2f 秒", room_id, wait)
                await asyncio.sleep(wait)
            # 失败尝试也计入限流，避免接口故障时出现请求突发。
            self.last_attempt = time.monotonic()

    async def send(
        self,
        session: aiohttp.ClientSession,
        notification_session: aiohttp.ClientSession,
        room: Room,
    ) -> bool:
        await self.reserve_slot(room.room_id)
        payload = {
            "roomid": room.room_id,
            "msg": room.msg,
            "rnd": int(time.time()),
            "fontsize": 25,
            "color": 16777215,
            "csrf": self.csrf,
            "csrf_token": self.csrf,
        }
        try:
            async with session.post(SEND_URL, data=payload) as response:
                result = await response.json(content_type=None)
                code = result.get("code")
                if response.status == 200 and code == 0:
                    logging.info("[%s] 弹幕发送成功", room.room_id)
                    await self.notifier.report_recovery(notification_session, room.room_id)
                    return True
                logging.warning(
                    "[%s] 发送失败：HTTP %s, code=%s, message=%s",
                    room.room_id, response.status, code,
                    result.get("message") or result.get("msg"),
                )
                await self.notifier.report_api_error(
                    notification_session,
                    room.room_id,
                    response.status,
                    code,
                    result.get("message") or result.get("msg"),
                )
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            logging.error("[%s] 弹幕请求失败：%s", room.room_id, exc)
        return False

    async def send_loop(
        self,
        session: aiohttp.ClientSession,
        notification_session: aiohttp.ClientSession,
        room: Room,
    ) -> None:
        failures = 0
        logging.info("[%s] 启动弹幕循环（间隔 %.1fs）", room.room_id, room.interval)
        while True:
            success = await self.send(session, notification_session, room)
            failures = 0 if success else failures + 1
            delay = room.interval
            if failures:
                delay = max(delay, min(MAX_BACKOFF, 2 ** min(failures, 8)))
                delay += random.uniform(0, min(3.0, delay * 0.1))
                logging.warning("[%s] 连续失败 %s 次，%.1f 秒后重试", room.room_id, failures, delay)
            await asyncio.sleep(delay)

    async def monitor(
        self,
        session: aiohttp.ClientSession,
        notification_session: aiohttp.ClientSession,
        room: Room,
    ) -> None:
        task: asyncio.Task | None = None
        query_failures = 0
        logging.info("[%s] 开始监控房间", room.room_id)
        try:
            while True:
                if task is not None and task.done():
                    if not task.cancelled() and (exc := task.exception()) is not None:
                        logging.error(
                            "[%s] 弹幕任务异常退出", room.room_id,
                            exc_info=(type(exc), exc, exc.__traceback__),
                        )
                    task = None

                live = await self.is_live(session, room.room_id)
                if live is None:
                    query_failures += 1
                    delay = min(MAX_BACKOFF, MONITOR_INTERVAL * 2 ** min(query_failures - 1, 3))
                    delay += random.uniform(0, 3)
                else:
                    query_failures = 0
                    delay = MONITOR_INTERVAL
                    if live and task is None:
                        logging.info("[%s] 主播开播，启动发送", room.room_id)
                        task = asyncio.create_task(
                            self.send_loop(session, notification_session, room)
                        )
                    elif not live and task is not None:
                        logging.info("[%s] 主播下播，停止发送", room.room_id)
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        task = None
                await asyncio.sleep(delay)
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def main() -> None:
    global_interval, rooms, notification_config = load_config()
    csrf, cookie_header = load_auth()
    timeout = aiohttp.ClientTimeout(total=10)
    notifier = ServerChanNotifier(notification_config)
    bot = Bot(global_interval, csrf, notifier)
    async with aiohttp.ClientSession(
        headers={"User-Agent": "Mozilla/5.0", "Cookie": cookie_header}, timeout=timeout
    ) as session, aiohttp.ClientSession(timeout=timeout) as notification_session:
        await asyncio.gather(
            *(bot.monitor(session, notification_session, room) for room in rooms)
        )


if __name__ == "__main__":
    configure_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("收到 Ctrl+C，程序退出")
    except Exception:
        logging.exception("主程序异常退出")
