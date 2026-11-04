# 接口契约（SPEC-API-CONTRACT）

## CTR-ORD-001 搜索接口的错误响应
- status: active
- kind: contract
- module: order-query
- tags: error, contract, http, response
- applies_to: ledgerflow.service.handle_search
- verified_by: tests/test_pagination.py, tests/test_amount_validation.py

可预期的入参错误必须返回 HTTP 400 与统一错误体：

```json
{"error": {"code": "<ERROR_CODE>", "message": "<可读说明>"}}
```

`code` 取值受控：`INVALID_REQUEST`、`INVALID_STATUS`、`INVALID_PAGE`、`INVALID_PAGE_SIZE`、
`CURRENCY_REQUIRED`、`INVALID_AMOUNT`、`INVALID_RANGE`。`message` 面向调用方，不得包含堆栈或内部路径。

## CTR-ORD-002 搜索接口的成功响应
- status: active
- kind: contract
- module: order-query
- tags: response, contract, items
- applies_to: ledgerflow.service.handle_search
- verified_by: tests/test_search_basic.py

成功响应固定为：

```json
{"items": [...], "page": 1, "page_size": 20, "total": 0}
```

`items` 中每条订单固定包含 `order_id`、`customer_id`、`status`、`currency`、`amount`、`created_at`、
`channel` 七个字段；`amount` 是保留两位小数的字符串，`created_at` 是 `YYYY-MM-DDTHH:MM:SSZ` 字符串。
新增筛选能力不得改变响应结构。
