#!/usr/bin/env bash
# setup_github.sh —— 一键把 HashMM 代码推到你新建的 GitHub 仓库（给 CI 跑 Docker 基准用）。
#
# 【第一步·在浏览器】先去 GitHub 建一个【空】仓库（不要勾 README/gitignore/license）：
#   https://github.com/new  → 填个名字（如 hashmm-bench）→ 选 Private → Create repository
#   建好后复制它的地址，形如：https://github.com/你的用户名/hashmm-bench.git
#
# 【第二步·准备一个 Token】（推送要用，代替密码）：
#   https://github.com/settings/tokens  → Generate new token (classic)
#   勾选 repo 权限 → 生成 → 复制那串 ghp_xxx（只显示一次，记下来）
#
# 【第三步·在你服务器上，进入源码根目录跑本脚本】：
#   cd /root/autodl-tmp/hashmm         # 你解压 V325 源码的目录（含 hashmm/ scripts/ .github/）
#   bash setup_github.sh https://github.com/你的用户名/hashmm-bench.git
#   然后按提示输入用户名和 Token（Token 当密码粘贴，粘贴时看不到是正常的）。
#
# 安全：.gitignore 已排除密钥/模型/数据/venv——本脚本推送前还会再扫一遍，发现疑似密钥会中止。

set -e
REPO_URL="$1"
if [ -z "$REPO_URL" ]; then
  echo "用法：bash setup_github.sh <你的仓库地址.git>"
  echo "例：  bash setup_github.sh https://github.com/yourname/hashmm-bench.git"
  exit 1
fi

cd "$(cd "$(dirname "$0")" && pwd)"
echo "== 工作目录：$(pwd) =="

# 确认这是源码根目录
if [ ! -d "hashmm" ] || [ ! -f "hashmm/__init__.py" ]; then
  echo "!! 当前目录不像 HashMM 源码根（没找到 hashmm/__init__.py）。请 cd 到解压源码的目录再跑。"
  exit 1
fi

command -v git >/dev/null 2>&1 || { echo "!! 没装 git：apt-get install -y git"; exit 1; }

# ── 推送前安全扫描：绝不把密钥推上去 ──
echo "== 安全扫描：检查是否有密钥会被推送 =="
STAGED_SECRETS=0
# 会被 git 跟踪的文件里，扫常见密钥特征
for pat in "sk-" "ghp_" "service_role" "SERVICE_KEY=ey" "JWT_SECRET=" "SUPABASE_SERVICE"; do
  # 只看没被 gitignore 的文件
  HITS=$(git init -q 2>/dev/null; git add -A -n 2>/dev/null | awk '{print $NF}' | \
         xargs grep -l "$pat" 2>/dev/null | grep -vE '\.md$|setup_github\.sh|README_CI' || true)
  if [ -n "$HITS" ]; then
    echo "  ⚠️ 疑似密钥「$pat」出现在将被推送的文件："; echo "$HITS" | sed 's/^/     /'
    STAGED_SECRETS=1
  fi
done
if [ "$STAGED_SECRETS" = "1" ]; then
  echo "!! 检测到疑似密钥会被推送——已中止。请把这些文件加进 .gitignore，或删掉其中的密钥后重试。"
  echo "   （启动脚本 start-hashmm*.sh 默认已在 .gitignore 里；若你改了名字，请手动加进去。）"
  exit 1
fi
echo "  ✓ 未发现会被推送的密钥"

# ── git 初始化 + 提交 + 推送 ──
[ -d .git ] || git init -q
git add -A
if git diff --cached --quiet; then
  echo "-- 没有新变更需要提交"
else
  git -c user.email="hashmm@local" -c user.name="hashmm" commit -q -m "HashMM 源码（供 CI 跑 Docker 基准）" || true
fi
git branch -M main 2>/dev/null || true
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_URL"

echo ""
echo "== 开始推送到 $REPO_URL =="
echo "   接下来会让你输入 GitHub 用户名 和 Token（Token 当密码粘贴，看不见是正常的）"
git push -u origin main

echo ""
echo "════════════════════ 完成 ════════════════════"
echo "代码已推上去。接下来去仓库页面："
echo "  1) Settings → Secrets and variables → Actions → 配 5 个 Secret（见 benchmarks/README_CI.md）"
echo "  2) Actions 页 → 「Docker Benchmarks → 回传后端」→ Run workflow → 选基准 → 运行"
echo "  跑完分数会自动回传你后端，进「和大厂对比」。"
