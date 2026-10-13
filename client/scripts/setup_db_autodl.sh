#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════
# AutoDL 容器内一键安装 PostgreSQL + Redis（无需 Docker）
#
# 适用场景：你的 AutoDL 容器没有独立数据库服务器，直接在容器内跑。
# 这个脚本会：
#   1. 安装 PostgreSQL 和 Redis（apt）
#   2. 启动它们
#   3. 创建 hashmm 数据库和用户
#   4. 打印你需要设置的环境变量
#
# 用法：  bash scripts/setup_db_autodl.sh
# ════════════════════════════════════════════════════════════════════════
set -e

PG_USER="${HASHMM_PG_USER:-hashmm}"
PG_PASS="${HASHMM_PG_PASS:-hashmm}"
PG_DB="${HASHMM_PG_DB:-hashmm}"

echo "═══ [1/4] 安装 PostgreSQL + Redis ═══"
if ! command -v psql >/dev/null 2>&1; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql postgresql-contrib redis-server
else
    echo "  已安装，跳过"
fi

echo "═══ [2/4] 启动服务 ═══"
# AutoDL 容器通常没有 systemd，直接用 pg_ctl / redis-server 后台启动
PG_BIN=$(ls -d /usr/lib/postgresql/*/bin | head -1)
PG_DATA="/var/lib/postgresql/data"
mkdir -p "$PG_DATA"
chown -R postgres:postgres "$PG_DATA" 2>/dev/null || true

# 初始化数据目录（首次）
if [ ! -f "$PG_DATA/PG_VERSION" ]; then
    su postgres -c "$PG_BIN/initdb -D $PG_DATA" >/dev/null 2>&1 || true
fi
# 启动 PostgreSQL
su postgres -c "$PG_BIN/pg_ctl -D $PG_DATA -l /tmp/pg.log -o '-p 5432' start" 2>/dev/null || \
    echo "  PostgreSQL 可能已在运行"
sleep 3
# 启动 Redis（后台）
redis-server --daemonize yes --port 6379 2>/dev/null || echo "  Redis 可能已在运行"
sleep 1

echo "═══ [3/4] 创建数据库和用户 ═══"
su postgres -c "psql -p 5432 -tc \"SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'\"" | grep -q 1 || \
    su postgres -c "psql -p 5432 -c \"CREATE USER $PG_USER WITH PASSWORD '$PG_PASS';\""
su postgres -c "psql -p 5432 -tc \"SELECT 1 FROM pg_database WHERE datname='$PG_DB'\"" | grep -q 1 || \
    su postgres -c "psql -p 5432 -c \"CREATE DATABASE $PG_DB OWNER $PG_USER;\""

echo "═══ [4/4] 完成 ═══"
echo ""
echo "PostgreSQL: 127.0.0.1:5432  (db=$PG_DB user=$PG_USER)"
echo "Redis:      127.0.0.1:6379"
echo ""
echo "现在设置环境变量后初始化 schema 并启动："
echo ""
echo "  export HASHMM_DB_BACKEND=postgres"
echo "  export HASHMM_PG_DSN=\"postgresql://$PG_USER:$PG_PASS@127.0.0.1:5432/$PG_DB\""
echo "  export HASHMM_REDIS_URL=\"redis://127.0.0.1:6379/0\""
echo ""
echo "  python scripts/init_postgres.py            # 建表"
echo "  python scripts/migrate_sqlite_to_pg.py     # （可选）迁移旧 SQLite 数据"
echo "  CUDA_VISIBLE_DEVICES=0 python -m uvicorn hashmm.api.server:app --host 0.0.0.0 --port 6006"
echo ""
echo "⚠️  注意：AutoDL 容器重启后需要重新运行本脚本启动 PG/Redis（或写进启动脚本）。"
echo "    如果你只想要更快但不想折腾，可以【不设这些环境变量】，继续用调优后的 SQLite——"
echo "    对你这种读多写少的场景，SQLite+WAL 足够撑住 10000 用户的日常负载。"
