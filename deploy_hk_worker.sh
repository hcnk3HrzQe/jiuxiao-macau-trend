#!/usr/bin/env bash
# 香港兜底 Worker 部署脚本 —— 只创建/更新 hk-snap-timer，全程不碰澳门的 jqys-snap-timer。
#
# 做三件事：
#   1) PUT 上传 hk_worker.js（独立 Worker，和旧 Worker 物理隔离）
#   2) 绑定 GH_TOKEN（沿用主仓 app/gh_token.txt 同一个 GitHub PAT）
#   3) 设 8 条 cron：UTC 0 16/19/22/1/4/7/10/13 = 东八区 0/3/6/9/12/15/18/21 点
# 最后打印新旧两个 Worker 的状态对照，证明澳门侧没被动过。
#
# 用法： ./deploy_hk_worker.sh
# 密钥： cf_token.txt（本目录，600，已 gitignore，绝不入库）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
API="https://api.cloudflare.com/client/v4"
AID="e8870d247d13c2de02215db5b065d432"      # 账号 id（非密钥，控制台可见）
WNAME="hk-snap-timer"                        # 新增：香港
OLDNAME="jqys-snap-timer"                    # 澳门：只读校验，不写
TOKEN="$(tr -d '\r\n' < "$ROOT/cf_token.txt")"
GHTOK_FILE="${GHTOK_FILE:-$ROOT/../mimo-workspace/app/gh_token.txt}"
GH="$(tr -d '\r\n' < "$GHTOK_FILE")"
# PUT /schedules 的 body 是「纯 JSON 数组」且整体替换（POST/PATCH 会被 10405 拒）
# ⚠ Cloudflare 免费版每账号 cron 上限 5 条，澳门已占 2 条 → 香港用「小时列表」合并成 1 条
#   0 16,19,22,1,4,7,10,13 * * * = 东八区 0/3/6/9/12/15/18/21 点，账号用量 3/5
CRONS='[{"cron":"0 16,19,22,1,4,7,10,13 * * *"}]'

J() { python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
except Exception:
    print('PARSE_FAIL'); sys.exit(0)
print('success=%s errors=%s' % (d.get('success'), json.dumps(d.get('errors'), ensure_ascii=False)))
"; }

auth() { curl -sS --max-time 40 -H "Authorization: Bearer $TOKEN" "$@"; }
ok() { python3 -c "import sys,json
try: d=json.load(sys.stdin)
except Exception: sys.exit(1)
sys.exit(0 if d.get('success') else 1)"; }

echo "== 0) 部署前：澳门 Worker 现状（只读） =="
OLD_SCHEDULES_BEFORE="$(auth "$API/accounts/$AID/workers/scripts/$OLDNAME/schedules")"
printf '%s' "$OLD_SCHEDULES_BEFORE" | python3 -c "
import sys,json;d=json.load(sys.stdin)
print('  澳门 cron 条数=',len((d.get('result') or {}).get('schedules') or []))
for s in (d.get('result') or {}).get('schedules') or []: print('   -',s['cron'])"

echo "== 1) 上传 hk_worker.js（只针对 $WNAME） =="
auth -X PUT "$API/accounts/$AID/workers/scripts/$WNAME" \
  -F 'metadata={"main_module":"worker.js","compatibility_date":"2024-01-01","compatibility_flags":["global_fetch_strictly_public"]};type=application/json' \
  -F "worker.js=@$ROOT/hk_worker.js;filename=worker.js;type=application/javascript+module" | J

echo "== 2) 绑定 GH_TOKEN（新 Worker） =="
export GH
SECRET_BODY="$(python3 -c "
import json,os
print(json.dumps({'name':'GH_TOKEN','text':os.environ['GH'],'type':'secret_text'}))
")"
SECRET_OK=0
for m in PUT POST; do
  RESP="$(auth -X "$m" -H "Content-Type: application/json" -d "$SECRET_BODY" \
      "$API/accounts/$AID/workers/scripts/$WNAME/secrets")"
  if printf '%s' "$RESP" | ok; then
    echo "  密钥绑定成功 (method=$m)"
    SECRET_OK=1
    break
  fi
  printf '%s' "$RESP" | head -c 300
  echo
done
[ "$SECRET_OK" = 1 ] || { echo "❌ GH_TOKEN 绑定失败"; exit 1; }

echo "== 3) 设 cron（1 条合并表达式覆盖 8 档） =="
for m in PUT; do
  RESP="$(auth -X "$m" -H "Content-Type: application/json" -d "$CRONS" \
      "$API/accounts/$AID/workers/scripts/$WNAME/schedules")"
  if printf '%s' "$RESP" | ok; then
    echo "  cron 设置成功 (method=$m)"
    break
  fi
done

echo "== 4) 部署后校验 =="
echo "-- 新 Worker ($WNAME) --"
auth "$API/accounts/$AID/workers/scripts/$WNAME/schedules" | python3 -c "
import sys,json;d=json.load(sys.stdin)
ss=(d.get('result') or {}).get('schedules') or []
print('  cron 条数=',len(ss))
for s in ss: print('   -',s['cron'])"
NEW_CRON_OK="$(auth "$API/accounts/$AID/workers/scripts/$WNAME/schedules" | python3 -c "
import sys,json;d=json.load(sys.stdin);print(len((d.get('result') or {}).get('schedules') or []))")"
auth "$API/accounts/$AID/workers/scripts/$WNAME/secrets" | python3 -c "
import sys,json;d=json.load(sys.stdin)
names=[s.get('name') for s in (d.get('result') or [])]
print('  secrets=',names)
sys.exit(0 if 'GH_TOKEN' in names else 1)" || { echo "❌ 新 Worker 缺 GH_TOKEN 绑定"; exit 1; }
[ "$NEW_CRON_OK" = 1 ] || { echo "❌ 新 Worker cron 数=$NEW_CRON_OK（应为 1 条合并表达式）"; exit 1; }
echo "  ✅ 1 条合并 cron（覆盖 8 档）+ GH_TOKEN 就位"
echo "-- 旧 Worker ($OLDNAME) 是否原样 --"
OLD_SCHEDULES_AFTER="$(auth "$API/accounts/$AID/workers/scripts/$OLDNAME/schedules")"
printf '%s' "$OLD_SCHEDULES_AFTER" | python3 -c "
import sys,json;d=json.load(sys.stdin)
ss=(d.get('result') or {}).get('schedules') or []
print('  cron 条数=',len(ss))
for s in ss: print('   -',s['cron'])"
[ "$OLD_SCHEDULES_BEFORE" = "$OLD_SCHEDULES_AFTER" ] \
  && echo "  ✅ 澳门 schedules 前后完全一致（未被触碰）" \
  || echo "  ❌ 澳门 schedules 变了！立即检查"

echo "完成。"
