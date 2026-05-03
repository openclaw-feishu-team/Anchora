---
name: feishu-memory
description: "Use this skill whenever the user wants to record, query, or recall project decisions in Feishu (Lark) group chats or personal chats. Triggers include: any mention of '记录决策', '查一下决策', '项目决策', 'decision record', '历史决策', or requests to save/query/project decisions in Feishu. Supports structured extraction (decision/reasoning/conclusion/objections/deadline), semantic vector search, Ebbinghaus forgetting, contradiction versioning (Supersedes), proactive DDL heartbeat push, auto chat listening, daily logging, and Feishu Bitable auto-sync."
permissions:
  - network
  - filesystem
---

# feishu-memory

飞书项目决策记忆 — Multi-Agent 架构 + 上下文记忆增强 + 心跳主动推送 + 群聊自动监听

支持：结构化抽取（含DDL提取）、语义向量检索、Ebbinghaus遗忘、矛盾覆盖、心跳推送、群聊自动记录、每日日志、多维表格自动同步。

## Triggers

- 记录决策 / 保存决策 / 记一下决策
- 查一下XX项目的决策 / 查看决策记录 / 历史决策
- 项目决策 / decision record
- 同步决策到多维表格
- 群聊中出现"决定/确定/结论/上线/截止/DDL/里程碑"等关键词（自动触发记录）

## CRITICAL INSTRUCTIONS FOR AGENT (群聊/私聊通用)

**YOU MUST EXECUTE COMMANDS DIRECTLY. NO PLANNING. NO SUB-AGENTS. NO EXCUSES.**

When this skill is triggered by ANY message:
1. **IMMEDIATELY** use the `exec` tool to run the exact `python` command shown in Quick Commands below
2. **THIS SKILL HAS NO NATIVE TOOL** — you MUST use `exec` with the shell command. Do NOT look for a `feishu_memory` tool.
3. **DO NOT** say "我将使用", "I will try", "让我来", "我将启动", "我将再次尝试" or ANY variant
4. **DO NOT** spawn sub-agents, tasks, or planning steps
5. **DO NOT** explain what you are about to do before doing it
6. **DO NOT** ask for confirmation or user input
7. **DO NOT** generate intermediate messages like "请稍等片刻"
8. If a command fails, retry once with the exact same command, then report the error in ONE sentence

**CORRECT behavior:**
- User: "记录决策：项目A用Vue3"
- Agent: [immediately runs command] → "已记录：项目A决定使用Vue3"

**WRONG behavior (STRICTLY FORBIDDEN):**
- ❌ "我将使用feishu-memory技能..."
- ❌ "我将启动一个新的子代理..."
- ❌ "我将再次尝试使用feishu-memory技能..."
- ❌ "我们将使用feishu-memory技能来确保..."
- ❌ "请稍等片刻，我正在处理..."

**WHY:** These phrases waste time and break user trust. The user sees NO ACTION. Execute FIRST, speak SECOND.

## Multi-Agent 架构

```
┌─────────────────────────────────────────┐
│  Feishu Message (群聊/私聊)              │
│  "项目A决定用Vue3，5月10日上线"          │
└─────────────┬───────────────────────────┘
              ▼
┌─────────────────────────────────────────┐
│  Chat Auto Listener (群聊自动监听)        │
│  • 关键词触发：决定/上线/截止/DDL         │
│  • 无需 @，自动提取决策+deadline         │
│  • 自动 record + sync 到多维表格         │
└─────────────┬───────────────────────────┘
              ▼
┌─────────────────────────────────────────┐
│  Context Engineer (上下文工程师)          │
│  • 加载 memory.md 长期记忆               │
│  • 加载最近7天决策                       │
│  • 加载指定项目的全部决策                │
└─────────────┬───────────────────────────┘
              ▼
┌─────────────────────────────────────────┐
│  Memory Agent (决策存储)                 │
│  • 规则抽取 (< 10ms) + deadline 提取     │
│  • SQLite 结构化存储                     │
│  • Chroma 向量存储                       │
└─────────────┬───────────────────────────┘
              ▼
┌─────────────────────────────────────────┐
│  Heartbeat Agent (心跳推送)              │
│  • 每12h扫描DDL节点                      │
│  • DDL前一周每三天推送提醒卡片           │
│  • 自动发送飞书群消息                    │
└─────────────┬───────────────────────────┘
              ▼
┌─────────────────────────────────────────┐
│  Daily Log Agent (每日日志)              │
│  • 每天生成 logs/YYYY-MM-DD.md           │
│  • 超30天自动归档                        │
└─────────────────────────────────────────┘
```

