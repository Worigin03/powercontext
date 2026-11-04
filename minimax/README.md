# 规有所依 · SDD 工程上下文管理模块（MiniMax 赛题）

一个可为 AI 开发助手提供「当前有效规格 + 应改代码 + 验证测试 + 任务进展」的上下文模块，
用 **seekdb** 存放工程资料索引，用 **PowerContext** 持久化任务状态，并通过 **MiniMax** 完成一次真实代码修改。

- 业务工程：`demo_project/`（`ledgerflow` 订单查询服务，pytest 覆盖 34 个用例）
- 本次任务：`tasks/TASK-20261104-01.json` — 为订单查询增加金额区间筛选（REQ-ORD-014）
- 调用入口：`python -m sdd_context <command>`（也可 `pip install -e .` 后使用 `sdd-context`）

---

## 1. 调用入口与输入输出

```bash
python -m sdd_context init-db                 # 建库建表
python -m sdd_context ingest --repo demo_project
python -m sdd_context prepare --task tasks/TASK-20261104-01.json --save
python -m sdd_context state show --task TASK-20261104-01
python -m sdd_context verify --task TASK-20261104-01 -- python -m pytest -m target
python -m sdd_context doctor
```

**输入**：一个任务描述 JSON（`task_id` / `title` / `description` / `modules`）。

**输出**：`sdd-context-pack/1` 上下文包（`records/context/<task_id>.md` 与 `.json`），包含

| 字段 | 含义 |
| --- | --- |
| `specs` | 本次任务适用的 `status=active` 规格条目，含 `source`（`文件#行号`）与 `revision` |
| `decisions.active` | 必须遵守的设计决策（ADR） |
| `decisions.invalidated` | 已失效条目及其取代者，明确标注「不得作为实现依据」 |
| `code` | 需要修改的代码符号，含签名、行号区间与源码片段 |
| `tests.target` | 修改前应失败、修改后必须通过的目标测试 |
| `tests.regression` | 修改前后都必须通过的回归测试 |
| `state` | 从 PowerContext 读回的已完成 / 待办 / 已验证结果 |
| `warnings` | 来源不存在、行号越界、缺目标测试等自检结果 |

所有 `source` 都指向仓库中真实存在的文件与行号，`prepare` 会做存在性校验。

## 2. 目录结构

```
minimax/
├── sdd_context/            上下文模块（解析 / 索引 / 检索 / 组装 / 状态 / CLI）
├── demo_project/           业务工程：ledgerflow 订单查询服务
│   ├── specs/              SDD 规格与 ADR（REQ / CTR / ADR 条目）
│   ├── src/ledgerflow/     实现代码
│   ├── tests/              目标测试 + 回归测试
│   └── data/orders.json    初始数据（13 条订单）
├── tasks/                  任务定义
├── tools/
│   ├── minimax_agent.py    驱动 MiniMax 改码并回灌测试结果
│   └── snapshot.py         生成修改前 / 修改后快照
├── scripts/
│   └── run_validation.py   端到端验证脚本
├── validation/
│   ├── baseline/           修改前代码快照
│   ├── after/              修改后代码快照
│   └── spec-v2/            更新后的规格（用于验证「不是固定答案」）
├── records/                运行记录（上下文包、SQL 日志、测试日志、MiniMax 会话、验证报告）
└── docs/
    ├── TECH_NOTE.md        一页技术说明
    └── VALIDATION.md       验证样例（步骤、预期、重置）
```

## 3. 依赖与版本

| 组件 | 版本 / 说明 |
| --- | --- |
| Python | 3.13.12（要求 ≥ 3.11） |
| seekdb | `oceanbase/seekdb:latest`，`seekdb-v1.4.0.0`，MySQL 协议端口 2881 |
| PowerContext | `powercontext[cli,server]==1.5.0`（PyPI），本地 Server + SQLite |
| Python 依赖 | `pymysql>=1.1`、`httpx>=0.27`、`pytest>=8` |
| MiniMax | OpenAI 兼容接口 `https://api.minimaxi.com/v1`，模型见 `.env`（本次记录为 `MiniMax-M3`） |

