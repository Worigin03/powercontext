# 验证记录 · Memory Pawnshop v3

本文件记录 2026-11-06 在本地环境对**发布版 WebApp** 的独立复跑结果。原始证据都在 `records/`，不是手写 transcript。

## 1. 环境

| 项 | 值 |
| --- | --- |
| Dify | 1.19.0，Chatflow / advanced-chat，DSL 0.7.0 |
| 应用 | 记忆当铺（完整版，35 节点）/ 记忆当铺 · 离线版（33 节点） |
| 本地 WebApp | `http://127.0.0.1:8080/chat/zvZnt2hsSFr2ykII` |
| 服务 | FastAPI `127.0.0.1:8700`，`X-Pawnshop-Token` 鉴权 |
| seekdb | `5.7.25-OceanBase seekdb-v1.4.0.0` |
| 向量 | fastembed `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，384 维 |
| 测试玩家 | 完整版 `acct:e2e-2026`；离线版 `acct:lite-2026` |

## 2. 复跑命令

```bash
# 完整版：从 120 枚重置开始，跑 11 轮，记录原始 SSE 与节点级 JSON
DIFY_API_KEY=app-... python scripts/replay_e2e.py --reset --account e2e-2026

# 离线版把 DIFY_API_KEY 换成离线版应用的 key，account 换 lite-2026
```

`DIFY_API_KEY` 在 Dify 的「访问 API → API 密钥」创建；脚本不写入任何密钥，只写 `records/*.json` 与 `records/*.sse`。如果令牌出现在 HTTP 节点的 `process_data.request` 中，脚本会在落盘前把 `X-Pawnshop-Token` 的值替换为 `<redacted>`。

## 3. 完整版 11 轮：输入、实际结果与节点证据

每轮都是同一会话、同一玩家；第 2、3 轮故意在空柜台上赎回/托梦，用来验证嵌套 IF/ELSE 的失败分支。

| # | 输入 | 余额变化 | 实际结果 | `workflow_run_id` | 关键节点 |
| --- | --- | --- | --- | --- | --- |
| 1 | 我还有多少记忆币？ | 120 | 开户送 120，0 存当，0 赎回 | `7f305944-174b-4890-9128-2a514be875dd` | `balance_wallet → balance_fmt` |
| 2 | 赎回一段关于雨夜的记忆 | 120 | 「柜台上没有你当掉的记忆」 | `31752536-e58d-49bb-b61e-00ffe213fa50` | `redeem_if → redeem_fail` |
| 3 | 帮我把当掉的记忆缝成一个梦 | 120 | 「你还没在当铺留下任何东西」 | `b1415f08-040f-4912-9f56-10ca88580045` | `dream_if → dream_fail` |
| 4 | 当掉：去年冬天奶奶在厨房煮姜汤…… | +47 → 167 | 估价 47，情绪「平静」 | `4947b10d-d9b1-44c0-91b4-fb130fc87b28` | `pawn_appraise → pawn_store → pawn_ledger` |
| 5 | 当掉：凌晨三点我们在宿舍分一锅泡面…… | +38 → 205 | 估价 38，情绪「未命名」 | `8a0e9321-42fd-4954-b773-fa4d0f226515` | 同上 |
| 6 | 当掉：毕业典礼把帽子抛向天空…… | +36 → 241 | 估价 36，情绪「狂喜」 | `cc01e076-dce9-40f0-87b4-33f0f87131bb` | 同上 |
| 7 | 赎回一段关于奶奶在厨房煮姜汤的记忆 | −40 → 201 | 相似度 0.70，手续费 40，返回原文 | `9172c4d8-bbe3-4fef-9952-74858d0fc9dd` | `redeem_choose → redeem_if → redeem_commit` |
| 8 | 逛逛黑市，看看别人的秘密 | 201 | 4 条匿名记忆 | `3432a8da-26c0-44b9-ae26-8da73e5dd52c` | `browse_search → browse_parse → browse_iter×4` |
| 9 | 帮我把当掉的记忆缝成一个梦 | −10 → 191 | 2 段旧记忆缝成第二人称短梦 | `96cae058-2381-4b9e-8dd7-ea07a8d7ebe4` | `dream_check → dream_if → dream_ledger → dream_llm` |
| 10 | 你好，掌柜 | 191 | 掌柜介绍四种玩法 | `5bc9404b-f853-43d9-a9bc-c326000e8d40` | `chat_llm → chat_clean` |
| 11 | 我还有多少记忆币？ | 191 | 2 存当，1 已赎回，最近三笔账 | `fa4b9d64-148c-4b95-9b2c-50b0e71fe9bb` | `balance_wallet → balance_fmt` |

账目校验：`120 + 47 + 38 + 36 − 40 − 10 = 191`。

### 3.1 分支/迭代的节点级证据

来自 [`records/webapp-e2e-full.json`](../records/webapp-e2e-full.json)：

- 第 2 轮 `node_counts`：`redeem_wallet 1, redeem_search 1, redeem_choose 1, redeem_if 1, redeem_fail 1`，说明 `redeem_if` 走了 ELSE 分支。
- 第 3 轮 `node_counts`：`dream_check 1, dream_ledger_prep 1, dream_if 1, dream_fail 1`，说明 `dream_if` 走了 ELSE 分支。
- 第 8 轮 `node_counts`：`browse_line: 4`，说明 iteration 对 4 个候选各执行一次；原始 SSE 中能看到 4 个 `node_started/node_finished` 的 `browse_line`。
- 第 9 轮：`dream_llm` 状态 `succeeded`，token 2763；`dream_reply` 输出「用了 2 段旧记忆」。
- 第 7 轮：`redeem_commit` 的返回 `balance=201`；同一 `turn_id=9172...` 的账本记录为 `-40`，与 Dify `redeem_choose` 算出的手续费一致，后端只执行事务，不自行算费。

### 3.2 账本回查

[`records/seekdb-evidence.log`](../records/seekdb-evidence.log) 里 `acct:e2e-2026` 的账本：

```text
{"delta": 120,  "turn_id": null,                                   "reason": "开户：掌柜塞给你的启动记忆币"}
{"delta": 47,   "turn_id": "4947b10d-d9b1-44c0-91b4-fb130fc87b28",  "reason": "典当《去年冬天奶奶在厨房煮姜汤 窗外下》"}
{"delta": 38,   "turn_id": "8a0e9321-42fd-4954-b773-fa4d0f226515",  "reason": "典当《凌晨三点我们在宿舍分一锅泡面 谁》"}
{"delta": 36,   "turn_id": "cc01e076-dce9-40f0-87b4-33f0f87131bb",  "reason": "典当《毕业典礼把帽子抛向天空 落下来时》"}
{"delta": -40,  "turn_id": "9172c4d8-bbe3-4fef-9952-74858d0fc9dd",  "reason": "赎回《去年冬天奶奶在厨房煮姜汤 窗外下》"}
{"delta": -10,  "turn_id": "96cae058-2381-4b9e-8dd7-ea07a8d7ebe4",  "reason": "托梦：把旧记忆缝在一起"}
```

账本 `turn_id` 与上表 `workflow_run_id` 逐条相同，说明 Dify 的运行 ID 真正贯穿到了 seekdb 事务。

## 4. seekdb 证据

[`records/seekdb-evidence.log`](../records/seekdb-evidence.log) 的摘要：

```text
version: 5.7.25-OceanBase seekdb-v1.4.0.0
tables: ledger, memories, meta, wallets

FULLTEXT KEY ft_mem (content) WITH PARSER ik
VECTOR KEY vidx_mem (embedding) WITH (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG)
UNIQUE KEY uk_owner_turn (owner_id, turn_id)

query: 淋雨跑回家
id=10 大雨跑回家  distance=0.1763  similarity=0.8237
id=18 下大雨没带伞 索性跑回家 进门时  distance=0.2923  similarity=0.7077
```

向量检索走的是数据库内 `ORDER BY cosine_distance(embedding, %s)`，不是应用层全量扫描。

## 5. 语义向量对比

[`records/semantic-probe.log`](../records/semantic-probe.log)：

```text
query: 淋成落汤鸡推开家门，被家人一边数落一边照顾

fastembed（默认，语义）:
  0.5573  大雨跑回家
  0.4422  外婆的缝纫机
  0.4034  凌晨三点的泡面

hash（离线降级，字面重合）:
  0.2570  大雨跑回家
  0.0693  地铁站的分手
  0.0536  毕业礼帽
```

同一句改写，fastembed 能显著拉开相关记忆与无关记忆；hash 只是字符重合，作为显式降级保留。

## 6. 鉴权与幂等

[`records/service-smoke.log`](../records/service-smoke.log) 记录（服务端直连，不经 Dify）：

- 无 token 的 `/api/wallet/get` → HTTP 401；
- `player_id=guest` → `{"ok": false, "error": "bad_player"}`；
- 同一 `turn_id=smoke-pawn-1` 的 `/api/wallet/apply` 连发两次：第一次 `applied: true`，余额 167；第二次 `applied: false`，余额仍 167；
- 赎回手续费 40 后余额 127，`fragments` 为空，`pawned=0, redeemed=1`。

## 7. 离线版（无模型）

[`records/webapp-e2e-lite.json`](../records/webapp-e2e-lite.json) 是离线版发布 WebApp 的同样 11 轮：

- 余额序列同样是 `120 → 167 → 205 → 241 → 201 → 191`；
- 第 8 轮 `browse_line: 4`；
- 第 9 轮走 `dream_weave`（Code）而不是 `dream_llm`，输出「离线版不生成新叙事」并扣 10 枚；
- 第 10 轮走 `chat_fmt`（Code），无 token 消耗。

## 8. 独立复跑说明

评审可导入任一 DSL、发布 WebApp、创建应用 API Key，然后执行：

```bash
python scripts/replay_e2e.py \
  --base http://127.0.0.1:8080 \
  --account e2e-2026 \
  --reset \
  --out records/replay-local.json
```

脚本会用 `inputs.account` 固定玩家，逐轮打印 `workflow_run_id`、节点数、token 与回答，并写出 `.sse` 与 `.json`。如果只想复核服务端的 seekdb 数据，执行 `python scripts/verify_seekdb.py --player acct:e2e-2026`。

首次复跑前请确保 `app` 已启动、token 与 DSL Header 一致、`account` 未被占用；`--reset` 会删除该玩家的钱包、账本与当票。
