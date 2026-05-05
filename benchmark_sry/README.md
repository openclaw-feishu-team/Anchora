# benchmark_sry 使用说明

本目录是独立于原 `benchmark.py` 的增强基准测试产物，不会改动原有测试框架。

## 目录结构

- `run_benchmark_sry.py`：独立 Runner
- `cases/p1_decision_sink_cases.json`：痛点1（决策沉没）20 条
- `cases/p2_decision_fragment_cases.json`：痛点2（决策碎片）20 条
- `cases/p3_context_invalidated_cases.json`：痛点3（上下文失效）20 条
- `cases/p4_conflict_update_cases.json`：痛点4（决策漂移/矛盾更新）20 条
- `cases/p5_repeat_push_cases.json`：痛点5（决策重复）20 条
- `docs/BENCHMARK_CASES_REFERENCE.md`：**设计理念与各 JSON 字段释义（必读）**
- `docs/benchmark解释版.md`：**非代码背景的完整 walkthrough（与 REFERENCE 互补）**
- `outputs/`：运行后自动生成报告

当前总用例数：100（5 个痛点 * 每个 20 条）

测试集 JSON **仅为纯数组**，不在文件内写文档结构；说明统一见 `docs/BENCHMARK_CASES_REFERENCE.md`。

## 运行方式

在 `feishu-memory` 目录执行：

```bash
python3 benchmark_sry/run_benchmark_sry.py run \
  --cases benchmark_sry/cases \
  --tag split20
```

说明：

- `--cases` 既支持单个 JSON 文件，也支持目录。
- 传目录时，Runner 会自动读取 `*_cases.json` 并合并执行。
- 根格式：`*_cases.json` 为 **case 数组**。Runner 仍兼容历史上的 `{ "cases": [...] }` 包裹写法，便于迁移。
- 默认会清理 `project` 前缀为 `_benchmark_sry_` 的测试数据，避免污染业务数据。

## 硬性指标对应（case 驱动）

1. 抗干扰测试：`p1_decision_sink_cases.json` 中 `P1-ANTI-001`（及同类 `anti_interference`）
2. 矛盾更新测试：`p4_conflict_update_cases.json` 全部 20 条

## 注意事项

- 当前环境若缺少 `requests` 依赖，Runner 启动会失败（因为 `memory.py` 依赖该包）。
