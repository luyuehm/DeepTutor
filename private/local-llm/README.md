# DeepTutor 私有化 — 本地 LLM 网关切换

DeepTutor 默认使用**云端网关**（CPA / OpenAI 兼容端点），私有化部署时可将 LLM
切到**本地模型端点**（Ollama / vLLM / llama.cpp）作为兜底，数据不出服务器。

> KISS 原则：这里**不新增任何代码**，只是把 `model_catalog.json` 里的连接
> `base_url` 指向本地端点即可 —— 复用现有的 provider 注册机制。

---

## 一、一分钟起一个本地 LLM(以 Ollama 为例)

### 方式 A：用内置 compose 覆盖，随 DeepTutor 一起起

```bash
./private/deploy.sh --local-llm
```

这会额外拉起 `deeptutor-ollama` 容器（仅绑定 127.0.0.1:11434），数据在
`./data/ollama`。

拉一个中文/通用模型：

```bash
# 进入 ollama 容器拉模型(约 4~8GB，视模型而定)
docker compose --env-file data/user/settings/docker.env \
  -f docker-compose.ghcr.yml -f private/compose.local-llm.yml \
  exec ollama ollama pull qwen2.5:7b
```

### 方式 B：本机已装 Ollama（不用容器）

直接用宿主的 Ollama，跳过 `--local-llm`。DeepTutor 容器通过 `host.docker.internal`
访问宿主机。

---

## 二、把 DeepTutor 切到本地模型

1. 打开 **设置 → 模型**（或直接编辑 `data/user/settings/model_catalog.json`）。
2. 把连接 `base_url` 改成本地网关地址：

   | 服务 | Chat/LLM base_url | Embedding base_url |
   |------|------------------|--------------------|
   | Ollama | `http://host.docker.internal:11434/v1` | `http://host.docker.internal:11434/api/embed` |
   | vLLM | `http://host.docker.internal:8000/v1` | `http://host.docker.internal:8000/v1` |
   | llama.cpp | `http://host.docker.internal:8080/v1` | （同上） |
   | LM Studio | `http://host.docker.internal:1234/v1` | `http://host.docker.internal:1234/v1` |

   `model` 填你实际已 pull/加载的模型名（Ollama 用 `ollama list` 查看）。

3. 保存后，新会话即走本地模型。可把云端连接保留切换用。

> 端口注意：容器内 `localhost` 是容器自己，不是宿主机——所以必须用
> `host.docker.internal`（compose 里已加 host-gateway 别名）。

---

## 三、离线纯本地（可选）

连 Ollama 镜像也要离线时，把 `ollama/ollama:latest` 也放进
`private/offline-images.sh` 的 `IMAGES` 列表再 export/import 即可。

---

## 四、验证

```bash
# 看模型是否就绪
curl -s http://127.0.0.1:11434/api/tags
# 试一次对话
curl -s http://127.0.0.1:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:7b","messages":[{"role":"user","content":"1+1=?"}]}'
```

期望返回 `{"choices":[{"message":{"content":"2"}}]}`。

---

## 五、常见问题

- **部署后模型不生效？** 改 `model_catalog.json` 后需重启：`docker compose --env-file data/user/settings/docker.env -f docker-compose.ghcr.yml restart deeptutor`。
- **Ollama 拉模型失败 / 太慢？** Ollama 官方镜像 hub 可达性要求外网；机构内网可预先在
  有网机器 `ollama pull` 后把 `./data/ollama`（模型目录）同步过去。
- **vLLM 自行部署？** 参照你的 vLLM 文档起服务，保证 `0.0.0.0:8000` 监听、且宿主机
  `--add-host=host.docker.internal:host-gateway`（compose 默认覆盖了这一别名，正常即通）。