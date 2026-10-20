#!/usr/bin/env python3
"""香港·九肖中特(tid=31, lt=1) 连中榜 + 家禽野兽(tid=62) —— 每天 8 档快照，一期一个文件。

本文件是 collect.py 的香港独立副本：**collect.py（澳门生产线）一行未改**，
两套期号互不通用（澳门 292 / 香港 112），故分目录、分脚本、分 workflow。

数据来源：第三方论坛只读接口（GET）。
  1. apiHost                                -> 动态域名 forumUrlForH5
  2. lastLotteryRecord?lotteryType=1        -> 当期 nextLotteryNumber / 开奖时刻 title
  3. prediction/rank  type=1 tid=31 lt=1    -> 连中榜名单（board 人数）
  4. prediction/detail userId=...           -> 每人全史选肖（取当期累加票数）
  5. predictionList?title=家禽野兽          -> 家禽野兽玩法当期发帖列表（全量发帖人）
  6. prediction/detail tid=62               -> 每人当期选「家禽/野兽」

输出：data_hk/{期号:04d}.json —— JSON 数组，每次快照末尾追加一条记录：
  {"t","period","drawAt","board","n","votes","jqys"}
  字段与澳门 data/ 完全同款，只是 period 是香港编号。
  其中外层 board/n/votes = 九肖连中榜（成员口径）；
  jqys = 家禽野兽 {"n","votes":{"家禽","野兽"}}（全量发帖人口径）。
  ⚠ 两者人群不同（连中榜成员 vs 全部发帖人），故分开存放，勿混算。

调度：每天 CST 0/3/6/9/12/15/18/21 点 = UTC 0 16/19/22/1/4/7/10/13。
  GitHub schedule 与 CF hk-snap-timer 同点位双触发 → 20 分钟窗内并轨成 1 条。
"""
import gzip
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data_hk")

LT = 1          # 1 = 香港（澳门那份写死 2）
TID = 31        # 31 = 九肖中特
TID2 = 62       # 62 = 家禽野兽
UA = "Mozilla/5.0 (Linux; Android 13)"

API_HOST = "https://49208.com/unite49/h5/index/apiHost"
LAST = "https://49208.com/unite49/h5/index/lastLotteryRecord?lotteryType=%d" % LT
RANK = ("%s/forums/h5/prediction/rank?lotteryType=%d&predictionTypeId=%d"
        "&type=1&pageNum=%d&pageSize=20")
DETAIL = ("%s/forums/h5/prediction/prediction/detail?userId=%s"
          "&predictionTypeId=%d&lotteryType=%d&pageNum=1&pageSize=500")
# 家禽野兽：论坛搜索框按标题检索（pageSize 被服务端忽略，固定每页 15 条）
LIST = ("%s/forums/h5/prediction/predictionList?lotteryType=%d"
        "&pageNum=%d&pageSize=15&title=%s")
JQYS_KW = urllib.parse.quote("家禽野兽")

ZODIAC = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]
CTX = ssl.create_default_context()

# 香港无开奖前密档，双触发（GitHub :00 + CF）间隔可达数分钟 → 窗口放宽到 20 分钟。
# 澳门那份仍是 120s（它有每 5 分钟密档，不能放宽），两份互不影响。
DUP_WINDOW = 1200

# 收人窗口：标题搜往前多收 6 期的发帖人，票数仍只按当期 detail 聚合。
# ⚠ 不收跨期就漏人：部分当期帖标题不含「家禽野兽」，只能靠其更早帖把 uid 捞进来
#   （实测 112 期：只搜当期=16人 8/8；往前 3 期=27人 → 10/14=24，与 App 一致）
JQYS_WINDOW = 6


def get(url, tries=3):
    """带递增退避的重试：失败后等 10s、30s… 再重拉（最后一次失败不再空等）。"""
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
            if i < tries - 1:                 # 还有下一次才等：10s → 30s → 90s…（上限 120s）
                time.sleep(min(10 * (3 ** i), 120))
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


