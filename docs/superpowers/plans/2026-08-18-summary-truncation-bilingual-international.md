# 完整句摘要与国际媒体双语简报 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让摘要不再以半句话截断，并让扩展相关性范围内的国际媒体在 Word 中同时显示英文与中文摘要。

**Architecture:** `summarizer.py` 提供完整句收束和元数据翻译；`main.py` 复用现有翻译适配钩子；`international.py` 用配置中的中美关系成对关键词给出可审计分类；`word_digest.py` 渲染双语字段。翻译失败时保留英文元数据并说明限制，不抓取国际媒体正文。

**Tech Stack:** Python 3、python-docx、PyYAML、现有 `DeepSeekClient`、pytest。

**Spec:** `docs/superpowers/specs/2026-08-18-summary-truncation-and-bilingual-international-design.md`

## Global Constraints

- 国际翻译只传标题、已采集摘要与来源名，禁止抓取正文。
- `INTERNATIONAL_TRANSLATION_ENABLED=true` 时才允许调用 DeepSeek；单元测试不得访问网络。
- 150 字为摘要目标长度，不允许为了严格长度从半句处切断。
- 不改变飞书禁用策略；验收仅生成本地 Word。

### Task 1: 完整句摘要收束

**Files:** `app/summarizer.py`, `tests/test_summarizer.py`

**Interfaces:** 新增 `truncate_to_complete_sentence(text: str, max_length: int) -> str` 和 `summary_needs_rewrite(article: Article, max_length: int) -> bool`。

- [ ] **Step 1: 写失败测试**

```python
def test_clean_rss_summary_keeps_last_complete_sentence():
    text = "第一句完整。第二句很长" + "字" * 200
    assert clean_rss_summary(text, max_length=20) == "第一句完整。"

def test_clean_rss_summary_keeps_full_text_without_sentence_boundary():
    text = "字" * 30
    assert clean_rss_summary(text, max_length=20) == text

def test_parse_summaries_response_does_not_cut_mid_sentence():
    result = parse_summaries_response({"u": "完整第一句。" + "字" * 200}, {"u"}, max_length=20)
    assert result["u"] == "完整第一句。"
```

- [ ] **Step 2: 运行测试确认失败**

`python -m pytest tests/test_summarizer.py -q`

- [ ] **Step 3: 写最小实现**

```python
SENTENCE_ENDINGS = "。！？!?"

def truncate_to_complete_sentence(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    ends = [index for index, char in enumerate(text[:max_length]) if char in SENTENCE_ENDINGS]
    return text[:ends[-1] + 1].rstrip() if ends else text
```

让 RSS 和 LLM 解析都调用该函数；以 `…` 结尾的历史硬截断摘要进入既有 DeepSeek 重写候选。超过目标但句子完整的摘要直接保留，避免每次导出重复调用模型；模型失败时保留完整原文。

- [ ] **Step 4: 运行测试确认通过**

`python -m pytest tests/test_summarizer.py -q`

### Task 2: 外媒元数据翻译与双语渲染

**Files:** `app/summarizer.py`, `app/main.py`, `app/word_digest.py`, `.env.example`, `tests/test_international_wiring.py`, `tests/test_word_digest.py`

**Interfaces:** 新增 `translate_metadata(title: str, summary: str | None, *, source_name: str) -> tuple[str, str]`，由 `_build_international_translator()` 自动适配为 `InternationalNewsTranslator`。

- [ ] **Step 1: 写失败测试**

```python
def test_word_international_article_contains_bilingual_summaries(tmp_path, cfg):
    article = make_article("US approves arms sales to Taiwan", "Reuters", "u1", summary="The package was announced in Washington.")
    translated = TranslationResult("美國批准對台軍售", "華府宣布對台軍售方案。", "translated", None, 0)
    output = build_word_digest([article], tmp_path, international_config=cfg, international_translations={"u1": translated})
    texts = [p.text for p in Document(output).paragraphs]
    assert "英文摘要：The package was announced in Washington." in texts
    assert "中文摘要：華府宣布對台軍售方案。" in texts
```

```python
def test_translate_metadata_uses_only_supplied_metadata(monkeypatch):
    class FakeClient:
        def analyze(self, system, user):
            assert "https://" not in user
            return {"status": "success", "title": "中文標題", "summary": "中文摘要。"}
    monkeypatch.setattr(summarizer, "_load_deepseek_client", lambda: FakeClient())
    assert summarizer.translate_metadata("English title", "English teaser", source_name="Reuters") == ("中文標題", "中文摘要。")
```

- [ ] **Step 2: 运行测试确认失败**

`python -m pytest tests/test_international_wiring.py tests/test_word_digest.py -q`

- [ ] **Step 3: 写最小实现**

`translate_metadata` 用现有 `DeepSeekClient.analyze()` 提交严格 JSON 的 `{title, summary, source_name}`，验证 `title` 和 `summary` 均为非空字符串。未启用开关、无密钥或模型失败时抛出错误，由现有 `translate_article` 输出英文 fallback。Word 国际栏目固定输出 `英文摘要：`；仅在翻译成功时输出 `中文摘要：`，否则保留限制说明。`.env.example` 新增 `INTERNATIONAL_TRANSLATION_ENABLED=false`。

- [ ] **Step 4: 运行测试确认通过**

`python -m pytest tests/test_international_wiring.py tests/test_word_digest.py tests/test_summarizer.py -q`

### Task 3: 扩展中美关系筛选并验收

**Files:** `config/international_media.yaml`, `app/international.py`, `tests/test_international.py`, `docs/INTERNATIONAL_MEDIA_OPERATOR_GUIDE.md`

**Interfaces:** 配置新增 `china_us_china_entities` 和 `china_us_us_entities`；双组均命中时返回 `RelevanceDecision(tier="china_us", relevant=True, ...)`。

- [ ] **Step 1: 写失败测试**

```python
def test_china_us_relationship_is_relevant(cfg):
    result = classify_international("China and the United States resume trade talks", None, "Reuters", cfg)
    assert result.relevant is True
    assert result.tier == "china_us"

def test_china_only_story_is_not_promoted_to_china_us(cfg):
    result = classify_international("China credit growth slows", None, "Reuters", cfg)
    assert result.relevant is False
```

- [ ] **Step 2: 运行测试确认失败**

`python -m pytest tests/test_international.py -q`

- [ ] **Step 3: 写最小实现**

在 YAML 增加中国实体 `[China, Chinese, Beijing, Xi Jinping, CCP]` 和美国实体 `[United States, US, U.S., Washington, White House, Trump, State Department, Pentagon, Congress]`。在 `evaluate_relevance` 中于台湾直接相关之后、既有宽泛 China/US 判断之前要求两组同时命中，返回 `china_us`，并把命中词写入审计原因。

- [ ] **Step 4: 全量回归与本地 Word 验收**

依次运行：

```text
python -m pytest tests/test_international.py tests/test_international_wiring.py tests/test_summarizer.py tests/test_word_digest.py -q
python -m pytest -q
set DISABLE_FEISHU_SEND=1 && set NOTIFIER=console && python -m app.main --export-word 120
```

验收新 Word：至少一条国际媒体含英文摘要和中文摘要，所有梗概均不以被截断的半句省略号结束，且没有飞书发送日志。

### Version Control

每个任务完成后应提交对应代码和测试；若当前目录仍未识别为 Git 仓库，则只记录该限制，绝不伪造提交。
