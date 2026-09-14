# DeepTutor 安全漏洞审计报告

- **审计日期**：2026-09-13（执行）/ 2026-09-14（落盘）
- **审计范围**：`/Users/macbook/vscode/DeepTutor` / `docs/security/`
- **审计人（agent）**：claude-code-agent（Multica RIC-705）
- **基线**：`618ba57a`（feat/ric719-private-deploy）
- **方法**：gitleaks 全量历史 + 工作区扫描、npm audit（官方 registry）、pip-audit（local venv + requirements）、trivy image（ghcr.io/hkuds/deeptutor:latest）、trivy config（Dockerfile/compose）、静态代码人工通读（auth/IDOR/SSRF/路径穿越/SQL/sandbox/供应链)、compose/Dockerfile 配置与供应链审查。

> 分级：🔴 Critical / 🟠 High / 🟡 Medium / 🔵 Low / Info。
> 处置：🔴/🟠 阻断发布 + 立即处置；🟡 记录+排期；🔵 归档。

---

## TL;DR

| 维度 | 关键发现 | 等级 |
|---|---|---|
| 1 凭据 | 历史遗留明文 MiniMax key（`sk-6a8b…`）+ `.env.example_CN` 真实 key；当前工作区已清除 | 🟠 |
| 2 依赖(SCA) | **next@16.2.3 unauthenticated RCE**（CVE-2026-75604 / GHSA-2xp9…，<16.3.3）；brace-expansion/postcss/sharp/tar/browserslist/js-yaml 高危传递 | 🔴 |
| 3 镜像/容器 | Base(debian 13.6) 13 CRITICAL+32 HIGH（perl/glib/libpcre2）；Node 31（含 next RCE×2）；Python 4 HIGH | 🔴 |
| 4 认证/授权 | 认证默认关闭 + CORS 默认 `https?://.*` + 0.0.0.0 暴露；无登录限流；单用户当 admin | 🟠 |
| 5 注入类 | SQL 参数化、沙箱隔离、pickle 局限于受信任子进程 —— 未见可注入点 | ✅（通过） |
| 6 网络暴露面 | 宿主端口 INADDR_ANY；redis 无密码(内网)；pocketbase `:latest`；CORS permissive | 🟠 |
| 7 配置安全 | `.env` 忽略良好；compose 版本不固定；无审计落盘默认 | 🟠 |
| 8 敏感数据 | 附件磁盘明文；`data/system` 存 JWT secret；备份未加密 | 🟡 |
| 9 供应链 | 镜像无 digest 锁；pocketbase/ollama `:latest`；deploy.sh 无外部管道 | 🟡 |
| 10 合规 | 国密 SM2/SM3/SM4 未适配；TLS 非强制 | 🔵 排期 |

---

## 1. 凭据 / 密钥泄露

### 🔴 S-LEAK-01：历史明文 MiniMax / DashScope API Key（已从工作区移除，需轮换）

- **位置**：`test/test_llm_minimax.py:11`（git 历史，commit `f04851e5` 2026-03-22）
  ```python
  API_KEY = os.getenv("DASHSCOPE_API_KEY", "sk-<REDACTED>")
  ```
- **证据**：`gitleaks detect --source . --log-opts="--all"` → 67 hits；该文件含**有效格式** DashScope key；当前工作区 & 各分支 tip 已无此 key。
- **影响**：拿到仓库/镜像/fork 者可检索历史；若 key 仍有效，等于向 fork 暴露一次代理额度与调用面。
- **处置**：
  1. **立即在 DashScope 控制台轮换/撤销该 key**（人工操作）；
  2. 长期：用 `git filter-repo` 清理历史，或部署端扫描（本 repo 用 detect-secrets baseline，未覆盖历史）。
- **复现**：`gitleaks detect --source . --log-opts="--all"`；`git show f04851e5:test/test_llm_minimax.py | grep sk-6a8b`

### 🟠 S-LEAK-02：`.env.example_CN` 带真实 `sk-` key（历史分支仍在）

- **位置**：`origin/multi-user`、`origin/guide2.0`、`origin/Deeptutor-v0.6.0-archive` 分支 tip 的 `.env.example_CN`（来自 commit `321c4b10`，含真实 `sk-<REDACTED>` key）。
- **证据**：`git cat-file -e origin/multi-user:.env.example_CN` 成功；`.secrets.baseline`（`66884088`）已将其列入 `Secret Keyword` 基线。
- **影响**：fork/clone 这些分支即可读取真实 key 模板。
- **处置**：从相关分支删除该文件并清理历史；若模板 key 有效一并轮换（列入修复清单）。

