# NAS 部署笔记（Synology SA6400）

> 2026-10-20 首次部署记录。目标是让 `refresh.py` 每天在 NAS 上跑一次，
> 探活后把结果 push 回本仓库。

## 为什么不在 GitHub Actions 跑

Actions runner 在美国，探的是「美国 → 源站」。家里播放时走的是
「NAS → 网关 Clash → 源站」。两条路径不同，判活结果会大面积失真。

**探活必须在播放端的网络路径上执行。** 所以 CI 已降级为手动冒烟测试
（`workflow_dispatch`，不写回仓库）。

## 架构

```
NAS 每天 11:00（DSM 任务计划）
  └─ run.sh
       ├─ 读 .env 得到 IPTV_PROXY / GIT_TOKEN / IPTV_WORKERS
       ├─ python3 refresh.py      ← 探活走网关 Clash 的 7890
       └─ git commit + push       ← 同样走代理
            ↓
       GitHub raw
            ↓
       Kodi / SrcBox
```

## 环境事实（部署前先核对）

| 项 | 值 |
|---|---|
| 主机 | `synology_epyc7002_sa6400`，x86_64 |
| SSH | `ssh root@nas`（免密） |
| Python | `python3` = **3.8.15** → 禁用 3.9+ 语法（海象、`dict\|dict`、`removeprefix`） |
| git | 2.39.1 |
| 磁盘 | `/volume1` 127 GB 可用 |
| 出网 | 到 GitHub 15–22 kB/s 且波动；**必须走代理** |
| 代理 | 网关 `192.168.31.1:7890`（HTTP，需 BASIC 认证） |

> ⚠️ NAS 被**故意排除**在 OpenClash 透明代理之外（避免 BT 流量走代理），
> 所以脚本必须显式设置 `IPTV_PROXY`。

## 目录

```
/volume1/appdata/iptv-refresh/
├─ repo/                 ← 仓库工作副本
│   ├─ .env              (600) IPTV_PROXY / GIT_TOKEN / IPTV_WORKERS=12
│   ├─ run.sh            ← 每日入口
│   └─ refresh.py
└─ refresh.log           ← run.sh 自己写入
```

**不要用 `/volume1/docker/`** —— DSM 重启可能把它改名成 `docker_1`，
导致所有 bind mount 失效（容器直接 `Exited (128)`）。

## 传输代码到 NAS

`scp` 在 Synology 上默认被拒（`Connection closed`）。用 tar 管道：

```bash
cd ~/projects/iptv-english
tar czf - . | ssh root@nas 'mkdir -p /volume1/appdata/iptv-refresh/repo \
  && tar xzf - -C /volume1/appdata/iptv-refresh/repo'
```

首次之后还要处理：

```bash
ssh root@nas 'cd /volume1/appdata/iptv-refresh/repo \
  && git config --global --add safe.directory $PWD'
```

## DSM 任务计划

`synoschedtask` **没有 `--add`**，只有 `--get / --del / --run / --sync`。
任务以 key=value 文件形式存放：

```
/usr/syno/etc/synoschedule.d/root/<id>.task
```

关键字段（daily 脚本任务）：

```ini
id=5
type=daily
week=1111111
run hour=11
run min=0
state=enabled
name=IPTV refresh
cmdArgv=<base64 of "/volume1/appdata/iptv-refresh/repo/run.sh">
cmd=<base64 of 每个字符前加反斜杠的同一路径>
```

生成方式：

```python
import base64
path = "/volume1/appdata/iptv-refresh/repo/run.sh"
cmd = base64.b64encode("".join("\\" + c for c in path).encode()).decode()
argv = base64.b64encode(path.encode()).decode()
```

写完执行 `/usr/syno/bin/synoschedtask --sync`，再用 `--get` 确认。

**手动触发要加参数**：

```bash
/usr/syno/bin/synoschedtask --run id=5 check_time=0 check_status=0
```

默认 `check_time=1` 会比对日程时间，非触发点直接跳过 —— 表现为"命令没报错但什么都没发生"。

---

# 踩过的坑（14 条）

## 传输与权限

**1. `scp` 被拒** — 报 `Connection closed`。改用 `tar | ssh` 管道。

**2. `dubious ownership`** — 文件属主与 root 不一致。
`git config --global --add safe.directory <path>`。

**3. CRLF 毁掉 shebang** — Windows 写的 `run.sh` 首行是 `#!/bin/sh\r`，
Linux 报 `No such file or directory`（文件明明存在，误导性极强）。
传输后 `tr -d '\r' < run.sh > tmp && mv tmp run.sh`。

## 网络

