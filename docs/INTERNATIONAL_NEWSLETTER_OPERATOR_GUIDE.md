# 国际 Newsletter 操作指南

本层只读取合法收到的 Newsletter，不抓取邮件链接的文章正文，也不绕过付费墙。生产开关默认关闭；没有 Gmail 授权时必须保持 `MAILBOX_AUTH_REQUIRED`，不能把来源标为已验收。

## 邮箱准备

1. 在专用 Gmail 邮箱建立精确名称为 `InternationalNews` 的 label。
2. 只把 Reuters、FT、WSJ/Dow Jones、Bloomberg 的官方 Newsletter 过滤到该 label。
3. Gmail 使用 OAuth `https://www.googleapis.com/auth/gmail.readonly` scope。程序默认不标记已读、不删除、不移动、不回复、不转发。
4. OAuth client secret 与 token 必须保存在项目目录之外的受保护本地路径；不要写入仓库、YAML、SQLite、日志、fixture 或报告。

## 一次性授权

授权由操作员在本机完成。自动验证不会打开浏览器，也不会替你保存账号凭据。授权完成后，使用项目外的 credentials/token 路径运行验证命令；路径示例只表示位置，不要把真实内容粘贴进命令或文档。

## 独立验证

公开目录和 Gmail 邮件是两个独立门禁，先分别生成证据，再生成只读 summary。输出文件是 write-once，路径已有文件时命令会失败。

public evidence 的状态 `official_url_registered` 只表示已登记并校验官方 HTTPS URL、域名和端口；它不表示页面已在线访问成功，也不表示 Newsletter 已实际送达。只有 Gmail evidence 在只读授权下取得 allowlist 邮件时，summary 才可能为 `verified`。

```powershell
python -m app.newsletter_ingestion.verify_sources --mode public --source wsj_newsletter --public-page https://www.wsj.com/newsletters --as-of 2026-08-14 --output validation/international_media/newsletter_availability/wsj_newsletter_public_2026-08-14.json
python -m app.newsletter_ingestion.verify_sources --mode gmail --mailbox gmail --label InternationalNews --source wsj_newsletter --since 30d --credentials C:\secure\gmail-client.json --token C:\secure\gmail-token.json --output validation/international_media/newsletter_availability/wsj_newsletter_gmail_2026-08-14.json
python -m app.newsletter_ingestion.verify_sources --mode summary --source wsj_newsletter --public-evidence validation/international_media/newsletter_availability/wsj_newsletter_public_2026-08-14.json --gmail-evidence validation/international_media/newsletter_availability/wsj_newsletter_gmail_2026-08-14.json --output validation/international_media/newsletter_availability/wsj_newsletter_summary_2026-08-14.json
```

Bloomberg 使用相同命令，将 source 改为 `bloomberg_newsletter`。没有授权或没有真实邮件时，Gmail evidence 必须是 `operator_action_required`；summary 不能是 `verified`。

## 故障排查与停用

- `MAILBOX_AUTH_REQUIRED`：检查项目外 token 路径和 readonly scope；不要把 token 复制到项目内。
- `LABEL_NOT_ALLOWED`：label 必须严格为 `InternationalNews`。
- 邮件数量为零：先检查官方 Newsletter 是否送达、sender 是否在 allowlist；不要放宽到整个邮箱。
- 要停用单一来源，只把对应 source 的 `enabled` 设为 `false`，保留历史 Article 和 evidence；不要删除生产数据库或修改 Scheduler。
