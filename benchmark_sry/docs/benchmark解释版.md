# benchmark_sry 大白话说明

面向不熟悉代码的读者：把 **`benchmark_sry`** 当成「一个小裁判程序」——它自动把设定好的题目喂给 **feishu-memory 记忆系统**，逐题打分，最后生成成绩单和报告文件。下面全部对照仓库里**真实题目**（题号、原文路径），不另编抽象故事。

---

## 1. 这个东西到底是干嘛的？

**feishu-memory** 会把聊天里的决策写进数据库（SQLite），有时还会用向量库（Chroma）做搜索。

**`benchmark_sry/run_benchmark_sry.py`** 就是裁判：

- 按 **`benchmark_sry/cases/`** 下 5 个 JSON（每个痛点一个文件）一共 **100 道题**的顺序做题；
- 每道题要么整题通过，要么不通过（有的题有多项检查，会出现「部分通过」）；
- 算总分、算汇总指标（抗干扰、矛盾更新、重复推送等）；
- 在 **`benchmark_sry/outputs/`** 里生成 **一个 `.json`（机器可读）** 和 **一个 `.md`（人可读）**。

它和项目根目录的 **`benchmark.py`** 是两套东西：**benchmark_sry** 专门为五大痛点另起炉灶，**不修改旧 benchmark**。

---

## 2. 跑一趟之前、之中、之后，电脑在干什么？

可以想成「考试流程」。

### 开始前

1. **初始化数据库**：确保表和字段齐全（和平时 `memory.py` 一样）。
2. **大扫除**：删掉所有 **`project` 名字以 `_benchmark_sry_` 开头的假数据**（决策、访问日志、审计、图谱边等），避免和上次测试混在一起，也尽量不伤真实业务数据（只要真实项目名不是这个前缀）。

### 考试中

3. **逐题执行**：每道题对应 JSON 里的一个对象（含 `case_id`、`case_type` 等）。
4. **`case_type`** 决定裁判走哪套逻辑（见下文第 4 节）。
5. **打分**：每题有 **`points`**（满分）。一题里常有**多项检查**；**通过了几项，就按比例拿几分**。

### 结束后

6. **再大扫除**：同样删掉 `_benchmark_sry_` 前缀的假数据。
7. **写报告**：总分、每题结果、核心指标汇总写入 `outputs/`。

因此 **`memory.db` 在运行过程中可能短暂出现测试数据**，跑完又会被清掉，这是刻意的。

---

## 3. 每条题都有的「通用零件」

| 字段 | 大白话 |
|------|--------|
| **`case_id`** | 题号，如 `P1-CAP-001`，报告里用它定位哪题挂了。 |
| **`case_type`** | 题型，裁判靠它选考试流程。 |
| **`pain_point`** | 这道题挂在白皮书五痛点里的哪一类（给人看的标签）。 |
| **`points`** | 本题满分。 |
| **`project`** | 可选。**「这道题用哪个项目抽屉」**。不写则自动用 **`_benchmark_sry_` + `case_id`**，避免和别的题串抽屉。 |

字段的书面表格还可查阅：**`BENCHMARK_CASES_REFERENCE.md`**（与本文互补）。

---

## 4. 五种题型——对照真实题目说明

### 4.1 P1：`decision_capture`（决策沉没 · 该不该自动记）

**文件**：`cases/p1_decision_sink_cases.json` — **`P1-CAP-001`、`P1-CAP-002`、`P1-CAP-NEG-001`**。

**在测什么**

- 调用 **`chat_auto_record.py`** 里的 **`auto_record_message(..., dry_run=True)`**。  
  **`dry_run=True`**：**假装跑一遍自动记录规则**，看会不会判定「这条值得记」，**此时不按完整飞书同步链路真推送**；裁判后面再决定是否 **`store_decision`**。

**怎么判**

