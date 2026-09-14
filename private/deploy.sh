#!/usr/bin/env bash
# ============================================
# DeepTutor 一键私有化部署
#============================================
# 在自备服务器 / 机构服务器上从零拉起 DeepTutor：
#   * 数据默认落本地 ./data（学习/订单/用户/知识库/日志全部本地）
#   * 默认使用官方 GHCR 镜像（在线一键拉起）
#   * 断网环境：见 offline-images.sh 导出镜像后，用 --offline 离线导入
#   * LLM 默认云端网关（CPA/OpenAI 兼容）；私有化可切本地模型端点（见
#     local-llm/README.md，Ollama / vLLM / llama.cpp）
#
# 用法:
#   ./private/deploy.sh                # 在线一键拉起（认证默认开启，首次需注册管理员）
#   ./private/deploy.sh --offline      # 离线（从 images/ 导入镜像后拉起）
#   ./private/deploy.sh --local-llm    # 额外启动本地 LLM 网关(Ollama)
#   ./private/deploy.sh --host 203.0.113.10   # 远端服务器(浏览器从别处访问)
#   ./private/deploy.sh --auth admin secret123 # 显式设置管理员账密（推荐）
#   ./private/deploy.sh --auth         # 强制认证：随机生成管理员口令并打印一次
#   ./private/deploy.sh --no-auth      # 关闭认证（仅 loopback 单机自用，勿用于公网）
#   ./private/deploy.sh down           # 停止（保留数据卷）
#   ./private/deploy.sh wipe           # 停止并清空本地数据（破坏性）
#   ./private/deploy.sh --check        # 只做前置检查，不拉起（只读）
#
# 常用选项:
#   --host <ip|hostname>   设置 system.json next_public_api_base_external
#   --auth [<user> <pass>] 开启管理员基础认证（写入 auth.json）。不带账密时
#                          随机生成口令并在控制台打印一次——强制认证模式的推荐用法
#   --no-auth              关闭认证（AUTH_ENABLED=false）。仅适合 loopback 单机
#                          自用；非本地网络部署严禁使用（S-AUTH-01, RIC-754）
#   --offline              offline 模式（镜像从 private/images/ 导入）
#   --local-llm            同时拉起本地 LLM 网关（Ollama，默认 localhost:11434）
#   --port <backend>:<frontend>  覆盖宿主机端口映射
# ============================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

COMPOSE_BASE_FILE="docker-compose.ghcr.yml"
PRIVATE_COMPOSE_FILES=()
OFFLINE_MODE=0
LOCAL_LLM=0
CHECK_ONLY=0
HOST_EXTERNAL=""
AUTH_USER=""
AUTH_PASS=""
AUTH_RANDOM=0
AUTH_EXPLICIT=0
NO_AUTH=0
BACKEND_PORT_OVERRIDE=""
FRONTEND_PORT_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    down|stop)
      local_llm_files=()
      [[ "$LOCAL_LLM" == "1" ]] && local_llm_files+=(-f private/compose.local-llm.yml)
      if [[ "${#local_llm_files[@]}" -gt 0 ]]; then
        docker compose --env-file data/user/settings/docker.env -f "$COMPOSE_BASE_FILE" "${local_llm_files[@]}" stop
      else
        docker compose --env-file data/user/settings/docker.env -f "$COMPOSE_BASE_FILE" stop
      fi
      echo "已停止 (数据卷保留于 ./data)"; exit 0 ;;
    wipe)
      local_llm_files=()
      [[ "$LOCAL_LLM" == "1" ]] && local_llm_files+=(-f private/compose.local-llm.yml)
      if [[ "${#local_llm_files[@]}" -gt 0 ]]; then
        docker compose --env-file data/user/settings/docker.env -f "$COMPOSE_BASE_FILE" "${local_llm_files[@]}" down -v
      else
        docker compose --env-file data/user/settings/docker.env -f "$COMPOSE_BASE_FILE" down -v
      fi
      echo "已停止并清空本地数据。"; exit 0 ;;
    --check) CHECK_ONLY=1; shift ;;
    --host) HOST_EXTERNAL="$2"; shift 2 ;;
    --auth)
      # --auth alone  => 强制认证 + 随机口令；--auth user pass => 显式账密
      AUTH_EXPLICIT=1
      if [[ $# -ge 3 && "${2}" != -* && "${3}" != -* ]]; then
        AUTH_USER="$2"; AUTH_PASS="$3"; shift 3
      else
        AUTH_RANDOM=1; shift
      fi ;;
    --no-auth) NO_AUTH=1; shift ;;
    --offline) OFFLINE_MODE=1; shift ;;
    --local-llm) LOCAL_LLM=1; shift ;;
    --port) BACKEND_PORT_OVERRIDE="${2%%:*}"; FRONTEND_PORT_OVERRIDE="${2##*:}"; shift 2 ;;
    -h|--help) grep -E "^#   " "$0" | sed 's/^#   //'; exit 0 ;;
    *) echo "未知参数: $1 (用 --help 查看用法)"; exit 2 ;;
  esac
