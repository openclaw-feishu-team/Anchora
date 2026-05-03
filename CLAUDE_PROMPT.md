# Claude Prompt：feishu-memory 高级功能补齐

## 背景

你正在为一个 OpenClaw 项目开发 **feishu-memory** 技能，用于飞书（Feishu/Lark）群聊中的项目决策长期记忆。基础功能已实现（SQLite + Chroma 向量检索、Ebbinghaus 遗忘、心跳推送、群聊自动记录、每日日志、多维表格同步、Governance 审查层）。

现在需要补齐以下 **5 个高级功能**，以匹敌竞品项目 `adjcjh777/lark_ai_challenge_openclaw_longterm_memory`。

---

## 当前代码架构

```
skills/feishu-memory/
  memory.py                   # 核心：SQLite + Chroma + Governance
  memory.md                   # Agent 长期记忆层
  SKILL.md                    # Agent 调用规范
  heartbeat.py                # DDL 主动推送
  chat_auto_record.py         # 群聊自动监听
  daily_log.py                # 每日日志
  context_engineer.py         # 上下文构建
  scripts/feishu_memory_cli.py # Agent 统一 CLI
  config.json                 # Bitable 配置
```

### 核心表结构（SQLite）

**decisions 表**：
- id, project, decision, reasoning, conclusion, objections, decision_maker, chat_id
- created_at, updated_at, ttl, last_accessed, version, superseded_by, embedding_id
- deadline (YYYY-MM-DD), status (active/candidate), evidence (原始消息), confidence (0-1)

**audit_log 表**：
- id, memory_id, action, actor, details, created_at

**memory_edges 表**：
- from_id, to_id, edge_type (Supersedes), created_at

**access_log 表**：
- memory_id, accessed_at

### 现有核心函数签名

```python
def store_decision(project, decision, reasoning="", conclusion="", objections="",
                   decision_maker="", chat_id="", ttl=2592000, deadline="",
                   evidence="", confidence=0.8) -> dict:
    # 自动 review_policy → status = 'active' or 'candidate'
    # 自动写 audit_log
    # 返回包含 id, status, superseded 的字典

def query_decisions(project=None, query_text=None, include_superseded=False,
                    top_k=10, status_filter="active") -> list:

def recall_relevant_decisions(message: str, project=None, top_k=3) -> list:

def confirm_candidate(mem_id: str, actor: str = "system") -> dict:

def write_audit_log(memory_id: str, action: str, actor: str, details: str):

def review_policy(record: dict, has_conflict: bool = False) -> str:
    # confidence<0.5 / 敏感词 / 冲突 → candidate，否则 active
```

---

## 需要实现的 5 个高级功能

### 功能 1：飞书交互式卡片（Interactive Cards）

**目标**：当产生 `candidate` 状态的记忆时，向飞书群聊发送一张可交互卡片，用户点击「确认」或「驳回」按钮即可改变记忆状态，无需手动命令行操作。

**要求**：
1. 新建 `interactive_cards.py`，封装卡片构建与回调处理
2. 卡片需包含：
   - 决策摘要（project + decision + deadline）
   - 证据链预览（evidence 前 100 字）
   - 两个按钮：「✅ 确认入库」和「❌ 驳回」
3. 按钮 callback 携带 `memory_id` 和 `action=confirm|reject`
4. 用户点击后，调用 `confirm_candidate(mem_id)` 或标记 `status='rejected'`
5. 需要写一个飞书回调 HTTP handler（可以用 Flask 或嵌入到 OpenClaw 的某个服务中）
6. 在 `feishu_memory_cli.py` 的 `cmd_record` 中，当返回 `status='candidate'` 时，自动发送交互卡片到对应的 chat_id

**参考**：飞书 Card Builder 文档，消息卡片回调机制（card.action.trigger）。

---

### 功能 2：L0/L1/L2/L3 分层检索（Layered Retrieval）

**目标**：实现多级记忆检索，从快到慢，从精确到语义，提升召回质量。

**层级定义**：

| 层级 | 名称 | 范围 | 延迟 | 触发条件 |
|------|------|------|------|----------|
| L0 | 热缓存 | 最近 24h + 高频访问 | < 1ms | 每次 query 必先查 |
| L1 | 精确匹配 | SQLite 关键词 LIKE | < 10ms | L0 未命中 |
| L2 | 向量语义 | Chroma 向量检索 | < 50ms | L1 未命中或需要扩展 |
| L3 | 深度推理 | LLM 总结归纳 | 500ms-2s | L2 结果不足或用户要求综合 |

