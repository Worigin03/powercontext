# 订单查询规格（SPEC-ORDER-QUERY）

本文件是 `ledgerflow` 订单查询能力的唯一需求来源。每条 `## <ID> <标题>` 为一个可检索的规格条目，
`- key: value` 为条目属性，其余内容为条目正文。`status` 只允许 `active` / `superseded` / `deprecated`。

## REQ-ORD-001 状态与客户筛选
- status: active
- kind: requirement
- module: order-query
- tags: status, customer, filter, search
- applies_to: ledgerflow.service.OrderQueryService.search
- verified_by: tests/test_search_basic.py

`status` 与 `customer_id` 可以单独使用，也可以组合使用，语义为逻辑与。
`status` 只接受 `pending`、`paid`、`shipped`、`cancelled`、`refunded`，其他取值返回
`400 INVALID_STATUS`。空字符串与纯空白视为未提供该筛选条件，不参与过滤。

## REQ-ORD-002 结果排序契约
- status: active
- kind: requirement
- module: order-query
- tags: ordering, sort, stable
- applies_to: ledgerflow.service.OrderQueryService.search
- verified_by: tests/test_search_basic.py

结果必须先按 `created_at` 倒序排列；`created_at` 相同的订单按 `order_id` 升序排列。
排序必须在分页切片之前完成，任何新增筛选条件都不得改变该排序契约。

## REQ-ORD-003 分页契约
- status: active
- kind: requirement
- module: order-query
- tags: pagination, page, total
- applies_to: ledgerflow.service.OrderQueryService.search
- verified_by: tests/test_pagination.py

`page` 从 1 开始，小于 1 返回 `400 INVALID_PAGE`；`page_size` 默认 20，小于 1 返回
`400 INVALID_PAGE_SIZE`，大于 100 时截断为 100。`total` 是过滤后、分页前的记录总数，
不是当前页条数。超出末页返回空 `items` 且 `total` 不变。

## REQ-ORD-004 未知字段向后兼容
- status: active
- kind: requirement
- module: order-query
- tags: compatibility, contract, payload
- applies_to: ledgerflow.service.OrderQueryService.search
- verified_by: tests/test_search_basic.py

请求体中未定义的字段必须被忽略，不得因此返回 4xx。该规则保证旧客户端在服务端新增筛选项后仍然可用；
新增筛选项本身不得放宽本规则。

## REQ-ORD-009 金额上限筛选（已废止）
- status: superseded
- kind: requirement
- module: order-query
- tags: amount, legacy
- superseded_by: REQ-ORD-014
- applies_to: ledgerflow.service.OrderQueryService.search

历史需求：只支持 `amount_max`，使用 `float` 比较，上界不包含，缺省币种按 CNY 处理。
该条目已被 REQ-ORD-014 取代，不得据其实现金额筛选。

## REQ-ORD-014 订单应付金额区间筛选
- status: active
- kind: requirement
- module: order-query
- tags: amount, range, filter, decimal, currency, payable
- applies_to: ledgerflow.service.OrderQueryService.search
- verified_by: tests/test_amount_filter.py, tests/test_amount_validation.py
- supersedes: REQ-ORD-009

请求体新增三个可选字段：`amount_min`、`amount_max`、`currency`。

金额口径为订单应付金额（订单的 `amount` 字段），不含运费与税费；不同币种的金额不得合并比较。

1. `amount_min` 与 `amount_max` 为十进制字符串，使用 `decimal.Decimal` 做精确数值比较，禁止转换为
   `float`；区间为闭区间 `[amount_min, amount_max]`，上下界都必须能命中。
2. 只给出其中一个边界时按单侧区间处理；两个边界都不给时金额区间不参与过滤。
3. 任一金额边界出现（非空字符串）时，必须显式提供 `currency`，否则返回
   `400 CURRENCY_REQUIRED`。不得为缺失的币种假定默认值。
4. `currency` 单独出现时也必须生效，只返回币种完全相同的订单。
5. 金额格式只允许 `^[0-9]{1,12}(\.[0-9]{1,6})?$`；千分位、科学计数法、负号、币种符号、其他字符
   一律返回 `400 INVALID_AMOUNT`。空字符串与纯空白视为未提供。
6. `amount_min > amount_max` 返回 `400 INVALID_RANGE`；两者相等时按精确金额匹配。
7. 金额筛选必须与其他筛选条件、排序契约（REQ-ORD-002）和分页契约（REQ-ORD-003）组合生效，
   `total` 仍为全部过滤条件生效后的总数。

## REQ-ORD-016 金额筛选的验证要求
- status: active
- kind: requirement
- module: order-query
- tags: verification, regression, test
- applies_to: tests/test_amount_filter.py
- verified_by: tests/test_amount_filter.py, tests/test_amount_validation.py

验证必须同时包含目标功能测试与回归测试：目标测试覆盖闭区间边界、单侧区间、币种隔离、精度比较与
错误码；回归测试覆盖既有筛选、排序、分页与未知字段兼容。修改不得删除或跳过任何既有断言。
