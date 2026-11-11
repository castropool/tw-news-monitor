# 台南选情自动报告系统 — 部署交接清单

发布候选：`tainan-assessment-offline-rc1`

本清单按实际部署顺序编写。所有密钥只通过**用户级环境变量**或部署机进程环境注入，禁止写入项目文件、配置模板或本清单。

---

## 一、复制并校验部署包

1. 将归档 `dist/releases/tainan-assessment-offline-rc1.zip` 复制到部署电脑。
2. 解压归档，保持目录结构完整。
3. 校验归档 SHA256（与 `release/release_manifest.json` 中 `release_archive.sha256` 一致）：

```powershell
Get-FileHash -LiteralPath <归档路径> -Algorithm SHA256
```

4. 对解压后的部署包执行只读验证，必须返回 `bundle_valid=true`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\validate_tainan_assessment_deployment.ps1 -BundleDir <解压目录>
```

## 二、安装 Python 依赖

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 三、配置 DEEPSEEK_API_KEY

在用户级环境变量中配置 `DEEPSEEK_API_KEY`，值来自真实 DeepSeek 账号；不写入任何项目文件。

## 四、配置 DEEPSEEK_MODEL=deepseek-v4-flash

```text
DEEPSEEK_MODEL=deepseek-v4-flash
```

也可使用配置允许的 `deepseek-v4-pro`，须与 `config/election_assessment.yaml` 的 `allowed_models` 一致。

## 五、在飞书后台轮换 App Secret

1. 登录飞书开放平台开发者后台。
2. 找到本项目自建应用，重置 App Secret。
3. 记录新 Secret，仅写入部署电脑用户级环境变量。

## 六、如 Webhook 曾暴露则重建 Webhook

如果历史事件中 Webhook 曾进入任何文件，必须在飞书群中重新生成 Webhook，旧地址作废。

## 七、配置飞书环境变量

按交付模式配置：

```text
webhook_summary 模式：FEISHU_WEBHOOK
app_file_upload 模式：FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_CHAT_ID
```

不要求两种模式同时配置；缺凭据不会自动视为关闭交付。

## 八、确认 feishu_credentials_rotated_after_incident=true

在部署 YAML 配置（非项目源码）中设置：

```yaml
security:
  feishu_credentials_rotated_after_incident: true
  feishu_rotation_acknowledged_at: <YYYY-MM-DD>
```

未完成轮换确认时，production preflight 必须失败。

## 九、运行 development preflight

```powershell
python -m app.assessment.deployment_preflight --config config/election_assessment.yaml --level development --write-files
```

要求：`preflight_ready=true`。

## 十、运行 dry-run preflight

```powershell
python -m app.assessment.deployment_preflight --config config/election_assessment.yaml --level dry_run --as-of <YYYY-MM-DD> --write-files
```

要求：`preflight_ready=true`。

## 十一、执行真实 DeepSeek live test

```powershell
python -m app.assessment.generate_llm_report --config config/election_assessment.yaml --evidence-dir data/reports/tainan_2026/evidence_packages/<周期目录> --provider deepseek --model deepseek-v4-flash --deepseek-thinking disabled --allow-draft-with-gap --force-model-call
```

要求：真实模型调用成功，生成 `live_deepseek_output_review.md`，`deepseek_production_preflight.json` 中 `live_deepseek_test=passed`。

## 十二、核对真实模型报告

人工核对：

- 标题与总体判断是否符合证据；
- 八个固定章节完整；
- required disclosures 完整；
- 无外部事实、无未支持民调结论、无未支持概率表述；
- `structured_report_final.json` 的 `data_context` 与输入合同一致。

## 十三、执行飞书测试交付

自建应用模式：

```powershell
python -m app.main --test-feishu-app
```

Webhook/通知渠道模式：

```powershell
python -m app.main --test-notify
```

要求：部署电脑确认飞书群已收到测试消息。此步骤会发送真实消息，属部署验收动作。

## 十四、运行 production preflight

```powershell
python -m app.assessment.deployment_preflight --config config/election_assessment.yaml --level production --as-of <YYYY-MM-DD> --write-files
```

要求：`preflight_ready=true` 且 `production_llm_ready=true`（真实 live test 通过后）。

## 十五、手工执行一次 production

```powershell
python -m app.assessment.run_assessment_pipeline --config config/election_assessment.yaml --mode production --as-of <YYYY-MM-DD>
```

要求：`pipeline_status=success`。

## 十六、核对 Word 及飞书结果

- 打开生成的 `.docx`，核对信息栏：当前快照、覆盖版本、事实截止日、民调截止日；
- 核对飞书群收到 Word 文件或摘要（按交付模式）；
- 核对 `data/reports/tainan_2026/` 下归档文件命名与报告状态。

## 十七、安装 9 日、22 日计划任务

先预览（不注册）：

```powershell
powershell -File scripts\install_tainan_assessment_tasks.ps1 -DryRun -RunTime "09:00" -Mode production
```

确认无误后注册：

```powershell
powershell -File scripts\install_tainan_assessment_tasks.ps1 -RunTime "09:00" -Mode production
```

任务名固定为：

```text
Taiwan Election Assessment - Day 9
Taiwan Election Assessment - Day 22
```

## 十八、查询计划任务状态

```powershell
powershell -File scripts\status_tainan_assessment_tasks.ps1
```

要求：两个任务均存在、状态 Ready、上次退出码 0、下次运行时间正确。

## 十九、记录首次生产运行结果

将首次生产运行结果填入 `release/DEPLOYMENT_ACCEPTANCE_TEMPLATE.md`，并保存运行目录中的 `pipeline_manifest.json`、`delivery_receipt.json`、`artifact_validation.json` 作为验收证据。
