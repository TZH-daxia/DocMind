# DocMind 部署流程

面向 Linux 服务器的 Docker 部署手册。按本文顺序执行即可完成上线。

---

## 一、架构概览

```
浏览器 / 官网项目
   │  https://ai.wecanintl.com:8443/docmind/analysis/**
   ▼
docmind-gateway  (nginx:alpine)        ← 本项目自带，占用宿主 8443
   │  http://docmind:8000   （compose 内部网络，容器名互访）
   ▼
docmind  (FastAPI + LibreOffice)       ← 数据落宿主 ./data 与 ./logs
```

**与同机其它项目（官网 / 客服）零交集**：不共用 nginx、不占用它们的 80 / 443、
不加入它们的 Docker 网络、也不需要新增 DNS 记录。

| 项 | 值 |
|---|---|
| 项目目录 | `/data/apps/DocMind` |
| 对外地址 | `https://ai.wecanintl.com:8443` |
| 接口基址 | `https://ai.wecanintl.com:8443/docmind` |
| 宿主端口 | `8443`（HTTPS 对外）、`8000`（仅绑回环，调试用） |
| 容器 | `docmind`（应用）、`docmind-gateway`（入口 nginx） |
| 证书 | 只读复用泛域名证书，不新签、续期自动生效 |

**为什么用 8443 而不是 443**：宿主 80 / 443 已被同机其它站点占用。DocMind 自带入口
容器并换用备用端口，从而完全不依赖、也不改动其它项目的配置。若将来 DocMind 独立到
新服务器，只需把端口映射改回 `443:443`，其余配置一字不动。

---

## 二、前置条件

| 项 | 要求 |
|---|---|
| Docker | 24+ 与 Docker Compose v2（`docker compose version` 可查） |
| 服务器规格 | 建议 ≥ 2 核 4GB；5 份文件并发峰值约 2~3GB（主要来自 LibreOffice） |
| 端口 | 宿主 `8443` 空闲，并在阿里云安全组放行 |
| 证书 | 泛域名证书目录可读（默认只读挂载 `/data/docker/freight-agent/nginx/certs`，含 `fullchain.pem` + `privkey.key`） |
| 密钥 | DeepSeek API Key |
| 主数据（可选） | poOrder `PublicWebApi` 地址；留空则港口三字码归一化与两个下拉整体停用 |

---

## 三、部署步骤

### 1. 获取代码

```bash
mkdir -p /data/apps && cd /data/apps
git clone <仓库地址> DocMind
cd /data/apps/DocMind
```

已存在则：

```bash
cd /data/apps/DocMind && git pull
```

### 2. 准备数据与日志目录

容器以 `uid/gid 10001` 运行，而这两个目录是**绑定挂载**（直接用宿主属主，不像命名卷会
自动复制），所以必须先建好并交给该用户：

```bash
mkdir -p data logs
chown -R 10001:10001 data logs
```

> `data/` 在仓库里有 `.gitkeep`；`logs/` 不在仓库里，必须手工创建。
> `git pull` 不会覆盖这两个目录，所以 `chown` 是一次性的。

### 3. 配置 `.env`

```bash
cp .env.example .env
chmod 600 .env
```

需要填写的项：

```bash
# ===== 必需 =====
DEEPSEEK_API_KEY=sk-xxxxxx

# ===== 主数据（留空则港口归一化与两个下拉整体停用）=====
DOCMIND_PORT_API_BASE=http://<poOrder 内网地址>/PublicWebApi/
DOCMIND_CUSTOMER_API_BASE=

# ===== 跨域：填调用方（官网）的真实来源，多个用逗号分隔 =====
# 官网还没接上就先留空（空 = 不放行任何跨域）。
# 不要写 * —— 接口目前没有鉴权，写 * 等于任何站点都能读走响应内容。
DOCMIND_CORS_ALLOW_ORIGINS=https://ai.wecanintl.com

# ===== 并发（按机器规格调整，示例为 16 核 / 64G）=====
DOCMIND_LO_MAX_CONCURRENT=8       # 不要超过 CPU 核数；留一半给光栅化与 Web
DOCMIND_MODEL_MAX_CONCURRENT=8    # 纯网络等待，瓶颈在上游限流
DOCMIND_MAX_CONCURRENT_TASKS=30

# ===== 对外端口（改动后访问地址要跟着带新端口）=====
DOCMIND_HTTPS_PORT=8443

# ===== 构建加速（可选）=====
APT_MIRROR=mirrors.aliyun.com
```

