#!/usr/bin/env node
/**
 * 从 ModelScope 拉取 sentence-transformers/all-MiniLM-L6-v2 的 ONNX 权重。
 *
 * 说明：
 *  - HuggingFace 在部分网络环境下不可达，因此默认从 ModelScope 镜像（Xenova/all-MiniLM-L6-v2）
 *    下载同一份 Transformers.js 格式的 ONNX 产物（权重同源，均为 sentence-transformers 官方导出的
 *    all-MiniLM-L6-v2，384 维）。
 *  - 默认模型 bge-small-zh-v1.5（BAAI，512 维，中文检索强，INT8 后 22.8 MiB）；
 *    可用 MODEL_ID 环境变量切换，例如 MODEL_ID=Xenova/all-MiniLM-L6-v2。
 *  - 如果想改用 HuggingFace 官方源，设置 HF=1 即可。
 *
 * 用法：node scripts/fetch-model.mjs [--out web/public/models/all-MiniLM-L6-v2]
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { createWriteStream } from 'node:fs';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';

const MODEL_ID = process.env.MODEL_ID || 'Xenova/bge-small-zh-v1.5';
const OUT = process.argv.includes('--out')
  ? process.argv[process.argv.indexOf('--out') + 1]
  : path.resolve(`web/public/models/${(process.env.MODEL_ID || 'Xenova/bge-small-zh-v1.5').split('/').pop()}`);

const MIRROR = process.env.MS_HOST || 'https://www.modelscope.cn';
const HF = process.env.HF === '1';

const FILES = [
  'config.json',
  'tokenizer.json',
  'tokenizer_config.json',
  'special_tokens_map.json',
  'vocab.txt',
  'onnx/model.onnx',
  'onnx/model_fp16.onnx',
  'onnx/model_quantized.onnx',
];

function urlFor(file) {
  if (HF) return `https://huggingface.co/${MODEL_ID}/resolve/main/${file}`;
  return `${MIRROR}/api/v1/models/${MODEL_ID}/repo?Revision=master&FilePath=${encodeURIComponent(file)}`;
}

async function download(file) {
  const dest = path.join(OUT, file);
  await fs.mkdir(path.dirname(dest), { recursive: true });
  const tmp = `${dest}.part`;
  const res = await fetch(urlFor(file), { redirect: 'follow' });
  if (!res.ok) throw new Error(`${file}: HTTP ${res.status}`);
  await pipeline(Readable.fromWeb(res.body), createWriteStream(tmp));
  await fs.rename(tmp, dest);
  const st = await fs.stat(dest);
  return st.size;
}

const started = Date.now();
let total = 0;
for (const f of FILES) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const size = await download(f);
      total += size;
      console.log(`  ${f.padEnd(32)} ${(size / 1048576).toFixed(2)} MiB`);
      break;
    } catch (err) {
      if (attempt === 3) throw err;
      console.warn(`  retry ${attempt} ${f}: ${err.message}`);
      await new Promise((r) => setTimeout(r, 800 * attempt));
    }
  }
}
console.log(`\n模型就绪：${OUT}`);
console.log(`合计 ${(total / 1048576).toFixed(2)} MiB，用时 ${((Date.now() - started) / 1000).toFixed(1)} s`);
