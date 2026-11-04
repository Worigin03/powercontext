/**
 * 文本切块：按段落聚合到目标长度，超长段落按句子边界切分，块间保留字符级重叠。
 * 目的：让每个向量覆盖语义完整的片段，同时把单块控制在模型 512 token 上限之内
 * （bge-small-zh 按字切分，600 汉字约 600 token，超长尾部由 tokenizer 截断）。
 */

export type Chunk = {
  id: string;
  text: string;
  metadata: {
    source: string;
    doc_id: string;
    title: string;
    chunk_index: number;
    chars: number;
    imported_at: string;
  };
};

export type SourceDoc = { source: string; text: string };

function fnv1a(str: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(36).padStart(7, '0');
}

/** 句子切分：中英文句号/问号/换行 */
function splitSentences(text: string): string[] {
  return text
    .replace(/\r\n/g, '\n')
    .split(/(?<=[。！？!?；;])\s*|\n{2,}/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function chunkDoc(doc: SourceDoc, maxChars: number, overlap: number): Chunk[] {
  const paragraphs = doc.text.replace(/\r\n/g, '\n').split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
  const pieces: string[] = [];

  for (const para of paragraphs) {
    if (para.length <= maxChars) {
      pieces.push(para);
      continue;
    }
    let buf = '';
    for (const sent of splitSentences(para)) {
      if ((buf + sent).length > maxChars && buf) {
        pieces.push(buf.trim());
        buf = overlap > 0 ? buf.slice(-overlap) : '';
      }
      buf += (buf && !buf.endsWith('\n') ? ' ' : '') + sent;
    }
    if (buf.trim()) pieces.push(buf.trim());
  }

  // 合并过短的相邻片段，避免出现只有一句话的碎片
  const merged: string[] = [];
  for (const piece of pieces) {
    const last = merged[merged.length - 1];
    if (last && last.length + piece.length + 1 <= maxChars * 0.6) {
      merged[merged.length - 1] = `${last}\n${piece}`;
    } else {
      merged.push(piece);
    }
  }

  const docId = fnv1a(`${doc.source}::${doc.text.length}::${doc.text.slice(0, 64)}`);
  const title = doc.source.replace(/\.[^.]+$/, '').split(/[\\/]/).pop() || doc.source;
  const importedAt = new Date().toISOString();

  return merged.map((text, i) => ({
    id: `${docId}:${i}`,
    text,
    metadata: {
      source: doc.source,
      doc_id: docId,
      title,
      chunk_index: i,
      chars: text.length,
      imported_at: importedAt,
    },
  }));
}

export function chunkDocs(docs: SourceDoc[], maxChars: number, overlap: number): Chunk[] {
  const out: Chunk[] = [];
  for (const doc of docs) {
    if (!doc.text.trim()) continue;
    out.push(...chunkDoc(doc, maxChars, overlap));
  }
  return out;
}
