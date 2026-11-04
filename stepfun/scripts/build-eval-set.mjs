#!/usr/bin/env node
/**
 * 生成评测集：语料（技术文档去掉代码块后截断）+ 查询（文档小节标题）+ 金标锚点。
 *
 * 金标由浏览器端基准脚本按 5-gram 覆盖率计算（见 web/src/bench.ts），
 * 因此这里只需要给出小节正文锚点，避免把切块逻辑复制两份。
 *
 * 用法：node scripts/build-eval-set.mjs
 */
import fs from 'node:fs/promises';
import path from 'node:path';

const DOCS_ROOT = path.resolve('../docs');
const OUT = path.resolve('web/public/eval/eval-set.json');

const DOCS = [
  'zh/development/memory-layer.md',
  'zh/development/plugin-contract.md',
  'zh/development/layered-skills.md',
  'zh/development/scope-source-discovery.md',
  'zh/development/pydantic-ai-inference.md',
  'zh/development/integration-guidance.md',
  'zh/development/remote-access-implementation.md',
  'zh/development/desktop-delivery-plan.md',
  'zh/docs/develop/http-api.md',
  'zh/docs/get-started/configure-models.md',
  'zh/docs/get-started/configure-server-environment.md',
  'zh/design/1557_recurring_failure_repair_design.md',
  'zh/rfcs/0014_memory_layer_design.md',
  'zh/rfcs/1560_recall_sufficiency_gate.md',
  'zh/rfcs/1417_topic_memory.md',
  'zh/benchmarks/index.md',
  'zh/rfcs/0002_core_sdk_product_model.md',
  'zh/rfcs/0016_pydantic_ai_inference_integration.md',
  'zh/rfcs/0019_local_source_memory_runtime.md',
  'zh/rfcs/0028_context_pack.md',
  'zh/rfcs/0080_memory_search_reranking.md',
  'zh/rfcs/0081_end_to_end_evaluation_architecture.md',
  'zh/docs/operate/index.md',
  'zh/docs/workflows/index.md',
];

const MAX_DOC_CHARS = 4200;
const MAX_SECTIONS_PER_DOC = 3;

function stripCode(text) {
  return text
    .replace(/```[\s\S]*?```/g, '\n')
    .replace(/~~~[\s\S]*?~~~/g, '\n')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s*\|.*\|\s*$/gm, '')
    .replace(/<!--[\s\S]*?-->/g, '');
}

function normalize(text) {
  return text.replace(/\r\n/g, '\n').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

function truncate(text, limit) {
  if (text.length <= limit) return text;
  const cut = text.slice(0, limit);
  const lastBreak = cut.lastIndexOf('\n\n');
  return lastBreak > limit * 0.6 ? cut.slice(0, lastBreak) : cut;
}

function sectionsOf(markdown) {
  const lines = markdown.split('\n');
  const sections = [];
  let current = { heading: null, body: [] };
  for (const line of lines) {
    const m = line.match(/^##\s+(.+?)\s*$/);
    if (m && !line.startsWith('###')) {
      if (current.heading && current.body.join('\n').trim().length > 160) {
        sections.push({ heading: current.heading, body: current.body.join('\n').trim() });
      }
      current = { heading: m[1], body: [] };
    } else if (!line.startsWith('###') && !line.startsWith('#')) {
      current.body.push(line);
    }
  }
  if (current.heading && current.body.join('\n').trim().length > 160) {
    sections.push({ heading: current.heading, body: current.body.join('\n').trim() });
  }
  return sections;
}

const corpus = [];
const queries = [];
let qid = 0;

for (const rel of DOCS) {
  const abs = path.join(DOCS_ROOT, rel);
  let raw;
  try {
    raw = await fs.readFile(abs, 'utf8');
  } catch {
    console.warn(`跳过（不存在）：${rel}`);
    continue;
  }
  const cleaned = normalize(stripCode(raw));
  if (cleaned.length < 400) continue;
  const body = truncate(cleaned, MAX_DOC_CHARS);
  corpus.push({ source: rel, text: body });

  const sections = sectionsOf(cleaned)
    .filter((s) => s.body.replace(/\s/g, '').length > 220)
    .slice(0, MAX_SECTIONS_PER_DOC);

  for (const s of sections) {
    const heading = s.heading.replace(/^\d+[.、)]\s*/, '').trim();
    if (heading.length < 3 || heading.length > 60) continue;
    // 金标锚点取小节正文的开头一到两句（<=160 字），保证能唯一落在一个块内
    const prose = normalize(s.body).replace(/\n+/g, ' ');
    const firstSentence = prose.split(/(?<=[。！？!?；;])/).slice(0, 2).join('');
    const anchor = (firstSentence || prose).slice(0, 160);
    if (anchor.replace(/\s/g, '').length < 60) continue;
    queries.push({
      id: `q${String(++qid).padStart(2, '0')}`,
      query: heading,
      anchor,
      doc: rel,
      type: 'section-title',
    });
  }
}

const payload = {
  meta: {
    builtAt: new Date().toISOString(),
    docs: corpus.length,
    queries: queries.length,
    note: '语料为仓库中文技术文档（去代码块、截断），查询为文档小节标题，金标由 5-gram 覆盖率匹配',
  },
  corpus,
  queries,
};

await fs.mkdir(path.dirname(OUT), { recursive: true });
await fs.writeFile(OUT, JSON.stringify(payload, null, 2), 'utf8');

const corpusChars = corpus.reduce((n, d) => n + d.text.length, 0);
console.log(`语料 ${corpus.length} 篇 / ${(corpusChars / 1024).toFixed(1)} KiB`);
console.log(`查询 ${queries.length} 条 → ${path.relative(process.cwd(), OUT)}`);
console.log(`平均每篇 ${(corpusChars / corpus.length).toFixed(0)} 字符，预计切块 ~${Math.round(corpusChars / 400)} 块`);
