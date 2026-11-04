# PowerContext 赛题实现说明

本仓库在冻结版本 `9db1753434162c0aa7c9f1189a1722e617880a52` 的基础上实现赛题二至十一。
平台验收只加载 `src/`；公开测试、输入及评分逻辑保持原样。

## 数量触发（赛题七）

`RuntimeConfig` 新增两个默认关闭的严格正整数配置：

```python
RuntimeConfig(
    memory_source_count_threshold=3,
    topic_memory_source_count_threshold=5,
)
```

Supervisor 分页读取持久化的 dirty intent、Source journal head 和各处理族的成功 cursor。
一个持久化来源身份对应一个 journal 位置，因此重复 capture 和 receipt 信任升级不增加计数；
lineage 来源计数但不成为生成证据。数量与时间条件分别接受同一种原生调用，显式 flush 保持原行为。

Memory 每次只处理 `source_window_limit` 限定的一窗，成功后按新 cursor 重新判断剩余数量。
Topic Memory 沿用原生持久化 wave target，继续完成已接受批次的后续窗口。
已接受调用的失败重试不重新受数量门槛限制，重启通过持久化 intent 和 cursor 恢复。

## 来源生命周期（赛题九）

```python
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleEvent

service = runtime.lifecycle.for_scope(scope_id)
await service.register_source("source", source_ref, revision=1)
await service.register_memory("fact", citation, parents=("source",), policy="all")
await service.register_topic("topic", artifact_ref, parents=("fact",), policy="any")
await service.apply(SourceLifecycleEvent(
    node_id="source", revision=1, sequence=1,
    available=False, reason="来源撤回",
))
audit_reference = await service.resolve("fact", purpose="audit")
```

图、原始引用、Source 版本、最后事件及期望可用状态存储于 `pc_source_lifecycle_nodes`。
注册要求父节点已存在并拒绝重复、自环及冲突绑定；注册时立即计算 all/any 状态。
事件严格校验字段、版本和递增序号，相同事件可以重放。

Memory 通过原生 `forget` / `reactivate` 改变 manifest 状态和搜索投影，正文版本不变。
Topic 的全文、向量和 browse 查询在数据库中排除不可用节点，原始正文和索引投影保留以支持恢复。
精确历史读取继续可用；`generation` 拒绝不可用引用，`audit` 返回注册时的引用。

事件先持久化期望状态与 reconciliation 标记，再执行原生状态写入。
失败后的同事件重试或重新打开 Runtime 会完成未完成传播；已一致的 Memory 不产生新修订。
此服务是内置 Python Runtime API，本题未新增 HTTP/MCP 端点。

## seekdb 单 SQL 混合候选（赛题十一）

`SeekDBMemoryIndex` 复用原生全文和向量投影。Hybrid 模式使用一条含两个有界 CTE 的 SQL，
通过 `UNION ALL` 返回带通道标记的两路候选，再沿用 Memory 的融合与精确正文读取。
Scope、artifact、tag 和每路候选上限均在 SQL 中生效；单通道继续使用原有实现。

查询沿用原有 current-head 和向量完整性校验，不按投影旧 `head_revision` 排除仍有效的条目。
返回引用通过已验证的请求 Memory head 构造，避免重写未变化条目的投影。

## 已有题目复查

赛题四、五、六、八、十及 CSV/Pi 采集保留公开契约。
HTTP 重排严格接受 JSON 数值分数并统一拒绝非整数 limit；Embedding 用量分别保留未知的
`input_tokens` / `output_tokens`，缓存命中不产生请求。
增量重建还会校验直接构造的 Delta 前缀，拒绝负数、布尔值和浮点数。

## 验证结果（2026-11-04）

| 验证范围 | 结果 |
| --- | --- |
| 全部赛题公开验收，Linux 联跑 | 95 passed，665.19 秒，无失败或跳过 |
| 原有 Supervisor、Topic wave 和 Topic 存储回归，Windows | 65 passed |
| 新增故障恢复、隔离和协议边界回归，Windows | 13 passed |
| 修改文件的 Ruff 格式与 lint、针对修改源码的 ty 检查 | 通过 |

Linux 原生验收使用 Python 3.12.3、`pylibseekdb==1.3.0.post4`、
`pyobvector==0.2.28` 和 `PyMySQL==1.2.0`。Hybrid 公开测试实际执行 SQL 和 EXPLAIN，
同时确认全文、向量索引及单 statement 候选访问。

## 验证命令

在 Linux Python 3.12 和公开固定依赖环境中运行：

```bash
make -C competition test
```

附加回归覆盖真实 SQLite 写入失败后的事件重试、重启恢复、scope 隔离、
HTTP 分数类型校验和 Embedding 可选用量字段。
公开样例验证不等同于平台私有语料的召回率、逐查询 MRR 和延迟验收。

本实现使用 OpenAI Codex 辅助编码及验证。
