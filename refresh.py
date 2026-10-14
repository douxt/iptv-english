#!/usr/bin/env python3
"""刷新英语 IPTV 榜单。

拉取 iptv-org 英语频道表 → 过滤 → 品牌白名单 → 并发探活 → 打分 → 品牌归并。
产出四个文件（见 OUT_FILES），全部落在脚本同目录。

用法:
    python refresh.py              # 全流程
    python refresh.py --no-probe   # 跳过探活，只做过滤与归并（快，几秒）
"""
import argparse
import concurrent.futures as cf
import io
import os
import re
import sys
import urllib.request

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_URL = "https://iptv-org.github.io/iptv/languages/eng.m3u"
UA = "VLC/3.0.20 LibVLC/3.0.20"
TIMEOUT = 10
WORKERS = 24

# 品牌白名单：命中即进候选
BRAND = re.compile(r"""(?ix)
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

# 知名度分档
TIER1 = re.compile(r"(?i)bbc\s*(news|world)|cnn\b|al\s*jazeera\s*english|deutsche\s*welle|\bdw\b|"
                   r"france\s*24|sky\s*news|cnbc|bloomberg|\bpbs\b|abc\s*news")
TIER2 = re.compile(r"(?i)nbc|msnbc|cbs\s*news|fox\s*news|\bcna\b|channel\s*news\s*asia|euronews|"
                   r"arirang|nhk\s*world|\bitv\b|channel\s*4|channel\s*5|cgtn|trt\s*world|i24|gb\s*news")
TIER3 = re.compile(r"(?i)discovery|national\s*geographic|nat\s*geo|\bhistory\b|animal\s*planet|"
                   r"smithsonian|\bamc\b|\bmtv\b|cartoon\s*network|nickelodeon|disney|espn|"
                   r"nba\s*tv|nfl\s*network|paramount|bbc\s*america|bbc\s*brit")
FAST = re.compile(r"(?i)tubi|samsung|pluto|plex|roku|stirr|xumo|rakuten|wurl\.tv|akamaized|cloudfront")

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

OUT_FILES = ("english-all.m3u", "english-clean.m3u", "english-top.m3u", "english-essentials.m3u")

DROP_GROUPS = {"Religious", "Legislative", "Undefined", "Shopping", "Shop"}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse(text):
    """→ [(extinf_line, name, url), ...]"""
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF") and i + 1 < len(lines):
            ext = lines[i]
            out.append((ext, ext.rsplit(",", 1)[-1], lines[i + 1].strip()))
            i += 2
        else:
            i += 1
    return out


def probe(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def canon(name):
    base = re.sub(r"\s*[\[(].*$", "", name).strip()
    for pat, c in FAMILIES:
        if re.match(pat, base):
            return c
    return base.lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-probe", action="store_true", help="跳过探活")
    args = ap.parse_args()

    print(f"拉取 {SRC_URL} ...")
    raw = fetch(SRC_URL)
    header = raw.splitlines()[0] if raw.startswith("#EXTM3U") else "#EXTM3U"
    items = parse(raw)
    print(f"  原始 {len(items)} 个频道")
    print(f"  头部: {header[:80]}")

    # 1) clean：去 geo/not24/噪声类别
    clean, dropped = [], {}
    for ext, name, url in items:
        grp = (re.search(r'group-title="([^"]*)"', ext) or [None, ""])[1]
        reason = None
        if "Geo-blocked" in name:
            reason = "geo"
        elif "Not 24/7" in name:
            reason = "not24"
        elif grp.split(";")[0] in DROP_GROUPS:
            reason = grp.split(";")[0]
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
        else:
            clean.append((ext, name, url))
    print(f"  clean {len(clean)}  (剔除 {dropped})")

    # 2) 品牌候选
    cands = [(ext, name, url) for ext, name, url in clean if BRAND.search(name)]
    print(f"  品牌候选 {len(cands)}")

    # 3) 探活
    if args.no_probe:
        status = {u: 200 for _, _, u in cands}
    else:
        print(f"  探活 {len(cands)} 个（并发 {WORKERS}）...")
        urls = [u for _, _, u in cands]
        status = {}
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for u, code in zip(urls, ex.map(probe, urls)):
                status[u] = code

    # 4) 打分
    scored = []
    for ext, name, url in cands:
        c = status.get(url, 0)
        if c not in (200, 206, 204):
            continue
        fame = 40 if TIER1.search(name) else 32 if TIER2.search(name) else 22 if TIER3.search(name) else 12
        fast = 8 if FAST.search(url) else 0
        m = re.search(r"\((\d{3,4})p\)", name)
        q = {"1080": 10, "720": 6, "576": 3, "480": 3}.get(m.group(1) if m else "", 5)
        scored.append((40 + fame + fast + q, ext, name, url))
    scored.sort(key=lambda r: (-r[0], r[2]))

    # 5) 品牌归并
    seen, essentials = set(), []
    for score, ext, name, url in scored:
        k = canon(name)
        if k in seen:
            continue
        seen.add(k)
        essentials.append((ext, name, url))
    essentials.sort(key=lambda x: x[1].lower())

    def write(fn, triples):
        """保留原始 EXTINF 属性（tvg-id / tvg-logo / group-title）与头部 x-tvg-url。"""
        with io.open(os.path.join(HERE, fn), "w", encoding="utf-8") as f:
            f.write(header + "\n")
            for ext, _name, url in triples:
                f.write(ext + "\n" + url + "\n")

    write(OUT_FILES[0], items)
    write(OUT_FILES[1], clean)
    write(OUT_FILES[2], [(e, n, u) for _, e, n, u in scored])
    write(OUT_FILES[3], essentials)

    print(f"\n完成 → {HERE}")
    print(f"  {OUT_FILES[0]:<26} {len(items)}")
    print(f"  {OUT_FILES[1]:<26} {len(clean)}")
    print(f"  {OUT_FILES[2]:<26} {len(scored)}  (探活通过)")
    print(f"  {OUT_FILES[3]:<26} {len(essentials)}  (每品牌一条)")


if __name__ == "__main__":
    main()
