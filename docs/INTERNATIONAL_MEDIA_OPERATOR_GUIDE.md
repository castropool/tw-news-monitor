# 国际媒体生产操作指南

本指南只描述 Release Candidate 通过后的人工操作。Wave、测试、隔离 runner 和子代理永远不能启用生产源，也不能调用真实 Feishu。四个源在 RC 前均保持 `enabled: false`。

## 来源与合法边界

| 来源 | 合法入口 | access_level | 正文能力 | 当前状态 |
| --- | --- | --- | --- | --- |
| Reuters | 官方 news sitemap | `metadata_only` | 仅标题、URL、时间等公开 metadata | 本 RC 未执行 live；待独立隔离证据；生产开关关闭 |
| Financial Times | 官方 FT Alphaville RSS | `public` | RSS 提供的公开字段；不抓付费正文 | 本 RC 未执行 live；待独立隔离证据；生产开关关闭 |
| Wall Street Journal | 官方 Newsletter 目录 + Gmail | `newsletter` | 只读已收到的邮件条目；不抓文章正文 | Gmail `MAILBOX_AUTH_REQUIRED` |
| Bloomberg | 官方 Newsletter 目录 + Gmail | `newsletter` | 只读已收到的邮件条目；不抓文章正文 | Gmail `MAILBOX_AUTH_REQUIRED` |

Newsletter 公共目录只证明官方 URL 已登记。2026-08-15 的 WSJ 目录请求为 301 后 401/CAPTCHA，Bloomberg 为 403/反机器人响应；两者均没有被标记为邮件已验证。详见 `validation/international_media/newsletter_availability/*_public_http_2026-08-15.json`。

严禁绕过付费墙、登录、robots、Cloudflare、验证码、共享 Cookie、代理池、盗版镜像、缓存绕过、未经授权 API 或抓取受限正文。`metadata_only` 和 `newsletter` 输入不能偷偷触发正文抓取，也不能伪造摘要。

## 启用前检查

1. 阅读 `validation/international_media/INTERNATIONAL_MEDIA_RELEASE_CANDIDATE.md`，确认 Q 状态和阻塞项；RC 未通过时不改开关。Gmail OAuth、Word 像素检查、生产开关、Scheduler 观察和真实 Feishu 都是人工操作/授权事项，不能由测试、隔离 runner 或子代理代办。
2. 确认 `validation/international_media/security_scan.json` 为零命中；只有在后续生成并复核 Package A v2 两轮证据后，才能检查两轮结果的 `real_feishu_calls=0`。当前旧 schema 1.0 的静态 `isolated_run_1/2.json` 不可作为通过证据；同时确认生产 `data/news.db`、Scheduler 未被验收使用。
3. 备份生产配置和数据库，并记录当前 Scheduler 状态；备份路径只写在操作员私有记录，不要提交凭据。没有人工审批和可追溯备份时，保持四个 `enabled: false`。
4. 只将需要启用的单一来源的 `enabled` 改为布尔 `true`，并由操作员记录授权人、时间和回滚点。本轮优先 Reuters/FT；WSJ/Bloomberg 只有 Gmail summary 变为 `verified` 后才能单独启用。任何自动任务或测试不得替改生产开关。
5. 先执行现有 manual/now 机制的 dry-run 或隔离运行，检查日志、freshness、relevance、importance、Word 和通知候选，再等待 Scheduler。不得把 compileall、全量 pytest 或 live 两轮结果在没有实际命令输出时记录为 PASS。

本 Wave6 RC 阶段未修改生产来源开关、Scheduler、生产数据库或 Feishu adapter，也未发送真实 Feishu；启用、Scheduler 观察/变更和真实发送均须由有授权的操作员单独执行并留痕。

## Gmail 一次性 OAuth（仅操作员执行）

在项目目录之外准备 Google OAuth client JSON，并使用一次性授权命令。示例路径是占位符，不要把真实内容写入命令、YAML、SQLite、日志或报告：

```powershell
python -m app.newsletter_ingestion.verify_sources --mode gmail --mailbox gmail --label InternationalNews --source wsj_newsletter --since 30d --credentials C:\secure\gmail-client.json --token C:\secure\gmail-token.json --output validation/international_media/newsletter_availability/wsj_newsletter_gmail_YYYY-MM-DD.json
```

Bloomberg 使用同一命令替换 `--source`。OAuth 必须是精确的 Gmail readonly scope；程序只查询 `InternationalNews` label，并重新检查 sender/domain allowlist：`reuters.com`、`ft.com`、`wsj.com`、`dowjones.com`、`bloomberg.com`。默认不标记已读、不删除、不移动、不回复、不转发。Token、client secret、密码和完整 Cookie 永不进 Git/YAML/数据库/日志/fixture。

生成 summary 前必须先分别生成 public 与 gmail evidence；输出是 write-once，已有文件不得覆盖：

```powershell
python -m app.newsletter_ingestion.verify_sources --mode summary --source wsj_newsletter --public-evidence validation/international_media/newsletter_availability/wsj_newsletter_public_YYYY-MM-DD.json --gmail-evidence validation/international_media/newsletter_availability/wsj_newsletter_gmail_YYYY-MM-DD.json --output validation/international_media/newsletter_availability/wsj_newsletter_summary_YYYY-MM-DD.json
```

没有授权时必须保留 `MAILBOX_AUTH_REQUIRED`，不能把可访问的目录页面或 parser fixture 当真实邮件验收。

## 验证与故障处理

隔离验收只能使用验证目录下独立 config、SQLite、reports 和 `NullNotifier`：

```powershell
python validation/international_media/run_isolated.py --config validation/international_media/config.yaml --db validation/international_media/operator_check.db --reports validation/international_media/operator_reports --dry-run
powershell -ExecutionPolicy Bypass -File validation/international_media/run_isolated.ps1 --config validation/international_media/config.yaml --db validation/international_media/operator_check.db --reports validation/international_media/operator_reports --dry-run
```

检查每个源的 `fetched`、`parsed`、`inserted`、`fresh`、`relevant`、`important`、`errors`。单源失败不得阻断台湾源、Word 或其他源；HTTP、解析、授权和空源要按 Source Health 状态区分。网络或目录 403 不通过加大重试、绕过验证或抓取正文来修复。

## 停用、回滚与 Feishu

- 停用单一源：只把该 source 的 `enabled` 设为 `false`，保留 Article、健康记录和证据；不要删除数据库、重置历史或改 Scheduler。
- 回滚：恢复备份的配置/代码 manifest，停止国际 delivery candidates（如需要），再次执行台湾新闻 smoke；不要删除生产 DB。
- 任何真实 Feishu 发送都必须在 RC 之后由操作员显式执行并取得发送授权；自动化、测试、子代理和隔离 runner 的允许调用仅限 Null/Recording notifier，不得发送真实消息。重大事件提醒按 event cluster 一次一条，普通相关国际新闻只进 Word。发送前须人工核对来源状态、候选内容和回滚联系人。

## Word 渲染人工动作

当前结构/OXML 检查已完成；本机没有 `soffice`、`pdftoppm`、PyMuPDF，且未安装系统软件，因此像素级渲染记录为 `operator_action_required`。在具备渲染工具的机器上，按 documents skill 的 `render_docx.py` 生成每页 PNG，逐页检查分页、标题层级、中英文字符、超链接、表格、coverage 和重复新闻；不得把结构通过伪装为像素通过。OOXML 验证器只证明结构，不证明视觉布局。
