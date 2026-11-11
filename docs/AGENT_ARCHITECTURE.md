# Agent 架构分工说明（架构师 + 执行者）

> 更新日期：2026-08-17
> 背景：用户希望今后任务由「GPT-5.6 Sol 负责架构设计和任务发布，DeepSeek V4 Flash 负责具体执行」。
> 现状：Sol 在当前 API key 下调用失败（未开通权限或模型名不一致），已实测可用 DeepSeek V4 Pro 作为架构师替代。

## 一、分工模式

架构师（Architect）:
- 理解需求，产出技术方案 / 任务分解 / 验收标准
- 负责"任务发布"：把可执行的任务包发给执行者

执行者（Executor）:
- 按方案落地：写代码 / 跑命令 / 验证 / 汇报
- 返回结构化结果（改动清单、验证证据、遗留问题）

流程: 架构师产出方案 -> 任务包(方案+约束+验收) -> 执行者落地 -> 结果回传 -> 汇总/质检/交付

## 二、实现机制（DSH workflow 工具）

DSH 的 workflow 工具支持对每个子代理单独指定 provider + model:

    const design = await agent("【架构设计】" + 任务描述, {
      provider: "commandcode-goat",
      model: "deepseek/deepseek-v4-pro"          // 架构师
    });
    const result = await agent("【执行】" + design + 任务详情, {
      provider: "commandcode-goat",
      model: "deepseek/deepseek-v4-flash"        // 执行者
    });

## 三、可用模型清单（2026-08-17 实测）

Provider: commandcode-goat（baseURL https://api.commandcode.ai/provider/v1）

### 可用

| 模型 id | 定位 | 上下文 |
|---|---|---|
| deepseek/deepseek-v4-pro | 架构师首选 | 1M |
| deepseek/deepseek-v4-flash | 执行者 | 1M |
| moonshotai/Kimi-K3 | 架构师备选 | 1M |
| moonshotai/Kimi-K2.7-Code | 代码备选 | 256K |
| zai-org/GLM-5.3 | 架构师备选 | 1M |
| Qwen/Qwen3.8-Max | 架构师备选 | 1M |
| xai/grok-4.5 | 备选 | 500K |
| tencent/hy3-paid | 备选 | 262K |

### 不可用（当前 API key 未开通 / 上游不可达）

gpt-5.6-sol、claude-opus-5、claude-sonnet-5、gpt-5.4、gpt-5.5、grok-4.6、google/gemini-3.6-flash

## 四、Sol 开通后的切换

在 commandcode.ai 控制台确认 gpt-5.6-sol 可用后，只需把架构师的 model 改为:

    model: "gpt-5.6-sol"   // provider 仍为 commandcode-goat

（若上游实际模型 id 带前缀，例如 openai/gpt-5.6-sol，以 commandcode.ai 控制台/API 文档为准。）

## 五、使用约定

1. 架构师输出必须包含：目标、约束、任务分解、验收标准。
2. 执行者输出必须包含：改动清单、验证证据、遗留问题。
3. 涉及真实资源（飞书发送、DB 写操作、部署）时，执行者不得自动执行，需先回报架构师/用户确认。
4. 模板见 scripts/agent_workflow_template.md。