- **`expected_candidate: true`（正例）**  
  - 检查 1：dry-run 必须是「这条消息会被当成值得记的项目/决策类消息」。  
  - 检查 2（若有 **`reason_keyword`**）：真写入一条决策后，**理由字段里要出现你写的那段字**。  
    - **`P1-CAP-001`**：Phoenix 项目 APISIX 网关那句——理由里要有「团队已有etcd集群」。  
    - **`P1-CAP-002`**：日志采集选 Vector 那句——理由里要有「资源占用更低」。
- **`expected_candidate: false`（负例）**  
  - **`P1-CAP-NEG-001`**：「午饭吃什么、下午开会」—— dry-run 必须判定 **不需要自动记**。若误判成要记，本题失败。

---

### 4.2 P1：`anti_interference`（抗干扰）

**同一文件**：**`P1-ANTI-001`**。

**在测什么**

**很久以前（假装 7 天前）**已经有一句关键话：周报发给 **A 组**。后来又塞进多条**别的决策**和一长串**闲聊**。最后用固定问题「周报发给谁」检索，仍要能盯住 **「发给 A 组」**，且排位要符合设定。

**程序大致顺序**

1. 把 **`anchor_event`** 写入 **`project`**：`_benchmark_sry_p1_anti_weekly`。  
2. 把这条记录的 **写入时间往前拨 7 天**（`simulate_days_ago: 7`），不必真等一周。  
3. 把 **`distractor_events`** 三条（Redis、Vector、灰度）写入**同一 project**，故意混淆。  
4. **`noise_events`** 里几十句闲聊：**每条只做 dry-run，不入库**，用来满足「大量无关输入」的强度，并检查条数 ≥ **`min_noise_inputs`**（本题 25）。  
5. 用 **`query`**（`/recall 周报发给谁`，前缀会被去掉）调 **`query_decisions`**，最多 **`top_k`** 条。  
6. 检查：  
   - **`expected_active_value`**「周报统一发给A组」要在结果里出现，且 **`max_rank: 1`** 表示必须是 **第 1 名**。  
   - **`forbidden_values`**（发给 C 组、发给 D 组）**不能错误出现在前几名结果正文里**。

---

### 4.3 P2：`decision_aggregation`（决策碎片）

**文件**：`cases/p2_decision_fragment_cases.json` — **`P2-AGG-001`～004**。

**在测什么**

每题：**同一个主题连写四句**——前三句像「群聊 / 文档 / 评审」，第四句以 **`/decide `** 开头表示**最终拍板**。四句按顺序写入**同一个 `project`**，再用 **`query`** 搜索，要求：

- 在 **前 3 条**结果里能找到 **`expected_active_value`** 那段结论；
- **`evidence_keywords`**（如 Implicit Flow、第三方客户系统、PKCE）要在返回结果的 **`reasoning` 或 `evidence`** 里被统计到足够次数（≥ **`source_count_min`**）；关键词写在不同 `events` 原文里，就是为让它们落在各自的 **`evidence`（整条原文备份）** 里便于计数。

四套具体内容就是你 JSON 里的：**OAuth2+PKCE、Prometheus+Grafana、S3 归档与生命周期、灰度发布与一键回滚**。

---

### 4.4 P3：`context_invalidated`（上下文失效）

**文件**：`cases/p3_context_invalidated_cases.json` — **`P3-CTX-001`～004**。

**在测什么**

每题连续写 **三条**进同一抽屉：

1. **`initial_event`**：旧定论（如 PostgreSQL）。  
2. **`invalidating_event`**：前提/约束变化（如 PG 专家转岗）。  
3. **`updated_event`**：新定论（如改 MySQL）。

再用 **`query`** 检索，检查：

- **`expected_active_value`**（新结论子串）能在靠前结果里找到；  
- **`forbidden_values`**（旧结论子串）**不能出现在排名第 1 的那条决策正文里**；  
- 库里存在 **顶替链**（程序检查 **`superseded_by`** 等是否体现覆盖）。

四套对应：**PostgreSQL→MySQL、Kafka→RocketMQ、Jenkins→GitHub Actions、Elasticsearch→OpenSearch**。

---

