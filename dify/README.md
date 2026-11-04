# 记忆当铺 · Memory Pawnshop

一个用 **Dify** 搭起来的聊天应用：你可以在当铺里把一段记忆当掉，换几枚「记忆币」；也可以只给一条线索，让掌柜凭 **seekdb 向量检索** 从柜底把那张旧当票找回来。

> 赛题：OceanBase 2026 数据库大赛 · Just for Fun「用 Dify 做一个好玩的智能应用」
>
> 核心依赖：**Dify 1.19（Chatflow）+ seekdb（原生向量检索）+ MiniMax（掌柜人格）**

---

## 玩法

| 动作 | 你可以说 | 发生什么 |
| --- | --- | --- |
| 典当 | 「当掉：去年冬天奶奶在厨房煮姜汤，窗外下着雪。」 | 估价、判情绪、写入 seekdb、记忆币入账 |
| 赎回 | 「赎回一段关于雨夜的记忆」 | 在**你自己**当掉的记忆里做向量检索，按相似度收手续费 |
| 逛黑市 | 「逛逛黑市，看看别人的秘密」 | 在别人公开的记忆里做相似度检索，匿名代号呈现 |
| 托梦 | 「帮我把当掉的记忆缝成一个梦」 | 随机抽三段自己存当的记忆，扣 10 枚，交给掌柜缝成第二人称短梦 |
| 查账 | 「我还有多少记忆币？」 | 余额、存当数、赎回数、最近五笔账 |

初始 120 枚记忆币。掌柜的语气由 MiniMax 负责，所有数字、检索结果都由 seekdb 柜台返回，**不编造**。

## 架构

```
玩家
 │
 ▼
Dify Chatflow（advanced-chat）
 ├─ 开始
 ├─ 柜台分诊（Code 节点）       把中文意图翻译成 action + JSON
 ├─ seekdb 柜台（HTTP 节点）    POST http://host.docker.internal:8700/api/tool
 │      │
 │      ▼
 │   FastAPI 记忆当铺服务
 │      ├─ fastembed 多语言向量模型（384 维）
 │      └─ seekdb（OceanBase MySQL 兼容）
 │            ├─ memories: VECTOR(384) + HNSW(cosine) + FULLTEXT(ik)
 │            ├─ wallets / ledger
 │            └─ cosine_distance(...) 排序
 ├─ 掌柜（LLM 节点，MiniMax-M2.5）把柜台 JSON 讲成当铺的话
 ├─ 去掉思考块（Code 节点）    推理模型会输出 <think>，这里只留正文
 └─ 直接回复
```

Dify 本身也配置为 `VECTOR_STORE=seekdb`：如果继续在 Dify 里建知识库，向量同样落在 seekdb 上。

## 目录

```
dify/
├─ app/                 # 记忆当铺服务（FastAPI + PyMySQL + fastembed）
│  ├─ appraise.py       # 估价与情绪判定
│  ├─ config.py         # 环境变量
│  ├─ db.py             # seekdb 连接、建表、向量字面量
│  ├─ embeddings.py     # fastembed 封装
│  ├─ main.py           # HTTP API
│  ├─ seed.py           # 12 段公开记忆（幂等）
│  └─ service.py        # 典当 / 赎回 / 黑市 / 托梦 / 账本
├─ dsl/
│  └─ memory-pawnshop.yml   # 可直接导入 Dify 的 Chatflow DSL（0.7.0）
├─ scripts/
│  └─ smoke.py          # 不经过 Dify 的服务冒烟测试
├─ requirements.txt
└─ .env.example
```

## 数据模型

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

赎回与黑市都走同一条 SQL：`ORDER BY cosine_distance(embedding, %s) LIMIT n`，相似度 = `1 - distance`。赎回只检索 `owner_id = 自己 AND status = 'pawned'`，黑市只检索 `owner_id <> 自己 AND is_public = 1`。

