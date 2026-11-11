# 重要度分级 v2 实施计划

- 日期：2026-08-04
- 依据设计文档：`docs/superpowers/specs/2026-08-04-importance-rules-v2-design.md`
- 说明：本机未安装 writing-plans 技能，按同等结构手工编写本计划。

## 阶段 0：准备

- 备份 `app/importance.py`、`app/main.py`、`config/importance_rules.yaml`、`tests/test_importance.py` 到 `backup-importance-v1-20260804/`。
- 确认 Python 运行路径（当前 Codex 沙箱 PATH 无 python；由用户提供或用户自行执行测试命令）。

## 阶段 1：app/importance.py

1. `ImportanceResult` 增加 `track`、`matched_tracks`、`capped` 字段。
2. 新增 `_rule_match()` 内部函数：负面词、主体/动作/场景、boost 命中判断。
3. 重写 `score_article()`：
   - 每条命中规则得分 = base_score + boost 加分 + 官方来源加分（可选）+ 多规则印证加分（可选）+ 分类加分（可选）；
   - 按 level_cap 约束级别；
   - 多规则命中时取最高级别、同级取最高分；
   - 记录主轨道与命中轨道集合。
4. 新增 `finalize_importance()`：5 名额分配 + 选情保底 + 未选中降级。
5. 扩展 `validate_rules_config()` 校验 v2 结构。
6. `classify_articles()`、`select_highlights()` 保持兼容。

## 阶段 2：config/importance_rules.yaml

- 写入 v2 配置：阈值 85/65、max_highlights 5、total_cap 5、选情保底 1、评分配置。
- 初始 16 条规则（4 条选情 + 12 条政经安全），含用户校准案例所需关键词。
- 移除 `confidence_levels`。

## 阶段 3：app/main.py

- 导入 `validate_rules_config`、`finalize_importance`。
- 加载规则后立即校验，结构错误打印原因并退出。
- `classify_articles()` 后调用 `finalize_importance()`，日志记录分配前后数量。

## 阶段 4：tests/test_importance.py

- 迁移样例配置到 v2。
- 新增校准用例（4 条用户案例，加载真实配置断言）。
- 新增名额分配、level_cap、boost、官方来源、接见区分、v2 校验测试。

## 阶段 5：验证

1. `python -m pytest tests/test_importance.py -v`
2. `python -m pytest -q`（全量回归）
3. `python -m app.main --dry-run`，观察 importance summary（critical+important ≤5）与 reasons
4. 观察 2～3 个真实推送周期

## 阶段 6：文档

- 更新 README 中重要度分级说明（双轨道、5 名额）。
