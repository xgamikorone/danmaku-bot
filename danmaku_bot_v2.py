import asyncio
import aiohttp
import json
import time
import logging
from logging.handlers import TimedRotatingFileHandler
import cookies

# ------------------ 日志配置 ------------------
log_format = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

file_handler = TimedRotatingFileHandler(
    filename="danmaku.log",
    when="D",
    interval=1,
    backupCount=7,
    encoding="utf-8",
)
file_handler.setFormatter(log_format)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_format)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler],
)

# ------------------ 配置读取 ------------------
with open("danmaku_cfg.json", "r", encoding="utf-8") as f:
    config = json.load(f)

cookie = cookies.get_cookies()
csrf = cookie["bili_jct"]
cookie_str = cookies.parse_cookies()

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Cookie": cookie_str,
    "Content-Type": "application/x-www-form-urlencoded",
}

PROXIES = None

GLOBAL_INTERVAL = config["global_interval"]
LAST_SEND_TIME = 0.0
SEND_LOCK = asyncio.Lock()


# ================== 核心逻辑 ==================

async def is_live(session: aiohttp.ClientSession, room_id: int) -> bool:
    """判断是否在播"""
    try:
        url = f"https://api.live.bilibili.com/room/v1/Room/get_info?id={room_id}"
        async with session.get(url) as resp:
            if resp.status != 200:
                return False
            data = await resp.json()
            return data.get("data", {}).get("live_status") == 1
    except Exception as e:
        logging.warning(f"[{room_id}] 检测直播状态失败：{e}")
        return False


async def send_danmaku(session: aiohttp.ClientSession, room_id: int, msg: str):
    """发送一条弹幕（带全局频率限制）"""
    global LAST_SEND_TIME

    async with SEND_LOCK:
        now = time.time()
        wait = GLOBAL_INTERVAL - (now - LAST_SEND_TIME)
        if wait > 0:
            logging.info(f"[{room_id}] ⏳ 全局频率限制，等待 {wait:.2f} 秒")
            await asyncio.sleep(wait)

        data = {
            "roomid": room_id,
            "msg": msg,
            "rnd": int(time.time()),
            "fontsize": 25,
            "color": 16777215,
            "csrf": csrf,
            "csrf_token": csrf,
        }

        success = False

        try:
            async with session.post(
                "https://api.live.bilibili.com/msg/send",
                data=data,
                headers=HEADERS,
                proxy=PROXIES,
            ) as resp:
                status = resp.status
                result = await resp.json()
                logging.info(f"[{room_id}] 弹幕返回：{status} | {result}")

                if status == 200 and result.get("code") == 0:
                    success = True

        except Exception as e:
            logging.error(f"[{room_id}] ❌ 发送失败：{e}")

        if success:
            LAST_SEND_TIME = time.time()


async def danmu_loop(
    session: aiohttp.ClientSession,
    room_id: int,
    msg: str,
    interval: float,
):
    """房间弹幕循环"""
    logging.info(f"[{room_id}] ✅ 启动弹幕循环（间隔 {interval}s）")

    try:
        while True:
            if not await is_live(session, room_id):
                logging.info(f"[{room_id}] ⛔ 主播已下播，停止发送")
                break

            await send_danmaku(session, room_id, msg)
            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        logging.info(f"[{room_id}] 🔴 弹幕任务被取消")
        raise
    except Exception as e:
        logging.error(f"[{room_id}] 弹幕循环异常：{e}")


async def monitor_room(session: aiohttp.ClientSession, room: dict):
    """监控单个房间"""
    if not room.get("enable", False):
        return

    room_id = room["room_id"]
    msg = room["msg"]
    interval = room["interval"]

    logging.info(f"[{room_id}] 👀 开始监控房间")

    danmu_task = None

    while True:
        try:
            now_live = await is_live(session, room_id)

            if now_live and danmu_task is None:
                logging.info(f"[{room_id}] 🎥 主播开播，启动弹幕发送")
                danmu_task = asyncio.create_task(
                    danmu_loop(session, room_id, msg, interval)
                )

            elif not now_live and danmu_task:
                logging.info(f"[{room_id}] 📴 主播下播，停止弹幕发送")
                danmu_task.cancel()
                try:
                    await danmu_task
                except asyncio.CancelledError:
                    pass
                danmu_task = None

        except Exception as e:
            logging.error(f"[{room_id}] 监控异常：{e}")

        await asyncio.sleep(30)


# ================== 主程序 ==================

async def main():
    timeout = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(
        headers=HEADERS,
        timeout=timeout,
    ) as session:

        tasks = [
            monitor_room(session, room)
            for room in config["rooms"]
        ]

        await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("收到 Ctrl+C，程序退出")
    except Exception as e:
        logging.error(f"主程序异常退出：{e}")
