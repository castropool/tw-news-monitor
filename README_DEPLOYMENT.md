# 台南选情半月研判部署说明

本部署包只包含程序、配置模板和正式事实种子，不包含任何 API 密钥、飞书 Webhook 或本机隐私路径。

## 环境变量（部署电脑一次性配置）

| 变量 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek 生产 API 密钥 |
| `DEEPSEEK_MODEL` | 默认 `deepseek-v4-flash` |
| `FEISHU_WEBHOOK` | 飞书群机器人 Webhook（仅文本摘要；不能上传 Word） |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_CHAT_ID` | 自建应用凭据（可选；用于 Word 文件上传） |

密钥只通过用户级环境变量或进程环境注入，禁止写入任何配置文件。

## 部署电脑一次性操作顺序

1. 解压或安装部署包（保持目录结构完整）。
2. 安装 Python 依赖：`pip install -r requirements.txt`。
3. 配置 `DEEPSEEK_API_KEY`。
4. 配置 `DEEPSEEK_MODEL=deepseek-v4-flash`（或允许的 `deepseek-v4-pro`）。
5. 配置 `FEISHU_WEBHOOK`（如需 Word 上传，另配置自建应用三件套）。
6. 运行 development 前置检查：
   `powershell -File scripts\validate_tainan_assessment_deployment.ps1`
   `python -m app.assessment.deployment_preflight --level development --write-files`
7. 运行 dry-run 前置检查：
   `python -m app.assessment.deployment_preflight --level dry_run --as-of <YYYY-MM-DD> --write-files`
8. 完成真实 DeepSeek live test：
   `python -m app.assessment.generate_llm_report --config config/election_assessment.yaml --evidence-dir data/reports/tainan_2026/evidence_packages/<周期目录> --provider deepseek --model deepseek-v4-flash --deepseek-thinking disabled --allow-draft-with-gap --force-model-call`
9. 完成飞书测试消息（发送一条测试文本，确认可达）。
10. 运行 production 前置检查：
    `python -m app.assessment.deployment_preflight --level production --as-of <YYYY-MM-DD> --write-files`
    必须返回 `preflight_ready=true`。
11. 安装 9 日、22 日计划任务：
    `powershell -File scripts\install_tainan_assessment_tasks.ps1 -RunTime "09:00" -Mode production`
12. 查询任务状态：
    `powershell -File scripts\status_tainan_assessment_tasks.ps1`
13. 手工立即运行一次：
    `powershell -File scripts\run_tainan_assessment_now.ps1 -Mode production`
14. 核对 Word 与飞书结果（报告状态、截止日披露、文件命名）。

## 运行模式

- `development`：Mock Provider + Mock 飞书，用于本地开发与验收。
- `dry_run`：正式事实库 + 正式证据包，生成本地 Markdown/Word，不发送外部消息。
- `production`：全部生产门禁通过后才允许，发送正式报告。

## 飞书交付模式

| 模式 | 需要的环境变量 | 能力 | file_delivery_supported |
| --- | --- | --- | --- |
| `webhook_summary` | `FEISHU_WEBHOOK` | 发送文本摘要 | false |
| `app_file_upload` | `FEISHU_APP_ID`/`FEISHU_APP_SECRET`/`FEISHU_CHAT_ID` | 上传 Word + 发送文件消息 | true（不要求 Webhook） |
| `delivery_disabled` | 无 | 明确关闭交付 | false |

配置示例：

```yaml
delivery:
  enabled: true
  mode: app_file_upload
  fallback_mode: none
```

缺凭据不等于关闭交付；`delivery.enabled=false` 才表示显式关闭。本轮不实现自动降级（`fallback_mode=none`）。

## 安全事件处理（必须执行）

历史记录显示 `.env.example` 曾包含真实 `FEISHU_APP_ID`/`FEISHU_APP_SECRET`，视为凭据泄露事件：

1. 在飞书开发者后台重置 App Secret；
2. 如 Webhook 曾进入任何文件，重新生成 Webhook；
3. 更新部署电脑用户级环境变量（不写入项目文件）；
4. 将部署配置中 `security.feishu_credentials_rotated_after_incident` 改为 `true`；
5. 填写 `feishu_rotation_acknowledged_at` 确认日期；
6. 重新运行安全审计与 production preflight。

未完成轮换确认时，production 飞书交付门禁必须失败：

```text
delivery: 飞书旧凭据尚未确认轮换（feishu_credentials_rotated_after_incident=false）
```

Git 历史处理：如审计发现 Git 历史存在旧凭据，仅提供人工处置建议（git filter-repo/rebase/force push 必须由管理员在确认传播范围后手动执行），Secret 轮换是强制项。

## 已知边界

- Webhook-only 环境不声称 Word 已上传；`file_delivery_supported=false`。
- 数据不完整草稿文件名带“数据不完整草稿”，且正文有显著标识。
- 报告统计周期始终为自然半月；9 日、22 日只是生成日。
- 部署电脑无需修改源代码；只修改用户级环境变量、部署 YAML 配置与安全轮换确认字段。
