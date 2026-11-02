---
name: iterwave-deploy
description: Deploy IterWave code to the remote server. Use when the user asks to pull git, sync code to server, update server, or deploy. Covers git fetch, merge remote branches, rsync to 152.136.139.147, frontend build, and systemd restart.
---

# IterWave Deploy

一键把本地代码同步到腾讯云服务器（`152.136.139.147`）并重启 iterwave 服务。

## 服务器信息

| 项 | 值 |
|---|---|
| 跳板 | 无（本机直连） |
| SSH | `ssh -i ~/.ssh/id_ed25519 ubuntu@152.136.139.147` |
| 项目路径 | `/home/ubuntu/iterwave/` |
| 进程托管 | systemd → `iterwave.service` |
| 前端目录 | `web/dist/`（Vite 构建） |
| 公网访问 | `http://152.136.139.147/iterwave/` |

## 部署流程

执行 `scripts/deploy.sh` 即可自动完成以下步骤：

### 1. Git 拉取 + 合并

```bash
git fetch --all
# 把 origin/main 和所有领先 main 的远程分支合并进本地 main
git pull origin main --no-edit
for b in $(git branch -r | grep -v HEAD); do
  count=$(git rev-list --count origin/main..$b 2>/dev/null)
  [ "$count" -gt 0 ] && git merge $b --no-edit
done
# 冲突处理：保留 HEAD（本地 main）的版本
git add -A && git commit -m "merge: resolve conflicts" && git push origin main
```

### 2. rsync 到服务器

**关键：必须分三次 rsync，且源路径末尾带 `/`（表示同步目录内容，不是目录本身）。**

```bash
# 后端 Python（排除大文件）
rsync -az --delete \
  --exclude='__pycache__/' --exclude='*.pyc' --exclude='.pytest_cache/' \
  --exclude='tests/' \
  src/ ubuntu@152.136.139.147:/home/ubuntu/iterwave/src/

# 前端源码（排除构建产物和 node_modules）
rsync -az --delete \
  --exclude='node_modules/' --exclude='dist/' \
  web/src/ ubuntu@152.136.139.147:/home/ubuntu/iterwave/web/src/

# 前端配置文件
rsync -az \
  web/package.json web/vite.config.ts web/tsconfig.json \
  ubuntu@152.136.139.147:/home/ubuntu/iterwave/web/

# 数据库迁移
rsync -az --delete \
  migrations/ ubuntu@152.136.139.147:/home/ubuntu/iterwave/migrations/
```

### 3. 服务器端：重建前端 + 重启

```bash
ssh ubuntu@152.136.139.147 '
  cd /home/ubuntu/iterwave/web && npm run build && \
  sudo systemctl restart iterwave && \
  sleep 3 && \
  sudo systemctl status iterwave --no-pager | head -10 && \
  curl -s http://127.0.0.1:8000/api/rounds | head -c 150 && \
  echo "" && \
  curl -s -o /dev/null -w "HTTP %{http_code}\n" http://152.136.139.147/iterwave/
'
```

## 常见坑

| 坑 | 解法 |
|---|---|
| rsync 后新文件在服务器上不存在 | rsync 源路径末尾必须带 `/`，且加 `--delete` |
| rsync shell glob 找不到文件（如 `.env*`） | 先用 `ls` 确认文件存在，或用 `--exclude` 代替逐个包含 |
| 冲突解决保留哪边 | 保留 HEAD（本地 main），因为它已包含之前所有合入分支的代码 |
| 服务器没有 `.git` | 服务器用 rsync 同步，不是 git clone，直接 rsync 即可 |
| systemd `KeepAlive` 自动重启 | 在本机 kill 端口只会让它自动拉起，必须 ssh 上去 `systemctl restart` |
| `launchctl` 在 macOS 沙箱里 permission denied | 本 skill 不涉及本机 launchctl，只管服务器 systemd |
