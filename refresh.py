#!/usr/bin/env python3
"""刷新 IPTV 播放列表。

按语言从 iptv-org 拉取频道表 → 过滤 → 可选品牌白名单 → 并发探活 → 打分 → 归并。
产出见 LANGS，全部落在脚本同目录。

用法:
    python refresh.py                    # 全流程
    python refresh.py --no-probe         # 跳过探活（快）
    python refresh.py --lang english     # 只跑某一语言
"""
import argparse
import concurrent.futures as cf
import io
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from urllib.parse import urljoin

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_URL = "https://iptv-org.github.io/iptv/languages/{}.m3u"
UA = "VLC/3.0.20 LibVLC/3.0.20"
TIMEOUT = 10
WORKERS = 24

# 命中任一分组即剔除（group-title 可能是 "Kids;Religious" 这种多标签）
DROP_GROUPS = ["Religious", "Legislative", "Undefined", "Shopping", "Shop"]

# 屏蔽名单：命中 EXTINF 行（含 tvg-id）或频道名即剔除
BLOCK = re.compile(r"(?i)NTDTV|新唐人|New\s*Tang\s*Dynasty")

# ---------------------------------------------------------------- 语言配置

BRAND_EN = re.compile(r"""(?ix)
 bbc\s*(news|world|one|two|three|four|america|brit)|itv|channel\s*4|channel\s*5|gb\s*news|sky\s*news|
 cnn|cnbc|bloomberg|fox\s*(news|business)|msnbc|nbc|abc\s*(news|live)|cbs\s*news|pbs|
 al\s*jazeera|deutsche\s*welle|\bdw\b|france\s*24|euronews|reuters|\bcna\b|channel\s*news\s*asia|
 cgtn|trt\s*world|i24|arirang|nhk\s*world|
 discovery|national\s*geographic|nat\s*geo|history|animal\s*planet|smithsonian|
 amc|syfy|tnt|tbs|usa\s*network|comedy\s*central|paramount|mtv|vh1|
 cartoon\s*network|nickelodeon|disney|pbs\s*kids|cbeebies|boomerang|
 espn|nba\s*tv|nfl\s*network|mlb|sky\s*sports|eurosport|
 pluto\s*tv|samsung\s*tv|tubi|plex|roku|stirr|xumo|rakuten
""")
TIER_EN = (
    re.compile(r"(?i)bbc\s*(news|world)|cnn\b|al\s*jazeera\s*english|deutsche\s*welle|\bdw\b|"
               r"france\s*24|sky\s*news|cnbc|bloomberg|\bpbs\b|abc\s*news"),
    re.compile(r"(?i)nbc|msnbc|cbs\s*news|fox\s*news|\bcna\b|channel\s*news\s*asia|euronews|"
               r"arirang|nhk\s*world|\bitv\b|channel\s*4|channel\s*5|cgtn|trt\s*world|i24|gb\s*news"),
    re.compile(r"(?i)discovery|national\s*geographic|nat\s*geo|\bhistory\b|animal\s*planet|"
               r"smithsonian|\bamc\b|\bmtv\b|cartoon\s*network|nickelodeon|disney|espn|"
               r"nba\s*tv|nfl\s*network|paramount|bbc\s*america|bbc\s*brit"),
)

# 中文表只有 200 余个频道，不做品牌白名单，全量参与
TIER_ZH = (
    re.compile(r"(?i)CCTV|央視|央视|鳳凰|凤凰|TVB|翡翠|明珠|中天|東森|东森|三立|民視|民视|"
               r"無綫|无线|澳視|澳视|華視|华视|台視|台视|中視|中视"),
    re.compile(r"(?i)衛視|卫视|湖南|浙江|東方|东方|江蘇|江苏|北京|上海|廣東|广东|深圳|山東|山东|"
               r"天津|重慶|重庆|四川|湖北|河南|安徽|福建|江西|貴州|贵州|雲南|云南|陝西|陕西|"
               r"甘肅|甘肃|遼寧|辽宁|吉林|黑龍江|黑龙江|廣西|广西|內蒙古|内蒙古|寧夏|宁夏|"
               r"新疆|西藏|青海|海南|河北|山西"),
    re.compile(r"(?i)中國|中国|華語|华语|中華|中华|亞洲|亚洲|財經|财经"),
)

FAST = re.compile(r"(?i)tubi|samsung|pluto|plex|roku|stirr|xumo|rakuten|wurl\.tv|akamaized|cloudfront")

LANGS = [
    {"name": "english", "code": "eng", "brand": BRAND_EN, "tiers": TIER_EN, "direct": False},
    # brand=None 表示不做白名单筛选，全部参与
    # direct=True 额外产出一份「源站在中国大陆 → 走直连」的子集
    {"name": "chinese", "code": "zho", "brand": None, "tiers": TIER_ZH, "direct": True},
]