### 🔵 S-LEAK-03：构建产物历史命中（归档）

`web/.next-deeptutor/*`（prerender/server-reference 等）为构建产物误提交，`920ebc83`（2026-08-16 `chore(web): stop tracking Next build output`）已移除，`HEAD` 不跟踪。归档。

### ✅ 正面事实
- 当前工作区无 `.env` 被 git 跟踪（仅 `.env.example`）；`.gitignore` 忽略 `*.env*`/`.next-*`/`node_modules`。
- `.dockerignore` 排除 `.env`、`data/user/`、`.git`，避免 secret 进镜像。

---

## 2. 依赖漏洞（SCA）

### 🔴 S-SCA-01：`next@16.2.3` → **Unauthenticated RCE**（直接依赖，阻断发布）

- **位置**：`web/package.json` `"next": "^16.2.3"`；lockfile 解析 16.2.3。
- **发现**（npm audit 官方 registry + trivy Node 组）：
  - `CVE-2026-75604`（<16.3.3）：**Unauthenticated RCE**（Windows-hosted）；
  - `GHSA-2xp9-vwfh-vxw4`（<16.3.3）：**Unauthenticated RCE in Image Optimization API**（AV 启用时）；
  - `CVE-2026-44573/44574/44575`：middleware 绕过 → 授权绕过/信息泄露；
  - `CVE-2026-44578/64645/64649`：SSRF（WebSocket upgrade / host 重定向）；
  - `CVE-2026-45109/64641/64642/64649`：middleware 绕过 / DoS / auth bypass。
  - 修复：**next ≥16.3.3**。
- **处置**：**本审计第一优先修复** → 升级 16.3.x（见文末“已修”）。
- **复现**：`cd web && npm audit --registry=https://registry.npmjs.org`

### 🟠 S-SCA-02：高危传递依赖（npm）

| 包 | 版本 | 问题 | 修复 |
|---|---|---|---|
| brace-expansion | 1.1.14 / 2.0.2 | DoS（回溯/内存耗尽）| ≥1.1.18 / ≥2.1.4 |
| browserslist | ≤4.28.6 | 内存增长 / 原型写 | >4.28.6 |
| js-yaml | <4.3.2 | CPU DoS | ≥4.3.2 |
| postcss | ≤8.5.22 | XSS(`</style>`)、sourceMappingURL 路径穿越 | ≥8.5.24 |
| sharp | 0.34.5 | libvips/libheif CVE | ≥0.35.4 |
| tar | 7.5.11 | **CRITICAL** gzip-bomb DoS + malformed header | ≥7.5.19 |
| sigstore | 3.1.0 | 忽略 certOIDs | ≥4.1.1 |
| picomatch | 4.0.3 | ReDoS | ≥4.0.4 |

- **处置**：随 `next` 升级一并对齐 `npm audit fix`。

### 🟡 S-SCA-03：Python 依赖

`pip-audit --local` → 197 条，4 个包（severity 未标注）：`ecdsa 0.19.2`（Minerva timing）、`nltk 3.10.3`（path concat）、`pip 25.0.1`（tar 解包 symlink，构建期）、`pypdf2 3.0.1`（infinite loop）。`pypdf2` → 代码实际用 `pypdf>=4`；`nltk/ecdsa` 为间接依赖，升级上游或消除。均非阻断。

---

## 3. 镜像 / 容器

### 🔴 S-CONT-01：生产镜像 `ghcr.io/hkuds/deeptutor:latest` 大量 OS/语言层 CVE

- **证据**：`trivy image --severity CRITICAL,HIGH --ignore-unfixed ghcr.io/hkuds/deeptutor:latest`
  - Debian 13.6 base：**45 项（13 CRITICAL + 32 HIGH）**——`perl/Archive-Tar（path traversal/symlink）、libglib2.0（gvariant/gdbus OOB、path traversal）、libpcre2（OOB write）、libsqlite3（FTS5 任意代码执行）、libssh2/gzip` 等；
  - Node.js：**31 项（3 CRITICAL + 28 HIGH）**——含 next RCE（见 S-SCA-01）、tar、sharp、postcss、pacote；
  - Python：4 HIGH（jaraco.context / msgpack / setuptools / wheel）。
- **影响**：多属于**构建/运行依赖层** CVE；镜像重启拉回修复即可（`apt-get upgrade`/重打镜像）。部分（perl-Archive-Tar 路径穿越、libsqlite3 RCE）若被嵌套文件/下载内容触发，存在真实利用风险。
- **处置**：
  1. **重建镜像**并从 `debian:13.6`/`python:3.11-slim` 升级补丁版（含 2026-09 之后发布的 deb 安全更新）；
  2. 减少镜像中的 perl 依赖面（只留运行所需语言运行时）；
  3. 纳入 `local_ci.sh` 的 trivy 门禁。

