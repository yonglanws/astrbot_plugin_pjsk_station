# PJSK Station

AstrBot 插件。查询 [Sekai Station（国服）](https://pjsk-zh.mid.red/) 和 [プロセカ Station（日服）](https://pjsk.mid.red/) 的实时房间列表，渲染成图片后回复。

数据直接来自站点接口，不启动浏览器：

- 国服走 SSE（`/station/api/v2/realtime`），与网页默认设置一致，只保留最近 5 分钟的房间。
- 日服走 WebSocket（`wss://api-pjsekai.mid.red/station`），与网页一致，只保留最近 10 分钟的房间，并去掉系统公告。

## 指令

消息内容需与下表完全一致（不含前后空格以外的内容）。

| 指令 | 说明 |
| --- | --- |
| `pjsk车站`、`车站`、`车牌列表`、`查车站`、`ycm` | 国服房间列表 |
| `jpycm` | 日服房间列表 |

回复先发一句「正在获取房间列表...」，成功后再发图片。失败时回复「获取失败,请稍后再试」，具体原因写在 AstrBot 日志里。

## 安装

把本目录放到 AstrBot 的插件目录（目录名保持 `astrbot_plugin_pjsk_station`），安装依赖后重启：

```bash
pip install -r requirements.txt
```

依赖：`httpx`、`websockets`、`Pillow`、`pilmoji`。不需要 Playwright 或 Chromium。

中文用插件自带的 `font.ttf`。找不到该文件时，会依次尝试文泉驿、Noto、微软雅黑、黑体等系统字体。

房间文案里的 emoji 按当前环境自动选择画法：系统里装有彩色 emoji 字体（Windows 自带 `seguiemj.ttf`，Linux 是 `fonts-noto-color-emoji`）时用本地字体绘制，不联网、十几个房间不到 0.2 秒；没有彩色字体时才用 pilmoji 从 `emojicdn.elk.sh` 下载贴图，慢一些但 emoji 更精致。Linux 上想走本地字体可以装 `fonts-noto-color-emoji`，不装也能正常显示。

## 行为说明

- 同一时间只处理一个查询。其他人同时触发时会排队，不会并发打上游接口。
- 列表按发布时间从新到旧排列。
- 数学粗体等特殊数字（例如 `𝟯𝟳𝟬𝟮𝟯`）会转成普通数字再画到图上。
- 当前没有房间时回复获取失败，而不是发一张空图。
