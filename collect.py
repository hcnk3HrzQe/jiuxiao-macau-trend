#!/usr/bin/env python3
"""澳门·九肖中特(tid=31, lt=2) 连中榜 —— 每小时快照，一期一个文件。

数据来源：第三方论坛只读接口（GET）。
  1. apiHost                                -> 动态域名 forumUrlForH5
  2. lastLotteryRecord?lotteryType=2        -> 当期 nextLotteryNumber / 开奖时刻 title
  3. prediction/rank  type=1 tid=31 lt=2    -> 连中榜名单（board 人数）
  4. prediction/detail userId=...           -> 每人全史选肖（取当期累加票数）

输出：data/{期号:04d}.json —— JSON 数组，每小时末尾追加一条记录：
  {"t","period","drawAt","board","n","votes"}
"""
import gzip
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")

LT = 2          # 2 = 澳门
TID = 31        # 31 = 九肖中特
UA = "Mozilla/5.0 (Linux; Android 13)"

API_HOST = "https://49208.com/unite49/h5/index/apiHost"
LAST = "https://49208.com/unite49/h5/index/lastLotteryRecord?lotteryType=%d" % LT
RANK = ("%s/forums/h5/prediction/rank?lotteryType=%d&predictionTypeId=%d"
        "&type=1&pageNum=%d&pageSize=20")
DETAIL = ("%s/forums/h5/prediction/prediction/detail?userId=%s"
          "&predictionTypeId=%d&lotteryType=%d&pageNum=1&pageSize=500")

ZODIAC = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]
CTX = ssl.create_default_context()


def get(url, tries=3):
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "x-lottery-type": str(LT),
                "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
                b = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    b = gzip.decompress(b)
            return json.loads(b.decode("utf-8"))
        except Exception as e:
            err = e
            time.sleep(0.6 * (i + 1))
    raise err


def get_host():
    return get(API_HOST)["data"]["forumUrlForH5"].rstrip("/")


def get_current():
    d = get(LAST)["data"]
    cur = int(d.get("nextLotteryNumber") or 0)
    last = int(d.get("period") or 0)
    year = int(d.get("year") or 0)
    draw = ""
    m = re.search(r"第\d+期\s+(\d{4})/(\d{2})/(\d{2})\s+(\d{2}:\d{2})",
                  d.get("title", ""))
    if m:
        draw = "%s-%s-%sT%s:00+08:00" % (m.group(1), m.group(2), m.group(3), m.group(4))
    return cur, last, year, draw


def board(host):
    uids = []
    for p in range(1, 31):
        d = get(RANK % (host, LT, TID, p))["data"]
        lst = d.get("list") or []
        if not lst:
            break
        for it in lst:
            u = str(it.get("userId", "") or "")
            if u:
                uids.append(u)
        if len(lst) < 20:
            break
        time.sleep(0.12)
    seen, out = set(), []
    for u in uids:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def pick_of(host, uid, period, year):
    d = get(DETAIL % (host, uid, TID, LT))["data"]
    for r in (d.get("list") or []):
        if int(r.get("period") or 0) != period:
            continue
        if not str(r.get("predictionId", "")).startswith(str(year)):
            continue
        vals = []
        for it in (r.get("detailList") or []):
            v = it.get("value")
            if isinstance(v, list):
                vals += [x for x in v if x]
            elif isinstance(v, str) and v:
                vals.append(v)
        return vals
    return None


def main():
    os.makedirs(DATA, exist_ok=True)

    host = get_host()
    cur, last, year, draw = get_current()
    if cur <= 0:
        print("ERROR: 当期期号取不到")
        sys.exit(1)
    # 保险①：当期 == 已开期 + 1（跨年重置允许 cur==1）
    if not (cur == last + 1 or cur == 1):
        print("WARN: 期号跳变可疑 last=%s cur=%s —— 跳过本次" % (last, cur))
        sys.exit(1)

    uids = board(host)
    if not uids:
        print("ERROR: 连中榜名单为空")
        sys.exit(1)
    limit = int(os.environ.get("LIMIT", "0") or 0)   # 调试用：只抓前 N 人
    if limit:
        uids = uids[:limit]

    votes = dict((z, 0) for z in ZODIAC)
    n = 0

    def work(uid):
        # 单人失败（重试耗尽）不该拖垮整期：当作"无当期记录"处理
        try:
            return pick_of(host, uid, cur, year)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        for vals in ex.map(work, uids):
            if vals is None:
                continue
            n += 1
            for z in vals:
                if z in votes:
                    votes[z] += 1

    # 无人有当期记录（多为刚开奖、新期还没人投票）→ 不写空记录
    if n <= 0:
        print("WARN: 当期%d 暂无选肖记录（可能刚开期），跳过本次写入" % cur)
        sys.exit(0)

    now = datetime.now(CST).replace(microsecond=0)
    rec = {
        "t": now.isoformat(),
        "period": cur,
        "drawAt": draw,
        "board": len(uids),
        "n": n,
        "votes": votes,
    }

    path = os.path.join(DATA, "%04d.json" % cur)
    arr = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                arr = json.load(f)
        except Exception:
            arr = []
    # 去重：与上一条间隔 < 2 分钟（重复跑 / 手动重试）→ 覆盖；否则追加
    # （开奖前密集窗口每 5 分钟一条，必须保留，不能按小时塌掉）
    dup = False
    if arr and isinstance(arr[-1], dict) and arr[-1].get("period") == cur:
        try:
            if (now - datetime.fromisoformat(arr[-1]["t"])).total_seconds() < 120:
                dup = True
        except Exception:
            pass
    if dup:
        arr[-1] = rec
    else:
        arr.append(rec)
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in arr)
    with open(path, "w", encoding="utf-8") as f:
        f.write("[\n" + lines + "\n]\n")

    top = sorted(votes.items(), key=lambda x: -x[1])[:3]
    print("期%d board=%d n=%d drawAt=%s top3=%s" % (cur, len(uids), n, draw, top))


if __name__ == "__main__":
    main()
