# Phase 4.1 DeepSeek 契约与 Preflight 实施计划

日期：2026-08-08  
设计基线：`docs/superpowers/specs/2026-08-08-phase41-deepseek-contract-preflight-design.md`

## 任务 1：请求契约与输出归一器

修改 `app/assessment/llm/deepseek_provider.py`：

- 用传入的、由正式 Schema 文件加载的 `output_schema` 构造 DeepSeek 请求信封。
- 请求信封保留原 v1.0 输入 Contract，不改变其业务内容。
- 记录/暴露可审计的请求形状、`output_schema_business_hash` 与能力标记。
- 增加只处理 BOM、首尾空白、单一 JSON 围栏和唯一 JSON 对象前后文字的格式归一器。
- 多 JSON、数组、prose-only 和语义缺失对象不得被修复。

修改 `app/assessment/generate_llm_report.py`：

- DeepSeek 首次正式请求的 effective system prompt 由 system prompt、JSON adapter、writer prompt 确定性组成。
- manifest 写入 `effective_system_prompt_hash`、`output_schema_business_hash` 和请求能力事实。
- 不修改 `contract_version=1.0` 或 v1.1 Schema。

测试：

- Provider 实际调用参数中包含完整 Schema 和完整 writer prompt。
- Schema 对象与正式文件程序化加载结果完全一致。
- 真实失败 fixture 继续被拒绝。
- 至少 20 个 normalizer/contract 黄金案例全部满足预期。

## 任务 2：Production Preflight 语义拆分

修改 `app/assessment/deployment_preflight.py` 及必要状态生成代码：

- 将技术错误、当前周期数据不完整和交付安全门禁分开收集。
- 增加 `production_system_ready`、`current_reporting_period_final_ready`、`scheduler_install_ready`、`scheduler_installed`。
- Coverage partial 仅阻断当前周期 final ready，不使技术组件失败。
- 飞书技术能力与凭据轮换确认分开；轮换未确认继续阻断生产交付。
- 保留兼容字段，但语义清晰且不会把 partial Coverage 误计为系统技术失败。

测试：

- 技术条件齐全而 Coverage partial 时，系统技术 ready、当前周期 final not ready。
- Scheduler 未安装保持 false。
- 飞书技术 ready 但轮换未确认时，production delivery not ready。

## 任务 3：本地分层验收

依次执行：

1. DeepSeek Provider、normalizer、失败 fixture 和 Preflight 定向测试。
2. 全部 Assessment 测试。
3. Mock Assessment，确认 Schema、引用、Claim–Evidence、Word 门禁。

任一步失败时先修复并重复本层，不进入 Live。

## 任务 4：有限 Live 验收

使用现有环境变量但不输出其值：

1. DeepSeek neutral structured Live 一次。
2. 冻结同一正式输入，执行 formal structured Live 第一次。
3. 第一次通过后，对同一冻结输入执行第二次稳定性复验。
4. 两次分别验证 Schema、事件引用、来源引用和 Claim–Evidence。

只有两次均通过才设置 `production_llm_ready=true`。不要求文本一致。记录调用次数、model、request ID、token 和耗时，审计文件不得含密钥。

## 任务 5：Word、交付资格与状态收口

- 仅在双次正式 Live 均通过后生成并验证 Word。
- 评估 Delivery eligibility，不发送正式生产交付。
- 生成飞书凭据轮换与复验说明；轮换确认保持 false。
- Scheduler 仅记录安装就绪，实际 installed 保持 false。

## 任务 6：RC3 与质量门禁

- 更新部署状态 Schema 和 Phase 4.1 审计工件。
- 执行全量测试并与 2019 passed / 4 skipped / 0 failed 基线比较。
- 构建 `tainan-assessment-production-rc3`，保留 RC2。
- 对 RC3 执行哈希、解压、关键文件和敏感信息复验。
- 对部署、运行、报告、Word 临时目录与 release 执行多通道扫描。
- 输出 `phase41_quality_gate.json`、Live 调用审计及 50 项最终报告。

## 停止边界

不安装 Scheduler，不正式启用生产，不发送正式报告，不修改正式事实、Snapshot、Coverage 或飞书轮换确认状态。