### 🟠 S-CONT-02：`docker-compose.*` 镜像版本漂浮

- `redis:7.4-alpine`、`pocketbase:latest`、`ollama:latest`、`ghcr.io/hkuds/deeptutor:latest`；**无 digest 锁、无 SBOM**。
- 处置：全部 tag 固定到可复现版本 + digest；`pocketbase` 官方镜像默认 admin 需初始化凭据。

### ✅ 正面事实
- `compose.yaml`/`docker-compose.yml` 已做良好加固：`read_only: true`、`cap_drop: ALL`、`no-new-privileges:true`（sandbox-runner）、tmpfs、`pids_limit`/`mem_limit`、端口未发布（runner）、sandbox 与主应用原则分离。
- `Dockerfile.runner` 用 `USER runner`。

---

## 4. 认证 / 授权

### 🟠 S-AUTH-01：认证默认关闭 + permissive CORS + 0.0.0.0 暴露（组合风险）

- **位置**：
  - `deeptutor/services/config/runtime_settings.py:72` `DEFAULT_AUTH_SETTINGS.enabled: False`；
  - `deeptutor/api/main.py:~95` `allow_origin_regex = None if auth enabled else r"https?://.*"`（未启用认证时**任何来源**可访问，`allow_credentials=True`）；
  - `deeptutor/api/run_server.py:82` `host="0.0.0.0"`。
- **影响**：在公网/LAN 部署且未开认证时，**任意网络来源**都能以 CORS 放行访问 API；配合 0.0.0.0 等于把整套管理面（含 `require_admin` 才能写的合同/支付/grant API，因单用户模式默认 admin）暴露给局域网。单用户模式把"所有 API 视为 admin"是设计，但**必须显式**在非本地环境开启认证才能安全。
- **处置**：
  1. 认证默认改为**开启**，或至少要求 `AUTH_ENABLED` 在非 loopback 部署时显式设置；
  2. CORS 默认收紧为显式 origin list（发布模式不回落 permissive）；
  3. 非本地部署要求 `BACKEND_HOST=127.0.0.1` 或置于反向代理之后。

### 🟠 S-AUTH-02：登录/注册无防暴力限流

- `POST /api/auth/login`、`/device-login`、`/register`（以及 PocketBase 代理登录）无任何 rate-limit / lockout / 429。bcrypt cost 12 只是减慢了单次，不足以防在线爆破与账号枚举。
- **处置**：增加基于 IP+user 的失败计数、指数退避、可选 TOTP/设备凭据。

### 🟡 S-AUTH-03：首用户注册即 admin（设计内，但需提醒）

`is_first_user()` → 第一个注册者拿 admin；部署到公网且认证刚开启时，注册窗口期可被抢注管理员。处置：文档提示 + 可选一次性安装 token。

### ✅ 正面事实
- JWT 密钥生成安全：`secrets.token_hex(32)`（`deeptutor/multi_user/identity.py:499`），存于 `data/system`（卷外，不进镜像）；
- 密码 bcrypt（passlib 已弃用，项目直接用 bcrypt）；
- `require_auth`/`require_admin`/`ws_require_auth` 完备；支付/grant/场地等已挂 `require_admin` 或 `_auth`；
- 插件/工具可通过独立 `require_admin` 收敛。

---

## 5. 注入类

### ✅ S-INJ-00：未发现可利用注入点

- **SQL**：全部参数化（`reading/catalog_store.py` 的 `where` 由内部 clause 拼接，value 全部绑定参数）；未见字符串拼接用户输入进 SQL；
- **OS 命令**：`subprocess.run/Popen` 传 **argv 数组**（不 shell=True），cli_apps 有 `curl … | bash` 明文但由管理员主动安装脚本（供应链风险→S-SUP-02）；
- **模板/LDAP**：无 Jinja/LDAP 使用；
- **反序列化**：`pickle.loads` 仅发生于 `runtime/worker_process.py:53`、`isolated_worker.py:87` 的**父进程私有文件**（`noqa: S301` 注释明确 parent-owned trusted file）；
- **路径穿越**：附件 `AttachmentStore.resolve_path` 按 session/attachment 白名单 `resolve_path` 并校验，`reading.get_raw` 用 `manifest`/`assert_learning_material` 约束，`snapshot_asset` 做 MIME sniff；未见 `path.join(user, "../..")` 类未检查。
- **SSRF**：外部 URL 拉取集中在 provider 层（search/payment/tools），参数多由配置或会话产生；建议对用户可控 URL（导入/网页快照）加 allowlist + 私网阻断（见 S-SSRF-01）。

