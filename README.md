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

房间文案里的 emoji 由 pilmoji 从 `emojicdn.elk.sh` 下载后贴上。这个 CDN 在国内经常不可达；下载失败时自动改用纯文字绘制，emoji 会变成缺字方块，图片仍会发出。

## 行为说明

- 同一时间只处理一个查询。其他人同时触发时会排队，不会并发打上游接口。
- 列表按发布时间从新到旧排列。
- 数学粗体等特殊数字（例如 `𝟯𝟳𝟬𝟮𝟯`）会转成普通数字再画到图上。
- 当前没有房间时回复获取失败，而不是发一张空图。
