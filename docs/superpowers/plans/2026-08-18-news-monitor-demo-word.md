# 台湾新闻监测系统功能演示 Word Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成一份可向业务用户演示新闻监测系统完整能力的 Word 文档。

**Architecture:** 新建一个独立的 Word 构建脚本，从 `config/sources.yaml` 读取来源清单，再组合固定且明确标注的演示新闻。脚本不访问网络、不写新闻库、不调用通知服务；生成后使用文档渲染工具逐页检查布局。

**Tech Stack:** Python 3、PyYAML、python-docx、LibreOffice 文档渲染工具。

**Spec:** `docs/superpowers/specs/2026-08-18-news-monitor-demo-word-design.md`

## Global Constraints

- 所有新闻均标注“演示样例，非实时监测结论”。
- 来源清单从当前 `config/sources.yaml` 读取；国际筛选范围遵循 `config/international_media.yaml`。
- 不触发采集、不写入新闻数据库、不发送任何通知。
- 国际样例只展示标题和摘要，不声称抓取国际正文。
- Word 必须渲染成页面图片并逐页检查，无裁切、重叠或表格溢出。

---

### Task 1: 构建演示内容模型与来源矩阵

**Files:**
- Create: `scripts/build_news_monitor_demo.py`
- Read: `config/sources.yaml`, `config/international_media.yaml`

**Interfaces:**
- Consumes: YAML 中每个来源的 `id`、`name`、`type`、`enabled`、`category` 或 `default_category`。
- Produces: `load_source_rows(path: Path) -> list[dict]`，返回按展示分组的来源行；`demo_articles() -> dict[str, list[dict]]`，返回政治、财经、军武和国际示例。

- [ ] **Step 1: 写入来源读取和演示数据函数**

```python
def load_source_rows(path: Path) -> list[dict]:
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(config.get("sources", []))

def demo_articles() -> dict[str, list[dict]]:
    return {
        "政治": [{"source": "中央社", "title": "演示样例标题", "summary": "演示摘要为完整句。"}],
        "财经": [{"source": "自由時報", "title": "演示样例标题", "summary": "演示摘要为完整句。"}],
        "军武": [{"source": "自由时报·军武", "title": "演示样例标题", "summary": "演示摘要为完整句。"}],
        "国际": [],
    }
```

- [ ] **Step 2: 运行脚本的内容自检模式**

Run: `python scripts/build_news_monitor_demo.py --check-content`

Expected: 输出启用来源总数、已停用来源数，以及政治、财经、军武和五类国际议题的样例数量；所有摘要均以句末标点结束。

### Task 2: 生成正式业务简报风格的 Word 演示文档

**Files:**
- Create: `scripts/build_news_monitor_demo.py`
- Create: `data/reports/台湾新闻监测系统功能演示_2026-08-18.docx`

**Interfaces:**
- Consumes: Task 1 的来源行和示例文章。
- Produces: `build_demo_docx(output_path: Path, source_rows: list[dict]) -> Path`。

- [ ] **Step 1: 定义版式与固定说明**

```python
doc.sections[0].page_width = Mm(210)
doc.sections[0].page_height = Mm(297)
doc.sections[0].top_margin = Mm(18)
doc.sections[0].bottom_margin = Mm(16)
```

封面、页眉、页脚均显示“台湾新闻监测系统功能演示”及“演示样例，非实时监测结论”。

- [ ] **Step 2: 写入系统流程、来源矩阵和分类新闻样例**

以流程箭头段落呈现：`多源采集 → 去重与过滤 → 完整句摘要 → 国际相关性筛选 → Word / 通知输出`。来源矩阵按官方来源、台湾媒体、军武、国际媒体、授权通讯源分组，逐条显示来源名、采集方式和启用状态。

- [ ] **Step 3: 写入五类国际双语样例**

每条均输出：

```text
英文原题：...
英文摘要：...
中文摘要：...
```

五类依次为台湾直接相关、两岸、台美、中美、台湾与其他国家关系；附注“仅依据已采集标题与导语生成中文摘要，不抓取受限正文”。

- [ ] **Step 4: 运行生成脚本**

Run: `python scripts/build_news_monitor_demo.py --output data/reports/台湾新闻监测系统功能演示_2026-08-18.docx`

Expected: 输出生成路径、来源统计和示例统计；不出现采集、数据库写入或通知发送日志。

### Task 3: 结构与版式验收

**Files:**
- Read: `data/reports/台湾新闻监测系统功能演示_2026-08-18.docx`
- Create: `data/reports/demo_render/`（内部核验图片）

**Interfaces:**
- Consumes: Task 2 生成的 DOCX。
- Produces: 每页 PNG 和结构核验结果。

- [ ] **Step 1: 结构核验**

Run: `python scripts/build_news_monitor_demo.py --verify data/reports/台湾新闻监测系统功能演示_2026-08-18.docx`

Expected: 确认存在全部来源矩阵、四类新闻样例、五类国际议题、英文原题/英文摘要/中文摘要字段，以及演示声明。

- [ ] **Step 2: 渲染文档**

Run: `python <documents-skill>/render_docx.py data/reports/台湾新闻监测系统功能演示_2026-08-18.docx --output_dir data/reports/demo_render`

Expected: 每一页生成一张 PNG。

- [ ] **Step 3: 逐页视觉检查**

检查封面、流程、来源表格、新闻样例、国际双语区和运行保障区；若发现裁切、重叠或表格溢出，调整脚本后重新生成和渲染。

### Version Control

当前目录不识别为 Git 仓库；保留脚本、设计和计划文件，不创建伪造提交。