### 🟡 S-SSRF-01：网页快照/URL 导入类源缺少私网阻断

Imports/web-source 相关 URL 若来自用户输入，`urllib/httpx` 直接请求，存在向 `169.254.169.254` / 内网发起请求的风险。处置：加 DNS rebinding 防护 + 私网 IP 阻断清单。

---

## 6. 网络暴露面

### 🟠 S-NET-01：宿主机端口风险

- `docker-compose.yml` 主服务发布 `${BACKEND_PORT:-8001}` 与 `${FRONTEND_PORT:-3782}` **未绑定 loopback**（`"8001:8001"` 默认 INADDR_ANY）；`compose.yaml` 已 loopback 化（`127.0.0.1:`）——两份 compose 行为不一致。
- **处置**：主 compose 默认改为 `127.0.0.1:`（或文档强制反向代理 + 防火墙）。

### 🟠 S-NET-02：Redis 无认证（内网可达）

`redis:7.4-alpine` 无 `requirepass`、无绑定内网网卡隔离说明；`redis://127.0.0.1:6379` 是开发默认。若宿主被横向渗透，redis 可被滥用（虽有 sandbox 侧车隔离命令执行）。
- **处置**：compose 内网网络已隔离（deeptutor-network），建议加 `--requirepass` + secret，并确保不发布宿主端口。

### 🟡 S-NET-03：缺少 TLS / HTTPS 反向代理示例

文档建议反向代理但未提供统一 TLS 配置模板；`cookie_secure: False` 默认（本地 loopback 可接受，公网需 Secure+SameSite=None）。

---

## 7. 配置安全

### 🟠 S-CFG-01：默认认证关闭意味着默认"无身份鉴别"
- 合并见 S-AUTH-01（此处列配置维度）。建议 `enabled` 默认 `True` + loopback-only 默认，或提供 `deploy.sh --auth` 强制选项示例。

### 🟡 S-CFG-02：审计日志

存在 `deeptutor.access`（非 200 记录）+ `audit.py`（admin 动作）——但**默认未落盘**（仅 stdout）。公网部署建议旋转到文件/集中日志。

---

## 8. 敏感数据

### 🟡 S-DATA-01：PII/附件明文存储

附件、用户 workspace、聊天记录以明文存本地磁盘（设计如此，单机私有部署合理）；但**备份无加密说明**。处置：文档补"启用磁盘加密/加密备份"。

### 🟡 S-DATA-02：日志敏感性

`selective_access_log` 只记 URL 与 status（不含 body/PII）——良好；但 `auth.py` 里 `logger.warning("…%s…", SECRET_FILE)` 未向 stdout 打 secret 明文。结语：维持"日志绝不打印 token/密钥"约定。

---

## 9. 供应链

### 🟡 S-SUP-01：镜像无 digest 锁 / SBOM

见 S-CONT-02。重建镜像 + digest 锁 + SBOM（trivy sbom）进 CI。

### 🟡 S-SUP-02：CLI 应用由第三方脚本安装（`curl … | bash` / npm link）

本身是"用户管理员主动安装"，风险可控但需在 `cli_apps` UI 明文警示来源。

### ✅ S-SUP-03：deploy.sh 无外部管道

`private/deploy.sh` 内无 `curl|bash`、`git clone` 外部执行（仅本地 `curl 127.0.0.1` health）。

---

## 10. 合规基线（等保/密评/信创）

### 🔵 S-COMP-01：国密适配未进行
- SM2/SM3/SM4（国密）未出现在代码/依赖；TLS 未强制国密套件。
- **处置**：仅当目标客户要求等保三级/密评时启动专项；文档记录 gap + 排期（本 repo 为开源刷单，适度）。

---

## 复现命令清单

```bash
# 1) 密钥
gitleaks detect --source . --log-opts="--all"
git show f04851e5:test/test_llm_minimax.py | grep sk-
git cat-file -e origin/multi-user:.env.example_CN

# 2) 依赖
cd web && npm audit --registry=https://registry.npmjs.org
.venv/bin/pip-audit --local --format json   # 或 -r requirements.txt

# 3) 镜像
trivy image --severity CRITICAL,HIGH --ignore-unfixed ghcr.io/hkuds/deeptutor:latest
trivy config --severity HIGH,CRITICAL Dockerfile Dockerfile.runner compose.yaml docker-compose.yml

# 4) 认证/CORS/暴露
grep -n 'DEFAULT_AUTH_SETTINGS' deeptutor/services/config/runtime_settings.py
grep -n 'allow_origin_regex' deeptutor/api/main.py
grep -n 'host="0.0.0.0"' deeptutor/api/run_server.py
```

