A simple danmaku bot for bilibili.com written in Python.


## Usage

1. Install dependencies: requests
2. Copy `danmaku_cfg.example.json` and rename it to `danmaku_cfg.json`
3. Filling in the necessary information in `danmaku_cfg.json`, in detail:
   - `csrf`: `bili_jct`
   - `sessdata`: `SESSDATA`
   A simple way to get `csrf` and `sessdata` is to use a browser extension such as *EditThisCookie*, and inspect the network requests made by the webpage when you login to bilibili.com.
   - `global_interval`: The interval between two consecutive danmaku in seconds.
   - `rooms`:
      - `room_id`: The ID of the room to send danmaku to. e.g. 23771189 for https://live.bilibili.com/23771189
      - `msg`: The danmaku message to send.
      - `interval`: The interval between two consecutive danmaku in seconds.
      - `enable`: Whether to enable this room for danmaku sending.
4. Run `danmaku_bot.py`