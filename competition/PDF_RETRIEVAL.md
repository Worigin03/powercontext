# 财报 PDF 全文解析与证据页检索

`powercontext.sources.pdf_pages` 从完整原 PDF 解析全部物理页，并保留既有页级 Source、
标准文本投影和精确 Memory 引用契约。检索只使用已打开 scoped Memory 的公开 API。

## 检索方式

1. 规范化财政年份、所有格和常见财务缩写，生成最多 24 路有界查询。
2. 汇总各路真实 `Memory.search` 候选，再用词形、BM25、文档标识中的主体与年份信息统一重排。
3. 根据问题需要覆盖损益表、现金流量表和资产负债表；区分正文报表、目录引用及其他综合收益表。
4. 对已召回的物理相邻续页保留少量位置。仅在存在续页候选时读取一次 `Memory.list`，
   按完整文档身份、PDF 摘要和物理页码判断相邻关系。
5. 使用真实 `Memory.get` 校验最终引用，按完整页级 Source 身份去重并遵守返回上限。

每路候选上限随调用方 limit 调整，在 `limit=20` 时为 320。当前次候选在问题处理结束后释放，
不保留跨 scope、数据库或 Memory head 的全局结果缓存。

完整 Source 身份保留 PDF 修订，因此相同文档页码的不同 PDF 修订互不覆盖；
长文档 ID 使用的摘要形式也可正常返回。排序用的词形规范化不会改写解析正文、存储内容或引用。

金融术语映射是通用查询处理，不包含特定公司、财报、问题、数字答案或标注页码。
产品代码不读取 FinanceBench 答案、证据标注、预解析文件或判题资产。

## 验证

- 完整 503 页公开财报：Linux Python 3.12 公开验收 6 项通过，包含全部页面入库、重复提交、
  重启、原始问题检索和精确引用。
- 新增 7 项真实 Runtime 回归：长文档摘要身份、修订身份、法律问法、跨公司干扰、
  目录/综合收益表干扰、经营地域及重启后物理续页和 limit。
- 修复前的检索函数在前四项新增用例上可复现失败；修复后的实现通过相应用例。
- Ruff 和针对 PDF 模块的 ty 检查通过。

使用已持久化的 503 页原始公开财报及五个原始问题检查耗时，`limit=20` 时每问题约
3.2–4.6 秒。多路召回与条件性的完整 Memory 列表读取增加了开销；未据此宣称检索加速。
该检查不读取答案或证据页标注，不能代替平台私有语料的解析质量与 Recall@20 验收。

```bash
make -C competition test-pdf
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src python -m pytest -q \
  -p pytest_asyncio.plugin -o asyncio_mode=auto tests/test_pdf_page_search.py
```