def jqys_tally(host, cur, year):
    """家禽野兽(tid=62)：论坛搜索列表(标题=家禽野兽) -> 逐人详情取 家禽/野兽 -> 汇总。

    与九肖不同，这里的人群是「该玩法当期全部发帖人」（不是榜单成员）。
    返回 (n, {"家禽":x,"野兽":y})。
    """
    posts = {}                                   # predictionId -> item（去重）
    for p in range(1, 8):
        d = get(LIST % (host, LT, p, JQYS_KW))["data"]
        lst = d.get("list") or []
        if not lst:
            break
        floor = max(1, cur - JQYS_WINDOW)          # 收人窗口下界（跨年不越界）
        all_older = True
        for it in lst:
            m = re.match(r"^(\d+)期", it.get("title") or "")
            per = int(m.group(1)) if m else int(it.get("number") or 0)
            if per >= floor:
                all_older = False
            if per >= floor:
                pid = str(it.get("predictionId", "") or "")
                if pid:
                    posts[pid] = it
        if all_older:                            # 本页已全是旧期 -> 到底
            break
        time.sleep(0.12)

    # 人池去重（App 同款 LinkedHashSet）：同一人跨多期发帖只能算一次，否则重复计票
    uid_list, seen = [], set()
    for _, it in posts.items():
        u = str(it.get("userId", "") or "")
        if u and u not in seen:
            seen.add(u)
            uid_list.append(u)

    def vote(uid):
        if not uid:
            return None
        try:
            d = get(DETAIL % (host, uid, TID2, LT))["data"]
        except Exception:
            return None                          # 单人失败不拖垮
        for r in (d.get("list") or []):
            if int(r.get("period") or 0) != cur:
                continue
            if not str(r.get("predictionId", "")).startswith(str(year)):
                continue
            for x in (r.get("detailList") or []):
                v = x.get("value")
                if isinstance(v, list) and v:
                    return str(v[0])
                if isinstance(v, str) and v:
                    return v
        return None

    counts = {}
    n = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for v in ex.map(vote, uid_list):
            if v:
                n += 1
                counts[v] = counts.get(v, 0) + 1
    return n, {"家禽": counts.get("家禽", 0), "野兽": counts.get("野兽", 0)}


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

    # 家禽野兽：全量发帖人口径。失败不影响九肖主流程。
    jn, jvotes = 0, {"家禽": 0, "野兽": 0}
    try:
        jn, jvotes = jqys_tally(host, cur, year)
    except Exception as e:
        print("WARN: 家禽野兽抓取失败:", e)

    # 两条流都空（多为刚开奖、新期还没人投票）→ 不写空记录
    if n <= 0 and jn <= 0:
        print("WARN: 当期%d 暂无选肖/家禽野兽记录（可能刚开期），跳过本次写入" % cur)
        sys.exit(0)

    now = datetime.now(CST).replace(microsecond=0)
    rec = {
        "t": now.isoformat(),
        "period": cur,
        "drawAt": draw,
        "board": len(uids),
        "n": n,
        "votes": votes,
        "jqys": {"n": jn, "votes": jvotes},
    }

    path = os.path.join(DATA, "%04d.json" % cur)
    arr = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                arr = json.load(f)
        except Exception:
            arr = []
    # 去重：与上一条间隔 < 20 分钟（GitHub :00 与 CF 同点位双触发）→ 覆盖；否则追加
    dup = False
    if arr and isinstance(arr[-1], dict) and arr[-1].get("period") == cur:
        try:
            if (now - datetime.fromisoformat(arr[-1]["t"])).total_seconds() < DUP_WINDOW:
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
    print("[HK] 期%d board=%d n=%d drawAt=%s top3=%s | 家禽野兽 n=%d %s"
          % (cur, len(uids), n, draw, top, jn, jvotes))


if __name__ == "__main__":
    main()
