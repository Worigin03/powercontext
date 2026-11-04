# 设计决策记录（DECISIONS）

决策条目使用 `## ADR-nnn` 编号。`status` 为 `active` 的决策必须遵守；`superseded` 的决策只用于解释历史，
不得作为实现依据。新决策通过 `supersedes` 指向被取代的旧决策，旧决策通过 `superseded_by` 指回新决策。

## ADR-003 金额筛选使用 float 且上界开区间
- status: superseded
- kind: decision
- module: order-query
- date: 2025-08-14
- tags: amount, float, exclusive, legacy
- superseded_by: ADR-007
- affects: REQ-ORD-009

早期实现为了与报表脚本对齐，把金额转成 `float` 后比较，并约定上界不包含；未传币种时默认 `CNY`。
该方案在对账场景出现过 `0.1 + 0.2` 类精度偏差，且默认币种掩盖了跨币种查询错误，已废止。

## ADR-007 金额比较使用 Decimal 闭区间并要求显式币种
- status: active
- kind: decision
- module: order-query
- date: 2026-03-12
- tags: amount, decimal, inclusive, currency
- supersedes: ADR-003
- affects: REQ-ORD-014

金额必须以 `decimal.Decimal` 解析并比较，区间为闭区间；查询方必须显式给出 `currency`，服务端不做
任何默认币种推断。理由：金额是财务数据，`float` 的二进制表示无法精确表达两位小数；闭区间与对账口径
（`min <= amount <= max`）一致；显式币种可以避免把 USD 订单混入 CNY 区间统计。

## ADR-011 未知请求字段保持静默忽略
- status: active
- kind: decision
- module: order-query
- date: 2026-05-06
- tags: compatibility, contract, payload
- affects: REQ-ORD-004

新增筛选项时不为未知字段引入严格校验：老客户端可能携带服务端尚未识别的字段，静默忽略可以保证升级期间
不发生 4xx。该决策只约束“未知字段”，不约束已定义字段的取值校验。