## 本地启动

### 1. seekdb

任选一种方式起一个 seekdb（MySQL 协议，默认 2881）：

```bash
docker run -d --name seekdb \
  -p 2881:2881 \
  -v seekdb_data:/root/seekdb \
  oceanbase/seekdb:latest
```

### 2. 记忆当铺服务

```bash
cd dify
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # 填 seekdb 密码
python -m app.main                 # 监听 127.0.0.1:8700
```

首次启动会自动建库建表、下载向量模型并写入 12 段公开记忆。

```bash
python scripts/smoke.py            # 典当 → 赎回 → 黑市 → 托梦 → 余额
```

### 3. Dify

```bash
git clone https://github.com/langgenius/dify.git
cd dify/docker
cp .env.example .env
```

在 `docker/.env` 中至少设置：

```dotenv
VECTOR_STORE=seekdb
OCEANBASE_VECTOR_HOST=<seekdb host>
OCEANBASE_VECTOR_PORT=2881
OCEANBASE_VECTOR_USER=root
OCEANBASE_VECTOR_PASSWORD=<password>
OCEANBASE_VECTOR_DATABASE=test

# 让 HTTP 节点可以访问宿主机上的记忆当铺服务（二选一）
SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=host.docker.internal
SSRF_PROXY_ALLOW_PRIVATE_IPS=192.168.65.254/32
```

```bash
docker compose up -d
```

完成 Dify 初始化后，安装 **MiniMax** 插件（Marketplace → `langgenius/minimax`），在「模型供应商」里填入 API Key 与 Base URL：

- Base URL：`https://api.minimaxi.com`（国内站；海外站为 `https://api.minimax.io`）
- 模型：`MiniMax-M2.5`（DSL 中的默认值，可在 Dify 里直接换）

### 4. 导入 Chatflow

Dify 控制台 → 创建空白应用 → 「导入 DSL 文件」→ 选择 `dify/dsl/memory-pawnshop.yml`。

导入后可先在「运行」面板里试一句：

> 当掉：凌晨三点的便利店，我买了两份关东煮，等一个不会来的人。

然后点「发布」→「访问 API / 发布」→「WebApp」，即可打开对话页。
注意 WebApp 地址形如 `http://127.0.0.1:8080/chat/<site_code>`，用的是站点码（site code）而不是应用 ID；
App ID 拼出来的链接会报「此应用不存在，或无法从您的网络访问」。

## 接口

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

`action` 可选：`pawn` / `redeem` / `browse` / `dream` / `balance` / `chat`。
失败时返回 `{"ok": false, "error": "not_enough_coins", "message": "..."}`，HTTP 状态码 400。

其他接口：`GET /api/health`、`GET /api/stats`、`GET /api/wallet/{player_id}`。

## 设计说明

- **为什么路由用 Code 节点而不是 LLM？** 典当/赎回会真的改动账本，路由必须稳定、可解释；掌柜的「人味」交给后面的 LLM。
- **为什么要一个「去掉思考块」的节点？** MiniMax-M2.5 会把推理过程写进正文的 `<think>` 块里；Code 节点在流式输出前把它剥掉，客人只看到掌柜的话。
- **为什么在 HTTP 节点前先序列化 JSON？** Dify 的 JSON 请求体是模板拼接，记忆里出现引号或换行会破坏请求体；Code 节点用 `json.dumps` 生成安全载荷。
- **为什么相似度过滤放在 seekdb？** 赎回是「在我自己的记忆里找最像的一条」，黑市是「在别人的公开记忆里找」；都是带 `WHERE` 的向量检索，交给数据库一次完成，不把全量向量拉回应用层。
- **估价是确定性的**：细节、感官、情绪、数字、人称各自加分，30–180 封顶；赎回手续费随相似度在 0.55–1.35 倍之间浮动，最低 8 枚。

## 许可

与仓库其他部分一致。
