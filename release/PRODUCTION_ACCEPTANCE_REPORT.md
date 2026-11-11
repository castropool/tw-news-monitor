# Phase 4 生产部署与 Live 验收报告

生成时间：`2026-08-08T22:05:14+08:00`  
发布候选：`tainan-assessment-production-rc2`

## 最终结论

**RC2 已完成真实部署和安全范围内的 Live 验收，但未获准正式启用。** 当前状态为 `production_activation_blocked`。没有安装生产计划任务，没有发送正式政治评估报告，也没有将候选事实自动写入正式库。

阻断原因：

1. DeepSeek 正式 Assessment 虽完成真实调用，但返回对象未通过 v1.1 严格输出契约，系统正确拒绝报告；未生成 Word，未交付。
2. 历史飞书凭据事件要求轮换，但尚无“轮换完成并确认”的证据；TEST ONLY Live 成功不能替代此门禁。
3. Production preflight 因上述两项及历史周期事实覆盖不足而失败。

## 已完成并通过

| 验收通道 | 结果 | 一手证据 |
| --- | --- | --- |
| 全量自动测试 | PASS | 2019 passed / 4 skipped / 0 failed |
| RC2 构建与独立解压校验 | PASS | 254 个受保护文件哈希全部一致；validator 无错误/警告 |
| DeepSeek 中性最小 Live | PASS | model=`deepseek-v4-pro`，latency=3093 ms，request_id=`e3462a52-e35f-46f4-972f-4af0d2c8ac27` |
| 飞书 TEST ONLY Live | PASS | 文字 message_id=`om_x100b68474024e4a4b043e515771ae4c`；文件 message_id=`om_x100b68474033c4a8b1fe0021cdc7b70` |
| 新闻采集 dry-run | PASS | 9/9 来源成功；抓取 144；失败来源 0；未写正式库、未通知 |
| 候选队列 since-last-success | PASS | run_id=`run_20260808_214731_449237`；检查 2075；匹配 79；正式写入调用 0 |
| 正式事实状态前后校验 | PASS | seed/db 业务哈希均为 `8a42da2ef1f7ca73dc9777898bc7676076fc5d96f919a68adaad6dab40383207` |
| 恢复与回滚专项 | PASS | 40 passed；正式环境无未完成 publication/recovery journal |
| 凭据值扫描 | PASS | 部署包、运行数据与生成物未命中 DeepSeek/飞书真实密钥值或 Authorization 头 |

以上证据来自不同环节：测试夹具、独立包哈希校验、真实外部 API、正式数据库业务哈希和运行日志，避免把同一份输出重复当作多通道证据。

## 未通过 / 未执行

| 项目 | 结果 | 说明 |
| --- | --- | --- |
| Development preflight | PASS | 无错误 |
| Dry-run preflight | PASS | 无错误 |
| Production preflight | FAIL | 缺少合格正式 DeepSeek 预检、飞书轮换未确认、历史周期覆盖不足 |
| 正式 Assessment 输出契约 | FAIL | report_status=`rejected`；structured_output_schema_valid=false |
| 正式 Word 与交付 | NOT ATTEMPTED | 严格门禁在上游拒绝，符合设计 |
| 9 日 / 22 日生产任务 | NOT INSTALLED | 生产门禁未通过；只读查询确认不存在 |
| 正式启用 | BLOCKED | `production_ready=false` |

## 数据与安全边界

- Active snapshot：`tn_state_20260801_v1`；Coverage：`fact_coverage_20260801_v4` / `partial`。
- `facts_cutoff=2026-07-27`，`poll_cutoff=2026-03-12`，未篡改 Coverage、Snapshot、正式事实或民调。
- 飞书 Live 只发送固定 TEST ONLY 文字和无敏感信息 Word；文件经过单页真实渲染检查。
- 通用扫描器对虚拟环境源码中的 Authorization 字样和绝对路径给出非零结果；精确凭据值检查均为 false，这些不属于密钥泄露。

## 回滚与恢复

当前无需执行数据回滚：没有正式事实写入、没有正式报告交付、没有生产计划任务。若需撤回 RC2，应先保持任务未安装状态，再从 `deployment/phase4/pre_phase4_deployment_backup/20260808_174617` 恢复四个 SQLite 一致性快照，并重新运行正式状态校验。旧版 `tainan-assessment-offline-rc1.zip` 已保留且哈希记录在发布清单中。

## 解除阻断后的启用顺序

1. 提供飞书凭据已轮换并由责任人确认的证据，更新安全确认配置。
2. 修复 DeepSeek 输出契约兼容性，使用明确授权的正式事实包重跑 Assessment，并通过 Claim–Evidence、Word 与交付门禁。
3. Production preflight 全部通过后，才安装每月 9 日与 22 日 09:00（UTC+8）的任务并完成一次调度器级运行。
4. 重新生成 `production_status.json`，只有全部门禁通过时才可设置 `production_ready=true`。