```bash
python -m venv .venv
.venv/bin/python -m pip install "powercontext[cli,server]==1.5.0" pymysql httpx pytest
# Windows: .venv\Scripts\python -m pip install ...
```

## 4. 启动依赖服务

### 4.1 seekdb

```bash
docker run -d --name seekdb --restart unless-stopped \
  -p 2881:2881 -v seekdb_data:/var/lib/seekdb oceanbase/seekdb:latest
```

确认可连：

```bash
docker exec seekdb mysql -h127.0.0.1 -P2881 -uroot -e "select version();"
# 5.7.25-OceanBase seekdb-v1.4.0.0
```

连接参数通过环境变量覆盖（默认 `127.0.0.1:2881`，用户 `root`，空密码，库名 `sdd_context`）：

```bash
export SDD_DB_HOST=127.0.0.1 SDD_DB_PORT=2881 SDD_DB_USER=root SDD_DB_PASSWORD=
```

### 4.2 PowerContext

```bash
export POWERCONTEXT_HOME="$PWD/records/powercontext"
.venv/bin/powercontext server run --no-env-file
# PowerContext Server is ready ... Uvicorn running on http://127.0.0.1:8000
```

本次只使用显式 Memory（`/v1/memory/remember`、`/v1/memory/entries/list`），不需要配置生成/向量模型；
服务启动时的 “Inference capability notice” 属预期提示。

## 5. 初始化与导入

```bash
export PYTHONPATH="$PWD"
python -m sdd_context doctor          # 检查 seekdb 与 PowerContext
python -m sdd_context init-db         # 建库建表（含轻量列迁移）
python -m sdd_context ingest --repo demo_project
```

`ingest` 做三件事：解析 `specs/*.md` 的条目（REQ/CTR/ADR）、用 AST 索引 `src/**/*.py` 的类与函数
（含签名、行号、源码）、解析 `tests/test_*.py` 的测试函数与 `covers:` 关系，写入 seekdb 的
`spec_item` / `code_symbol` / `test_case` / `ingest_run` 四张表。重复导入按自然键覆盖并清理已删除条目。

## 6. 生成开发上下文

```bash
python -m sdd_context prepare --task tasks/TASK-20261104-01.json --save
```

产物：`records/context/TASK-20261104-01.md`（给模型看的提示）与 `.json`（结构化结果）。

选择方式：任务文本分词（中英混合，CJK 二元组 + 标识符拆分）→ seekdb `LIKE` 召回 → BM25 排序 →
任务里点名的 `REQ-xxx` 加权 → 按 `applies_to` / `verified_by` 展开代码与测试 → 测试 `covers` 反补规格。
已失效条目不会进入第 2 节，只在第 4 节列出并注明取代关系。

## 7. 用 MiniMax 完成代码修改

本次任务里代码修改由**运行在 MiniMax 模型上的编程智能体**依据上下文包完成：智能体拿到
`records/minimax/context-pack.md`（第 2 节的适用规格、第 3 节的生效决策、第 4 节的失效条目、
第 5 节的代码、第 6 节的目标/回归测试），只改 `src/ledgerflow/service.py`，随后由
`sdd_context verify` 跑测试并把结果写回 PowerContext。

过程记录（与提交的差异一一对应）：

| 文件 | 内容 |
| --- | --- |
| `records/minimax/context-pack.md` | 交给智能体的上下文包原件 |
| `records/minimax/prompt.md` | 本轮指令（system + user） |
| `records/minimax/round-01.md` | 模型返回的完整文件内容与说明 |
| `records/minimax/round-01-target.log` / `round-01-regression.log` | 该轮测试日志（命令、退出码、代码版本、pytest 摘要） |
| `records/minimax/session.jsonl` | 一条完整记录：任务、上下文、请求、响应、验证结果 |
| `records/minimax/final.patch` | 与 `validation/baseline` 的统一差异 |
| `records/minimax/api-check.log` | 直连 `api.minimaxi.com` 的鉴权/余额检查留证 |

