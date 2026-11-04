# 验证样例

> 评审可按本文件逐条核对上下文输出与测试结果。所有路径均相对 `minimax/`。

## 1. 任务

| 项 | 值 |
| --- | --- |
| 任务文件 | `tasks/TASK-20261104-01.json` |
| 任务 | 订单查询支持金额区间筛选（`ledgerflow` 业务工程） |
| 要改什么 | 在 `POST /orders/search` 的进程内实现里支持 `amount_min` / `amount_max` / `currency`，闭区间、Decimal 精确比较、缺币种报 `CURRENCY_REQUIRED`、非法金额报 `INVALID_AMOUNT`、`min > max` 报 `INVALID_RANGE` |
| 哪些行为必须保持 | 排序（`created_at` 倒序、同刻 `order_id` 升序）、分页与 `total` 语义、`status` / `customer_id` 筛选、未知字段静默忽略、响应字段结构 |

## 2. 上下文中必须包含的内容

### 2.1 规格（`records/context/TASK-20261104-01.md` 第 2 节）

| 条目 | 来源 | 为什么必须出现 |
| --- | --- | --- |
| `REQ-ORD-014` 订单金额区间筛选 | `demo_project/specs/order-query.md#L63` | 本次需求本体 |
| `REQ-ORD-002` 结果排序契约 | `demo_project/specs/order-query.md#L18` | 必须保持的排序行为 |
| `REQ-ORD-003` 分页契约 | `demo_project/specs/order-query.md#L31` | 必须保持的分页行为 |
| `REQ-ORD-001` 状态与客户筛选 | `demo_project/specs/order-query.md#L7` | 既有筛选不能回归 |
| `REQ-ORD-004` 未知字段向后兼容 | `demo_project/specs/order-query.md#L41` | 新增字段不能引入严格校验 |
| `CTR-ORD-001` 搜索接口的错误响应 | `demo_project/specs/api-contract.md#L5` | 错误码与错误体契约 |
| `ADR-007` Decimal 闭区间 + 显式币种 | `demo_project/specs/decisions.md#L18` | 生效决策 |

### 2.2 已失效条目（第 4 节，必须标注不得依据）

| 条目 | 来源 | 取代者 |
| --- | --- | --- |
| `ADR-003` float + 上界开区间 | `demo_project/specs/decisions.md#L6` | `ADR-007` |
| `REQ-ORD-009` 仅 amount_max | `demo_project/specs/order-query.md#L52` | `REQ-ORD-014` |

### 2.3 代码（第 5 节）

| 符号 | 来源 |
| --- | --- |
| `ledgerflow.service.OrderQueryService.search` | `demo_project/src/ledgerflow/service.py#L27-L50` |
| `ledgerflow.service.handle_search` | `demo_project/src/ledgerflow/service.py#L101-L121` |

### 2.4 测试（第 6 节）

| 类型 | 文件 | 数量 | 说明 |
| --- | --- | --- | --- |
| 目标功能测试 | `tests/test_amount_filter.py` | 8 | 闭区间边界、单侧区间、币种隔离、精度比较、与其他筛选组合 |
| 目标功能测试 | `tests/test_amount_validation.py` | 9 | `CURRENCY_REQUIRED` / `INVALID_AMOUNT`（含 4 个参数化用例）/ `INVALID_RANGE` / 等值边界 |
| 回归测试 | `tests/test_search_basic.py`（9）、`tests/test_pagination.py`（8） | 17 | 排序、分页、既有筛选、未知字段、响应结构 |

## 3. 初始数据

`demo_project/data/orders.json`：13 条订单，覆盖 5 种状态、CNY/USD 两种币种、0 元订单与同 `created_at` 订单
（`ORD-1012` / `ORD-1013` 同为 `2026-09-23T15:40:00Z`，用于验证排序次级键）。

金额区间样例：`amount_min=1200.00`、`amount_max=3600.00`、`currency=CNY` 应命中
`ORD-1012, ORD-1013, ORD-1007, ORD-1004, ORD-1003, ORD-1002`（共 6 条，含 `cancelled` 的 `ORD-1003`，
因为本需求不改状态语义）。

## 4. 操作步骤与预期结果

| # | 命令 | 预期 |
| --- | --- | --- |
| 1 | `python -m sdd_context init-db` | 返回四张表计数（首次为 0） |
| 2 | `python -m sdd_context ingest --repo demo_project` | `specs=12, symbols=20, tests=30` |
| 3 | `cd demo_project && python -m pytest -m target` | **失败**（17 个目标用例全部因行为未实现而失败，非环境错误） |
| 4 | `python -m sdd_context prepare --task tasks/TASK-20261104-01.json --save` | 第 2/4/5/6 节包含上表全部来源；第 8 节无告警 |
| 5 | `python -m sdd_context state set --task TASK-20261104-01 --todo "实现 REQ-ORD-014"` | 写入 PowerContext Memory |
| 6 | `python tools/minimax_agent.py --context records/context/TASK-20261104-01.md --project demo_project` | 生成 `records/minimax/final.patch`，目标 + 回归测试通过 |
| 7 | `python -m sdd_context verify --task TASK-20261104-01 -- python -m pytest -m target` | exit 0，状态里出现该次验证结果 |
| 8 | 新进程执行 `python -m sdd_context prepare --task ... --save` | 第 7 节显示已完成 / 待办 / 已验证（状态复用） |
| 9 | `python -m sdd_context ingest --repo . --specs validation/spec-v2 --src demo_project/src --tests demo_project/tests` 后再次 `prepare` | 输出出现 v2 标题「订单应付金额区间筛选」与新增口径说明 |

第 3 步的失败必须是断言失败（行为缺失），不能是导入错误或缺少依赖；第 7 步的回归测试必须同时通过。

## 5. 重置方法

```bash
python scripts/run_validation.py --reset     # 恢复 validation/baseline 并清掉缓存
# 或手工
cp -r validation/baseline/src demo_project/ && cp -r validation/baseline/tests demo_project/
```

清空状态与索引（可选）：

```bash
docker exec seekdb mysql -h127.0.0.1 -P2881 -uroot -e "drop database sdd_context;"
rm -rf records/powercontext && POWERCONTEXT_HOME=$PWD/records/powercontext powercontext server run --no-env-file
```

## 6. 结果记录

`python scripts/run_validation.py --reset` 会把每一步的命令、退出码、输出尾部写入
`records/validation-report.json`，并对以下检查项给出布尔结果：

- `target_failed_before`、`target_passed_after`、`regression_passed_after`
- `pack_has_spec`、`pack_has_code_symbol`、`pack_has_target_tests`
- `pack_excludes_superseded_from_active`、`pack_has_state`、`spec_update_visible`
