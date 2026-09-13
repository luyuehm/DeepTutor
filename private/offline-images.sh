#!/usr/bin/env bash
# ============================================
# DeepTutor 离线镜像导出 / 导入
#============================================
# 在有外网的机器上导出整套镜像，拷贝到离线服务器后导入，
# 即可在无外网环境使用 ./private/deploy.sh --offline 拉起。
#
#   # 有网机器(导出):
#   ./private/offline-images.sh save
#   # 产物: private/images/*.tar（含 deeptutor + redis + pocketbase）
#
#   # 拷贝 private/ 目录整包到离线服务器后:
#   ./private/offline-images.sh load
#   ./private/deploy.sh --offline
# ============================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

IMAGES_DIR="private/images"
mkdir -p "$IMAGES_DIR"

# 与 docker-compose.ghcr.yml 保持一致的三组镜像
IMAGES=(
  "ghcr.io/hkuds/deeptutor:latest"
  "redis:7.4-alpine"
  "ghcr.io/muchobien/pocketbase:latest"
)

case "${1:-}" in
  save)
    for image in "${IMAGES[@]}"; do
      echo "🔽 拉取并导出 $image"
      docker pull "$image"
      tag="$(echo "$image" | tr '/:' '__')"
      docker save -o "$IMAGES_DIR/${tag}.tar" "$image"
    done
    echo ""
    echo "✅ 镜像已导出到 private/images/:"
    ls -lh "$IMAGES_DIR"/*.tar
    echo ""
    echo "下一步: 把整个 private/ 目录(可含 deploy.sh/offline-images.sh)拷贝到离线服务器，"
    echo "        再运行 ./private/offline-images.sh load && ./private/deploy.sh --offline"
    ;;
  load)
    for tarball in "$IMAGES_DIR"/*.tar; do
      [[ -e "$tarball" ]] || { echo "❌ private/images/ 下没有 .tar 镜像文件"; exit 1; }
      echo "📦 导入 $tarball"
      docker load -i "$tarball"
    done
    echo "✅ 离线镜像导入完成，可运行 ./private/deploy.sh --offline"
    ;;
  *)
    echo "用法: ./private/offline-images.sh save|load"
    echo "  save  — 在有网机器导出镜像到 private/images/"
    echo "  load  — 在离线服务器从 private/images/ 导入镜像"
    exit 2
    ;;
esac