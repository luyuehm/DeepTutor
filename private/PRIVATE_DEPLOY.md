# DeepTutor 私有化部署指南

> 本目录提供 DeepTutor 的**一键私有化部署**能力：机构/讲师在自备服务器上自托管，
> 数据默认本地化，无强制外部 telemetry 依赖。

- **在线一键拉起**：`./private/deploy.sh`
- **离线（断网）拉起**：`./private/offline-images.sh save` → 拷贝 `private/` 到离线服务器 → `load` → `./private/deploy.sh --offline`
- **LLM 切换**：`private/local-llm/README.md`
- **代码改动面**：**零业务代码改动**。复用现有 `docker-compose.ghcr.yml`、
  `scripts/docker_compose.py`、`data/user/settings/*.json` 运行配置。

---

## 1. 架构（复用现有组件，不新增第二套业务代码）

```
┌─ 服务器（自备 / 机构）───────────────────────────────┐
│  Docker Compose (docker-compose.ghcr.yml)           │
│    ├─ deeptutor       官方 GHCR 镜像，前后端一体       │
│    ├─ redis           内部协调(可选注入)               │
│    └─ pocketbase      可选 认证/存储 侧车(默认 SQLite)  │
│  ── 可选 ──────────────────────────────────────────    │
│    └─ ollama          本地 LLM 网关 (private/compose)  │
│  数据: ./data 全部本地                                 │
└─────────────────────────────────────────────────────┘
```

| 服务 | 镜像 | 端口(默认) | 数据 |
|------|------|-----------|------|
| deeptutor | `ghcr.io/hkuds/deeptutor:latest` | 后端 `8001`、前端 `3782` | `./data`（用户/订单/学习/日志/知识库/设置） |
| redis | `redis:7.4-alpine` | 内部 6379(未暴露) | `./data/redis` AOF |
| pocketbase | `ghcr.io/muchobien/pocketbase:latest` | `8090`（未启用时只用 SQLite） | `./data/pocketbase` |
| ollama（可选） | `ollama/ollama:latest` | `127.0.0.1:11434` | `./data/ollama` |

## 2. 数据 / 日志去向（全部本地）

| 内容 | 位置 |
|------|------|
| 运行时设置 JSON（含模型连接/密钥） | `data/user/settings/*.json` |
| 用户/订单/学习记录 | `data/users`、`data/user/workspace/chat_history.db`、SQLite 持久化 |
| 支付/订单流水 | `data/system`（订单账本、授权数据，见 RIC-546~550） |
| 知识库/记忆 | `data/memory`、`data/knowledge_bases`、`data/user/workspace/notebook` |
| 日志 | `data/user/logs/`；容器级 `docker compose -f docker-compose.ghcr.yml logs -f deeptutor` |
| 认证状态/密钥 | `data/system`（auth 状态、provider API key、per-owner secrets） |
| 备份 | `tar czf deeptutor-data.tgz ./data`（整树一把备份） |

> 与「SaaS 宿主」的唯一差别在 `system.json` / `auth.json` / `integrations.json` 的
> 配置值 —— 不会出现两套逻辑。

## 3. 离线（断网）部署流程

1. **有网机器**导出镜像：`./private/offline-images.sh save`
   - 产物：`private/images/*.tar`（deeptutor + redis + pocketbase）
2. **拷贝** `private/` 整个目录 + 仓库到离线服务器（`data/` 不含镜像时可不拷）。
3. **离线服务器**：
   ```bash
   ./private/offline-images.sh load
   ./private/deploy.sh --offline
   ```
   - `deploy.sh --offline` 自动 `docker load` 全部 tar，并用 `COMPOSE_PULL_POLICY=never`
     防止对 registry 的首次拉取。
4. 首次拉起时，`system.json` 若不存在会自动生成且 **`version_check_enabled=false`**
   （关闭 GitHub 版本联网检查）。已有部署可在设置里手动关闭，或直接改
   `data/user/settings/system.json` 的 `version_check_enabled` 为 `false`。

> 无外网影响面：核心学习/订单/审核链路零云依赖；唯一乐观联网点是 About 页
> 24h 一次的版本检查（8s 超时、失败不阻断、可关闭）。LLM 走本地端点则可完全不出网。

## 4. LLM：云端 vs 本地

| 场景 | 做法 |
|------|------|
| 默认云端网关（CPA/OpenAI 兼容） | 无需改动，`model_catalog.json` 已有默认连接 |
| 私有化切本地（Ollama/vLLM/llama.cpp） | `./private/deploy.sh --local-llm` + `private/local-llm/README.md` |

切换只改 `data/user/settings/model_catalog.json` 的 `base_url`，**不写第二套业务代码**。

## 5. 安全清单（10 维自查）

| 维度 | 本部署包状态 |
|------|-------------|
| 凭据 | `model_catalog.json` 的 API key 位于 gitignore 的 `data/`，不落库；`.secrets.baseline` 已覆盖 |
| 依赖 | 只复用官方镜像（deeptutor/redis/pocketbase/ollama）；镜像来自 GHCR/Docker Hub 官方命名空间 |
| 镜像 | `docker-compose.ghcr.yml` 用 `pull_policy: always`；离线导入用 `docker load` |
| 认证 | `--auth` 可选开启基础认证（PBKDF2-SHA256 口令哈希）；`auth.json` 默认 enabled=false |
| 注入 | DeepTutor 沙箱隔离 shell exec；`sandbox-runner` 侧车未在私有包启用时用受限子进程后端 |
| 暴露面 | 端口默认 `8001`/`3782` 环回绑定；要公网暴露需自行配置反向代理（HTTPS） |
| 配置 | `system.json` 关闭 `version_check`；CORS 空则同源 |
| 敏感数据 | 全部在 gitignore 的 `./data`；备份加密建议自行加脱敏 |
| 供应链 | 镜像 tag 固定说明；离线部署依赖信任的 `images.tar` |
| 合规 | 数据本地化满足数据不出园；日志按 `data/user/logs` 审计 |

> 参考差异文档 §8「私有化运维成本缓解」：本包把运维成本压到「Docker + 一个脚本」，
> 无需 K8s operator / 云控制面 / 独立运维平台。

## 6. 常用命令速查

```bash
./private/deploy.sh                          # 在线拉起
./private/deploy.sh --offline               # 离线拉起
./private/deploy.sh --local-llm             # 拉起 + 本地 LLM 网关
./private/deploy.sh --host <公网IP>          # 远端浏览器访问配置
./private/deploy.sh --auth admin 's3cret'   # 开启基础认证
./private/deploy.sh down                    # 停止(数据保留)
./private/deploy.sh wipe                    # 停止并清空本地数据
docker compose --env-file data/user/settings/docker.env -f docker-compose.ghcr.yml logs -f deeptutor   # 看日志
docker compose --env-file data/user/settings/docker.env -f docker-compose.ghcr.yml restart deeptutor   # 改配置后重启
```