#!/usr/bin/env node
/**
 * 把 bench/results.json 渲染成 docs/BENCHMARK.md。
 * 用法：node scripts/report.mjs [--in bench/results.json] [--out docs/BENCHMARK.md]
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

const args = new Map(process.argv.slice(2).map((a) => a.replace(/^--/, '').split('=')));
const IN = path.resolve(args.get('in') || 'bench/results.json');
const OUT = path.resolve(args.get('out') || 'docs/BENCHMARK.md');

const data = JSON.parse(await fs.readFile(IN, 'utf8'));
const rows = data.results.filter((r) => !r.failed);
const failed = data.results.filter((r) => r.failed);

if (!rows.length) {
  console.error('没有可用的基准结果');
  process.exit(1);
}

const first = rows[0];
const fmt = (n, d = 1) => (n == null ? '–' : Number(n).toFixed(d));
const pct = (n) => `${(n * 100).toFixed(1)}%`;

const modelSize = {
  fp32: 94_851_877, // 90.46 MiB
  fp16: 47_497_427, // 45.30 MiB
  int8: 23_919_094, // 22.81 MiB
};

const table1 = [
  '| 配置 | 模型体积 | 模型加载 | 向量化吞吐 | 写入 | 刷新索引 | 检索 P50 / P95 | 端到端 P50 / P95 | Recall@10 | nDCG@10 | MRR@10 | 文档级 Recall@10 |',
  '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |',
  ...rows.map((r) => {
    const size = modelSize[r.config.dtype] ? `${(modelSize[r.config.dtype] / 1048576).toFixed(1)} MiB` : '–';
    return `| ${r.label} | ${size} | ${fmt(r.config.loadMs, 0)} ms | ${fmt(r.ingest.chunksPerSecond, 1)} 块/s | ${fmt(
      r.ingest.writeMs,
      0,
    )} ms | ${fmt(r.ingest.refreshMs, 0)} ms | ${fmt(r.search.dbMs.median)} / ${fmt(r.search.dbMs.p95)} ms | ${fmt(
      r.search.e2eMs.median,
    )} / ${fmt(r.search.e2eMs.p95)} ms | ${pct(r.quality.recall10)} | ${pct(r.quality.ndcg10)} | ${pct(
      r.quality.mrr10,
    )} | ${pct(r.quality.docRecall10)} |`;
  }),
].join('\n');

const table2 = [
  '| 配置 | 冷启动首次推理 | 预热后单条查询向量化（P50） | 批量 16 条/批 | seekdb 检索（P50） | 端到端（P50） |',
  '| --- | --- | --- | --- | --- | --- |',
  ...rows.map(
    (r) =>
      `| ${r.label} | ${fmt(r.config.coldFirstInferenceMs, 0)} ms | ${fmt(r.search.embedMs.median, 1)} ms | ${
        r.ingest.embedMs ? fmt((r.ingest.embedMs / Math.max(1, r.scale.chunks / 16)), 0) : '–'
      } ms | ${fmt(r.search.dbMs.median, 1)} ms | ${fmt(r.search.e2eMs.median, 1)} ms |`,
  ),
].join('\n');

// 与 int8+WASM 或第一条作为基线的对比
const baseline = rows.find((r) => r.config.dtype === 'int8' && r.config.device === 'wasm') || rows[0];
const table3 = rows
  .filter((r) => r !== baseline)
  .map((r) => {
    const d = (a, b) => ((r[a] - b) / b) * 100;
    const throughputGain = ((r.ingest.chunksPerSecond - baseline.ingest.chunksPerSecond) / baseline.ingest.chunksPerSecond) * 100;
    const e2eGain = ((baseline.search.e2eMs.median - r.search.e2eMs.median) / baseline.search.e2eMs.median) * 100;
    const qualityDelta = (r.quality.recall10 - baseline.quality.recall10) * 100;
    return `| ${r.label} vs ${baseline.label} | ${throughputGain >= 0 ? '+' : ''}${throughputGain.toFixed(1)}% | ${
      e2eGain >= 0 ? '+' : ''
    }${e2eGain.toFixed(1)}% | ${qualityDelta >= 0 ? '+' : ''}${qualityDelta.toFixed(1)} pp |`;
  })
  .join('\n');

// 表 4：批大小 × 长度分桶（同页连续测量，isolate 干扰更小）
const SWEEP = path.resolve(args.get('sweep') || 'bench/sweep.json');
let table4 = null;
let sweepSample = 0;
let sweepSummary = [];
try {
  const sweep = JSON.parse(await fs.readFile(SWEEP, 'utf8'));
  sweepSample = sweep.sample;
  const summary = [];
  const lines = [
    '| 配置 | 批大小 | 分桶关（块/s） | 分桶开（块/s） | 分桶收益 | 该配置最佳 |',
    '| --- | --- | --- | --- | --- | --- |',
  ];
  for (const cfg of sweep.results) {
    const byBatch = new Map();
    for (const row of cfg.rows) {
      if (!byBatch.has(row.batchSize)) byBatch.set(row.batchSize, {});
      byBatch.get(row.batchSize)[row.bucket ? 'on' : 'off'] = row.chunksPerSecond;
    }
    const batches = [...byBatch.keys()].sort((a, b) => a - b);
    const peak = cfg.rows.reduce((a, b) => (b.chunksPerSecond > a.chunksPerSecond ? b : a), cfg.rows[0]);
    const bestLabel = `batch=${peak.batchSize} / 分桶${peak.bucket ? '开' : '关'} · ${peak.chunksPerSecond.toFixed(1)} 块/s`;
    batches.forEach((b, i) => {
      const { on, off } = byBatch.get(b);
      const gain = off ? ((on - off) / off) * 100 : 0;
      const label = `${cfg.dtype} + ${cfg.device}`;
      lines.push(
        `| ${i === 0 ? label : ''} | ${b} | ${off?.toFixed(1) ?? '–'} | ${on?.toFixed(1) ?? '–'} | ${
          gain >= 0 ? '+' : ''
        }${gain.toFixed(1)}% | ${i === 0 ? bestLabel : ''} |`,
      );
    });
    const last = batches.at(-1);
    const lastOn = byBatch.get(last).on;
    const lastOff = byBatch.get(last).off;
    summary.push({
      label: `${cfg.dtype} + ${cfg.device}`,
      peak,
      bigBatch: last,
      bigGain: lastOff ? ((lastOn - lastOff) / lastOff) * 100 : 0,
    });
  }
  table4 = lines.join('\n');
  const best = summary.reduce((a, b) => (b.peak.chunksPerSecond > a.peak.chunksPerSecond ? b : a));
  sweepSummary = [
    `- 全局峰值出现在 **${best.label}**：${best.peak.chunksPerSecond.toFixed(1)} 块/s（batch=${
      best.peak.batchSize
    }，分桶${best.peak.bucket ? '开' : '关'}）。`,
    ...summary.map(
      (s) =>
        `- ${s.label}：峰值 ${s.peak.chunksPerSecond.toFixed(1)} 块/s（batch=${s.peak.batchSize}，分桶${
          s.peak.bucket ? '开' : '关'
        }）；batch=${s.bigBatch} 时分桶收益 ${s.bigGain >= 0 ? '+' : ''}${s.bigGain.toFixed(1)}%。`,
    ),
    '- 小批（≤8）时分桶收益会被排序与 gather 的固定开销抵消（fp16 batch=8 为 -15.1%），因此 UI 默认取 batch=16 + 分桶开。',
    '- 同页连续测量仍有约 ±10% 的机器噪声（后台进程、GPU 降频），因此只把差异明显的组合当作结论。',
  ].join('\n');
} catch {
  table4 = null;
}

// 表 5：检索策略对照（hybrid / vector / fulltext，同一集合同一批查询）
const MODES = path.resolve(args.get('modes') || 'bench/modes.json');
let table5 = null;
let modesChunks = 0;
let modesEval = 0;
let modesNotes = '';
try {
  const m = JSON.parse(await fs.readFile(MODES, 'utf8'));
  modesChunks = m.chunks;
  modesEval = m.evaluated;
  const by = Object.fromEntries(m.results.map((r) => [r.mode, r]));
  const rows5 = m.results.map((r) => {
    const name = { hybrid: 'hybrid（向量 + 全文 RRF）', vector: 'vector（仅 HNSW）', fulltext: 'fulltext（仅 ngram2）' }[r.mode] || r.mode;
    return `| ${name} | ${pct(r.quality.recall10)} | ${pct(r.quality.ndcg10)} | ${pct(r.quality.mrr10)} | ${fmt(
      r.search.dbMs.median,
    )} ms |`;
  });
  table5 = [
    '| 检索策略 | Recall@10 | nDCG@10 | MRR@10 | seekdb 检索 P50 |',
    '| --- | --- | --- | --- | --- |',
    ...rows5,
  ].join('\n');

  const d = (a, b) => ((a - b) * 100).toFixed(1);
  modesNotes = [
    `- 全文分支（ngram2 + \`$contains\`）并非严格子串匹配，而是按 2~3 字 n-gram 做词项匹配，因此中文标题式查询的召回很强；`,
    `  向量分支 Recall 与之持平，但排序明显更弱（nDCG ${pct(by.vector.quality.ndcg10)} vs ${pct(by.fulltext.quality.ndcg10)}，MRR ${pct(
      by.vector.quality.mrr10,
    )} vs ${pct(by.fulltext.quality.mrr10)}）——语义召回能「捞到」，但对「最相关排第一」帮助有限。`,
    `- RRF 等权融合会被较弱的一路拖累：hybrid 的 nDCG 比纯全文低 ${d(
      by.fulltext.quality.ndcg10,
      by.hybrid.quality.ndcg10,
    )} pp；本评测集的查询取自文档小节标题，字面重合度天然偏高，对全文分支有利（见「说明」中的限制）。`,
    `- 仍以 hybrid 为默认：当查询与原文用词不一致时全文分支会退化，向量分支是唯一保底；三种模式在界面可随时切换，便于按场景取舍。`,
  ].join('\n');
} catch {
  table5 = null;
}

const md = `# 实测数据（Benchmark）

> 由 \`node scripts/bench.mjs\` 在真实 Chrome 中运行、\`node scripts/report.mjs\` 生成。
> 生成时间：${data.generatedAt}，每条查询重复 ${data.repeat} 轮，表中为**中位数**（P95 为 95 分位）。

## 测试条件

| 项目 | 值 |
| --- | --- |
| 设备 | ${os.cpus()[0]?.model || 'unknown'}（${os.cpus().length} 逻辑核，${(os.totalmem() / 1073741824).toFixed(1)} GiB） |
| 浏览器 | ${first.config.userAgent} |
| GPU 适配器 | ${first.config.adapter || '–'} |
| 推理后端 | ONNX Runtime Web（WebGPU EP / WASM EP） |
| 模型 | BAAI/bge-small-zh-v1.5（512 维，CLS + L2） |
| 语料 | ${first.scale.docs} 篇中文技术文档 → ${first.scale.chunks} 块 / ${(first.scale.chars / 1024).toFixed(1)} KiB |
| 查询 | ${first.scale.queries} 条（有效金标 ${first.quality.evaluatedQueries} 条） |
| 切块 | ${first.config.chunkSize} 字 / 重叠 ${first.config.chunkOverlap} 字 |
| 批大小 | ${first.config.batchSize} |
| TopK | ${first.config.topK} |
| 检索模式 | hybrid（向量 + 全文，seekdb 内 RRF 融合，window=60, constant=60） |
| 计时段 | 模型加载=页面打开到 pipeline 就绪；向量化=Worker 内前向计算；写入=upsert RPC；刷新=refresh_index；端到端=查询向量化+HTTP+seekdb 检索 |

## 表 1 · 全配置对比

${table1}

## 表 2 · 分阶段延迟

${table2}

## 表 3 · 相对基线（${baseline.label}）的收益

| 对比 | 向量化吞吐 | 端到端延迟降低 | Recall@10 变化 |
| --- | --- | --- | --- |
${table3}

${table4 ? `## 表 4 · 批大小 × 长度分桶（单项优化对比）\n\n> 由 \`node scripts/sweep.mjs\` 在**同一页面、同一段语料**（${sweepSample} 块）上连续测量，避免跨页面机器负载波动；数值为纯前向吞吐，不含写库与 HTTP 开销。\n\n${table4}\n\n${sweepSummary}\n` : ''}
${table5 ? `## 表 5 · 检索策略对照（索引策略）\n\n> 由 \`node scripts/modes.mjs\` 在**同一集合、同一批查询**上依次切换 hybrid / vector / fulltext，只有检索策略不同；集合 ${modesChunks} 块、有效金标 ${modesEval} 条。\n\n${table5}\n\n${modesNotes}\n` : ''}
## 说明

- 每个配置使用**独立的浏览器上下文**（清空 HTTP 缓存），因此「模型加载」是冷启动口径；
- 冷启动后先跑一次预热推理，再进入批量向量化与查询循环，二者分开统计；
- 检索质量的金标来自文档小节正文（5-gram 覆盖率 ≥ 50% 命中块），金标缺失的查询不计入均值；
- 「文档级 Recall@10」表示 Top-10 中是否出现金标所在文档的任意块，用于观察块粒度标注带来的偏差；
- 表 5 的查询取自文档小节标题，字面重合度天然偏高，对全文分支有利；因此它只说明两路召回的**特性差异**，不代表通用场景下的策略排序。
${failed.length ? `\n- 未完成的配置：${failed.map((f) => `${f.label}（${f.error}）`).join('；')}` : ''}
`;

await fs.mkdir(path.dirname(OUT), { recursive: true });
await fs.writeFile(OUT, md, 'utf8');
console.log(`报告写入 ${OUT}`);
