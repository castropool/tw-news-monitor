# Agent Workflow 模板（架构师 + 执行者）

> 用法：复制下方 script 到 DSH 的 workflow 工具调用中，替换 TASK_DESCRIPTION。
> 当前架构师 = deepseek-v4-pro（Sol 未开通）；开通后把 model 改为 gpt-5.6-sol。

## 模板（JS，workflow 工具的 script 参数）

    const task = "TASK_DESCRIPTION";   // ← 在这里写任务

    // 阶段 1: 架构师设计
    const design = await agent(
      "你是架构师。请为以下任务产出方案：" + task + "\n" +
      "输出必须包含: 目标 / 约束 / 任务分解(1-5步) / 验收标准。",
      { label: "architect", provider: "commandcode-goat", model: "deepseek/deepseek-v4-pro" }
    );
    if (!design) throw new Error("架构师失败");

    // 阶段 2: 执行者落地
    const result = await agent(
      "你是执行工程师。按架构师方案执行:\n\n" + design + "\n\n" +
      "输出必须包含: 改动清单 / 验证证据 / 遗留问题。",
      { label: "executor", provider: "commandcode-goat", model: "deepseek/deepseek-v4-flash" }
    );

    // 阶段 3: 汇总
    return {
      task,
      design: design.slice(0, 2000),
      execution: result ? result.slice(0, 4000) : null,
      ok: !!result
    };

## 变体

### 多执行者并行（fan-out）

    const items = [/* 子任务列表 */];
    const results = await parallel(items.map(item => () =>
      agent("执行子任务: " + item, { provider: "commandcode-goat", model: "deepseek/deepseek-v4-flash" })
    ));

### 带阶段声明的正式 workflow

    meta: {
      name: "arch-exec",
      description: "架构师+执行者两层分工",
      phases: [
        { title: "架构设计", provider: "commandcode-goat", model: "deepseek/deepseek-v4-pro" },
        { title: "执行落地", provider: "commandcode-goat", model: "deepseek/deepseek-v4-flash" }
      ]
    }

## 模型切换速查

| 角色 | 当前 | Sol 开通后 |
|---|---|---|
| 架构师 | deepseek/deepseek-v4-pro | gpt-5.6-sol |
| 执行者 | deepseek/deepseek-v4-flash | deepseek/deepseek-v4-flash（不变） |