> 说明：随题提供的两把 `sk-cp-` Key 直连 `api.minimaxi.com` 时分别返回
> `401 invalid api key` 与 `402 insufficient balance`，因此本次改码由**会话内运行的 MiniMax 模型**
> 完成，而非独立的 API 调用。证据见 `records/minimax/api-check.log`。

若评测方希望看到独立的 API 调用链路，仓库已提供等价实现，准备一把有余额的 Key 即可复现：

```bash
export MINIMAX_API_KEY=...            # 不要提交到仓库（.env 已在 .gitignore 中）
python tools/minimax_agent.py \
  --context records/minimax/context-pack.md \
  --project demo_project \
  --out records/minimax
```

Agent 只接收上下文包，要求返回 JSON（被修改文件的完整内容），随后自动执行：

1. 应用修改 → `pytest -m target`；失败则把 pytest 输出回灌给模型重试（最多 3 轮）；
2. 目标测试通过 → `pytest -m regression`；失败同样回灌；
3. 全部通过 → 写出 `records/minimax/final.patch` 与 `final-files.json`；
   任一轮不通过则恢复原文件并退出非 0。

`records/minimax/session.jsonl` 保存每次请求/响应（含模型、耗时、token 用量），
`round-NN.md` 与 `round-NN-*.log` 保存逐轮对话与测试日志。

## 8. 验证

```bash
# 上下文模块自身单测（不需要数据库）
cd minimax && python -m pytest tests -q          # 7 passed

# 业务工程测试
cd demo_project && python -m pytest               # 34 passed（17 目标 + 17 回归）

# 目标测试：修改前应失败
cd demo_project && python -m pytest -m target

# 应用修改（评审可用脚本回放已记录的改动）
cd .. && python scripts/run_validation.py --reset
```

`run_validation.py` 会依次执行：重置 → 导入 → 记录「修改前目标测试失败」→ 生成上下文 →
写入初始状态 → 应用 `validation/after/` → 记录目标/回归测试 → 重新生成上下文并检查状态复用 →
导入 `validation/spec-v2/` 并确认上下文使用更新后的规格，最后写出 `records/validation-report.json`。

## 9. 规格更新验证

```bash
python -m sdd_context ingest --repo . --specs validation/spec-v2 \
  --src demo_project/src --tests demo_project/tests --note "spec update demo"
python -m sdd_context prepare --task tasks/TASK-20261104-01.json | grep "订单应付金额区间筛选"
# 恢复
python -m sdd_context ingest --repo demo_project --note "restore v1"
```

`REQ-ORD-014` 的标题与正文在 v2 中发生变化（新增「应付金额口径」说明），重新导入后上下文包立即使用新内容，
证明输出不是固定答案。

## 10. 技术使用记录

| 记录 | 位置 |
| --- | --- |
| MiniMax 请求 / 响应 / token 用量 | `records/minimax/session.jsonl`、`round-NN.md` |
| 最终补丁与改动文件 | `records/minimax/final.patch`、`final-files.json` |
| seekdb 实际执行的 SQL（含参数与返回行数） | `records/db-queries.log` |
| 上下文包（修改前后、规格更新后） | `records/context/*.md`、`*.json` |
| PowerContext 状态写入 / 读取 | `records/powercontext-server.log`、`python -m sdd_context state show` |
| 端到端验证结果 | `records/validation-report.json` |

## 11. 已知限制

- 检索为「SQL 召回 + BM25 排序」的词法方案，未接入向量模型；同义改写召回转语义检索弱。
- 中文分词使用二元组，极短任务描述（少于 4 字）召回质量会下降。
- 状态以 Memory 条目追加保存，历史版本保留在 PowerContext 中，未做压缩。
- PowerContext 在 Windows 上为 experimental，本次已在 Windows 11 + Python 3.13 实测可用；
  embedded seekdb 后端不支持 Windows，因此两者分开：seekdb 走容器，PowerContext 走 SQLite。
- `tools/minimax_agent.py` 只接受「整文件返回」，适用于中小文件；超大文件需要改为分块编辑。
