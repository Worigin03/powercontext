# 一页技术说明 · Edge KB

> 2026 OceanBase 大赛 · 阶跃星辰赛题「端侧 AI 推理性能优化与 seekdb 混合检索」
> 代码：`powercontext/stepfun`；实测明细：[`BENCHMARK.md`](./BENCHMARK.md)；全部数字来自本机真实 Chrome 运行。

## 1. 目标与整体链路

一个纯本地运行的迷你知识库：**语料导入 → 浏览器切块 → 浏览器向量化 → 写入 seekdb → 混合检索**。
推理与检索都不出本机：浏览器只向后端提交**文本与向量**，服务端不加载任何模型，检索与融合全部在 seekdb 内完成。

```
文档/粘贴文本 → 切块(600 字/重叠 100) → Web Worker + Transformers.js → ONNX Runtime Web
   → WebGPU(fp32/fp16) ──探测失败──→ WASM(int8, 多线程)  →  512 维 L2 归一化向量
   → HTTP(仅向量+原文+元数据) → seekdb-js → seekdb(2881)
        ├─ 表: id / document / embedding VECTOR(512) / metadata JSON
        ├─ 索引: HNSW(cosine) + FULLTEXT(ngram2)
        └─ DBMS_HYBRID_SEARCH: 向量召回 + 全文召回 → RRF 融合（库内单次调用完成）
```

## 2. 模型与量化

| 项目 | 值 |
| --- | --- |
| 模型 | **BAAI/bge-small-zh-v1.5**（中文检索向，512 维，CLS pooling + L2 归一化） |
| 来源版本 | 上游 `BAAI/bge-small-zh-v1.5`；ONNX 权重取自 ModelScope 镜像 `Xenova/bge-small-zh-v1.5`（Transformers.js 兼容格式，含 config/tokenizer） |
| 查询侧处理 | 仅查询加前缀 `为这个句子生成表示以用于检索相关文章：`，文档侧不加（BGE 官方用法） |
| FP32 | 90.46 MiB（基线，`onnx/model.onnx`） |
| FP16 | **45.30 MiB，压缩 2.00×**，`convert_float_to_float16(keep_io_types=True)` |
| INT8 | **22.81 MiB，压缩 3.97×**，`quantize_dynamic(weight_type=QUInt8, MatMulConstBOnly=True)`，无需校准集 |

**量化后的质量变化**（同批 token 输入，FP32 为基准）：

| 精度 | 余弦相似度 | 最大绝对误差 | 检索 Recall@10 | nDCG@10 |
| --- | --- | --- | --- | --- |
| FP32 | 1.000 | 0 | 58.5% | 50.7% |
| FP16 | **1.000** | 7.7e-5 | **58.5%** | **50.7%** |
| INT8（WASM 上运行） | 0.975 | 3.7e-2 | **58.5%** | 47.9% |
| INT8（WebGPU 上运行） | 0.975 | 3.7e-2 | 52.1% | 44.0% |

结论：FP16 在本任务上**零质量损失**，是 WebGPU 路径的默认精度；INT8 在 WASM 上质量与 FP32 持平（Recall 相同，仅排序微降），
但在 WebGPU 上掉 6.4 pp——原因是动态量化引入的 `DynamicQuantizeLinear/MatMulInteger` 在 ORT WebGPU EP 中缺少 kernel，
按算子回落到 CPU 并触发频繁同步（单条查询向量化 67.4 ms vs WASM 8.1 ms）。因此**降级链刻意选择 WASM+INT8，而不是 WebGPU+INT8**。

## 3. 端侧运行与后端选择

- 运行时：**Transformers.js 4.3.2 + ONNX Runtime Web**，模型与 ORT wasm 运行时（`ort-wasm-simd-threaded.*`，8 个文件）
  全部由本地静态资源提供（`env.allowRemoteModels = false`），断网可用，不存在云端推理调用。
- 探测：启动时 `navigator.gpu.requestAdapter()` 实际申请适配器而非只看 `'gpu' in navigator`；
  成功记录 adapter 信息（实测 `amd / rdna-3`），失败则读 `reason` 并降级。
