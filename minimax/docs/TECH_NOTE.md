# 一页技术说明

## 1. 模块结构

```
任务 JSON ──► sdd_context.cli
                 ├── ingest   规格 Markdown / Python AST / 测试 AST ──► seekdb（spec_item / code_symbol / test_case）
                 ├── prepare  SQL 召回 → BM25 排序 → 规格链接展开 ──► ContextPack（Markdown + JSON）
                 └── state    PowerContext Memory（kind=sdd-task-state）◄──► 已完成 / 待办 / 验证结果
```

| 文件 | 职责 |
| --- | --- |
| `sdd_context/parser.py` | 解析 `## REQ-xxx` 条目的属性与正文；用 `ast` 抽取类/函数签名、行号、源码；解析测试的 `pytestmark` 与 `covers:` |
| `sdd_context/db.py` | seekdb（MySQL 协议）建表、幂等 upsert、按自然键清理；所有查询写入 SQL 日志 |
| `sdd_context/rank.py` | 中英混合分词（CJK 二元组 + snake/camel 拆分）与 BM25 |
| `sdd_context/prepare.py` | 选规格 → 展开代码/测试 → 反补被测试引用的规格 → 汇总状态与自检 |
| `sdd_context/render.py` | 渲染给模型看的 Markdown（带 `文件#行号` 引用） |
| `sdd_context/state.py` | 任务状态的写入与读取（PowerContext `/v1/memory/*`） |
| `tools/minimax_agent.py` | 把上下文包交给 MiniMax，应用返回文件，回灌测试失败，留存全过程 |

## 2. 上下文选择方式

1. **召回**：任务文本分词后，用 `search_text` 上的 `LIKE` 在 seekdb 里召回规格、代码符号、测试；
   如果召回不足 4 条则退化为全量读取，避免小语料下误杀。
2. **排序**：BM25（k1=1.2，b=0.75）。任务描述里点名的 `REQ-xxx` 额外加 30 分——这是显式指针，不是猜测。
3. **展开**：命中规格通过 `applies_to` 拉到精确代码符号（全限定名匹配），通过 `verified_by` 拉到测试文件。
4. **反补**：测试的 `covers:` 会反向拉入它引用的规格（例如错误码契约 `CTR-ORD-001`），避免只按标题相似度漏掉契约。
5. **失效处理**：`status=superseded` 的条目绝不进入「适用规格」，只在「已失效条目」中列出，
   并写明 `superseded_by`，让模型知道旧决策为什么不能用（例如 `ADR-003` 的 float + 开区间）。
6. **自检**：每条引用都检查文件存在与行号范围；缺目标测试、缺回归测试、`applies_to` 落空都会写进 `warnings`。

## 3. 各项技术的分工

| 技术 | 承担的作用 | 本次证据 |
| --- | --- | --- |
| seekdb | 存放规格条目、代码符号、测试索引；`prepare` 的每次检索都来自它 | `records/db-queries.log` 中的 `SELECT ... FROM spec_item/code_symbol/test_case`；`ingest` 统计 12/20/30 |
| PowerContext | 保存任务进展与验证结果，下一次运行读回并写进上下文包第 7 节 | `state set` → 重启进程 → `prepare` 输出「已完成 / 待办 / 已验证」；记忆 kind=`sdd-task-state` |
| MiniMax | 阅读上下文包并产出真实代码修改 | `records/minimax/session.jsonl`、`prompt.md`、`round-01.md`、`final.patch`、`round-01-*.log`；直连鉴权留证 `records/minimax/api-check.log` |

> MiniMax 参与方式说明：随题提供的两把 `sk-cp-` Key 直连 `api.minimaxi.com` 分别返回
> `401 invalid api key` 与 `402 insufficient balance`（`records/minimax/api-check.log`），
> 因此本次改码由**会话内运行在 MiniMax 模型上的编程智能体**依据上下文包完成；
> `tools/minimax_agent.py` 提供用自备 Key 直连 API 的等价流程（同一提示词、同一校验回路）。

## 4. 为什么这样设计

- **规格是唯一事实源**：代码符号与测试都通过规格里的 `applies_to` / `verified_by` 关联，而不是靠目录约定，
  因此改规格就能改变上下文（见 `validation/spec-v2/`）。
- **失效信息也要给模型看**：只给「有效规格」会让模型不知道为什么不能沿用旧实现；把
  `superseded` 条目连同取代关系一起给出，才能避免「照旧决策实现」这类错误。
- **状态与检索分离**：任务进展存在 PowerContext，工程资料存在 seekdb，各自可独立演进；
  重启后 `prepare` 先读状态再组装上下文，实现跨会话续作。
- **可核对**：输出中的每个来源都是 `文件#行号`，并经过存在性校验；SQL、模型对话、测试日志全部落盘。