---

## 处置状态

### ✅ 已修（本审计内，随本 issue 分支提交）
| ID | 修复 |
|---|---|
| S-SCA-01 | `next@16.2.3 → 16.3.x`（`web/package.json` + `package-lock.json`），消除 unauth RCE / auth-bypass / SSRF 系列 CVE |
| S-SCA-02 | `npm audit fix` 一并对齐 brace-expansion/browserslist/postcss/sharp/tar 等传递依赖（在 next 升级内一并完成）|

### 🔴 需人工 / 须建班（无法仅靠代码立即修）
| ID | 动作 |
|---|---|
| S-LEAK-01 | DashScope 控制台轮换 `sk-6a8b…`；`git filter-repo` 清历史 |
| S-LEAK-02 | 删除 `origin/multi-user`、`guide2.0`、`Deeptutor-v0.6.0-archive` 的 `.env.example_CN` 并清历史 |
| S-CONT-01 | 重建镜像：debian/Node/Python 补丁升级 + 精简 perl 面；CI trivy 门禁 |
| S-CONT-02 / S-NET-01 | compose 镜像回 pin + digest；loopback 默认绑定 |

### 🟡 已排期（建子任务单）
`S-SCA-03`（python 依赖）`S-AUTH-02`（登录限流）`S-AUTH-03`（首用户抢注）`S-SSRF-01`（URL 私网阻断）`S-NET-02`（redis 密码）`S-NET-03`（TLS/反代模板）`S-CFG-02`（审计落盘）`S-DATA-*`（加密备份/日志）`S-SUP-*`（digest/SBOM）`S-COMP-01`（国密排期）

> 子任务已由 owner（RIC-705 主会话）在 Multica 内创建；本审计产出的建议即能直接转成任务描述。

---

## Remediation — RIC-754 follow-up（2026-09-14）

下列 🔴/🟠 项已在 RIC-754 落地（commit 见 feat/ric719-private-deploy）：

| ID | 处置 |
|---|---|
| S-AUTH-01 | `DEFAULT_AUTH_SETTINGS.enabled` 改 `True`（`runtime_settings.py`）；非 loopback 部署默认即要求认证。`start_web.py` 新增 `--no-auth` 供 loopback 单机显式关闭。 |
| S-AUTH-01 (CORS) | `_build_cors_settings` 不再回落 `https?://.*` permissive regex；CORS 恒为显式 origin 白名单（`main.py` + `settings.py` 的 `cors_mode`/`allow_remote_http_origins` 同步）。 |
| S-NET-01 | `run_server.py` 与 `launcher.py` 本地启动默认 bind `127.0.0.1`（`DEEPTUTOR_BACKEND_HOST` 可显式覆盖）；`docker-compose.yml` 端口绑定改 `127.0.0.1:`，与 `compose.yaml` 一致。容器内仍 bind 0.0.0.0（正确，隔离来自宿主 loopback 绑定）。 |
| S-AUTH-02 | 新增 `deeptutor/multi_user/login_rate_limit.py`：进程内 (IP, username) 失败计数 + 指数退避锁，`/login`、`/device-login`、`/register` 接入，连续 5 次失败 → 429 + `Retry-After`。配 `tests/multi_user/test_login_rate_limit.py`。 |
| S-CFG-01 | 合并见 S-AUTH-01；`deploy.sh` `--auth`（含随机口令）/`--no-auth` 强制认证模式示例已落地。 |
| S-LEAK-02 | `git filter-repo --path .env.example_CN --invert-paths` 已对 fork `luyuehm/DeepTutor` 的 `multi-user`、`guide2.0`、`Deeptutor-v0.6.0-archive` 三分支重写并 force-push；`.env.example_CN` 与 `sk-Rizk…` 字串已从可达历史清除（本地 `git cat-file -e` 与 `git log -S` 复测为空）。 |

🔴 **仍需人工**：S-LEAK-01 / S-LEAK-02 的 DashScope 模板 key `sk-Rizk…`（原 commit `55472f05`）虽已从 fork 历史移除，但**控制台轮换/撤销**必须人工在 DashScope 完成；同时 `main` 及历史 release tag 的 `.env.example_CN` 仍含该 key（未 force-push 主线/标签，避免破坏协作），需另行统一清历史或确认 key 已失效。