- 决策顺序：`WebGPU 可用 → fp16`（零质量损失、体积减半，作为 GPU 路径默认）
  → 显式指定 WebGPU 但初始化抛错 → **自动降级 WASM + int8** → WebGPU 不可用 → 直接 WASM + int8。
  `?device=webgpu|wasm` 与 `?dtype=fp32|fp16|int8` 可强制覆盖，用于复现实验（fp32 亦在基准中跑通）。
- 界面显式提示：右上角徽标显示 `WebGPU · FP16` / `WASM · INT8`，并在降级时给出原因文案（如「未检测到 WebGPU 适配器」），
  不静默切换，用户始终知道当前用的是哪条路径。
- 线程：响应头下发 `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Embedder-Policy: require-corp`，
  使 `crossOriginIsolated === true`，WASM 后端才能启用 SharedArrayBuffer 多线程；WebGPU 与 WASM 均在 **Web Worker** 内运行，主线程只做 UI。
- 降级验证：无头 Chrome 下 `navigator.gpu` 为 undefined，自动落到 `WASM · int8` 并正常完成导入/检索；
  有头 Chrome 下拿到 `amd/rdna-3` 适配器走 WebGPU。两条路径都跑通了完整基准（5 组配置）。

## 4. seekdb 使用方式

- 接入：**seekdb-js 1.4.0**，server 模式（MySQL 协议 2881）；本地 `seekdb --nodaemon --port 2881 --base-dir ./run`，
  `docker-compose.yml` 提供容器化等价部署。
- 集合 schema：`VectorIndexConfig({ hnsw: { dimension: 512, distance: 'cosine' } })` +
  `FulltextIndexConfig('ngram2', { min_ngram_size: 2, max_ngram_size: 3 })`。选 `ngram2` 是因为它对中文无需分词词典，
  中英混排也能建索引，避免引入 jieba 之类的额外依赖。
- 关键约束：集合句柄**必须显式传 `embeddingFunction: null`**。否则 SDK 会按集合元数据里的 `default-embed` 去解析服务端推理函数并报
  `Embedding function 'default-embed' is not registered`——本题的向量一律由浏览器算好后传入，服务端不做任何推理。
- 混合检索：`collection.hybridSearch({ query: { whereDocument: { $contains } }, knn: { queryEmbeddings }, rank: { rrf: { rankWindowSize: 60, rankConstant: 60 } } })`。
  SDK 生成 `DBMS_HYBRID_SEARCH.GET_SQL(...)` 并在库内执行，**候选召回与 RRF 融合都发生在 seekdb 内**，客户端只接收最终的 Top-K，
  不做任何二次拼合或重排。界面上的 `vector` / `fulltext` 两个模式仅用于对照实验（`col.query` 与 `$contains` 单路）。
- 写入与刷新：`col.upsert({ ids, documents, embeddings, metadatas })`，**32 条/批**；写入后累计 `pendingRefresh`，
  调用 `col.refresh_index()` 后清零并记录最近刷新耗时。界面显示「N 条待刷新」，避免用户误以为写入即可检索。
- 增量导入：块 id 为 `文档哈希:块序号`，同一文档重复导入是幂等 upsert，不会产生重复行；多文档可分批导入、批间可查。

## 5. 状态管理与优化

**状态**：当前集合记在 `localStorage`（`kb.collection`），刷新后自动恢复；每个集合返回 `count / dimension / distance / pendingRefresh /
lastRefreshMs / lastRefreshAt`，切集合时重新拉取；后端选择与实测值记在 `stats` 中并在界面展示分阶段耗时（模型加载 / 向量化 / 写入 /
刷新 / 检索 / 端到端）。模型只加载一次，切换集合不重新初始化 pipeline。

**优化点与实测收益**（基线 = int8 + WASM，即无 GPU 设备的兜底路径）：

