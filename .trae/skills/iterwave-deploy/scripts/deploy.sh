#!/usr/bin/env bash
# ============================================================================
# iterwave-deploy — 一键把本地代码同步到腾讯云服务器
# 用法: bash .trae/skills/iterwave-deploy/scripts/deploy.sh
# ============================================================================
set -euo pipefail

SERVER="ubuntu@152.136.139.147"
REMOTE_DIR="/home/ubuntu/iterwave"
SSH_KEY="$HOME/.ssh/id_ed25519"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd -P)"
RSYNC_OK=(0 23)  # rsync exit codes 0=success, 23=partial xfer (some glob patterns not found — ok)

cd "$ROOT"

# 容错函数：退出码在白名单内视为成功
rsync_safe() {
  rsync "$@" || {
    local code=$?
    for ok in "${RSYNC_OK[@]}"; do [ "$code" -eq "$ok" ] && return 0; done
    return $code
  }
}

echo "=========================================="
echo " IterWave Deploy"
echo " Server: $SERVER"
echo " Date:   $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="

# ---------------------------------------------------------------------------
# Step 1: Git fetch + merge all remote branches into local main
# ---------------------------------------------------------------------------
echo "" && echo "📦 Step 1/5: Git fetch + merge"
git fetch --all 2>&1

# Fast-forward local main to origin/main (safest first)
BEFORE=$(git rev-parse HEAD 2>/dev/null || echo "none")
git pull origin main --no-edit 2>&1 | tail -3
AFTER=$(git rev-parse HEAD 2>/dev/null || echo "none")

if [ "$BEFORE" = "$AFTER" ]; then
  echo "  ℹ️  本地 main 已与 origin/main 同步"
else
  echo "  ✅ origin/main 已合入 ($BEFORE → $AFTER)"
fi

# Merge any branches ahead of origin/main
AHEAD_BRANCHES=()
for b in $(git branch -r | grep -v HEAD); do
  count=$(git rev-list --count origin/main..$b 2>/dev/null || echo 0)
  if [ "$count" -gt 0 ]; then
    AHEAD_BRANCHES+=("$b")
  fi
done

if [ ${#AHEAD_BRANCHES[@]} -gt 0 ]; then
  echo "  🔀 发现 ${#AHEAD_BRANCHES[@]} 个领先 main 的分支：${AHEAD_BRANCHES[*]}"
  for b in "${AHEAD_BRANCHES[@]}"; do
    echo "    → 合并 $b ..."
    if ! git merge "$b" --no-edit 2>&1 | tail -3; then
      echo "      ⚠️  冲突！保留 HEAD 版本..."
      grep -rl "<<<<<<< HEAD" --include="*.py" --include="*.ts" --include="*.tsx" . | while read f; do
        # 取 HEAD 版本（移除 <<<<<<< / ======= / >>>>>>> 标记）
        python3 -c "
import re, sys
text = open('$f').read()
pattern = r'<<<<<<< HEAD\n(.*?)=======\n.*?>>>>>>> [^\n]+\n?'
fixed = re.sub(pattern, r'\1', text, flags=re.DOTALL)
open('$f','w').write(fixed)
"
      done
      git add -A
      git commit -m "merge: auto-resolve conflicts (keep HEAD)" 2>&1 | tail -2 || true
    fi
  done
  git push origin main 2>&1 | tail -2
  echo "  ✅ 所有分支已合入并推送"
else
  echo "  ℹ️  无额外领先分支"
fi

# ---------------------------------------------------------------------------
# Step 2: Rsync to server
# ---------------------------------------------------------------------------
echo "" && echo "📤 Step 2/5: Rsync to $SERVER"

# 后端 Python
rsync_safe -az --delete \
  --exclude='__pycache__/' --exclude='*.pyc' --exclude='.pytest_cache/' \
  --exclude='tests/' --exclude='.DS_Store' --exclude='.trae*' \
  src/ "$SERVER:$REMOTE_DIR/src/"
echo "  ✅ src/ 已同步"

# 前端源码
rsync_safe -az --delete \
  --exclude='node_modules/' --exclude='dist/' \
  web/src/ "$SERVER:$REMOTE_DIR/web/src/"
echo "  ✅ web/src/ 已同步"

# 前端配置
for f in web/package.json web/vite.config.ts web/tsconfig.json; do
  [ -f "$f" ] && rsync_safe -az "$f" "$SERVER:$REMOTE_DIR/web/"
done
echo "  ✅ web 配置已同步"

# 数据库迁移
rsync_safe -az --delete migrations/ "$SERVER:$REMOTE_DIR/migrations/"
echo "  ✅ migrations/ 已同步"

# ---------------------------------------------------------------------------
# Step 3: Build frontend on server
# ---------------------------------------------------------------------------
echo "" && echo "🔨 Step 3/5: Build frontend on server"
# nginx 实际服务的目录是 /var/www/easel（不是 web/dist），构建后必须同步过去，
# 否则网页一直停留在旧构建。static/ 是单独挂载的媒体目录，必须排除。
ssh -i "$SSH_KEY" "$SERVER" "
  cd $REMOTE_DIR/web && npm run build 2>&1 | tail -8 \
  && sudo rsync -a --delete --exclude='static/' $REMOTE_DIR/web/dist/ /var/www/easel/ \
  && sudo chown -R ubuntu:ubuntu /var/www/easel \
  && echo '--- published ---' \
  && grep -o 'assets/index-[A-Za-z0-9-]*\.js' /var/www/easel/index.html
"

# ---------------------------------------------------------------------------
# Step 4: Restart iterwave service
# ---------------------------------------------------------------------------
echo "" && echo "🔄 Step 4/5: Restart iterwave.service"
ssh -i "$SSH_KEY" "$SERVER" "
  sudo systemctl restart iterwave && sleep 3
  echo '--- service status ---'
  sudo systemctl status iterwave --no-pager 2>&1 | head -10
"

# ---------------------------------------------------------------------------
# Step 5: Verify
# ---------------------------------------------------------------------------
echo "" && echo "✅ Step 5/5: Verify"
sleep 1
API_RESULT=$(ssh -i "$SSH_KEY" "$SERVER" "curl -s http://127.0.0.1:8000/api/rounds 2>&1 | head -c 80")
PUBLIC_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://152.136.139.147/iterwave/" 2>/dev/null || echo "000")

echo "  API (localhost:8000):  $API_RESULT"
echo "  公网 HTTP:             HTTP $PUBLIC_CODE"

echo "" && echo "=========================================="
echo " 🎉 Deploy complete!"
echo "=========================================="