done

echo "============================================"
echo "🚀 DeepTutor 私有化部署"
echo "============================================"

# ---- 0. 只读前置检查模式 ----
if [[ "$CHECK_ONLY" == "1" ]]; then
  command -v docker >/dev/null 2>&1 || { echo "❌ 需要 Docker"; exit 1; }
  docker info >/dev/null 2>&1 || { echo "❌ Docker daemon 未运行"; exit 1; }
  echo "✅ Docker: $(docker --version)"
  echo "✅ Compose: $(docker compose version 2>/dev/null || echo 'n/a')"
  echo "✅ 数据目录: $(ls -d data/user/settings 2>/dev/null || echo '不存在(首次运行会创建)')"
  if [[ "${#PRIVATE_COMPOSE_FILES[@]}" -gt 0 ]]; then
    python3 scripts/docker_compose.py -f "$COMPOSE_BASE_FILE" "${PRIVATE_COMPOSE_FILES[@]}" config --quiet && echo "✅ compose 配置有效"
  else
    python3 scripts/docker_compose.py -f "$COMPOSE_BASE_FILE" config --quiet && echo "✅ compose 配置有效"
  fi
  if [[ "$OFFLINE_MODE" == "1" ]]; then
    echo "✅ 离线镜像: $(ls private/images/*.tar 2>/dev/null | wc -l | tr -d ' ') 个 tar"
  else
    echo "ℹ️  将从 ghcr.io 拉取官方镜像（在线模式）"
  fi
  echo "✅ 前置检查通过"
  exit 0
fi