**4. NAS 不走 OpenClash** — 出口是 `27.197.253.224`（山东临沂联通），
境外站全挂、`github.com` 返回 200 但 0 B/s。这是有意为之（黑名单），
所以用显式代理而不是去改透明代理。

**5. 代理要 BASIC 认证** — 裸连返回 `000`，从响应头才看到
`407 Proxy Authentication Required`。凭据在路由器：

```bash
uci get 'openclash.@authentication[0].username'
uci get 'openclash.@authentication[0].password'
```

**6. URL 编码要分开做** — 把 `用户名:密码` 整体 quote 会把分隔的 `:`
编码成 `%3A`，导致拆不开。**用户名和密码分别 quote，再用 `:` 连接**。

**7. git 不认 `IPTV_PROXY`** — 环境变量只影响 Python，git 要单独配
`git config http.proxy`，否则首次 clone 卡死。

**8. 不要自己再分一次流** — Clash 的 HTTP 端口本身就是完整规则引擎
（`GEOIP,CN,DIRECT` + `MATCH,PROXY`），脚本把所有请求交给它即可，
与电视走透明代理时的判定一致。脚本侧再做 IP 归属分流是重复劳动。

## 运行时

**9. `read()` 不设上限会永久卡死** — 慢速流只要数据间隙小于超时，
`read()` 就永远不返回，线程被永久占住。实测 452 个候选卡在最后 2 个，
跑 25 分钟无进展。**清单限 256 KB、分片限 64 KB** —— 校验只需要确认
"能吐出数据"，不需要下完。

**10. DSM 的 `ps w` 不显示 python 进程** — 据此判断"进程已死"是错的，
会导致反复重启、堆出多个并发实例互相打架。**判存活必须扫 `/proc`**：

```sh
for p in /proc/[0-9]*; do
  c=$(tr "\0" " " < $p/cmdline 2>/dev/null)
  case "$c" in "python3 refresh.py "*|"/bin/sh ./run.sh "*) echo "$(basename $p)";; esac
done
```

**11. 遍历 `/proc` 杀进程会杀到自己** — shell 自己的 cmdline 里含
`refresh.py` 字样，会被 `case` 匹配到。必须排除 `$$` 且按行首精确匹配。

**12. commit 失败 `Author identity unknown`** — `run.sh` 里补仓库级
`git config user.name / user.email`。

**13. 并发数别开太大** — 24 并发时出现过卡死，**12 稳定**。
通过 `IPTV_WORKERS` 控制。

**14. 日志要自己重定向** — DSM 任务计划不接管输出。
`run.sh` 顶部加 `exec >> "$LOG" 2>&1`，否则日志全丢。

---

# 本机跑 vs NAS 跑

| 维度 | Windows PC | NAS |
|---|---|---|
| 常开 | ✗ | ✓ |
| 代理 | 本地 Clash（TUN / 系统代理） | 被黑名单排除，必须显式 `IPTV_PROXY` |
| Python | 3.x 新版 | **3.8.15**，语法受限 |
| 进程可见性 | 正常 | **`ps w` 看不到，必须扫 `/proc`** |
| 部署成本 | 直接跑 | tar 传输 + CRLF + ownership 三连 |
| 到 GitHub 的带宽 | 好 | 15–22 kB/s 且波动（走代理后正常） |
| 全量耗时 | — | ~3.5 分钟（12 并发） |

**探活结果对比**（同一份列表）：

| 探活位置 | 中文 | 英文 |
|---|---|---|
| PC 直连 | 36/128 | — |
| NAS 直连 | 30/128 | — |
| **NAS 走网关代理** | **38/128** | **142/452** |

中文数几乎不变 —— 中文源以大陆站为主，两种情况都按 `GEOIP,CN,DIRECT`
走直连，路径本来就一样。**英文才有明显差距**，那才是代理起作用的地方。

走代理后 NAS 的出口 IP 与 PC 一致（`103.204.174.169`），
即探活路径 == 播放路径。

**NAS 比 PC 更合适的真正原因不是带宽，是常开** —— 探活是快照，
只有每天都能跑，快照才有意义。

---

# 日常维护

```bash
# 看昨天跑得怎么样
ssh root@nas 'tail -30 /volume1/appdata/iptv-refresh/refresh.log'

# 手动跑一次（等同定时任务）
ssh root@nas '/usr/syno/bin/synoschedtask --run id=5 check_time=0 check_status=0'

# 更新脚本：本机改完 push，NAS 拉
cd ~/projects/iptv-english && git push origin main
ssh root@nas 'cd /volume1/appdata/iptv-refresh/repo && git pull --rebase'
```

注意：`run.sh` 是 CRLF 敏感文件，从 Windows 侧更新后要 `tr -d '\r'`。