GEO_API = "http://ip-api.com/batch?fields=query,countryCode"

# 归并同类噪声家族（先剥画质后缀再匹配）
FAMILIES = [
    (r"^NBC\s+(K|W)[A-Z]{2,4}(-TV)?$", "NBC (地方台)"),
    (r"^CBS News (Baltimore|Bay Area|Boston|Chicago|Colorado|Detroit|Los Angeles|Miami|"
     r"Minnesota|New York|Philly|Pittsburgh|Sacramento)$", "CBS News (地方台)"),
    (r"^PBS\s+(K|W)[A-Z]{2,4}(-TV)?$", "PBS (地方台)"),
    (r"^PBS Kids.*$", "PBS Kids"),
    (r"^PBS (Antiques|Nature|Travel).*$", "PBS (专题)"),
    (r"^Bloomberg.*$", "Bloomberg TV"),
    (r"^BBC News.*$", "BBC News"),
    (r"^Sky News.*$", "Sky News"),
    (r"^ABC News Live.*$", "ABC News Live"),
    (r"^CGTN.*$", "CGTN"),
    (r"^NBCLX.*$", "NBC LX"),
    (r"^Arirang.*$", "Arirang TV"),
    (r"^NHK World.*$", "NHK World-Japan"),
    (r"^TRT World.*$", "TRT World"),
    (r"^CNBC.*$", "CNBC"),
    (r"^CNN International.*$", "CNN International"),
    (r"^CNA.*$", "CNA"),
    (r"^Channel 44$|^MRVTV.*$", "Channel 44"),
]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def parse(text):
    """→ [(extinf_line, name, url), ...]"""
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines) - 1:
        if lines[i].startswith("#EXTINF"):
            out.append((lines[i], lines[i].rsplit(",", 1)[-1], lines[i + 1].strip()))
            i += 2
        else:
            i += 1
    return out