# ---- 0. 离线镜像导入 ----
if [[ "$OFFLINE_MODE" == "1" ]]; then
  if [[ ! -d private/images ]]; then
    echo "❌ --offline 需要 private/images/ 目录存在镜像 tarball。
   在有网机器上运行 ./private/offline-images.sh 导出，拷贝整个 private/ 目录到离线服务器。"
    exit 1
  fi
  echo "📦 导入离线镜像..."
  for tarball in private/images/*.tar; do
    [[ -e "$tarball" ]] || { echo "   (无镜像文件)"; break; }
    echo "   docker load -i $tarball"
    docker load -i "$tarball"
  done
  # 离线时禁止 first-pull，改用本地已有镜像
  export COMPOSE_PULL_POLICY=never
fi

# ---- 1. 前置检查 ----
command -v docker >/dev/null 2>&1 || { echo "❌ 需要 Docker。安装: https://docs.docker.com/engine/install/"; exit 1; }
docker info >/dev/null 2>&1 || { echo "❌ Docker daemon 未运行。请先启动 Docker。"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "❌ 需要 python3（用于渲染端口配置）。"; exit 1; }
echo "✅ Docker 就绪: $(docker --version)"

# ---- 2. 初始化本地数据目录（若不存在则生成默认 settings） ----
# init_user_directories 只补缺失的界面/运行配置文件，不会覆盖已有 JSON。
mkdir -p data/user/settings
if [[ ! -f data/user/settings/system.json ]]; then
  echo "📁 初始化 data/user/settings/system.json..."
  cat > data/user/settings/system.json <<'JSON'
{
  "version": 1,
  "version_check_enabled": false,
  "backend_port": 8001,
  "backend_workers": 1,
  "frontend_port": 3782,
  "next_public_api_base_external": "",
  "next_public_api_base": "",
  "cors_origin": "",
  "cors_origins": [],
  "disable_ssl_verify": false,
  "chat_attachment_dir": "",
  "sandbox_allow_subprocess": true,
  "capability_routing_enabled": false,
  "chat_attachment_max_file_mb": 20,
  "chat_attachment_max_total_mb": 25,
  "chat_attachment_max_chars_per_doc": 200000,
  "chat_attachment_max_chars_total": 150000
}
JSON
  echo "   (默认关闭版本联网检查 version_check_enabled=false)"
fi

# ---- 3. 认证 ----
# S-AUTH-01 (RIC-754): 认证默认开启。部署脚本据此保证：
# --auth user pass  -> 写入指定管理员账密（推荐）
# --auth (无参)     -> 随机生成管理员口令并打印一次（强制认证模式）
# --no-auth         -> 显式关闭认证（仅 loopback 单机自用）
# 未传任何认证参数 -> 不写 auth.json；服务以默认（认证开启）启动，首位用户经 /register 注册即成管理员
# 已存在的 auth.json 不会被覆盖（避免清掉运营中改过的口令）。
PY="python3"
if [[ -x ".venv/bin/python" ]] && .venv/bin/python -c "import bcrypt" 2>/dev/null; then
  PY=".venv/bin/python"
fi

if [[ "$NO_AUTH" == "1" ]]; then
  cat > data/user/settings/auth.json <<'JSON2'
{
  "version": 1,
  "enabled": false,
  "username": "admin",
  "password_hash": "",
  "token_expire_hours": 24,
  "cookie_secure": false
}
JSON2
  echo "🔓 已关闭认证（仅限 loopback 单机自用；公网/LAN 部署严禁使用）"
elif [[ -n "$AUTH_USER" && -n "$AUTH_PASS" ]]; then
  HASH="$("$PY" -c 'import sys, bcrypt; print(bcrypt.hashpw(sys.stdin.read().rstrip("\n").encode(), bcrypt.gensalt()).decode())' <<< "$AUTH_PASS")"
  cat > data/user/settings/auth.json <<JSON2
{
  "version": 1,
  "enabled": true,
  "username": "$AUTH_USER",
  "password_hash": "$HASH",
  "token_expire_hours": 24,
  "cookie_secure": false
}
JSON2
  echo "🔐 已开启基础认证（用户: $AUTH_USER）"
elif [[ "$AUTH_RANDOM" == "1" ]]; then
  AUTH_USER="admin"
  AUTH_PASS="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(18))')"
  HASH="$("$PY" -c 'import sys, bcrypt; print(bcrypt.hashpw(sys.stdin.read().rstrip("\n").encode(), bcrypt.gensalt()).decode())' <<< "$AUTH_PASS")"
  cat > data/user/settings/auth.json <<JSON2
{
  "version": 1,
  "enabled": true,
  "username": "$AUTH_USER",
  "password_hash": "$HASH",
  "token_expire_hours": 24,
  "cookie_secure": false
}
JSON2
  echo "🔐 强制认证：已生成随机管理员口令"
  echo "   用户名: $AUTH_USER"
  echo "   口令  : $AUTH_PASS"
  echo "   ⚠️ 请立即保存；此口令仅本次显示，不会再次输出"
elif [[ ! -f data/user/settings/auth.json ]]; then
  echo "🔐 认证默认开启。未指定 --auth：首位用户经 /register 注册即成管理员"
  echo "   （公网部署推荐: ./private/deploy.sh --auth <user> <pass> 或 --auth 随机口令）"
else
  echo "🔐 沿用已有 data/user/settings/auth.json"
fi

# ---- 4. 远端 host（API base URL） ----
if [[ -n "$HOST_EXTERNAL" ]]; then
  python3 - "$HOST_EXTERNAL" <<'PY'
import json, sys
host = sys.argv[1]
p = "data/user/settings/system.json"
try:
    d = json.load(open(p))
except Exception:
    d = {}
d["next_public_api_base_external"] = f"http://{host}:{d.get('backend_port', 8001)}"
json.dump(d, open(p, "w"), ensure_ascii=False, indent=2)
print(f"📌 next_public_api_base_external -> http://{host}:{d.get('backend_port', 8001)}")
PY
fi

# ---- 5. 端口覆盖 ----
if [[ -n "$BACKEND_PORT_OVERRIDE" || -n "$FRONTEND_PORT_OVERRIDE" ]]; then
  python3 - "$BACKEND_PORT_OVERRIDE" "$FRONTEND_PORT_OVERRIDE" <<'PY'
import json, sys
backend, frontend = sys.argv[1], sys.argv[2]
p = "data/user/settings/system.json"
d = json.load(open(p))
if backend: d["backend_port"] = int(backend)
if frontend: d["frontend_port"] = int(frontend)
json.dump(d, open(p, "w"), ensure_ascii=False, indent=2)
print("📌 端口 ->", d.get("backend_port"), d.get("frontend_port"))
PY
fi

# ---- 6. 渲染 compose 端口 + 拉起 ----
if [[ "$LOCAL_LLM" == "1" ]]; then
  echo "🦙 附带拉起本地 LLM 网关 (Ollama)..."
  PRIVATE_COMPOSE_FILES+=(-f private/compose.local-llm.yml)
fi

echo "⚙️  渲染 Docker Compose 端口配置..."
if [[ "$OFFLINE_MODE" == "1" ]]; then
  # 离线：镜像已由 docker load 导入，跳过网络拉取；COMPOSE_PULL_POLICY=never 兜底
  if [[ "${#PRIVATE_COMPOSE_FILES[@]}" -gt 0 ]]; then
    python3 scripts/docker_compose.py -f "$COMPOSE_BASE_FILE" "${PRIVATE_COMPOSE_FILES[@]}" up -d
  else
    python3 scripts/docker_compose.py -f "$COMPOSE_BASE_FILE" up -d
  fi
else
  if [[ "${#PRIVATE_COMPOSE_FILES[@]}" -gt 0 ]]; then
    python3 scripts/docker_compose.py -f "$COMPOSE_BASE_FILE" "${PRIVATE_COMPOSE_FILES[@]}" pull || { echo "   ⚠️ 在线拉取失败（可能无外网）。若为离线环境，请用 --offline + offline-images.sh。"; }
    python3 scripts/docker_compose.py -f "$COMPOSE_BASE_FILE" "${PRIVATE_COMPOSE_FILES[@]}" up -d
  else
    python3 scripts/docker_compose.py -f "$COMPOSE_BASE_FILE" pull || { echo "   ⚠️ 在线拉取失败（可能无外网）。若为离线环境，请用 --offline + offline-images.sh。"; }
    python3 scripts/docker_compose.py -f "$COMPOSE_BASE_FILE" up -d
  fi
fi

# ---- 7. 健康等待 ----
BACKEND_PORT="$(python3 -c 'import json;print(json.load(open("data/user/settings/system.json")).get("backend_port",8001))')"
FRONTEND_PORT="$(python3 -c 'import json;print(json.load(open("data/user/settings/system.json")).get("frontend_port",3782))')"
echo "⏳ 等待服务就绪 (backend :$BACKEND_PORT / frontend :$FRONTEND_PORT)..."
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${BACKEND_PORT}/health/ready" >/dev/null 2>&1; then
    echo "✅ 后端就绪"
    break
  fi
  [[ $i -eq 60 ]] && { echo "⚠️ 后端 60s 未就绪，请查看日志: docker compose -f $COMPOSE_BASE_FILE logs -f deeptutor"; exit 3; }
  sleep 2
done

echo ""
echo "============================================"
echo "🎉 DeepTutor 私有化部署完成"
echo "============================================"
echo "  前端:  http://localhost:${FRONTEND_PORT}"
echo "  后端:  http://localhost:${BACKEND_PORT}"
echo "  数据:  ./data   (全部本地，含用户/订单/学习/日志)"
echo "  日志:  ./data/user/logs + docker compose logs -f deeptutor"
echo ""
echo "  LLM:   默认云端网关（CPA）。切本地模型 → private/local-llm/README.md"
echo "  停止:  ./private/deploy.sh down"
echo "  备份:  tar czf deeptutor-data.tgz ./data"
echo "============================================"