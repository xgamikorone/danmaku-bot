# Bilibili Danmaku Bot

一个按直播状态自动发送弹幕的异步 Python bot。


## Usage

1. Install dependencies: `python -m pip install -r requirements.txt`
2. Copy `danmaku_cfg.example.json` and rename it to `danmaku_cfg.json`
3. Filling in the necessary information in `danmaku_cfg.json`, in detail:
   - Export the browser cookies to `cookies.json`. It must contain `bili_jct` and `SESSDATA`.
   - Never commit or share `cookies.json`.
   - `global_interval`: The interval between two consecutive danmaku in seconds.
   - `rooms`:
      - `room_id`: The ID of the room to send danmaku to. e.g. 23771189 for https://live.bilibili.com/23771189
      - `msg`: The danmaku message to send.
      - `interval`: The interval between two consecutive danmaku in seconds.
      - `enable`: Whether to enable this room for danmaku sending.
4. Run `python bot.py`

## Server酱通知

在 `danmaku_cfg.json` 中启用通知：

```json
"notifications": {
  "enabled": true,
  "cooldown": 1800,
  "notify_recovery": true,
  "daily_limit": 5
}
```

通过环境变量提供 SendKey，不要写入配置或提交到 Git：

```powershell
$env:SERVERCHAN_SENDKEY="你的SendKey"
python bot.py
```

Linux/systemd 环境可设置 `Environment=SERVERCHAN_SENDKEY=你的SendKey`。接口返回
非零 `code` 时会立即通知；相同房间和错误码在冷却时间内不会重复推送，恢复成功后可发送恢复通知。
告警和恢复通知合计每天最多发送 `daily_limit` 条，计数保存在
`.serverchan_state.json`，因此程序重启不会重置当天额度。

## Note

`bot.py` is the recommended asynchronous entry point. `danmaku_bot.py` and
`danmaku_bot_v2.py` are retained for compatibility.

Temporary network failures do not count as the streamer going offline. The bot
also backs off after repeated query or send failures.

If you made changes to `danmaku_cfg.json` and want to restart the bot, you can simply terminate the program and run it again. The program will automatically load the new configuration.
