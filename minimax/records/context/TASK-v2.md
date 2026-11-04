# 开发上下文包 · TASK-20261104-01 订单查询支持金额区间筛选

- 生成时间：2026-11-04T08:03:05Z
- 数据来源：seekdb `mysql://root@127.0.0.1:2881/sdd_context`（spec 7 / symbol 22 / test 30，本次 9 条查询）
- 任务状态来源：powercontext:memory/art_433734bab2bc49ed9cb8a12b7910e460
- 检索词：order、query、订单、单查、查询、询支、支持、持金、金额、额区

## 1. 任务

订单列表页需要按金额区间筛选订单。请按 REQ-ORD-014 实现金额区间筛选，并保证 REQ-ORD-002、REQ-ORD-003 规定的排序与分页行为不变；既有状态/客户筛选与未知字段兼容性（REQ-ORD-001、REQ-ORD-004）必须保持。

涉及模块：order-query

## 2. 本次任务适用的规格（status=active）

### REQ-ORD-014 订单应付金额区间筛选

- 来源：validation/spec-v2/order-query.md#L63 · 状态 `active` · 修订 `4c37cc1cf75a`
- 取代：REQ-ORD-009
- 适用位置：ledgerflow.service.OrderQueryService.search
- 验证：tests/test_amount_filter.py, tests/test_amount_validation.py

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

### REQ-ORD-004 未知字段向后兼容

- 来源：validation/spec-v2/order-query.md#L41 · 状态 `active` · 修订 `7b2b32ae4d1a`
- 适用位置：ledgerflow.service.OrderQueryService.search
- 验证：tests/test_search_basic.py

请求体中未定义的字段必须被忽略，不得因此返回 4xx。该规则保证旧客户端在服务端新增筛选项后仍然可用；
新增筛选项本身不得放宽本规则。

### REQ-ORD-002 结果排序契约

- 来源：validation/spec-v2/order-query.md#L18 · 状态 `active` · 修订 `38330c79404c`
- 适用位置：ledgerflow.service.OrderQueryService.search
- 验证：tests/test_search_basic.py

结果必须先按 `created_at` 倒序排列；`created_at` 相同的订单按 `order_id` 升序排列。
排序必须在分页切片之前完成，任何新增筛选条件都不得改变该排序契约。

### REQ-ORD-001 状态与客户筛选

- 来源：validation/spec-v2/order-query.md#L6 · 状态 `active` · 修订 `1b73c8ca9a7c`
- 适用位置：ledgerflow.service.OrderQueryService.search
- 验证：tests/test_search_basic.py

`status` 与 `customer_id` 可以单独使用，也可以组合使用，语义为逻辑与。
`status` 只接受 `pending`、`paid`、`shipped`、`cancelled`、`refunded`，其他取值返回
`400 INVALID_STATUS`。空字符串与纯空白视为未提供该筛选条件，不参与过滤。

### REQ-ORD-016 金额筛选的验证要求

- 来源：validation/spec-v2/order-query.md#L88 · 状态 `active` · 修订 `ea5f650240a9`
- 适用位置：tests/test_amount_filter.py
- 验证：tests/test_amount_filter.py, tests/test_amount_validation.py

验证必须同时包含目标功能测试与回归测试：目标测试覆盖闭区间边界、单侧区间、币种隔离、精度比较与
错误码；回归测试覆盖既有筛选、排序、分页与未知字段兼容。修改不得删除或跳过任何既有断言。

## 3. 生效的设计决策（必须遵守）

（无生效决策）

## 4. 已失效条目（不得作为实现依据）

- **REQ-ORD-009** 金额上限筛选（已废止） — 状态 `superseded`，被 `REQ-ORD-014` 取代；来源 validation/spec-v2/order-query.md#L52

## 5. 需要修改的代码

### `ledgerflow.service.OrderQueryService.__init__` — demo_project/src/ledgerflow/service.py:28-29

- 签名：`def __init__(self, repository: OrderRepository | None=None) -> None`

```python
    def __init__(self, repository: OrderRepository | None = None) -> None:
        self._repository = repository if repository is not None else OrderRepository()
```

### `ledgerflow.service.OrderQueryService.search` — demo_project/src/ledgerflow/service.py:31-70

- 签名：`def search(self, payload: Mapping[str, Any]) -> dict[str, Any]`
- 关联规格：REQ-ORD-014, REQ-ORD-004, REQ-ORD-002, REQ-ORD-001

```python
    def search(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        status = _optional_text(payload, "status")
        customer_id = _optional_text(payload, "customer_id")
        if status is not None and status not in ORDER_STATUSES:
            raise FilterError("INVALID_STATUS", f"unsupported status: {status}")
        page, page_size = _paging(payload)

        amount_min = _amount(payload, "amount_min")
        amount_max = _amount(payload, "amount_max")
        currency = _optional_text(payload, "currency")
        if (amount_min is not None or amount_max is not None) and currency is None:
            raise FilterError(
                "CURRENCY_REQUIRED", "currency is required when filtering by amount"
            )
        if amount_min is not None and amount_max is not None and amount_min > amount_max:
            raise FilterError("INVALID_RANGE", "amount_min must not be greater than amount_max")

        rows = [
            order
            for order in self._repository.all()
            if _matches(order, status=status, customer_id=customer_id)
            and _matches_amount(
                order,
                amount_min=amount_min,
                amount_max=amount_max,
                currency=currency,
            )
        ]
        rows.sort(key=lambda order: order.order_id)
        rows.sort(key=lambda order: order.created_at, reverse=True)

        total = len(rows)
        start = (page - 1) * page_size
        window = rows[start : start + page_size]
        return {
            "items": [order.to_dict() for order in window],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
```