### 4. 构建并启动

```bash
docker compose up -d --build
docker compose ps
```

预期：`docmind` 为 `healthy`，`docmind-gateway` 为 `Up`。

```bash
docker exec docmind-gateway nginx -t                          # nginx 配置自检
docker compose exec docmind tail -n 30 /app/logs/app.log      # 应用日志
```

> **改了 `.env` 必须加 `--force-recreate`**：`docker compose up -d --force-recreate`。
> 否则 compose 不一定识别出环境变量变化，表现为"改了没生效"。

### 5. 本机验证（不依赖安全组、不依赖外网）

```bash
curl -sk https://127.0.0.1:8443/health
# 期望 {"status":"ok"}；-k 是因为证书签给域名，这里用 127.0.0.1 访问

curl -sk "https://127.0.0.1:8443/docmind/analysis/ports?keyword=shanghai" | head -c 120

# 确认该封的都封了
curl -skI https://127.0.0.1:8443/ | head -1                        # 期望 404
curl -skI https://127.0.0.1:8443/docmind/analysis/files | head -1  # 期望 404
```

**这一步通过**，说明应用、nginx、证书三者都正确，剩下的只是网络入口问题。

### 6. 放行安全组

阿里云控制台 → 云服务器 ECS → 安全组 → 配置规则 → **入方向** → 手动添加：

| 协议类型 | 端口范围 | 授权对象 | 描述 |
|---|---|---|---|
| 自定义 TCP | `8443/8443` | `0.0.0.0/0`（想更严就填办公出口 IP） | DocMind HTTPS |

保存后立即生效。**不要改动已有的 80 / 443 规则**（其它站点在用）。

### 7. 外网验证

```bash
curl -s  https://ai.wecanintl.com:8443/health                # {"status":"ok"}
curl -s  "https://ai.wecanintl.com:8443/docmind/analysis/ports?keyword=shanghai" | head -c 120
curl -sI https://ai.wecanintl.com:8443/ | head -1            # 404
```

### 8. 联调内置前端（生产不暴露）

内置上传页面只用于联调，公网访问返回 404。用 SSH 隧道直达容器端口（不经过 nginx）：

```bash
ssh -L 8000:127.0.0.1:8000 root@<服务器IP>
# 本机浏览器打开 http://127.0.0.1:8000/
```

隧道内页面与接口同源，可完整走一遍：拖拽上传 → 节点时间线 → 核对弹窗 → 原件预览。
**隧道不经过 nginx，所以 `/files` 在这里仍然可用。**

重点验证两条：

- 传一个 **> 1MB** 的 PDF，不报 413；
- 上传后时间线**逐个**点亮（卡住不动 = SSE 被缓冲，检查 `proxy_buffering off`）。

---

## 四、对外接口

