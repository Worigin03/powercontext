# 文脉 PaperTrail · 科研文献智能助手

把中文研究问题，直接读成英文论文里的可核验证据。

- 本地解析 PDF → 切块 → 向量化 → 写入 seekdb
- seekdb 内 HNSW 向量索引 + IK 全文索引，两路召回
- RRF 融合 + 句子级重排 + 按论文去重
- 每条结论绑定论文、页码、章节与原始句子

项目网站：https://papertrail-w961um0ylvy.qoder.website

## 目录

| 路径 | 说明 |
| --- | --- |
| `app/` | FastAPI 后端：解析入库、检索、问答、对比、综述、笔记 |
| `web/` | 原生 JS 工作台（总览 / 检索 / 问答 / 对比 / 综述 / 文献库 / 阅读页） |
| `scripts/seed_corpus.py` | 建表并写入 13 篇 arXiv 示例论文 |
| `showcase/` | Qoder Sites 展示站静态源码 |
| `提交材料.md` | 完整提交说明、seekdb 用法、性能与演示脚本 |

## 运行

需要 Python 3.11+ 与 Docker。

```bash
# seekdb
docker run -d --name seekdb --restart unless-stopped \
  -p 2881:2881 -v seekdb_data:/var/lib/seekdb \
  oceanbase/seekdb:latest

# 依赖
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt

# 语料与启动
python scripts/seed_corpus.py
python run.py
# → http://127.0.0.1:8600
```

首次运行会下载约 90 MB 的多语言嵌入模型，之后可离线使用。默认不调用大模型；配置 `PAPERTRAIL_LLM_BASE_URL`、`PAPERTRAIL_LLM_API_KEY`、`PAPERTRAIL_LLM_MODEL` 后可启用基于证据的综述生成。

## seekdb 用法

`chunks` 表同时维护两种索引：

```sql
FULLTEXT INDEX ft_content (content) WITH PARSER ik,
VECTOR INDEX vidx_vec (embedding)
  WITH (distance = cosine, type = hnsw, lib = vsag)
```

向量召回使用 `cosine_distance()`，全文召回使用 `MATCH ... AGAINST`（IK 分词），应用层用 RRF（k=60）融合。中文默认分词按空格切分会导致整段失效，因此必须显式指定 `WITH PARSER ik`。

## 数据与延迟

13 篇论文 / 305 页 / 2,220 个证据块 / 115 条中英术语；混合检索典型 88–305 ms，问答 0.7–1.3 s（含句子重排）。

## 作者

王起源 · 物联242 · U202442553 · 2026 OceanBase 数据库大赛