def _get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def probe(url):
    """两段验证：① 清单能解析成 HLS ② 首个分片能真正拉到。

    只看清单的 HTTP 状态码会大量误判 —— 实测存在两类假健康：
      · 锁区：服务器回 200 + "not available in your area" 纯文本
      · 僵尸流：清单照常滚动，但分片全部 404
    两者都返回 200，必须下到分片才算数。
    返回 (是否健康, 失败原因)。
    """
    try:
        body = _get(url).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return False, f"http{e.code}"
    except Exception:
        return False, "unreachable"

    if "#EXTM3U" not in body:
        return False, "not-hls"

    base = url
    lines = [l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")]

    # 主清单 → 下探一层拿变体清单。必须用 urljoin 按 RFC 3986 解析：
    # 手写 base+"/"+v 在变体行是绝对路径（/a/b.m3u8）时会拼成双前缀，导致 414/404 误杀
    if "#EXT-X-STREAM-INF" in body:
        if not lines:
            return False, "empty-master"
        vurl = urljoin(base, lines[0])
        try:
            body = _get(vurl).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return False, f"variant{e.code}"
        except Exception:
            return False, "variant-unreachable"
        base = vurl
        lines = [l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")]

    if not lines:
        return False, "no-segment"
    seg = urljoin(base, lines[0])
    try:
        if not _get(seg):
            return False, "empty-segment"
    except urllib.error.HTTPError as e:
        return False, f"seg{e.code}"
    except Exception:
        return False, "seg-unreachable"
    return True, ""


def host_of(url):
    m = re.match(r"^https?://([^/:]+)", url)
    return m.group(1) if m else ""


def cn_hosts(urls):
    """返回「解析到中国大陆 IP」的主机名集合。失败时返回 None（不阻断主流程）。"""
    hosts = sorted({h for h in map(host_of, urls) if h})

    def one(h):
        try:
            return h, sorted({ai[4][0] for ai in socket.getaddrinfo(h, None)})
        except Exception:
            return h, []

    try:
        with cf.ThreadPoolExecutor(max_workers=16) as ex:
            resolved = dict(ex.map(one, hosts))
        ips = sorted({ip for lst in resolved.values() for ip in lst})
        geo = {}
        for i in range(0, len(ips), 100):
            req = urllib.request.Request(
                GEO_API, data=json.dumps(ips[i:i + 100]).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                for item in json.load(r):
                    geo[item.get("query")] = item.get("countryCode")
        return {h for h, lst in resolved.items()
                if any(geo.get(ip) == "CN" for ip in lst)}
    except Exception as e:
        print(f"  [warn] 直连检测失败，跳过: {e}")
        return None


def strip_quality(name):
    """'CCTV-1 HD (1080p)' → 'CCTV-1'"""
    s = re.sub(r"\s*[\[(].*$", "", name).strip()
    return re.sub(r"(?i)\s+(HD|SD|FHD|UHD|4K|8K)$", "", s).strip()


def canon(name):
    base = strip_quality(name)
    for pat, c in FAMILIES:
        if re.match(pat, base):
            return c
    return base.lower()


def run_lang(cfg, do_probe):
    name, code = cfg["name"], cfg["code"]
    print(f"\n=== {name} ({code}) ===")
    raw = fetch(BASE_URL.format(code))
    header = raw.splitlines()[0] if raw.startswith("#EXTM3U") else "#EXTM3U"
    items = parse(raw)
    print(f"  原始 {len(items)}")

    clean, dropped = [], {}
    for ext, ch, url in items:
        grp = (re.search(r'group-title="([^"]*)"', ext) or [None, ""])[1]
        parts = {p.strip() for p in grp.split(";") if p.strip()}
        hit = next((d for d in DROP_GROUPS if d in parts), None)
        reason = ("geo" if "Geo-blocked" in ch else
                  "not24" if "Not 24/7" in ch else
                  "block" if BLOCK.search(ext) else
                  hit)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
        else:
            clean.append((ext, ch, url))
    print(f"  clean {len(clean)}  (剔除 {dropped})")

    brand = cfg["brand"]
    cands = [x for x in clean if brand.search(x[1])] if brand else clean
    print(f"  候选 {len(cands)}")

    if do_probe:
        print(f"  探活 {len(cands)}（并发 {WORKERS}，含分片验证）...")
        urls = [u for _, _, u in cands]
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            status = dict(zip(urls, ex.map(probe, urls)))
        bad = {}
        for ok, reason in status.values():
            if not ok:
                bad[reason] = bad.get(reason, 0) + 1
        print(f"  探活通过 {sum(1 for ok, _ in status.values() if ok)}/{len(cands)}  失败明细 {bad}")
    else:
        status = {u: (True, "") for _, _, u in cands}

    # 大陆源站的豁免名单：探测点（CI 在美国）无权判定大陆源站死活
    cn = cn_hosts([u for _, _, u in clean]) if cfg.get("direct") else None

    t1, t2, t3 = cfg["tiers"]
    scored, exempted = [], 0
    for ext, ch, url in cands:
        ok, reason = status.get(url, (False, "missing"))
        # 远端探活无权否决大陆源站：网络不可达 / 分片拉不动，在探测点之外
        # 并不代表源已死（CI 跑在美国，大陆源多只对国内可达）
        if not ok and cn and host_of(url) in cn and (
                reason == "unreachable" or reason.startswith(("seg", "http5"))):
            ok, exempted = True, exempted + 1
        if not ok:
            continue
        fame = 40 if t1.search(ch) else 32 if t2.search(ch) else 22 if t3.search(ch) else 12
        fast = 8 if FAST.search(url) else 0
        m = re.search(r"\((\d{3,4})[pi]\)", ch)
        q = {"1080": 10, "720": 6, "576": 3, "480": 3}.get(m.group(1) if m else "", 5)
        scored.append((40 + fame + fast + q, ext, ch, url))
    scored.sort(key=lambda r: (-r[0], r[2]))

    seen, essentials = set(), []
    for _s, ext, ch, url in scored:
        k = canon(ch)
        if k in seen:
            continue
        seen.add(k)
        essentials.append((ext, ch, url))
    essentials.sort(key=lambda x: x[1].lower())

    def write(suffix, triples):
        fn = f"{name}-{suffix}.m3u"
        with io.open(os.path.join(HERE, fn), "w", encoding="utf-8") as f:
            f.write(header + "\n")
            for ext, _ch, url in triples:
                f.write(ext + "\n" + url + "\n")
        print(f"  {fn:<28} {len(triples)}")

    write("all", items)
    write("clean", clean)
    write("top", [(e, c, u) for _s, e, c, u in scored])
    write("essentials", essentials)

    if cfg.get("direct"):
        if cn:
            hits = [(e, c, u) for _s, e, c, u in scored if host_of(u) in cn]
            print(f"  源站在中国大陆（走直连）: {len(hits)}   探活豁免: {exempted}")
            write("direct", hits)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-probe", action="store_true", help="跳过探活")
    ap.add_argument("--lang", help="只跑指定语言（english / chinese）")
    args = ap.parse_args()

    for cfg in LANGS:
        if args.lang and cfg["name"] != args.lang:
            continue
        run_lang(cfg, do_probe=not args.no_probe)
    print(f"\n完成 → {HERE}")


if __name__ == "__main__":
    main()