接口基址：`https://ai.wecanintl.com:8443/docmind`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/analysis/tasks` | 上传托书创建任务（multipart：`file`、`request_id`、`schema_version`、`context`、`auto_start`） |
| GET | `/analysis/tasks/{task_id}` | 任务状态与进度 |
| GET | `/analysis/tasks/{task_id}/events` | **SSE** 实时事件流（进度时间线） |
| GET | `/analysis/tasks/{task_id}/events/history` | 历史事件回放 |
| GET | `/analysis/tasks/{task_id}/result` | 结构化结果与字段级元数据 |
| POST | `/analysis/tasks/{task_id}/pause` \| `/resume` \| `/cancel` | 暂停 / 继续 / 取消 |
| GET | `/analysis/tasks/{task_id}/pages/{page_no}` | 原件页面渲染图 |
| GET | `/analysis/customers?keyword=` | 委托客户下拉候选 |
| GET | `/analysis/ports?keyword=` | 港口下拉候选 |
| GET | `/health` | 健康检查 |

**三条注意**：

1. **基址必须带 `:8443`** —— 漏掉端口会打到同域名的 443（其它站点）上。
2. **调用方来源要填进 `DOCMIND_CORS_ALLOW_ORIGINS`** —— 端口不同即跨域，即使同域名也算。
3. 以下已由 nginx 关闭，公网不可达：内置前端（`/`、`/static/**`）、接口文档
   （`/docs`、`/redoc`、`/openapi.json`）、以及 `GET /analysis/files`（会列出全部任务，
   仅供本地联调）。

---

## 五、日常运维

| 事项 | 命令 / 说明 |
|---|---|
| 应用日志 | `docker compose exec docmind tail -f /app/logs/app.log`（容器 stdout 上基本没有内容） |
| 网关日志 | `docker compose logs docmind-gateway` |
| 改 nginx 配置 | 改 `deploy/nginx/docmind.conf`，然后 `docker exec docmind-gateway nginx -t && docker exec docmind-gateway nginx -s reload` |
| 升级 | `git pull && docker compose up -d --build`，**挑没人跑任务时**——重启会把在途任务标记为中断 |
| 副本数 | **只跑一个副本、一个 uvicorn worker**，多开会互相把对方任务误判为中断 |
| 数据位置 | `./data/parsed_documents/<task_id>/`、`./data/analysis_results/`；产物默认保留 24 小时自动清理 |
| 磁盘 | `df -h /data`；清理用 `rm -rf data/parsed_documents/*`（属主是 10001，root 可删） |
| 停止 | `docker compose down`（本项目的容器与网络，不影响其它项目） |

---

## 六、故障排查

| 现象 | 原因 / 排查 |
|---|---|
| `127.0.0.1:8443` 连接被拒 | gateway 没起来或端口没映射 → `docker compose ps` |
| 外网**超时**（不是拒绝） | 安全组没放行 8443 |
| **502 Bad Gateway** | gateway 通了但 docmind 没起来 → `docker compose ps`、`docker compose logs docmind` |
| 浏览器证书警告 | 用 IP 访问（证书签给域名）；用域名仍警告则检查证书路径 → `nginx -t` |
| 全站 404 | nginx 配置没加载 → `docker exec docmind-gateway nginx -t` |
| 改了 `.env` 没生效 | 少了 `--force-recreate` |
| 改了 nginx 配置没生效 | 少了 `nginx -s reload` |
| 上传大文件 413 | `client_max_body_size` 与 `DOCMIND_MAX_FILE_SIZE_BYTES` 不一致 |
| 进度条不动 | SSE 被缓冲 → 检查 `proxy_buffering off` 与 `proxy_read_timeout` |
| DOC / XLS 转换失败 | `docker compose exec docmind bash -lc 'soffice --version; ls -l /usr/lib/libreoffice/program/python'` |
| 中文渲染成方块 | 镜像已装文泉驿；需要其它字体时挂载 `/usr/share/fonts` 并 `fc-cache -f` |
| 容器反复重启 | 多为 `DEEPSEEK_API_KEY` 缺失，看 `docker compose logs docmind` |
| 内存占用高 | 调小 `DOCMIND_LO_MAX_CONCURRENT`（每个 soffice 约 200~400MB）；PDF 不经过 LibreOffice |
| 转换报超时 | 5 并发对低配机器偏重，调到 2~3；单份转换超时阈值为 180s |
| **构建**卡在下载依赖 | 境外服务器加 `--build-arg UV_INDEX_URL=https://pypi.org/simple` |
| **构建**卡在 `apt-get update` | 加 `--build-arg APT_MIRROR=mirrors.aliyun.com`（或 `mirrors.tuna.tsinghua.edu.cn`） |
| 拉基础镜像超时 | 镜像已用 `ghcr.io` 源；仍慢可在 Docker Engine 配 `registry-mirrors` 加速 |

---

## 七、附录

### 关键文件

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 镜像构建：非 root（uid 10001）、内置 LibreOffice 与中文字体 |
| `docker-compose.yml` | 两个服务：`docmind`（应用）+ `gateway`（入口 nginx）；数据/日志绑定挂载 |
| `deploy/nginx/docmind.conf` | 入口配置（完整 server 块，容器内监听 443） |
| `.env.example` | 环境变量模板 |
| `README.md` | 功能说明与接口总览 |

### 迁移到独立服务器

整目录搬到新机器即可，**三个部署文件都不用改**：

```bash
mkdir -p /data/apps && cd /data/apps
git clone <仓库地址> DocMind && cd DocMind
mkdir -p data logs && chown -R 10001:10001 data logs
cp .env.example .env    # 填同样的值
# 证书若不再共用：拷 fullchain.pem / privkey.key 到 deploy/nginx/certs/，
# 并把 docker-compose.yml 里那一行挂载改为 ./deploy/nginx/certs:/etc/nginx/certs:ro
docker compose up -d
# 最后改 DNS 指向新机器
```

新机器上 443 若空闲，把端口映射改回 `- "443:443"` 即可使用标准端口，无需带端口访问。
