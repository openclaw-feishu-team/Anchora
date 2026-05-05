# benchmark_sry 测试集说明（设计理念与字段释义）

本文档整合 **`cases/*.json`** 的设计理念与各字段含义。JSON 文件仅保留「可执行数据」（数组），避免混入文档结构；阅读与答辩请以本 MD 为准。

Runner：`benchmark_sry/run_benchmark_sry.py`。加载规则简述：

- `*_cases.json`：根节点为 **case 对象数组**。
- 仍兼容旧版 `{ "cases": [...] }`（若你本地曾用过包裹格式），但 **当前仓库已统一为纯数组**。

测试数据默认使用 `project` 前缀 `_benchmark_sry_`，Runner 运行前后会清理，避免污染业务库。

---

## 通用字段（多数 case）

| 字段 | 含义 |
|------|------|
| `case_id` | 唯一编号，报告定位用。 |
| `case_type` | Runner 分发类型，见各痛点小节。 |
| `pain_point` | 白皮书痛点中文名。 |
| `points` | 本条满分；部分通过按检查项比例折算。 |
| `project` | 可选。SQLite/Chroma 隔离用项目名；省略时为 `_benchmark_sry_{case_id}`。 |

---

## P1 决策沉没 — `p1_decision_sink_cases.json`

### 设计理念

群聊噪声高，关键决策易被淹没。本文件两类验证：

1. **`decision_capture`**：对齐 `chat_auto_record` 的触发门槛（dry-run）与入库后的 `reasoning` 关键词断言。
2. **`anti_interference`（抗干扰）**：先锚定一条「一周前」关键记忆（通过回拨 `created_at` 模拟），再在同 project 注入多条干扰决策与大量闲聊噪声（闲聊不入库，仅计条数），最后用固定 query 检索，要求 Recall 排位与禁止泄漏项成立。

### `decision_capture` 字段

| 字段 | 含义 |
|------|------|
| `text` | 单条飞书消息正文。 |
| `expected_candidate` | `true` 期望触发自动记录；`false` 为负例，期望不触发。 |
| `expected_output.reason_keyword` | 可选；断言入库记录 `reasoning` 含该子串。 |

### `anti_interference` 字段

| 字段 | 含义 |
|------|------|
| `simulate_days_ago` | 锚点记录写入后，将 `created_at` 向前拨若干天，模拟「一周前注入」。 |
| `anchor_event` | 必须先入库的关键结论来源文本。 |
| `distractor_events` | 同 project 下额外「决策型」消息，增加检索混淆。 |
| `noise_events` | 闲聊列表；Runner 仅 `dry_run` 计数，**不入库**，用于满足「大量无关输入」强度。 |
| `query` | 检索句；支持 `/recall`、`/query` 前缀（Runner 会剥离）。 |
| `top_k` | `query_decisions` 返回上限。 |
| `min_noise_inputs` | 断言 `noise_events` 条数下限。 |
| `expected_output.expected_active_value` | 检索结果 `decision` 应出现的子串。 |
| `expected_output.max_rank` | 允许的最高排位（如 1 表示 Recall@1）。 |
| `expected_output.forbidden_values` | 不应在 Top-N 结果正文中出现的子串列表。 |

---

## P2 决策碎片 — `p2_decision_fragment_cases.json`

### 设计理念

同一主题分散在「群聊 / 文档 / 评审」等多段表述中，最后由 `/decide` 拍板句收口。**不评测是否合并为单条 DB 记录**，而评测：同一 `project` 下多条碎片入库后，用短 query 能否在 Top3 命中最终口径，且返回的多条记录中，`evidence`（各轮全文）能覆盖足够多的关键词。

**命题约束**：`evidence_keywords` 应分散出现在不同 `events` 原文中，以便写入 SQLite `evidence` 列后可被统计；`expected_active_value` 宜为拍板句中稳定出现的短子串，降低规则抽取抖动。

### `decision_aggregation` 字段

| 字段 | 含义 |
|------|------|
| `events` | 按序注入的消息；可为字符串或 `{"content":"..."}`。前缀「群聊：」「文档：」等仅便于人类阅读。以 **`/decide `** 开头的行为拍板行，Runner 去前缀后再抽取入库。 |
| `query` | 聚合检索用语。 |
| `expected_output.expected_active_value` | 检索结果 `decision` 应含该子串（Top3 内）。 |
| `expected_output.evidence_keywords` | 关键词列表；Runner 在返回结果集上统计「任一行的 reasoning 或 evidence 含该词」的命中次数之和。 |
| `expected_output.source_count_min` | 上述命中次数之和的下限（非 distinct 关键词个数）。 |

---

## P3 决策上下文失效 — `p3_context_invalidated_cases.json`

### 设计理念

前置条件或环境变化后，旧结论可能失效。在尚未实现独立 `needs_review` 状态机的前提下，用「三轮写入」近似：**旧决策 → 失效宣告 → 新决策**。断言：新结论可检索、Top1 不泄漏旧结论话术、库中存在 supersede（覆盖）关系。

### `context_invalidated` 字段

| 字段 | 含义 |
|------|------|
| `initial_event` | 第一条：原决策（含可被推翻的前提）。 |
| `invalidating_event` | 第二条：前提被破坏或约束变化（仍入库，模拟讨论流）。 |
| `updated_event` | 第三条：新上下文下的更新决策（当前口径）。 |
| `query` | 用户追问。 |
| `expected_output.expected_active_value` | 新结论应出现的子串。 |
| `expected_output.forbidden_values` | 不应出现在检索 Top1 的旧结论子串。 |

---

## P4 决策漂移（矛盾更新）— `p4_conflict_update_cases.json`

### 设计理念

同一主题先后两条冲突指令时，须尊重时序：**新覆盖旧**，存储层存在 `superseded_by` 链（与 `memory.store_decision` 相似度冲突逻辑对齐）。检索侧：Top1 为新口径；结构侧：旧行指向新行 id。对应赛题「矛盾更新」。

### `contradiction_update` 字段

| 字段 | 含义 |
|------|------|
| `old_event` | 先写入的旧决策。 |
| `new_event` | 后写入的新决策。 |
| `query` | 追问当前有效规矩。 |
| `expected_output.expected_active_value` | 检索应命中的新决策子串。 |
| `expected_output.forbidden_values` | Top1 不应出现的旧决策关键词。 |
| `project` | 若多 case  deliberately 共用同一项目需显式指定（如周报 A/B 场景）。 |

---

## P5 决策重复 — `p5_repeat_push_cases.json`

### 设计理念

话题再次出现时应能通过 `recall_relevant_decisions` 拉回历史结论；纯关键词撞车的闲聊不应误判（负例）。当前 Runner 未实现 cooldown：`expected_reminder=true` 断言召回命中；`false` 断言未形成有效召回（或未命中预期结论）。

### `decision_repeat_push` 字段

| 字段 | 含义 |
|------|------|
| `events` | 预写入的历史决策（字符串或 `content` 对象）。 |
| `trigger_message` | 模拟后续群发言（正例偏业务重提，负例偏闲聊）。 |
| `expected_output.expected_active_value` | 正例期望召回的决策子串；负例可省略。 |
| `expected_output.expected_reminder` | 是否期望形成有效提醒/命中。 |

---

## 赛题硬性指标与文件对应

| 要求 | 对应数据 |
|------|----------|
| 抗干扰 | P1 `P1-ANTI-001`（及同类 `anti_interference`） |
| 矛盾更新 | P4 全部 case |
