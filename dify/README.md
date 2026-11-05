# 记忆当铺 · Memory Pawnshop

一个用 **Dify** 搭起来的聊天应用：把一段记忆当掉，换几枚「记忆币」；只给一条线索，掌柜会凭 **seekdb 向量检索** 从柜底把那张旧当票找回来。

> OceanBase 2026 数据库大赛 · Just for Fun「用 Dify 做一个好玩的智能应用」
>
> Dify 1.19 Chatflow + seekdb（原生向量 / 全文索引）+ MiniMax 掌柜（可选）

**创意**：把「记忆」当成可以典当的旧物。你说一段往事，当铺给它估价、判情绪、开当票；想找回时不用复述全文，给一条线索（「便利店」「关东煮」），柜台就从你自己的存当里做向量检索，按相似度收手续费。还能逛别人的黑市、把存当缝成一场梦。所有数字来自 seekdb，掌柜只负责把它讲成旧时代的话。

12 段公开记忆（`app/seed.py`）是为演示原创的示例数据；本项目未复用公开工作流、代码或数据集。

## 目录

- [1. 玩法](#1-玩法)
- [2. DSL 文件与导入顺序](#2-dsl-文件与导入顺序)
- [3. 架构与分工](#3-架构与分工)
- [4. 启动](#4-启动)
- [5. 在 Dify 中配置模型](#5-在-dify-中配置模型)
- [6. 验证](#6-验证)
- [7. 数据模型](#7-数据模型)
- [8. 接口](#8-接口)
- [9. 目录](#9-目录)
- [10. 已知限制](#10-已知限制)

## 1. 玩法

| 动作 | 你可以说 | 发生什么 |
| --- | --- | --- |
| 典当 | 「当掉：去年冬天奶奶在厨房煮姜汤，窗外下着雪。」 | 估价、判情绪、写入 seekdb、记忆币入账 |
| 赎回 | 「赎回一段关于雨夜的记忆」 | 在**你自己**当掉的记忆里做向量检索，按相似度收手续费 |
| 逛黑市 | 「逛逛黑市，看看别人的秘密」 | 在别人公开的记忆里做相似度检索，匿名代号呈现 |
| 托梦 | 「帮我把当掉的记忆缝成一个梦」 | 随机抽三段自己存当的记忆，扣 10 枚，交给掌柜缝成第二人称短梦 |
| 查账 | 「我还有多少记忆币？」 | 余额、存当数、赎回数、最近五笔账 |

初始 120 枚记忆币。所有数字、检索结果都由 seekdb 柜台返回，掌柜只负责把它们讲成当铺里的话。

## 2. DSL 文件与导入顺序

| 文件 | 说明 | 依赖 |
| --- | --- | --- |
| [`dsl/memory-pawnshop-lite.yml`](dsl/memory-pawnshop-lite.yml) | **离线版，建议先导入这个**：五个动作全部可用，柜台 JSON 由 Code 节点整理成文字 | 无模型、无插件 |
| [`dsl/memory-pawnshop.yml`](dsl/memory-pawnshop.yml) | 完整版：掌柜由 LLM 扮演，语气更自然 | MiniMax 插件（或任意聊天模型） |

导入方式：Dify 控制台 → 创建空白应用 → 「导入 DSL 文件」。两个 DSL 的应用名不同（`记忆当铺 · 离线版` / `记忆当铺`），可以同时存在。

- Dify 版本：1.19.0（Chatflow / advanced-chat）
- DSL 版本：0.7.0
- MiniMax 插件：`langgenius/minimax:0.0.23`（仅在完整版中使用）
- seekdb：`oceanbase/seekdb:latest`，实测 `5.7.25-OceanBase seekdb-v1.4.0.0`

两个 DSL 的 HTTP 节点默认都指向 `http://host.docker.internal:8700/api/tool`。如果你的 Dify 不在 Docker Desktop 里，见 [4.3 地址怎么填](#43-dify-访问宿主机服务地址怎么填)。

## 3. 架构与分工

```
玩家
 │
 ▼
Dify Chatflow
 ├─ 开始
 ├─ 柜台分诊（Code）        中文意图 → action + 安全 JSON
 ├─ seekdb 柜台（HTTP）     POST /api/tool
 │      │
 │      ▼
 │   记忆当铺服务（FastAPI）
 │      ├─ 估价与情绪判定（确定性规则）
 │      ├─ 向量化（hash 默认 / fastembed 可选）
 │      └─ seekdb
 │            ├─ memories：VECTOR(384) + HNSW(cosine) + FULLTEXT(ik)
 │            ├─ wallets / ledger
 │            └─ ORDER BY cosine_distance(...) LIMIT n
 │
 ├─ 掌柜（LLM，可选）        把柜台 JSON 讲成当铺的话
 ├─ 去掉思考块（Code）       剥掉推理模型的 <think>
 └─ 直接回复
```

- **路由为什么用 Code 节点？** 典当/赎回会真实改动账本，路由必须稳定、可解释；掌柜的「人味」交给后面的 LLM。
- **为什么先序列化 JSON？** Dify 的 JSON 请求体是模板拼接，记忆里出现引号或换行会破坏请求体；Code 节点用 `json.dumps` 生成安全载荷。
- **为什么检索放在 seekdb？** 赎回和黑市都是带 `WHERE` 的「取最相似的几条」，交给数据库一次完成，不把全量向量拉回应用层。
- **没有模型能用吗？** 能。离线版把「掌柜」换成一个 Code 节点，工作流 `dependencies` 为空。
- **服务没启动会怎样？** HTTP 节点配置了 `error_strategy: default-value`，会返回「柜台打烊」的提示，工作流仍然给出回答。

## 4. 启动

### 4.1 一条命令起后端（seekdb + 记忆当铺服务）

```bash
cd dify
cp .env.example .env
# 编辑 .env，至少把 SEEKDB_PASSWORD 改成你自己的密码
docker compose up -d --build
curl http://127.0.0.1:8700/api/health
```

期望：

```json
{"ok": true, "embedding": "hash:char-ngram-384"}
```

`docker compose` 会启动两个容器：

- `seekdb`：`oceanbase/seekdb:latest`，数据卷 `seekdb_data`，默认映射到宿主机 `2881`；
- `pawnshop`：镜像由本目录 `Dockerfile` 构建，监听 `8700`，等待 seekdb 健康后启动。

首次启动自动建库 `memory_pawnshop`、建表、写入 12 段公开记忆。之后重启不会重复写入（`meta` 表记录初始化状态）。

> 内存较小的机器可在 `.env` 里调低 `SEEKDB_MEMORY_LIMIT`（默认 `2G`）；端口被占用时改 `SEEKDB_PORT` / `PAWNSHOP_PORT`。
>
> 首次运行需要能拉取 `python:3.12-slim` 与 `oceanbase/seekdb:latest` 镜像；完全离线的评审机请提前 `docker pull` 后用 `docker save` / `docker load` 导入。服务本身不需要外网（默认 hash 向量，不下载模型）。

### 4.2 本机直接跑服务（可选）

```bash
cd dify
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # 指向已有 seekdb，填密码
python -m app.main               # 监听 127.0.0.1:8700
```

需要语义向量时：

```bash
pip install -r requirements-fastembed.txt
# .env: PAWNSHOP_EMBED_BACKEND=fastembed
```

首次会下载约 470MB 模型；服务发现后端变化后会自动重算已有记忆的向量。

### 4.3 Dify 访问宿主机服务：地址怎么填

Dify 的 HTTP 节点从容器里发起请求，`127.0.0.1` 指向容器自己，不能用。按部署方式选：

| Dify 部署方式 | HTTP 节点 URL | 额外配置 |
| --- | --- | --- |
| Docker Desktop（Windows / macOS） | `http://host.docker.internal:8700/api/tool` | 通常开箱可用；若开启 SSRF 代理，在 Dify 的 `docker/.env` 中设置 `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=host.docker.internal` |
| Linux + Docker Compose | `http://host.docker.internal:8700/api/tool` | 给 Dify 的 `api`（和 `worker`）加 `extra_hosts: ["host.docker.internal:host-gateway"]` |
| 任意 | `http://<宿主机局域网IP>:8700/api/tool` | 服务已监听 `0.0.0.0`（compose 中设置）；放行防火墙 8700 |

修改位置：打开 DSL 中的「seekdb 柜台」节点 → URL。保存后记得点「发布」。

如果 Dify 与 seekdb 部署在同一台机器，也可以把 Dify 自身的向量库设为 seekdb（`VECTOR_STORE=seekdb` 及 `OCEANBASE_VECTOR_*`）；本应用的检索不依赖这个设置。

### 4.4 导入并发布 Chatflow

1. 导入 `dsl/memory-pawnshop-lite.yml`（先跑通，零模型依赖）。
2. 在「运行」面板试一句「我还有多少记忆币？」。有回答即链路通。
3. 点「发布」→「访问 API / 发布」→「WebApp」。
4. WebApp 地址形如 `http://127.0.0.1:8080/chat/<site_code>`，**用的是站点码（site code），不是应用 ID**；用应用 ID 拼链接会提示「此应用不存在」。

需要掌柜人格时再导入 `dsl/memory-pawnshop.yml` 并配置模型（下一节）。

## 5. 在 Dify 中配置模型

完整版默认使用 MiniMax：

1. Marketplace 安装 `langgenius/minimax`（DSL 固定为 `0.0.23`）。
2. 「模型供应商」→ MiniMax → 填 API Key 与 Base URL：
   - 国内站：`https://api.minimaxi.com`
   - 海外站：`https://api.minimax.io`
3. 模型：`MiniMax-M2.5`（可在应用里换成同供应商的其他聊天模型）。

安装插件与配置 Key 需要能访问 Dify Marketplace 和 MiniMax API；找不到插件包的评审环境可直接使用离线版 DSL，玩法与数字完全一致。

也可以换成任意聊天模型：打开 LLM 节点，选择你的供应商与模型即可。柜台 JSON 的字段不变，提示词不需要改。

> 仓库不包含任何真实 API Key 或密码；`.env.example` 只有占位值。MiniMax-M2.5 的 `<think>` 块由 `去掉思考块` 节点在输出前剥掉。

## 6. 验证

完整的初始状态、输入、步骤、期望/实际结果与证据文件见 **[`docs/VALIDATION.md`](docs/VALIDATION.md)**。最短路径：

```bash
# 服务端五动作（典当×2 → 赎回 → 黑市 → 托梦 → 查账）
python -m scripts.reset_player verify-2026
python scripts/smoke.py http://127.0.0.1:8700 verify-2026

# seekdb 版本、建表语句、向量/全文检索、钱包账本
python -m scripts.verify_seekdb --player verify-2026 --query "关东煮"
```

实测记录（原始输出）：

- [`records/service-smoke.log`](records/service-smoke.log)
- [`records/seekdb-evidence.log`](records/seekdb-evidence.log)
- [`records/dify-e2e-lite.json`](records/dify-e2e-lite.json)：离线版六轮对话
- [`records/dify-e2e-minimax.json`](records/dify-e2e-minimax.json)：完整版两轮对话
- [`records/dify-webapp-e2e.json`](records/dify-webapp-e2e.json)：已发布 WebApp 的连续会话
- [`records/webapp.png`](records/webapp.png)：已发布 WebApp 的实测截图
- [`records/dify-e2e-fallback-lite.json`](records/dify-e2e-fallback-lite.json)、[`records/dify-e2e-fallback.json`](records/dify-e2e-fallback.json)：服务停机时的兜底回答

重置某个玩家（钱包、账本、当票一起删除）：

```bash
python -m scripts.reset_player <player_id>
```

Dify 对话里的玩家是 Dify 账号 ID；想复现文档中的「120 → 158 → 205 → 173 → 163」，先按账号 ID 重置一次。

## 7. 数据模型

```sql
CREATE TABLE memories (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  owner_id VARCHAR(64) NOT NULL,
  title VARCHAR(120) NOT NULL,
  content TEXT NOT NULL,
  emotion VARCHAR(24) NOT NULL,
  appraisal INT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pawned',
  is_public TINYINT NOT NULL DEFAULT 1,
  embedding VECTOR(384) NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  redeemed_at DATETIME NULL,
  FULLTEXT INDEX ft_mem (content) WITH PARSER ik,
  VECTOR INDEX vidx_mem (embedding) WITH (distance=cosine, type=hnsw, lib=vsag),
  KEY idx_owner_status (owner_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

赎回与黑市走同一条 SQL：`ORDER BY cosine_distance(embedding, %s) LIMIT n`，相似度 = `1 - distance`。赎回只检索 `owner_id = 自己 AND status = 'pawned'`；黑市只检索 `owner_id <> 自己 AND is_public = 1`。

`wallets` 保存余额，`ledger` 保存每一笔收支，`meta` 保存初始化与向量后端标记。

## 8. 接口

`POST /api/tool`

```json
{
  "player_id": "u-123",
  "action": "pawn",
  "text": "去年冬天奶奶在厨房煮姜汤，窗外下着雪。",
  "query": "",
  "limit": 3
}
```

`action` 可选：`pawn` / `redeem` / `browse` / `dream` / `balance` / `chat`。失败时返回 `{"ok": false, "error": "...", "message": "..."}`；为让 Dify 工作流继续，`/api/tool` 的业务错误也以 HTTP 200 返回，`ok` 字段区分成败。

其他接口：`GET /api/health`、`GET /api/stats`、`GET /api/wallet/{player_id}`。

## 9. 目录

```
dify/
├─ app/                     # 记忆当铺服务（FastAPI + PyMySQL）
│  ├─ appraise.py           # 估价与情绪判定
│  ├─ config.py             # 环境变量
│  ├─ db.py                 # seekdb 连接、建表、向量字面量
│  ├─ embeddings.py         # hash / fastembed 向量
│  ├─ main.py               # HTTP API
│  ├─ seed.py               # 12 段公开记忆（幂等）
│  └─ service.py            # 典当 / 赎回 / 黑市 / 托梦 / 账本
├─ dsl/
│  ├─ memory-pawnshop.yml       # 完整版（LLM 掌柜）
│  └─ memory-pawnshop-lite.yml  # 离线版（无模型依赖）
├─ docs/VALIDATION.md       # 验证样例与实测证据
├─ records/                 # 冒烟、seekdb、Dify E2E 的原始日志
├─ scripts/
│  ├─ smoke.py              # 服务端五动作冒烟
│  ├─ verify_seekdb.py      # seekdb 读写与索引证据
│  ├─ reset_player.py       # 重置玩家
│  └─ build_lite_dsl.py     # 从完整版生成离线 DSL
├─ docker-compose.yml       # seekdb + 服务
├─ Dockerfile
├─ requirements.txt
├─ requirements-fastembed.txt
└─ .env.example             # 只有占位值
```

## 10. 已知限制

- 默认 `hash` 后端衡量字面重合度，不是语义相似度；语义检索请切到 `fastembed`。
- 黑市固定返回最相似的 3 条，没有阈值；分数很低也会展示。
- 赎回在「自己未赎回的记忆」中取最相似的一条；一条都没有时会提示先去典当。
- seekdb 的向量距离由 HNSW 索引计算，属于近似值。
- 服务没有鉴权与 TLS，面向本地演示；公网部署需自行加网关。
- 完整版需要模型与插件；没有 Key 时使用离线版，功能不受影响。