## Quick Commands (Agent Must Use These)

### 0. context — 构建上下文（解决遗忘，每次对话前必做）

```bash
python skills/feishu-memory/context_engineer.py build --chat-id "oc_xxx" --project "项目A"
```

### 1. record — 记录决策（最常用，自动同步到多维表格）

```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py record "项目A决定用Vue3，5月10日上线" --project "项目A" --chat "oc_xxx"
```

**特点**：
- 默认规则抽取，< 10ms 响应
- **自动提取 deadline**（日期格式：2026-05-10 / 5月10日 / 10月31日上线）
- **自动同步到飞书多维表格**（无需额外 sync 指令）
- 自动检测矛盾冲突

### 2. query — 查询决策

```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py query --project "项目A" --q "为什么选Vue3" --limit 5
```

### 3. recall — 主动推送检索

```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py recall "前端框架选什么好" --project "项目A" --limit 3
```

**返回**：`cards` 字段可直接发送到飞书的格式化决策卡片

### 4. projects — 列出所有项目

```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py projects
```

### 5. sync — 手动同步到多维表格

```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py sync --account group
```

> 注：record 已自动同步，此命令仅在需要全量手动同步时使用。

### 6. heartbeat — 检查并推送 DDL 提醒

```bash
# 立即检查（dry-run 模式只输出不发送）
python skills/feishu-memory/heartbeat.py check --dry-run

# 实际推送
python skills/feishu-memory/heartbeat.py check

# 列出未来30天关键节点
python skills/feishu-memory/heartbeat.py list --days 30
```

### 7. auto-record — 群聊消息自动记录（无需@）

```bash
# 分析单条消息并自动记录
python skills/feishu-memory/chat_auto_record.py auto "oc_xxx" "项目A决定用Vue3，5月10日上线"

# 模拟运行
python skills/feishu-memory/chat_auto_record.py auto "oc_xxx" "消息内容" --dry-run
```

### 8. daily-log — 写入/查看每日日志

```bash
# 写入今天的日志
python skills/feishu-memory/daily_log.py write

# 列出所有日志
python skills/feishu-memory/daily_log.py list
```

### 9. review — 查看待审核记忆

```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py review
```

### 10. confirm — 确认记忆为 active

```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py confirm <memory_id> --actor admin
```

### 11. reject — 驳回 candidate 记忆

```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py reject <memory_id>
```

### 12. interactive-cards — 发送 candidate 审核卡片到飞书群聊

**依赖**: `pip install lark-oapi`