### 4.5 P4：`contradiction_update`（矛盾更新 / 决策漂移）

**文件**：`cases/p4_conflict_update_cases.json` — **`P4-CONFLICT-001`～004**。

**在测什么**

只两轮：**先 `old_event`，再 `new_event`**。例如 **`P4-CONFLICT-001`**：先「周报发给 A」，再「不对，发给 B」；搜索「周报发给谁」时要：

- 能命中 **发给 B**；  
- **排名第 1 的结果里不能再拿着发给 A 当过期货**（对照 **`forbidden_values`**）；  
- 库里旧记录 **`superseded_by`** 指向新记录（与 `memory.store_decision` 冲突覆盖逻辑一致）。

其余三道：**告警多通知技术负责人、发版窗口周五改周三、周会周一改周二**。

---

### 4.6 P5：`decision_repeat_push`（决策重复）

**文件**：`cases/p5_repeat_push_cases.json`。

**在测什么**

1. 把 **`events`** 里的历史决策写入抽屉（如网关 APISIX、OAuth2+PKCE、Redis Cluster）。  
2. 用 **`trigger_message`** 模拟群里又来了相关话题（如要不要换 Kong、Implicit Flow、是否重讨论缓存）。  
3. 调用 **`recall_relevant_decisions`**（根据新消息尝试拉回旧决策）。

**怎么判**

- **`expected_reminder: true`**（**`P5-PUSH-001`～003**）：召回结果里要能命中 **`expected_active_value`**。  
- **`expected_reminder: false`**（**`P5-PUSH-NEG-001`～`P5-PUSH-NEG-005`**）：「Redis **咖啡店**排队」等闲聊——**不应**误判成要推送相关决策；若仍召回决策则本题失败。

---

## 5. 报告里的汇总指标是什么意思？

跑完后 **`summary`** 常见项：

- **`total_cases`**：总题数（100）。  
- **`earned_points` / `total_points`**：得分 / 满分。  
- **`score_percent`**：百分制得分。

**`metrics`** 里典型项：

- **`case_pass_rate`**：整题全通过的占比。  
- **`case_partial_rate`**：只通过部分的占比。  
- **`anti_interference_recall_at_1`**：抗干扰是否「第一名就对」（1.0 表示本题维度过关）。  
- **`contradiction_overwrite_success_rate`**：矛盾更新类「新结论 + 顶替链」同时满足的占比。  
- **`stale_leakage_rate`**：旧结论不该出现时仍泄漏的比例（越低越好）。  
- **`repeat_push_hit_rate`**：决策重复类里「该召回则召回」的命中占比。

---

## 6. 和仓库其它代码的关系（只需记住结论）

- **`run_benchmark_sry.py`** 依赖 **`memory.py`**：**`extract_decision_structured`**（抽结构化字段）、**`store_decision`**（入库）、**`query_decisions`**（查）、**`recall_relevant_decisions`**（按新消息召回）。  
- **P1 捕获题**还依赖 **`chat_auto_record.auto_record_message`**（测自动记账门槛）。  
- 若 Chroma 向量检索报错（终端可能出现「语义检索警告」），结果会更依赖 SQLite 路径或出现异常召回——属于底层检索配置问题，不是题目 JSON 写错就一定看不出来。

---

## 7. 相关文件一览

| 路径 | 用途 |
|------|------|
| `benchmark_sry/run_benchmark_sry.py` | 裁判主程序 |
| `benchmark_sry/cases/p*_*.json` | 五大痛点共 100 道题 |
| `benchmark_sry/docs/BENCHMARK_CASES_REFERENCE.md` | 字段表格式说明 |
| `benchmark_sry/README.md` | 如何运行命令 |
| `benchmark_sry/outputs/` | 每次运行生成的报告 |

---

*本文档由助手根据 benchmark_sry 设计与题库整理，与代码行为一致；若代码更新，请以 `run_benchmark_sry.py` 与 `BENCHMARK_CASES_REFERENCE.md` 为准并对照修订本文。*