| 优化 | 做法 | 实测 |
| --- | --- | --- |
| 批处理 | 一次前向喂 16 条，摊薄 kernel launch 与 WebGPU 提交开销 | fp32+WebGPU 48.6 块/s，比 fp32+WASM 10.7 块/s **+354%** |
| 长度分桶 | 批内按文本长度排序后组批，减少 padding 到最长样本的浪费（结果按原下标回填，顺序不变） | fp16+WebGPU batch=16 从 57.0 → **73.6 块/s（+29.1%）**；fp32 batch=64 不开分桶会退化到 8 块/s，开分桶恢复 43.1 块/s |
| 后端选择 | WebGPU 优先，无 GPU 时 WASM 多线程 + INT8 | int8+WASM 19.8 块/s，端到端 P50 **29.0 ms**（全组最低） |
| 模型加载 | 量化模型 + 本地静态资源 + 预热一次推理 | 冷启动 877–1512 ms；首次推理 337–406 ms（WebGPU）、42–44 ms（WASM） |
| 切块策略 | 段落聚合 + 句子边界切分 + 合并短碎片，600 字/重叠 100 | 600 字的 Recall@10 优于 480 字（已对比，见 README 取舍） |
| 查询编码 | 查询侧加 BGE 前缀 + 单条直通（不分桶） | 查询向量化 P50 **8.1–10.8 ms**（int8+WebGPU 例外：67.4 ms） |
| 检索 | 融合下推到 seekdb（一次 RPC，无客户端合并） | seekdb 检索 P50 11.0–13.0 ms，P95 ≤ 22.1 ms；端到端 P50 **29.0–32.4 ms**（int8+WebGPU 例外：90.4 ms） |
| 索引策略 | HNSW(cosine) + ngram2 全文双索引，查询时按场景选路 | 同集合同查询下调参：hybrid 58.5% / nDCG 50.7%，vector 58.5% / 39.5%，fulltext 71.3% / 59.8%（见表 5） |

**检索质量**（262 块 / 50 条查询 / 47 条有效金标，hybrid Top-10）：Recall@10 **58.5%**、nDCG@10 **50.7%**、MRR@10 **48.8%**；
文档级 Recall@10 **93.6%**（Top-10 中出现正确文档的任意块）。FP16/FP32 两档质量完全一致，说明量化没有牺牲检索效果。

**索引策略的一个反直觉结论**：本评测集的查询取自文档小节标题，字面重合度高，因此纯全文（ngram2，按 2~3 字 n-gram 词项匹配，
并非严格子串）反而最强——Recall 71.3%、nDCG 59.8%，高于 hybrid 的 58.5% / 50.7%；向量分支 Recall 相同但排序明显更弱
（nDCG 39.5%、MRR 33.7%），RRF 等权融合会被这一路拖累。我们仍以 hybrid 为默认（查询措辞与原文不一致时，全文分支会退化，
向量分支是唯一保底），并把三种模式暴露在界面上供按场景切换；同时如实标注该评测集对全文分支的偏向。

## 6. 复现

```bash
npm run setup        # 拉模型 + INT8/FP16 量化 + 复制 ORT 运行时
npm run build && npm start     # http://127.0.0.1:8787
node scripts/bench.mjs --repeat=3      # 5 组配置 → bench/results.json
node scripts/sweep.mjs                 # 批大小 × 分桶 → bench/sweep.json
node scripts/modes.mjs                 # hybrid/vector/fulltext 对照 → bench/modes.json
node scripts/report.mjs                # 渲染 docs/BENCHMARK.md
```

## 7. 已知限制

- 评测金标由「小节正文 5-gram 覆盖率 ≥ 50%」自动生成，块粒度标注存在偏差，因此 Recall@10 的绝对值偏低而文档级 Recall 达 93.6%；
  两档之间的**相对比较**仍然有效。
- 600 汉字约 600 token，超过 bge 的 512 上限，超长块尾部会被 tokenizer 截断；但实测 600 字切块的整体召回仍优于更短的切块，
  因此保留该取值并在 README 中标注此行为。
- 本机只有 Radeon 760M（核显），WebGPU 相对 WASM 的领先幅度在独显上应更明显；INT8+WebGPU 的算子回落问题也与 ORT 版本强相关。