**飞书后台配置（必须）**：
1. 打开 [飞书开发者平台](https://open.feishu.cn/app) → 你的应用 → **事件与回调**
2. **回调配置** → 选择 **「使用长连接接收回调」**
3. 如果没有 Encrypt Key，点击「重置」生成一对新的 **Encrypt Key** 和 **Verification Token**
4. 将这两个值填入 `openclaw.json` → `channels.feishu.accounts.group.encryptKey` 和 `verificationToken`
5. **事件配置** → 添加订阅事件：**卡片回传交互**（`card.action.trigger`）
6. **权限管理** → 搜索并开启：
   - `im:message:send_as_bot`（以机器人身份发送消息）
   - `im:chat:readonly`（读取群聊信息）

**启动长连接客户端**：
```bash
python skills/feishu-memory/interactive_cards.py ws --account group
```

**发送 candidate 卡片到群聊**：
```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py record "测试决策" --project "测试" --chat "oc_xxx" --confidence 0.3
```

当记录为 `candidate` 状态时，会自动发送审核卡片到群聊。用户点击「确认入库」或「驳回」后，长连接客户端自动处理状态变更。

### 11. reject — 驳回 candidate 记忆

```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py reject <memory_id> --actor admin
```

### 12. layered query — L0/L1/L2/L3 分层检索

```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py query --project "项目A" --q "为什么选Vue3" --limit 5 --layer L3
```

层级说明：
- `L0`：最近 24h + 高频访问热缓存
- `L1`：SQLite LIKE 精确匹配
- `L2`：Chroma 向量语义检索
- `L3`：LLM/规则综合归纳

### 13. kg — 知识图谱查询

```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py kg "项目A用了哪些技术"
python skills/feishu-memory/scripts/feishu_memory_cli.py kg --rebuild
```

### 14. interactive-cards — 飞书交互式审核卡片

```bash
python skills/feishu-memory/interactive_cards.py server --port 8787
python skills/feishu-memory/interactive_cards.py reject <memory_id> --actor admin
```

当 `record` 产生 `candidate` 状态且传入 `--chat` 时，会自动尝试向该飞书群聊发送「确认入库 / 驳回」交互式卡片。

### 15. benchmark — 记忆质量评估

```bash
python skills/feishu-memory/benchmark.py run
python skills/feishu-memory/benchmark.py report --days 7
```

报告写入：`skills/feishu-memory/benchmarks/YYYY-MM-DD.json`。`daily_log.py write` 会自动附加质量评分摘要。

### 16. dashboard — 本地管理后台

```bash
python skills/feishu-memory/dashboard.py --port 8080
```

访问：`http://127.0.0.1:8080`

页面：
- `/` 概览
- `/decisions` 决策列表
- `/decisions/<id>` 决策详情
- `/review` candidate 审核队列
- `/audit` 审计日志

## Workflow for OpenClaw Agent (解决上下文遗忘)

### 标准对话流程

```
用户发送消息 → Agent 执行以下步骤：

1. 【加载上下文】
   python skills/feishu-memory/context_engineer.py build --chat-id "{chat_id}" --project "{项目名}"
   → 获取 system_prompt（包含该会话历史决策）

2. 【判断是否需要自动记录】
   如果消息包含"决定/确定/结论/上线/截止/DDL/里程碑/日期"：
   python skills/feishu-memory/chat_auto_record.py auto "{chat_id}" "{消息内容}"
   → 自动记录 + 自动同步到多维表格

3. 【生成回复】
   使用 system_prompt 作为上下文，调用 LLM 生成回复

4. 【存储对话】
   python skills/feishu-memory/context_engineer.py store \
     --chat-id "{chat_id}" --project "{项目}" --decision "{决策摘要}"
```

### 场景 1：用户说"记录决策：项目A用Vue3，5月10日上线"

**必须执行**：
```bash
python skills/feishu-memory/scripts/feishu_memory_cli.py record "项目A决定用Vue3，5月10日上线" --project "项目A" --chat "{chat_id}"
```

**结果**：
- 决策记录到 SQLite
- **自动提取 deadline = 2026-05-10**
- **自动同步到飞书多维表格**
- 如果有冲突，返回 warning

### 场景 2：群聊中有人说"我们项目D明天要提测了"

**Agent 自动执行**（无需用户@）：
```bash
python skills/feishu-memory/chat_auto_record.py auto "{chat_id}" "我们项目D明天要提测了"
```

**结果**：
- 检测到项目词+DDL词，自动记录
- 提取 deadline（明天日期）
- 自动同步到多维表格

### 场景 3：DDL 提醒心跳推送

**每12小时自动执行**：
```bash
python skills/feishu-memory/heartbeat.py check
```

**推送规则**：
- DDL 前一周开始推送
- 每三天推送一次（避免骚扰）
- 推送过的节点记录在 `push_log` 表，不重复推送
- 卡片格式：🔴紧急 / 🟠预警 / 🟡提醒 + 项目名 + 剩余天数

## 心跳推送（Heartbeat）详细说明

```
heartbeat.py
  ├─ check    — 扫描所有带 deadline 的决策，推送需要提醒的
  ├─ list     — 列出未来 N 天的关键节点
  └─ dry-run  — 模拟运行，不实际发送
```

**推送时间线示例**：

| 距离DDL | 推送频率 |  urgency |
|---------|---------|---------|
| 7 天    | 第1次推送 | 🟡 提醒 |
| 4 天    | 第2次推送 | 🟠 预警 |
| 1 天    | 第3次推送 | 🔴 紧急 |

**配置定时任务**（建议每12小时执行一次）：
```bash
# 可以添加到系统计划任务或 OpenClaw Gateway cron
python skills/feishu-memory/heartbeat.py check
```

## 群聊自动监听（Chat Auto Record）

**触发关键词**：
- 决策词：决定、确定、结论、拍板、选定、选用、采用、敲定、方案、架构、设计、需求变更、变更、调整
- DDL词：上线、截止、ddl、deadline、里程碑、节点、交付、发布、提测、评审、验收、发版、计划、排期
- 项目词：项目、产品、模块、功能、系统、平台、服务

**触发规则**：
- 消息包含决策词 → 自动记录
- 或：消息同时包含 DDL词 + 项目词 → 自动记录

**日期提取支持格式**：
- 2026年10月31日
- 2026-10-31 / 2026/10/31
- 10月31日（默认今年）
- DDL/截止/上线 后面跟日期

## 每日日志（Daily Log）

**自动生成内容**：
- 今日新增决策数量
- 今日推送次数
- 累计决策/项目/推送总数
- 每条决策的详细信息
- 即将到达的关键节点（未来14天）

**文件位置**：`skills/feishu-memory/logs/YYYY-MM-DD.md`
- 超过30天自动归档到 `logs/archive/`

## 长期记忆层（memory.md）

**文件位置**：`skills/feishu-memory/memory.md`

**作用**：
- Agent 被艾特时固定加载的最小上下文
- 包含 Agent 身份、行为准则、数据库结构速查、关键路径
- 内容超过300行时自动蒸馏（保持言简意赅）

**当前记忆核心**：
1. 听到即记录 — 群聊中任何项目讨论自动记录
2. DDL 即生命 — 所有时间节点必须提取 deadline
3. 推送即关怀 — DDL前一周每三天推送提醒
4. 同步即归档 — 每次记录自动同步到多维表格

## Governance Layer（治理层）

新增 **Candidate → Active 审查机制**，灵感来自竞品的审计与版本治理：

### 状态机

```
store_decision() → 默认 status='candidate'
        │
        ▼
   review_policy() ──→ 低风险/无冲突 ──→ status='active'（自动确认）
        │
        └──→ 高冲突 / 敏感词 / 低置信度 ──→ status='candidate'（需人工审核）
                        │
                        ▼
              confirm_candidate(id) ──→ status='active'
```

### 审查策略（review_policy）

| 条件 | 结果 |
|------|------|
| confidence < 0.5 | candidate |
| 含敏感词（密码/token/薪资/身份证号等） | candidate |
| 与现有 active 决策冲突（相似度>0.5） | candidate |
| 其他 | active（自动确认） |

### 审计日志（audit_log）

每次 `create / confirm / reject / update` 都会写入 `audit_log`：

```bash
# 查看某条记忆的审计历史
python skills/feishu-memory/memory.py query --status all | grep <id>
```

### 证据链（evidence）

- `evidence` 字段存储原始消息内容或来源链接
- 群聊自动记录时，自动填充原始消息作为证据
- 支持溯源：任何决策可追溯到原始聊天记录

## Data Schema

### SQLite: decisions 表

| 字段 | 说明 |
|------|------|
| id | 记忆唯一ID |
| project | 项目名称 |
| decision | 决策内容 |
| reasoning | 理由/原因 |
| conclusion | 结论 |
| objections | 反对意见 |
| decision_maker | 决策人 |
| chat_id | 飞书会话ID |
| created_at | 创建时间 |
| updated_at | 更新时间 |
| ttl | 存活时间（秒） |
| superseded_by | 被哪个新决策覆盖 |
| deadline | **DDL日期（YYYY-MM-DD）** |
| status | **状态：active / candidate / forgotten** |
| evidence | **证据链（原始消息来源）** |
| confidence | **置信度 0-1** |

### SQLite: audit_log 表

| 字段 | 说明 |
|------|------|
| id | 自增ID |
| memory_id | 关联决策ID |
| action | 操作类型：create / confirm / reject / update |
| actor | 执行人（system / admin / user） |
| details | 详情 |
| created_at | 时间 |

### SQLite: push_log 表

| 字段 | 说明 |
|------|------|
| id | 唯一ID |
| decision_id | 关联决策ID |
| push_type | 推送类型（ddl_reminder / heartbeat） |
| pushed_at | 推送时间 |
| content | 推送内容摘要 |

### SQLite: decision_contexts 表

| 字段 | 说明 |
|------|------|
| id | 唯一ID |
| chat_id | 飞书会话ID |
| project | 项目名称 |
| summary | 决策摘要 |
| created_at | 创建时间 |

## Configuration

`skills/feishu-memory/config.json`：

```json
{
  "bitable": {
    "base_id": "P3GhbvtbSaWc6lsiVinc8dyXnmc",
    "table_id": "tblFyLH8VH1XSr47"
  }
}
```

## File Structure

```
skills/feishu-memory/
  SKILL.md                    # 本文档
  memory.md                   # 长期记忆层（Agent被艾特时加载）
  memory.py                   # 核心模块（Memory Agent）
  context_engineer.py         # 上下文工程师
  heartbeat.py                # 心跳推送引擎（DDL提醒）
  chat_auto_record.py         # 群聊自动监听与记录
  daily_log.py                # 每日日志生成器
  scripts/
    feishu_memory_cli.py      # Agent 统一入口
  config.json                 # 多维表格配置
  memory.db                   # SQLite 数据库
  chroma_db/                  # Chroma 向量库
  logs/                       # 每日日志
    YYYY-MM-DD.md
    archive/
```

## Performance Optimization

| 优化点 | 实现 |
|--------|------|
| 决策抽取速度 | 规则引擎优先（< 10ms），LLM 后备 |
| 语义检索速度 | API embedding + SQLite 缓存：首次~500ms，后续~10ms |
| 记忆检索速度 | Chroma 本地向量检索（< 50ms） |
| 上下文加载 | 只加载最近7天 + 项目全部决策 |
| 响应延迟 | 异步存储，不阻塞回复 |
| 自动同步 | record 后自动 sync，无需额外指令 |

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Agent 说"将启动"但不执行 | 检查 Agent 是否调用了 `feishu_memory_cli.py` |
| 上下文加载失败 | 检查 `context_engineer.py` 路径和 `--chat-id` 参数 |
| 未找到 openclaw.json | 检查 openclaw.json 路径 |
| 获取 token 失败 | 检查飞书 appId/appSecret |
| 同步失败 | 检查 Bitable 权限和 base_id/table_id |
| heartbeat 无推送 | 检查 decisions 表是否有 deadline 字段和值 |
| 自动记录未触发 | 检查消息是否包含决策/DLL/项目关键词 |

## Security

- 所有数据存储在本地 SQLite + Chroma
- 不上传任何数据到第三方（除用户主动 sync 到 Bitable）
- 向量模型通过 qwen API 调用，embedding 结果本地缓存
- 心跳推送使用配置的 group 账号发送消息
