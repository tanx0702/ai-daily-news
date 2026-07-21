"""
RSS 源健康检查脚本

用法：
    python scripts/check_sources.py

检查 config/rss_sources.json 中每个 RSS 源：
1. 是否能正常访问（HTTP 200）
2. 是否返回有效 RSS/XML
3. 是否有 entries
4. dated entries 比例（有发布时间的条目占比）
5. 最新发布时间
6. 过去 24h / 36h 可用条数
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

# Windows 控制台 UTF-8 编码修复
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def check_source(source: dict, timeout: int = 15) -> dict:
    """检查单个 RSS 源的健康状态。"""
    name = source["name"]
    url = source["url"]
    result = {
        "name": name,
        "url": url,
        "reachable": False,
        "is_rss": False,
        "total_entries": 0,
        "dated_entries": 0,
        "latest_pub": None,
        "items_24h": 0,
        "items_36h": 0,
        "error": "",
    }

    # 1. HTTP 请求
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (compatible; AIDailyNewsBot/1.0)"
        })
        resp.raise_for_status()
        result["reachable"] = True
    except requests.exceptions.Timeout:
        result["error"] = "timeout"
        return result
    except requests.exceptions.RequestException as e:
        result["error"] = str(e)[:80]
        return result

    # 2. 检查 Content-Type（宽松：只要能 parse 出 entries 就算 RSS）
    content_type = resp.headers.get("Content-Type", "").lower()
    is_xml = "xml" in content_type or "rss" in content_type or "atom" in content_type
    is_html_page = "text/html" in content_type and "<html" in resp.text[:500].lower()

    if is_html_page:
        result["error"] = f"returned HTML page, not RSS (Content-Type: {content_type})"
        # 仍然尝试 parse，有些源 content-type 不对但实际是 RSS

    # 3. 解析 RSS
    feed = feedparser.parse(resp.content)
    if not feed.entries:
        if feed.bozo and not is_xml:
            result["error"] = result["error"] or f"no entries, bozo: {getattr(feed, 'bozo_exception', 'unknown')}"
        else:
            result["error"] = result["error"] or "no entries"
        return result

    result["is_rss"] = True
    result["total_entries"] = len(feed.entries)

    # 4. 检查 dated entries
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_36h = now - timedelta(hours=36)

    import calendar

    for entry in feed.entries:
        pub_dt = None
        # 尝试所有可能的日期字段
        for key in ("published_parsed", "updated_parsed", "created_parsed"):
            parsed = entry.get(key)
            if parsed is not None:
                try:
                    epoch = calendar.timegm(parsed[:9])
                    pub_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
                    break
                except Exception:
                    continue

        if pub_dt is None:
            for key in ("published", "updated", "created"):
                raw = entry.get(key, "")
                if raw:
                    try:
                        struct = feedparser._parse_date(raw)
                        if struct is not None:
                            epoch = calendar.timegm(struct[:9])
                            pub_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
                            break
                    except Exception:
                        continue

        if pub_dt is not None:
            result["dated_entries"] += 1
            if result["latest_pub"] is None or pub_dt > result["latest_pub"]:
                result["latest_pub"] = pub_dt
            if pub_dt >= cutoff_24h:
                result["items_24h"] += 1
            if pub_dt >= cutoff_36h:
                result["items_36h"] += 1

    return result


def main():
    # 加载配置
    config_path = "config/rss_sources.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"{RED}Error: {config_path} not found{RESET}")
        sys.exit(1)

    sources = data.get("sources", [])
    if not sources:
        print(f"{YELLOW}No sources found in {config_path}{RESET}")
        sys.exit(0)

    print(f"{BOLD}AI Daily News — Source Health Check{RESET}")
    print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Sources: {len(sources)}")
    print()
    print(f"{'Source':<22} {'Status':<10} {'Entries':<8} {'Dated%':<8} {'24h':<5} {'36h':<5} {'Latest'}")
    print("-" * 100)

    results = []
    ok_count = 0
    warn_count = 0
    fail_count = 0

    for source in sources:
        r = check_source(source)
        results.append(r)

        # 状态判断
        if r["is_rss"] and r["dated_entries"] > 0:
            status = f"{GREEN}OK{RESET}"
            ok_count += 1
        elif r["is_rss"] and r["total_entries"] > 0:
            status = f"{YELLOW}WARN{RESET}"
            warn_count += 1
        else:
            status = f"{RED}FAIL{RESET}"
            fail_count += 1

        # 格式化输出
        dated_pct = f"{r['dated_entries'] / max(r['total_entries'], 1) * 100:.0f}%"
        latest = r["latest_pub"].strftime("%m-%d %H:%M") if r["latest_pub"] else "-"
        err = f"  [{r['error']}]" if r["error"] and not r["is_rss"] else ""

        print(f"{r['name']:<22} {status:<17} {r['total_entries']:<8} {dated_pct:<8} "
              f"{r['items_24h']:<5} {r['items_36h']:<5} {latest}{err}")

    print()
    print(f"Summary: {GREEN}{ok_count} OK{RESET}, {YELLOW}{warn_count} WARN{RESET}, {RED}{fail_count} FAIL{RESET}")

    # 详细错误
    failures = [r for r in results if not r["is_rss"]]
    if failures:
        print(f"\n{BOLD}Failed Sources:{RESET}")
        for r in failures:
            print(f"  {RED}✗{RESET} {r['name']}: {r['error']}")

    # 无日期警告
    no_date_warn = [r for r in results if r["is_rss"] and r["dated_entries"] == 0 and r["total_entries"] > 0]
    if no_date_warn:
        print(f"\n{YELLOW}Sources with no dated entries:{RESET}")
        for r in no_date_warn:
            print(f"  ⚠ {r['name']}: {r['total_entries']} entries, 0 with dates")

    # 退出码：有 FAIL 则非 0
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
