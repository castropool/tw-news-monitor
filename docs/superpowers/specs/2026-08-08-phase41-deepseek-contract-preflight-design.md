# Phase 4.1 DeepSeek 输出契约兼容与 Production Preflight 语义校正设计

日期：2026-08-08  
状态：方案 A 与实施补充约束已获批准  
适用版本：`tainan-assessment-production-rc2` → `tainan-assessment-production-rc3`

## 1. 目标

在不降低报告输出 Schema、Validator、Claim–Evidence 与事实门禁的前提下，修复 DeepSeek 正式 Assessment 输出无法满足 v1.1 报告契约的问题；同时把生产系统技术就绪状态与当前报告周期数据完整度分开表达。

本轮不是新功能开发，不安装 Scheduler，不正式启用生产，不改变正式事实、Snapshot 或 Coverage。

## 2. 版本口径

- `task_spec_version_ambiguity_resolved=true`
- `input_contract_version=1.0`
- `report_output_schema_version=1.1`

任务书中的 `llm_input_contract_version=1.1` 解释为要求继续执行现行 v1.1 Schema 严格门禁，不代表升级输入 Contract。本轮不得修改 `contract_version=1.0`。

## 3. 已确认根因

Phase 4 正式 DeepSeek 请求使用 `response_format={"type":"json_object"}`，但没有把 `output_schema` 实际发送给服务端；writer prompt 虽被加载和哈希记录，也没有进入首次正式请求。

失败响应是完整、可解析、未截断的 JSON 对象，但采用了另一套报告结构，缺少 v1.1 必需字段、八节结构、Claims 和引用契约。因此失败类型为：

- `D_schema_shape`
- `E_semantic_contract`
- 汇总：`G_multiple`

该失败不是传输错误、JSON 序列化错误或纯格式错误，不能由 normalizer 修复。

## 4. 选定方案

采用“显式传输契约 + 严格本地校验”方案：

1. DeepSeek 继续使用已验证可用的 `json_object` 模式。
2. system message 同时包含现有 system prompt 与 writer prompt。
3. user message 使用请求信封，分别携带原样 v1.0 输入 Contract 和完整 v1.1 输出 Schema。
4. 如实记录 `native_json_schema=false` 与 `server_side_strict_schema=false`；不根据 neutral Live 推断严格 Schema 能力。
5. 所有响应仍通过原 v1.1 Schema、报告 Validator、引用校验和 Claim–Evidence 校验。

不采用以下方案：

- 未经真实能力证明直接切换 DeepSeek 原生 `json_schema`。
- 用第二次 LLM 调用自动补造缺失章节、Claims、引用或事实。
- 通过放宽 Schema 或字段别名映射掩盖语义缺失。

## 5. 请求与输出边界

### 5.1 正式请求

DeepSeek 请求应保留：

- `stream=false`
- `response_format={"type":"json_object"}`
- 配置中的 model、timeout、max tokens 和 thinking mode

请求审计仅保存脱敏后的结构、字段名称、类型、布尔能力标记和必要哈希，不保存 API Key、Authorization header 或 reasoning content。

### 5.2 Provider output normalizer

允许的确定性转换：

- 移除 UTF-8 BOM。
- 移除首尾空白。
- 解开唯一的 JSON Markdown 代码围栏。
- 从非 JSON 前后说明文字中提取唯一、完整、平衡的 JSON 对象。

禁止的转换：

- 字段改名或别名映射。
- 补充必需字段、章节、Claims、引用或 Data Context。
- 修改字段类型、枚举值、事实或结论。
- 合并多个 JSON 对象。
- 将 prose-only、数组根节点或语义不完整对象包装成合格报告。

Phase 4 真实失败 fixture 必须继续被严格拒绝，并标记 `normalization_ready=false`。

## 6. Prompt、Schema 与 Validator 对齐

- v1.1 JSON Schema 保持 `additionalProperties=false` 等严格约束。
- 八个固定章节及顺序保持不变。
- 必需字段、枚举、引用数组和 Data Context 规则保持不变。
- writer prompt 必须有“实际进入请求”的单元测试，不能只验证哈希。
- 不修改 Claim–Evidence 校验规则。
- 不修改正式输入包中的事件、来源、快照、Coverage 或复杂事实。

## 7. Production Preflight 语义

生产状态至少区分：

- `production_system_ready`：代码、运行环境、正式 LLM 契约、Word 等技术能力是否就绪。
- `current_reporting_period_final_ready`：当前报告周期数据是否足以生成最终报告。
- `scheduler_install_ready`：调度器是否具备安装条件。
- `scheduler_installed`：调度器是否已经安装，本轮必须保持 `false`。

`coverage_status=partial` 和 `facts_cutoff=2026-07-27` 是正确的当前事实状态，只能使 `current_reporting_period_final_ready=false`，不能单独使技术组件失败。

飞书状态必须分开记录：

- 技术 TEST ONLY Live 成功可记为 `feishu_technical_live_ready=true`。
- `feishu_credentials_rotated_after_incident=false` 保持不变。
- 因轮换未确认，`production_delivery_ready=false`。

## 8. 证据与测试

实施前冻结 RC2、失败运行摘要、失败契约、原 Production Preflight、凭据轮换状态及受保护文件哈希。失败响应只保存脱敏副本。

建立至少 20 个黄金案例，要求：

- 合法案例接受率 `1.00`。
- 非法案例拒绝率 `1.00`。
- `unsafe_schema_relaxation_count=0`。
- `fabricated_field_count=0`。

真实失败 fixture 位于：

`tests/fixtures/deepseek_live_contract/formal_live_failure_20260808.json`

## 9. 验收顺序

必须依次执行，任何一步失败即停止：

1. 本地 fixture contract tests。
2. Mock Assessment。
3. DeepSeek neutral structured Live。
4. DeepSeek formal structured Live。
5. Claim–Evidence 校验。
6. Word 生成与校验。
7. Delivery eligibility。

正式 Live 仅在以下条件全部满足时通过：

- `http_success=true`
- `response_received=true`
- `structured_output_schema_valid=true`
- `all_event_references_valid=true`
- `all_source_references_valid=true`
- `claim_evidence_valid=true`
- `report_status=accepted`

通过后才可设置 `formal_assessment_live_ready=true` 和 `production_llm_ready=true`。Live 调用次数、model 与 request ID 必须审计，但不得泄漏凭据。

## 10. 发布与安全

如代码发生变化，创建 `tainan-assessment-production-rc3`，保留 RC2。完成：

- 全量测试并对照 `2019 passed / 4 skipped / 0 failed` 基线。
- RC3 归档、哈希、解压复验和关键文件复核。
- 对部署目录、运行产物、Assessment 工件、Word 临时目录和 release 进行敏感信息扫描。
- API Key 与 Authorization header 值命中数必须为 0。

本轮停止于生产阻断状态收口：不安装 Scheduler、不正式启用、不发送正式生产报告、不确认或代替人工执行飞书凭据轮换。

## 11. 实施补充约束

1. `output_schema` 必须直接从当前正式 v1.1 Schema 文件程序化加载并序列化进入请求，不得在 Provider 或 Prompt 中维护第二份手写 Schema；请求审计记录 `output_schema_business_hash`。
2. writer prompt 的测试必须验证最终实际发送给 Provider 的完整内容，而不仅是加载、存在或哈希；同时记录 `effective_system_prompt_hash`。
3. 正式 DeepSeek 契约修复首次通过后，对同一冻结输入增加一次有限稳定性复验；两次均须通过 Schema、事件/来源引用和 Claim–Evidence，才设置 `production_llm_ready=true`。不要求两次报告文本一致。
