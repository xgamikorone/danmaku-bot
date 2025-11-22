import requests
import time
import json
import threading
import logging

from logging.handlers import TimedRotatingFileHandler

log_format = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

file_handler = TimedRotatingFileHandler(
    filename="danmaku.log",
    when="D",       # 'S', 'M', 'H', 'D', 'W0'-'W6', 'midnight'
    interval=1,     # 每1天轮换一次
    backupCount=7,  # 保留7天日志
    encoding='utf-8'
)
file_handler.setFormatter(log_format)


console_handler = logging.StreamHandler()
console_handler.setFormatter(log_format)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)

with open("danmaku_cfg.json", "r", encoding="utf-8") as f:
    config = json.load(f)

csrf = config["global"]["csrf"]
sessdata = config["global"]["sessdata"]

cookies = {
    "SESSDATA": sessdata,
    "bili_jct": csrf
}

headers = {
    "User-Agent": "Mozilla/5.0"
}

proxies = {
    "http": "http://127.0.0.1:7890",      # 本地 HTTP 代理示例
    "https": "http://127.0.0.1:7890"
}

send_lock = threading.Lock()
last_send_time = 0
global_interval = config["global_interval"]

def is_live(room_id):
    try:
        url = f"https://api.live.bilibili.com/room/v1/Room/get_info?id={room_id}"
        res = requests.get(url, headers=headers, timeout=5, proxies=proxies)
        data = res.json()
        return data["data"]["live_status"] == 1
    except Exception as e:
        logging.warning(f"[{room_id}] 检测直播状态失败：{e}")
        return False

def danmu_loop(room_id, msg, interval):
    global last_send_time
    logging.info(f"[{room_id}] ✅ 开始发送弹幕")
    while is_live(room_id):
        with send_lock:
            wait = global_interval - (time.time() - last_send_time)
            if wait > 0:
                logging.info(f"[{room_id}] ⏳ 等待 {wait:.2f} 秒")
                time.sleep(wait)
            data = {
                "roomid": room_id,
                "msg": msg,
                "rnd": int(time.time()),
                "fontsize": 25,
                "color": 16777215,
                "csrf": csrf,
                "csrf_token": csrf
            }
            try:
                res = requests.post("https://api.live.bilibili.com/msg/send", data=data,
                                    cookies=cookies, headers=headers, proxies=proxies)
                logging.info(f"[{room_id}] 弹幕返回：{res.status_code} | {res.json()}")
            except Exception as e:
                logging.error(f"[{room_id}] ❌ 发送失败：{e}")
            last_send_time = time.time()
        time.sleep(interval)
    logging.info(f"[{room_id}] ⛔ 主播已下播，停止发送")

def monitor_room(room):
    if not room.get("enable"):
        return
    room_id = room["room_id"]
    msg = room["msg"]
    interval = room["interval"]
    live = False
    while True:
        now_live = is_live(room_id)
        if now_live and not live:
            # 主播刚刚开播，启动弹幕线程
            threading.Thread(target=danmu_loop, args=(room_id, msg, interval), daemon=True).start()
            live = True
            logging.info(f"[{room_id}] 🎥 主播开播，启动弹幕发送")
        elif not now_live and live:
            # 主播下播，等待下次开播
            live = False
            logging.info(f"[{room_id}] 📴 主播下播，停止弹幕发送")
        time.sleep(30)  # 检查间隔时间可调

if __name__ == '__main__':
    
    # 启动所有房间监控线程
    for room in config["rooms"]:
        threading.Thread(target=monitor_room, args=(room,), daemon=True).start()

    # 主线程保持运行
    while True:
        time.sleep(3600)
