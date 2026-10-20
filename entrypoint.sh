#!/bin/sh
set -eu

: "${GIT_TOKEN:?需要设置 GIT_TOKEN（GitHub fine-grained PAT，Contents: Read and write）}"
GIT_REMOTE="${GIT_REMOTE:-github.com/douxt/iptv-english.git}"
INTERVAL="${INTERVAL:-86400}"

# 走网关 OpenClash 的 HTTP 代理（可选）
export IPTV_PROXY="${IPTV_PROXY:-}"
REPO=/data/repo

if [ ! -d "$REPO/.git" ]; then
  echo "[init] clone $GIT_REMOTE"
  git clone "https://x-access-token:${GIT_TOKEN}@${GIT_REMOTE}" "$REPO"
fi

cd "$REPO"
git config user.name  "iptv-refresh"
git config user.email "iptv-refresh@localhost"

while :; do
  echo "[$(date -Iseconds)] refresh 开始"
  # 探活在本机执行 —— 这就是与播放端一致的网络路径
  if python refresh.py; then
    date -u +'%Y-%m-%dT%H:%M:%SZ' > .last-refresh
    git add -A
    if git diff --cached --quiet; then
      echo "[$(date -Iseconds)] 无变化"
    else
      git commit -q -m "chore: refresh $(date -u +%F)"
      if ! git push -q; then
        echo "[warn] push 失败，尝试 rebase 后重推"
        git pull --rebase -q && git push -q || echo "[error] 重推仍失败"
      fi
      echo "[$(date -Iseconds)] 已提交并推送"
    fi
  else
    echo "[error] refresh.py 执行失败，本轮跳过提交"
  fi
  echo "[$(date -Iseconds)] 休眠 ${INTERVAL}s"
  sleep "$INTERVAL"
done
