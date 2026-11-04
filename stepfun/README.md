# Edge KB · 端侧向量化 + seekdb 混合检索

2026 OceanBase 大赛 · 阶跃星辰赛题：**端侧 AI 推理性能优化与 seekdb 混合检索**

📄 [一页技术说明](docs/TECH_REPORT.md) · 📊 [实测数据与复现条件](docs/BENCHMARK.md)

一个迷你个人知识库：文档在**浏览器本地**切块并向量化（WebGPU 优先、WASM 自动降级），
向量与原文写入 **seekdb**，查询时先用本地模型编码查询，再由 **seekdb 的 `DBMS_HYBRID_SEARCH`**
在一次调用里完成「向量召回 + 全文召回 + RRF 融合」，返回排序结果。

```
┌────────────────────────────── 浏览器（用户设备） ──────────────────────────────┐
│  文档 / 粘贴文本                                                                │
│      │ 切块（600 字 / 100 重叠）                                                │
│      ▼                                                                          │
│  Web Worker + Transformers.js ──► ONNX Runtime Web                              │
│      │        WebGPU（fp32/fp16）──失败──► WASM（int8，多线程）                 │
│      │ 384/512 维归一化向量                                                     │
└──────┼──────────────────────────────────────────────────────────────────────────┘
       │ HTTP（只传向量与文本，不传任何推理请求）
       ▼
┌──────────────────────────── Node 服务（同机，可容器化） ───────────────────────┐
│  seekdb-js  ──►  seekdb (server 模式，MySQL 协议 2881)                          │
│                    ├─ 表：id / document / embedding VECTOR(512) / metadata JSON  │
│                    ├─ 索引：HNSW(cosine) + FULLTEXT(ngram2)                      │
│                    └─ DBMS_HYBRID_SEARCH：向量 + 全文 → RRF 融合（库内完成）      │
└────────────────────────────────────────────────────────────────────────────────┘
```

## 目录结构

```
stepfun/
├── web/                 前端（Vite + TypeScript + Transformers.js）
│   ├── src/worker.ts    端侧推理 Worker：模型加载、批量向量化、查询编码
│   ├── src/embedder.ts  WebGPU 探测 / 后端选择 / Worker 协议封装
│   ├── src/main.ts      导入 → 向量化 → 写库 → 检索 的完整交互
│   ├── src/bench.ts     浏览器内基准（延迟 + Recall/nDCG）
│   └── public/models/   量化后的 ONNX 模型（npm run setup 生成）
├── server/              Node 后端（Express + seekdb-js）
│   └── src/db.mjs       集合 schema、批量 upsert、refresh_index、混合检索
├── scripts/
│   ├── fetch-model.mjs  从 ModelScope 拉取 ONNX 权重
│   ├── quantize.py      INT8 动态量化 + FP16 转换 + 数值校验 + 体积报告
│   ├── prepare-assets.mjs 复制 ORT wasm 运行时（离线可用）
│   ├── build-eval-set.mjs 生成评测语料与查询
│   └── bench.mjs        Playwright 驱动真实 Chrome 跑基准
├── docs/TECH_REPORT.md  一页技术说明
├── docs/BENCHMARK.md    实测数据与复现条件
└── docker-compose.yml   seekdb + 应用的容器编排
```

## 快速开始

### 方式 A：Docker Compose（推荐）

```bash
cd stepfun
npm run setup          # 下载模型 + 量化 + 复制 ORT 运行时（首次约 2 分钟）
npm run build          # 构建前端静态资源
docker compose up -d --build
# 打开 http://127.0.0.1:8787
```

### 方式 B：本机运行（Windows / macOS / Linux 均可）

1) 启动 seekdb（任选其一）

```bash
# B1. Docker
docker run -d --name seekdb -p 2881:2881 -v seekdb_data:/var/lib/seekdb oceanbase/seekdb:latest

# B2. 已有本地 seekdb 二进制
./seekdb --nodaemon --port 2881 --base-dir ./run
```

2) 准备模型与应用

```bash
cd stepfun
npm install                      # 根依赖（Playwright，可选）
npm --prefix server install --legacy-peer-deps
npm --prefix web install --legacy-peer-deps
npm run setup                    # 模型下载 + 量化 + ORT 资源
npm run build
npm start                        # http://127.0.0.1:8787
```

> `--legacy-peer-deps` 是因为 `seekdb@1.4.0` 的 peer 依赖里带了一个未发布的 `workspace:` 声明，
> npm 严格模式会拒绝安装（不影响功能）。

3) 浏览器要求：Chrome / Edge 最新版。WebGPU 可用时自动使用 GPU，否则自动降级 WASM 并在右上角标出。

## 模型与量化

| 项目 | 值 |
| --- | --- |
| 模型 | `BAAI/bge-small-zh-v1.5`（Transformers.js 格式，来源 ModelScope 镜像 `Xenova/bge-small-zh-v1.5`） |
| 维度 / pooling | 512 / CLS + L2 归一化 |
| FP32 | 90.46 MiB（`onnx/model.onnx`） |
| FP16 | 45.30 MiB（`onnx/model_fp16.onnx`），压缩 **2.00×** |
| INT8 | 22.81 MiB（`onnx/model_int8.onnx`），压缩 **3.97×** |

量化脚本 `scripts/quantize.py`：

```bash
python scripts/quantize.py          # 需要 onnx / onnxruntime / numpy
```

- INT8：`onnxruntime.quantization.quantize_dynamic(weight_type=QUInt8, MatMulConstBOnly=True)`，权重对称量化、激活运行时动态量化，无需校准集；
- FP16：`onnxruntime.transformers.float16.convert_float_to_float16(keep_io_types=True)`，转换后用 ORT 载入校验；
- 数值校验：同一批 token 输入下与 FP32 输出对比（见 `web/public/models/*/quantization.json`）。

## seekdb 用法

- 集合 schema：`vectorIndex = HNSW(dim=512, distance=cosine)`，`fulltextIndex = ngram2(2~3)`
  （ngram2 对中文无需分词词典，中英混排都能建索引）；
- 写入：`collection.upsert({ids, documents, embeddings, metadatas})`，批量 32 条/次；
- 刷新：写入后 `collection.refresh_index()`，使新文档在 HNSW 中可见（界面显示「待刷新」计数）；
- 检索：`collection.hybridSearch({ query: {$contains}, knn: {queryEmbeddings}, rank: {rrf} })`
  → SDK 生成 `DBMS_HYBRID_SEARCH.GET_SQL(...)` 并在 seekdb 内执行，融合发生在数据库侧。

## 复现基准

```bash
npm run eval:build      # 生成评测集（23 篇中文技术文档 → 评测语料 + 50 条查询）
node scripts/bench.mjs --repeat=3
```

结果写入 `bench/results.json`，汇总表见 `docs/BENCHMARK.md`。

## 已知取舍

- 切块上限 600 字，BGE-zh 的词表按字切分，超过 512 token 的尾部会被模型截断；实测 600 字的检索质量优于 480 字，
  因此默认取 600，并在文档中标注该行为。
- `fp16 + WebGPU` 与 `fp32 + WebGPU` 的整体吞吐接近（44.7 vs 48.6 块/s），fp16 的收益主要体现在**模型体积与显存占用**；
  但在 batch=16 + 长度分桶的组合下 fp16 更快（73.6 vs 41.4 块/s），因此 UI 默认选 fp16，fp32 可通过 `?dtype=fp32` 复现。
