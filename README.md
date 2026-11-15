# pdf-page-anchored-extraction

**扫描版与文字层 PDF → 带印刷页码的纯文本。**

这个仓解决的不是 OCR，是**页码**。

OCR 出的字谁都能做。学术引用要的是「第几卷、第几页」——页码一丢，这套文本只能读，不能引。

市面上 PDF → 文本的工具一律丢掉页码。这批脚本花了两年时间在补这一件事。

## 核心问题

一本 400 页的书：

- PDF 页序号 1 是封面，不是正文第 1 页
- 正文前有版权页、目录、序，页码用罗马数字
- 左右页的页眉位置不同，页码在奇数页靠右、偶数页靠左
- 跨页断句：一页末尾断在半句，下一页接着
- 双栏排版，抽出来是左右交错
- 有的 PDF 文字层被拆成「一 字 一 行」
- 有的扫描件页眉印在版心内，跟正文混在一起

每一条都会让「这一页是书上第几页」这个信息失效。这批脚本就是逐条对付它们。

## 脚本

### 抽取

| 文件 | 干什么 |
|---|---|
| `deep_read.py` | 文档深度解析：PDF/docx/doc/pptx/xlsx/odt → 无损全文。有文字层直接抽，Office 走 LibreOffice 转 PDF 再抽，**只有扫描件才提示要 OCR** |
| `pdf2txt_paged.py` | 带文字层 PDF → 「实测印刷页码 + 正文」纯文本 |
| `ocr_pdf_paged.py` | 纯扫描件 PDF → 带页码标记 txt（RapidOCR 本地 ONNX，不联网） |
| `pdf_render_pages.py` | PDF 逐页渲染灰度 PNG，喂给 OCR |
| `ocr_pages.py` | 批量识别 PNG 页 → 带页码标记全文。增量落盘 + 断点续跑 |
| `rapid_ocr_jsonl.py` | 页图 → **行级 JSONL**（带坐标，重排页码靠它） |
| `rapid_ocr_parallel.py` | 页级并行版 |
| `ocr_parallel.py` | 多进程调度：分片跑 `ocr_pages.py`，按页码合并 |
| `ocr_batch.py` | 批量：扫描件 PDF → 分层目录里的带页码 txt |
| `ocr_supervisor.py` | OCR 队列看门狗（长时间批次不断） |
| `ocr_jsonl2txt.py` | `.ocr.jsonl` → 标准 txt（带页码标记） |
| `merge_ocr_full.py` | 多份 OCR 输出合并成全书，校验页数完整性 |

### 页码标定

| 文件 | 干什么 |
|---|---|
| `calib_offset.py` | 自动标定「PDF 页序号 → 印张页码」的偏移 |
| `fix_page_offset.py` | 用实测稳定偏移补全缺失页码 |
| `fix_header_folio.py` | 从页眉补页码（左右页交替那种） |
| `refolio.py` | 从 OCR jsonl 坐标**重新判定**印刷页码并重写 txt |
| `page_audit.py` | 全库页码标定审计（按覆盖率分级，不按「全或不全」） |
| `page_report.py` | 生成逐卷页码率台账 |
| `page_classify.py` | 把「不可引用的页」和「乱码页」分开 |

### 版式修复

| 文件 | 干什么 |
|---|---|
| `clean_inline_header.py` | 清「印在版心内的页码 + 书名眉」 |
| `clean_split_header.py` | 清「被页标记劈开的页眉」 |
| `strip_layout_noise.py` | 清抽取器带进来的版式垃圾 |
| `strip_pmark.py` | 清网页版文件里的伪页码 |
| `join_page_breaks.py` | **跨页断句拼接**（一页末尾断在半句） |
| `reflow_text.py` | 修复「文字层被拆成一字一行」的 PDF |
| `fix_punct_map.py` | 修复「标点被字体映射成 ASCII」 |
| `split_columns.py` | 双栏文本左右栏切分 |

## 用法

每个脚本都有 `--help` 或文件头 docstring。典型链路：

```bash
# 1. 判定有无文字层
python deep_read.py book.pdf probe.txt

# 2a. 有文字层
python pdf2txt_paged.py book.pdf book.txt

# 2b. 扫描件
python pdf_render_pages.py book.pdf pages/
python ocr_pages.py pages/ ocr.txt
python ocr_pdf_paged.py book.pdf book_paged.txt

# 3. 修版式
python strip_layout_noise.py book.txt book_clean.txt
python join_page_breaks.py book_clean.txt book_joined.txt

# 4. 校准页码
python calib_offset.py book_joined.txt
python page_audit.py book_joined.txt
```

## 路径

原版脚本里写的是作者本机的绝对路径，本仓**已替换成占位符**：

| 占位符 | 含义 |
|---|---|
| `YOUR_USERNAME` | 你的 Windows 用户名 |
| `G:\path\to\knowledge-base` | 你的语料根目录 |

跑之前改成本机实际路径，或改成命令行参数。

## 依赖

- Python 3.10+
- `pymupdf`（PDF 文字层抽取与渲染）
- `rapidocr-onnxruntime`（OCR，本地 ONNX，不需要联网、不需要 GPU）
- LibreOffice（仅 `deep_read.py` 处理 Office 格式时需要，可用环境变量 `DSH_SOFFICE` 指定 `soffice.exe`）

## 精度声明

- OCR 结果**一定有错字**。本仓只负责「把字和页码取出来」，错字审计见姊妹仓 [`chinese-ocr-audit`](https://github.com/sjy888888/chinese-ocr-audit)。
- 页码是「实测」的，不是猜的：能从页眉读到就用页眉，读不到就用标定偏移，两者冲突时人工复核。
- **引用前必须回纸本或官方电子版核对。** 本仓产出的是工作文本，不是善本。

## 姊妹仓

- [chinese-ocr-audit](https://github.com/sjy888888/chinese-ocr-audit) —— 中文 OCR 错字审计
- [citation-verification](https://github.com/sjy888888/citation-verification) —— 引文三态核验
- [marx-engels-historisch-kritische-gesamtausgabe-index](https://github.com/sjy888888/marx-engels-historisch-kritische-gesamtausgabe-index) —— 马克思恩格斯历史考证版卷目索引

## 许可

MIT
