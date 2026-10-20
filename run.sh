#!/bin/sh
# NAS 定时刷新入口。由 DSM 任务计划每天调用。
set -eu
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

: "${IPTV_PROXY:?在 $(pwd)/.env 里设置 IPTV_PROXY}"
: "${GIT_TOKEN:?在 $(pwd)/.env 里设置 GIT_TOKEN}"

echo "[$(date -Iseconds)] 开始"
python3 refresh.py

# 让 git 也走代理，并把令牌写进远端地址（仓库级配置，不污染全局）
git config http.proxy  "$IPTV_PROXY"
git config https.proxy "$IPTV_PROXY"
git remote set-url origin "https://x-access-token:${GIT_TOKEN}@github.com/douxt/iptv-english.git"

date -u +'%Y-%m-%dT%H:%M:%SZ' > .last-refresh
git add -A
if git diff --cached --quiet; then
  echo "[$(date -Iseconds)] 无变化"
else
  git commit -q -m "chore: refresh $(date -u +%F)"
  if git push -q; then
    echo "[$(date -Iseconds)] 已推送"
  else
    echo "[$(date -Iseconds)] push 失败，尝试 rebase"
    git pull --rebase -q && git push -q && echo "  重推成功" || echo "  重推仍失败"
  fi
fi
echo "[$(date -Iseconds)] 完成"
