# iptv-english

英语 / 中文频道的 IPTV 播放列表，从 [iptv-org/iptv](https://github.com/iptv-org/iptv)
按语言表自动筛选、探活、打分、归并，每天由 GitHub Actions 自动刷新。

**本项目只托管播放列表文本，不托管任何视频内容。** 所有流地址均来自 iptv-org 收录的公开源。

## 播放列表

### 英语（`eng`）

| 文件 | 频道数 | 说明 |
|---|---|---|
| [`english-essentials.m3u`](english-essentials.m3u) | ~236 | **推荐**。每个品牌只保留一条，全部经过探活 |
| [`english-top.m3u`](english-top.m3u) | ~373 | 探活通过的全部频道，按综合分数排序 |
| [`english-clean.m3u`](english-clean.m3u) | ~2188 | 剔除锁区/非全天/宗教/议会/购物 |
| [`english-all.m3u`](english-all.m3u) | ~2993 | iptv-org 英语表原样 |

### 中文（`zho`）

| 文件 | 频道数 | 说明 |
|---|---|---|
| [`chinese-essentials.m3u`](chinese-essentials.m3u) | ~72 | **推荐**。同频道不同画质只留一条 |
| [`chinese-top.m3u`](chinese-top.m3u) | ~75 | 探活通过的全部频道 |
| [`chinese-clean.m3u`](chinese-clean.m3u) | ~136 | 剔除锁区/非全天/宗教/购物 |
| [`chinese-all.m3u`](chinese-all.m3u) | ~216 | iptv-org 中文表原样 |

中文表频道数少，不做品牌白名单，全量参与探活。内容以 CCTV 全套、香港（Jade / KIX / 美亚）、
台湾（TVBS-Asia）、新加坡（8 频道 / meWATCH）及各省市台为主。

### 在线地址

**主用 `raw`**（永远最新，无缓存）：

```
https://raw.githubusercontent.com/douxt/iptv-english/main/english-essentials.m3u
https://raw.githubusercontent.com/douxt/iptv-english/main/chinese-essentials.m3u
```

**备用 jsDelivr**（实测会滞后一个版本，仅在 raw 拉不动时使用）：

```
https://cdn.jsdelivr.net/gh/douxt/iptv-english@main/english-essentials.m3u
```

## 评分模型

满分 98，用在 `english-top.m3u` 的排序上：

| 维度 | 权重 | 判据 |
|---|---|---|
| 稳定 | 40 | 探活返回 200 / 206 / 204 |
| 知名度 | 40 / 32 / 22 / 12 | 国际大台 → 国家级 → 垂直品牌 → 其他白名单 |
| 基础设施 | +8 | 位于 Tubi / Samsung / Pluto / Roku / Wurl / Akamai / CloudFront |
| 画质 | 0–10 | 1080p=10 · 720p=6 · 480p=3 · 未知=5 |

## 用法

**Kodi** — 安装 `PVR IPTV Simple Client`，M3U 地址填上面的 CDN 链接。

**SrcBox / VLC / mpv / TiviMate / IPTV Smarters** — 直接粘贴同一个 URL。

## 本地运行

```bash
python refresh.py                 # 全流程（两种语言，约 3 分钟）
python refresh.py --no-probe      # 跳过探活，几秒出结果
python refresh.py --lang chinese  # 只跑中文
```

纯标准库，无第三方依赖。

## 已知限制

- **探活在 GitHub Actions 上执行，测的是美国到源站的网络路径**，不是你的本地路径。
  死链在任何网络下都是死链（这部分有效），但「本地不可达」这一维测不出来。
- **EPG 未附带。** iptv-org 的 EPG 需自行运行其 Node 工具生成；
  [epg.pw](https://epg.pw) 使用纯数字 id，与 iptv-org 的域名式 id（如 `AlJazeera.qa@English`）不兼容。
- 公开源寿命短，榜单每天重算，但个别频道仍可能随时失效。

## 致谢与声明

数据来源 [iptv-org/iptv](https://github.com/iptv-org/iptv)（Unlicense）。
本项目仅做筛选与排序，不对任何流的可用性、稳定性或合法性作担保。
使用者需自行确认所在地区的相关规定。
