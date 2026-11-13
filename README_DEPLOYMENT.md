# 部署说明

本项目只包含程序、配置模板和正式事实种子，不包含任何 API 密钥、飞书 Webhook 或本机隐私路径。
密钥只通过用户级环境变量或 `.env`（不入库）注入，禁止写入任何配置文件。

> 选情研判（台南/新北半月研判报告的生成、人工终审与交付）已从本项目移除，
> 不再提供任何研判命令或计划任务。已安装的旧研判任务需按下文「清理旧研判计划任务」手工卸载。

## 环境变量

完整列表见 `.env.example` 与 `README.md`「配置」一节。常用项：

| 变量 | 用途 |
| --- | --- |
| `NOTIFIER` | `console` / `feishu` / `telegram` |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_CHAT_ID` | 飞书自建应用（新闻推送、Word 简报上传） |
| `FEISHU_WEBHOOK_URL` | 飞书 Webhook（旧渠道，仅文本） |
| `SUMMARIZER_*` | 文章梗概 LLM 端点（默认本地 Command Code 网关） |
| `DISABLE_FEISHU_SEND` | 设为 `1` 时停用飞书发送（验证/演练时使用） |

## 一次性部署步骤

1. 解压或检出项目（保持目录结构完整）。
2. 安装 Python 依赖：`pip install -r requirements.txt`。
3. 复制 `.env.example` 为 `.env` 并填写所需变量。
4. 安装计划任务（均可重复执行；候选监控/自动发布安装脚本支持 `-DryRun` 只打印不注册）：

| 计划任务 | 安装 | 状态 | 卸载 |
| --- | --- | --- | --- |
| `Taiwan News Monitor`（新闻采集/摘要/Word 简报/推送） | `install_task.bat` | `task_status.bat` | `uninstall_task.bat` |
| `Tainan Election Candidate Monitor`（每 30 分钟） | `powershell -File scripts\install_candidate_monitor_task.ps1` | `scripts\status_candidate_monitor_task.ps1` | `scripts\uninstall_candidate_monitor_task.ps1` |
| `New Taipei Election Candidate Monitor` | 同上加 `-Election new_taipei` | 同上加 `-TaskName "New Taipei Election Candidate Monitor"` | 同上加同样的 `-TaskName` |
| `Tainan Election Fact Auto Publisher`（每 30 分钟错峰） | `powershell -File scripts\install_auto_publish_task.ps1` | `scripts\status_auto_publish_task.ps1` | `scripts\uninstall_auto_publish_task.ps1` |
| `New Taipei Election Fact Auto Publisher` | 同上加 `-Election new_taipei` | 同上加 `-TaskName "New Taipei Election Fact Auto Publisher"` | 同上加同样的 `-TaskName` |

5. 手工立即运行新闻任务一次：`run_task_now.bat`，核对 Word 简报与飞书结果。

## 清理旧研判计划任务

旧版本可能已在部署电脑注册以下研判任务；新代码不再包含其入口脚本，任务继续运行只会报错。
请以管理员 PowerShell 逐一卸载（不存在时会提示，可忽略）：

```powershell
foreach ($name in @(
    "Tainan Election Assessment",
    "New Taipei Election Assessment",
    "Taiwan Election Assessment - Day 9",
    "Taiwan Election Assessment - Day 22"
)) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "已卸载：$name"
    } else {
        Write-Host "任务不存在：$name"
    }
}
```

部署目录中如残留旧 `app\assessment\`、`config\election_assessment*.yaml`、
`scripts\*assessment*` 等文件，可在确认任务已卸载后手工删除。
`data\election_assessment\`、`data\reports\` 下的历史报告与正式事实数据**不要删除**，
它们作为历史归档保留。

## 安全事件处理

历史记录显示 `.env.example` 曾包含真实 `FEISHU_APP_ID`/`FEISHU_APP_SECRET`，视为凭据泄露事件：

1. 在飞书开发者后台重置 App Secret；
2. 如 Webhook 曾进入任何文件，重新生成 Webhook；
3. 更新部署电脑用户级环境变量或 `.env`（不写入项目文件）。

Git 历史处理：如审计发现 Git 历史存在旧凭据，仅提供人工处置建议（git filter-repo/rebase/force push
必须由管理员在确认传播范围后手动执行），Secret 轮换是强制项。