**要求**：
1. 新建 `retrieval_engine.py`，实现 `layered_retrieve(query, project, top_k=5)` 函数
2. L0 用 Python dict 做内存缓存（基于 `access_log` 统计最近 24h 高频访问）
3. L1 复用 `query_decisions` 的 SQLite LIKE 查询
4. L2 复用 `recall_relevant_decisions` 的 Chroma 查询
5. L3 当 L0+L1+L2 合计不足 top_k 条时，调用 LLM 对已有结果进行归纳，并尝试生成「推测性回答」（如"基于历史决策，项目A倾向于选择 Vue3，但最近一次评审在3月，可能需要重新确认"）
6. 在 `feishu_memory_cli.py query` 命令中增加 `--layer L2` 参数支持

---

### 功能 3：Cognee 知识图谱集成（Knowledge Graph）

**目标**：将决策记忆构建成知识图谱，支持「项目A的决策人是谁」「哪些决策与 Vue3 有关」等关系型查询。

**要求**：
1. 可选集成 `cognee`（Python 知识图谱库），如果环境不允许则手写简化版
2. 手写简化版方案：
   - 新建 `knowledge_graph.py`
   - 实体类型：Project, Decision, Person, Technology, Deadline
   - 关系类型：DECIDED_BY, BELONGS_TO, USES, HAS_DEADLINE, SUPERSEDES
   - 每次 `store_decision` 后，自动抽取实体和关系写入 `graph.json`（或 SQLite `knowledge_triples` 表）
   - 支持查询：`kg_query("项目A的决策人")` → 解析为 SPARQL-like 查询并在 triples 表中执行
3. 实体抽取可用简单规则（如决策人 → Person，项目名 → Project，技术名词 → Technology）
4. 在 `feishu_memory_cli.py` 中增加 `kg` 子命令：
   ```bash
   python scripts/feishu_memory_cli.py kg "项目A用了哪些技术"
   ```

---

### 功能 4：记忆质量评估框架（Benchmark）

**目标**：定期评估记忆的准确性、完整性和时效性，生成质量报告。

**要求**：
1. 新建 `benchmark.py`
2. 评估维度：
   - **准确性**：candidate 比例过高 = 审查过严；active 中被 superseded 比例过高 = 冲突检测过松
   - **完整性**：deadline 提取率（有 deadline 的决策 / 总决策）
   - **时效性**：遗忘率（FORGOTTEN 决策数 / 总决策数），平均访问间隔
   - **覆盖率**：项目覆盖率（有决策的项目数 / 总项目数）
3. 生成 `benchmarks/YYYY-MM-DD.json` 报告
4. 在 `daily_log.py` 中集成 benchmark 摘要，每天日志附带质量评分
5. CLI 命令：
   ```bash
   python benchmark.py run
   python benchmark.py report --days 7
   ```

---

### 功能 5：管理后台（Admin Dashboard）

**目标**：一个简单的本地 Web 界面，用于查看和管理记忆。

**要求**：
1. 新建 `dashboard.py`，使用 Flask 或纯 HTTP server
2. 页面功能：
   - `/` — 概览：总决策数、active/candidate 比例、今日新增、即将到期 DDL
   - `/decisions` — 决策列表，支持按 project/status/keyword 过滤
   - `/decisions/<id>` — 决策详情，显示审计历史、证据链、关系图谱
   - `/review` — 待审核队列（candidate 列表），带「确认」和「驳回」按钮
   - `/audit` — 审计日志查询
3. 样式用简单内联 CSS 即可，不需要复杂前端框架
4. 启动命令：
   ```bash
   python dashboard.py --port 8080
   ```
5. 在 `SKILL.md` 中增加 Dashboard 说明

---

## 实现约束

1. **所有新增代码必须兼容 Windows + Python 3.10+**
2. **不得引入重型依赖**（如需要 Flask，先检查是否已安装，否则 fallback 到 http.server）
3. **所有数据库变更必须通过 `_ensure_*_columns()` 兼容旧表**
4. **所有新模块必须能在 `feishu_memory_cli.py` 中通过子命令调用**
5. **保持现有代码风格，中文注释，函数文档字符串**
6. **错误处理：任何异常不得阻塞主流程，必须 try/except + stderr 输出**

---

## 输出要求

请按以下顺序输出代码：

1. `interactive_cards.py`（含卡片构建 + 回调处理）
2. `retrieval_engine.py`（L0-L3 分层检索）
3. `knowledge_graph.py`（简化版知识图谱）
4. `benchmark.py`（质量评估框架）
5. `dashboard.py`（管理后台）
6. `feishu_memory_cli.py` 的增量修改（只显示修改部分）
7. `memory.py` 的增量修改（如需修改，只显示修改部分）
8. `SKILL.md` 的增量修改（新增命令和文档）

每个文件开头注明：文件路径、修改类型（新增/修改）、依赖说明。