### `ledgerflow.service._matches_amount` — demo_project/src/ledgerflow/service.py:81-96

- 签名：`def _matches_amount(order: Order, *, amount_min: Decimal | None, amount_max: Decimal | None, currency: str | None) -> bool`
- 说明：Closed-interval amount filter; both bounds are inclusive (REQ-ORD-014).

```python
def _matches_amount(
    order: Order,
    *,
    amount_min: Decimal | None,
    amount_max: Decimal | None,
    currency: str | None,
) -> bool:
    """Closed-interval amount filter; both bounds are inclusive (REQ-ORD-014)."""

    if currency is not None and order.currency != currency:
        return False
    if amount_min is not None and order.amount < amount_min:
        return False
    if amount_max is not None and order.amount > amount_max:
        return False
    return True
```

### `ledgerflow.service.default_service` — demo_project/src/ledgerflow/service.py:143-147

- 签名：`def default_service() -> OrderQueryService`

```python
def default_service() -> OrderQueryService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = OrderQueryService()
    return _SERVICE
```

### `ledgerflow.service.handle_search` — demo_project/src/ledgerflow/service.py:150-170

- 签名：`def handle_search(payload: Mapping[str, Any], *, service: OrderQueryService | None=None) -> dict[str, Any]`
- 说明：Handle one ``POST /orders/search`` call and return status + JSON body.

```python
def handle_search(
    payload: Mapping[str, Any], *, service: OrderQueryService | None = None
) -> dict[str, Any]:
    """Handle one ``POST /orders/search`` call and return status + JSON body."""

    if not isinstance(payload, Mapping):
        return {
            "status": 400,
            "body": {
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "payload must be a JSON object",
                }
            },
        }
    active = service if service is not None else default_service()
    try:
        body = active.search(payload)
    except FilterError as exc:
        return {"status": 400, "body": {"error": {"code": exc.code, "message": exc.message}}}
    return {"status": 200, "body": body}
```

## 6. 验证测试

### 6.1 目标功能测试（修改前应失败，修改后必须全部通过）

- `test_amount_filter_keeps_contract_ordering` — demo_project/tests/test_amount_filter.py#L33 （covers: REQ-ORD-014）
- `test_amount_max_includes_zero_valued_order` — demo_project/tests/test_amount_filter.py#L57 （covers: REQ-ORD-014）

### 6.2 回归测试（修改前后都必须通过）

- `test_default_listing_returns_every_order_in_contract_order` — demo_project/tests/test_search_basic.py#L31 （covers: REQ-ORD-001, REQ-ORD-002, REQ-ORD-004）
- `test_same_created_at_is_broken_by_order_id_ascending` — demo_project/tests/test_search_basic.py#L42 （covers: REQ-ORD-001, REQ-ORD-002, REQ-ORD-004）

## 7. 任务状态（来自 PowerContext）

- 更新时间：2026-11-04T08:03:01Z
- 已完成：已导入规格、代码与测试索引
- 待办：实现 REQ-ORD-014 金额区间筛选；运行目标与回归测试
- 已验证：`C:\Users\Worigin\Desktop\oceanbase\.venv-pc\Scripts\python.exe -m pytest -m target -q --tb=line` → exit 0，no pytest summary
- 已验证：`C:\Users\Worigin\Desktop\oceanbase\.venv-pc\Scripts\python.exe -m pytest -m regression -q --tb=line` → exit 0，no pytest summary
- 已验证：`C:/Users/Worigin/Desktop/oceanbase/.venv-pc/Scripts/python.exe -m pytest -m target -q --tb=short -p no:cacheprovider` → exit 0，no pytest summary
- 已验证：`C:/Users/Worigin/Desktop/oceanbase/.venv-pc/Scripts/python.exe -m pytest -m regression -q --tb=short -p no:cacheprovider` → exit 0，no pytest summary
- 已验证：`C:/Users/Worigin/Desktop/oceanbase/.venv-pc/Scripts/python.exe -m pytest -m target --tb=short -p no:cacheprovider` → exit 0，17 passed, 17 deselected
- 已验证：`C:/Users/Worigin/Desktop/oceanbase/.venv-pc/Scripts/python.exe -m pytest -m regression --tb=short -p no:cacheprovider` → exit 0，17 passed, 17 deselected
- 已验证：`C:\Users\Worigin\Desktop\oceanbase\.venv-pc\Scripts\python.exe -m pytest -m target --tb=line` → exit 0，17 passed, 17 deselected
- 已验证：`C:\Users\Worigin\Desktop\oceanbase\.venv-pc\Scripts\python.exe -m pytest -m regression --tb=line` → exit 0，17 passed, 17 deselected

## 8. 上下文自检

- ⚠ 规格来源不存在: validation/spec-v2/order-query.md
- ⚠ 规格来源不存在: validation/spec-v2/order-query.md
- ⚠ 规格来源不存在: validation/spec-v2/order-query.md
- ⚠ 规格来源不存在: validation/spec-v2/order-query.md
- ⚠ 规格来源不存在: validation/spec-v2/order-query.md
- ⚠ 代码来源不存在: demo_project/src/ledgerflow/service.py
- ⚠ 代码来源不存在: demo_project/src/ledgerflow/service.py
- ⚠ 代码来源不存在: demo_project/src/ledgerflow/service.py
- ⚠ 代码来源不存在: demo_project/src/ledgerflow/service.py
- ⚠ 代码来源不存在: demo_project/src/ledgerflow/service.py
- ⚠ 测试来源不存在: demo_project/tests/test_amount_filter.py
- ⚠ 测试来源不存在: demo_project/tests/test_amount_filter.py
- ⚠ 测试来源不存在: demo_project/tests/test_search_basic.py
- ⚠ 测试来源不存在: demo_project/tests/test_search_basic.py
