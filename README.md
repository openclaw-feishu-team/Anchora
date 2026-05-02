# 🧠 Feishu Memory - 飞书智能决策记忆系统

> **让 AI 记住每一个重要决策，再也不遗忘**

<div align="center">

![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Python](https://img.shields.io/badge/python-3.8+-orange.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)

[🎬 演示](#-功能演示) | [✨ 特性](#-核心特性) | [🚀 快速开始](#-快速开始) | [🏗️ 技术架构](#️-技术架构) | [📖 使用指南](#-使用指南)

</div>

---

## 🎬 功能演示

### Demo 1: 群聊自动记录决策

<table>
<tr>
<td width="50%">

**📹 视频演示**

![自动记录 GIF](image/demo1.gif)

> 💡 群聊中说"项目A决定用Vue3，5月10日上线"，系统自动提取结构化信息并记录

</td>
<td width="50%">

**📸 效果截图**

![自动记录截图](image/demo1.png)

**自动提取结果：**
```json
{
  "project": "项目A",
  "decision": "使用Vue3",
  "reasoning": "React学习成本高",
  "deadline": "2026-05-10"
}
```
</td>
</tr>
</table>

---

### Demo 2: 语义检索相关决策

<table>
<tr>
<td width="50%">

**📹 视频演示**

![语义检索 GIF](image/demo2.gif)

> 💡 输入"前端框架选什么好"，系统理解语义并返回相关的 Vue3 决策

</td>
<td width="50%">

**📸 效果截图**

![语义检索截图](image/demo3.png)

**检索特点：**
- ✅ 理解"前端框架" = "Vue3/React"
- ✅ 按相关性排序
- ✅ 显示决策理由和反对意见

</td>
</tr>
</table>

---

### Demo 3: DDL 心跳推送卡片

<table>
<tr>
<td width="50%">

**📹 视频演示**

![DDL 提醒 GIF](image/demo3.gif)

> 💡 DDL 前7天开始，每3天推送一次提醒，避免遗漏

</td>
<td width="50%">

**📸 推送卡片**

![DDL 提醒截图](image/demo5.png)

**推送规则：**
```markdown
| 距离DDL | 频率 | 紧急度 |
|---------|------|--------|
| 7 天    | 第1次 | 🟡 提醒 |
| 4 天    | 第2次 | 🟠 预警 |
| 1 天    | 第3次 | 🔴 紧急 |
```
</td>
</tr>
</table>

---

### Demo 4: 多维表格自动同步

<table>
<tr>
<td width="50%">

**📹 视频演示**

![同步演示 GIF](image/demo4.gif)

> 💡 所有决策一键同步到飞书 Bitable，团队实时可见

</td>
<td width="50%">

**📸 同步效果**

![多维表格截图](image/demo4.png)

**同步特点：**
- ✅ 智能字段映射
- ✅ 自动去重
- ✅ 支持自定义表格结构

</td>
</tr>
</table>

---

## ✨ 核心特性

### 🎯 零感知记录
- **群聊自动监听** - 无需手动 @，自动识别决策关键词
- **智能结构化抽取** - 从自然语言中提取项目/决策/理由/DDL
- **毫秒级响应** - 规则引擎优先（< 10ms），LLM 后备

### 🔍 语义级检索
- **ChromaDB 向量搜索** - 理解语义相似度，不只是关键词匹配
- **Ebbinghaus 遗忘曲线** - 动态调整记忆强度，自动归档过期决策
- **混合排序策略** - 结合时间、相关性、访问频率

### ⏰ 主动提醒
- **DDL 心跳推送** - 提前7天开始提醒，每3天一次
- **智能去重** - push_log 表记录，避免重复骚扰
- **紧急度分级** - 🟡提醒 → 🟠预警 → 🔴紧急

### 🔄 版本管理
- **矛盾检测** - 自动发现冲突决策
- **Supersedes 机制** - 建立决策覆盖关系链
- **完整历史追溯** - 保留所有版本，随时回查

### 📊 团队协作
- **飞书多维表格同步** - 一键同步，团队共享
- **每日日志生成** - 自动生成决策日报
- **上下文工程师** - 解决 AI 对话遗忘问题

---

## 🚀 快速开始

### 前置要求

- Python 3.8+
- OpenClaw Gateway 运行中
- 飞书应用凭证（appId + appSecret）

### 安装步骤

#### 1️⃣ 克隆仓库

```bash
cd ~/.openclaw/skills
git clone https://github.com/openclaw-feishu-team/Anchora.git feishu-memory
```
#### 2️⃣ 安装依赖

```bash
pip install chromadb requests numpy
```
#### 3️⃣ 配置飞书凭证

创建 `config.json`：
```json
{ 
  "bitable": {
      "base_id": "<your_base_id>",
      "table_id": "<your_table_id>"
  } 
}
```
**获取 base_id / table_id：**
1. 打开飞书多维表格
2. URL 中 `base/` 后面是 `base_id`
3. `table=` 后面是 `table_id`

**权限配置：**
1. 飞书开放平台 → 应用 → 权限管理
2. 添加权限：`bitable:app`、`bitable:record`
3. 多维表格 → 分享 → 添加应用为协作者
4. 重新发布应用版本

### 基础用法

#### 📝 记录决策
```markdown
python scripts/feishu_memory_cli.py record
"项目A决定用Vue3，5月10日上线"
--project "项目A" --chat "oc_xxx"
```
#### 🔍 查询决策
```markdown
python scripts/feishu_memory_cli.py query
--project "项目A" --q "为什么选Vue3" --limit 5
```
#### 📋 列出所有项目
```markdown
python scripts/feishu_memory_cli.py projects
```
#### 🔄 同步到多维表格
```markdown
python scripts/feishu_memory_cli.py sync --account group
```
#### ⏰ 检查 DDL 提醒
```markdown
python heartbeat.py check --dry-run
```
---

## 📖 使用指南

### 场景 1：群聊自动记录

当群聊中出现以下关键词时，**自动触发记录**：

```markdown
✅ 决策词：决定、确定、结论、拍板、选定、方案、架构 
✅ DDL词：上线、截止、ddl、deadline、里程碑、交付 
✅ 项目词：项目、产品、模块、功能、系统
示例：
"我们项目D明天要提测了" → 自动记录 deadline
"前端框架确定用Vue3" → 自动记录决策
```
### 场景 2：构建对话上下文

每次对话前，Agent 自动加载历史决策：

```bash
python context_engineer.py build
--chat-id "oc_xxx" --project "项目A"
```
返回包含最近 7 天决策的 system prompt，确保 AI 不会遗忘之前的讨论。

### 场景 3：DDL 主动推送

每 12 小时自动扫描数据库，推送即将到期的节点：

#### 添加到系统计划任务
```markdown
python heartbeat.py check
``` 
**推送卡片示例：**
```markdown
🔴 紧急 项目节点提醒
📋 项目：项目A 
📝 内容：使用Vue3重构前端... 
📅 DDL：2026-05-10 
⏰ 剩余：2 天 👤 负责人：张三
—— 来自 feishu-memory 心跳推送
```

### 场景 4：每日日志生成

每天自动生成决策日报：

```bash
python daily_log.py write
```
输出文件：`logs/2026-05-01.md`

---

## 🏗️ 技术架构

### 系统架构图

```mermaid 
flowchart LR
    %% ========== 输入层 ==========
    subgraph L1["📥 输入层"]
        A["飞书消息<br/>群聊 / 私聊"]
    end

    %% ========== 监听与预处理 ==========
    subgraph L2["🔍 监听与预处理"]
        B["Chat Auto Listener<br/>群聊监听器"]

        subgraph KW["关键词分类"]
            B1["决策词<br/>决定 / 确定 / 结论"]
            B2["DDL词<br/>上线 / 截止 / deadline"]
            B3["项目词<br/>项目 / 产品 / 模块"]
        end

        C{"关键词检测"}
        E["普通消息<br/>直接回复"]

        B --> KW
        KW --> C
    end

    %% ========== 核心处理层 ==========
    subgraph L3["🧠 核心处理层 · Multi-Agent"]

        subgraph D_Block["Context Engineer"]
            D["上下文工程师"]
            D1["加载最近7天决策"]
            D2["构建 System Prompt"]
            D --> D1 --> D2
        end

        subgraph F_Block["Memory Agent"]
            F["决策存储引擎"]
            F1["规则抽取 (<10ms)"]
            F2["LLM结构化抽取（后备）"]
            F3["矛盾检测 + 版本管理"]
            F --> F1 --> F2 --> F3
        end

        subgraph R_Block["Recall Agent"]
            R["主动推送代理"]
            R1["语义向量检索"]
            R2["遗忘曲线过滤"]
            R --> R1 --> R2
        end

        D --> F --> R
    end

    %% ========== 存储层 ==========
    subgraph L4["💾 数据存储层"]

        subgraph SQL["SQLite"]
            G1["decisions"]
            G2["push_log"]
            G3["access_log"]
            G4["embed_cache"]
        end

        subgraph VEC["ChromaDB"]
            H1["decisions collection"]
            H2["预计算 embedding"]
        end
    end

    %% ========== 输出层 ==========
    subgraph L5["📤 输出与同步"]

        subgraph SYNC["数据同步"]
            I["Auto Sync"]
            J["飞书 Bitable"]
            I --> J
        end

        subgraph HEART["Heartbeat Agent"]
            K["心跳推送"]
            K1["每12h扫描DDL"]
            K2["智能去重"]
            M["发送飞书卡片"]
            K --> K1 --> K2 --> M
        end

        subgraph LOG["Daily Log"]
            O["日志Agent"]
            P["生成 Markdown<br/>logs/YYYY-MM-DD.md"]
            O --> P
        end
    end

    %% ========== 主流程 ==========
    A --> B
    C -->|匹配| D
    C -->|不匹配| E

    %% ========== 数据流 ==========
    F --> SQL
    F --> VEC
    R --> VEC

    F --> I
    K1 --> SQL
    O --> SQL
```
#### 简化版本
```mermaid
flowchart LR
    A["📥 Feishu Input"] --> B["🔍 Listener"]

    B --> C{"Intent Detection"}

    C -->|Decision / DDL| D["🧠 Multi-Agent Core"]
    C -->|Normal Chat| E["💬 Direct Reply"]

    subgraph CORE["Multi-Agent System"]
        D1["Context Engineer"]
        D2["Memory Agent"]
        D3["Recall Agent"]
        D1 --> D2 --> D3
    end

    D --> CORE

    CORE --> F["💾 Storage Layer"]

    subgraph STORAGE
        S1["SQLite<br/>结构化"]
        S2["Vector DB<br/>语义检索"]
    end

    F --> STORAGE

    STORAGE --> G["📤 Output System"]

    subgraph OUTPUT
        O1["Bitable Sync"]
        O2["Heartbeat Push"]
        O3["Daily Log"]
    end
```
### 技术栈全景图

<div align="center">

```mermaid 
flowchart LR

    %% ================= 应用层 =================
    subgraph L1["🚀 应用层 Application"]
        direction LR
        A1["OpenClaw<br/>Gateway"]
        A2["Skill<br/>规范"]
        A3["Multi-Agent<br/>架构"]
    end

    %% ================= 业务层 =================
    subgraph L2["⚙️ 业务逻辑层 Business Logic"]
        direction LR
        B6["Chat Auto<br/>Listener"]
        B1["Context<br/>Engineer"]
        B2["Memory<br/>Agent"]
        B4["Recall<br/>Agent"]
        B3["Heartbeat<br/>Agent"]
        B5["Daily Log<br/>Agent"]
    end

    %% ================= 数据层 =================
    subgraph L3["💾 数据访问层 Data Access"]
        direction LR
        C1["SQLite<br/>ORM"]
        C2["ChromaDB<br/>Client"]
        C3["Feishu API<br/>SDK"]
        C4["Qwen<br/>DashScope API"]
        C5["规则引擎"]
        C6["缓存管理器"]
    end

    %% ================= 基础设施 =================
    subgraph L4["🏗️ 基础设施层 Infrastructure"]
        direction LR
        D1["Python<br/>3.8+"]
        D2["requests / argparse<br/>hashlib / re"]
        D3["pathlib / datetime<br/>json / io"]
    end

    %% ================= 分层关系 =================
    L1 --> L2
    L2 --> L3
    L3 --> L4

    %% ================= 样式 =================
    style L1 fill:#e3f2fd,stroke:#333,stroke-width:2px
    style L2 fill:#fff3e0,stroke:#333,stroke-width:2px
    style L3 fill:#eeeeee,stroke:#333,stroke-width:2px
    style L4 fill:#e8f5e9,stroke:#333,stroke-width:2px

    classDef nodeStyle fill:#ffffff,stroke:#666,stroke-width:1px,rx:10
    class A1,A2,A3,B1,B2,B3,B4,B5,B6,C1,C2,C3,C4,C5,C6,D1,D2,D3 nodeStyle
```
</div>
---

## 🛠️ 技术栈

### 核心框架

<table>
<tr>
<td width="50%">

**Python 3.8+**
- 跨平台支持
- 丰富的标准库

</td>
<td width="50%">

**OpenClaw SDK**
- AI Agent 运行时
- Skill 规范集成

</td>
</tr>
</table>

### 数据存储

#### SQLite3（5张核心表）

| 表名 | 用途 |
|------|------|
| `decisions` | 主决策表（13字段） |
| `push_log` | DDL推送日志 |
| `access_log` | 记忆访问记录 |
| `embed_cache` | Embedding缓存 |
| `decision_contexts` | 上下文摘要 |

#### ChromaDB

- 向量相似度搜索
- 预计算 embedding
- 元数据过滤查询

### AI & NLP

#### 阿里云 DashScope API

**端点：** `https://dashscope.aliyuncs.com/compatible-mode/v1`

| 模型 | 用途 |
|------|------|
| `text-embedding-v3` | 文本向量化（1024维） |
| `qwen-turbo-1101` | 结构化抽取（可选） |

#### 规则引擎

- **日期提取**：支持 `2026年10月31日`、`2026-10-31`、`10月31日`
- **项目识别**：`项目X`、`XX项目组`
- **触发词**：30+决策词、20+DDL词、7+项目词

### 飞书集成

**Feishu Open API v2**

| API | 用途 |
|-----|------|
| `auth/v3/tenant_access_token` | 获取租户令牌 |
| `im/v1/messages` | 发送消息卡片 |
| `bitable/v1/apps/.../records` | 多维表格CRUD |

### 关键算法

#### Ebbinghaus 遗忘曲线
```python
strength = exp(-age / ttl) 
if strength < 0.1: archive()
```
#### Jaccard 相似度
```python
similarity = |A∩B| / |A∪B|
if > 0.5: mark_superseded()
```
#### SHA256 缓存
```python
hash = SHA256(text)[:16] 
cache_hit_rate > 90%
```
### 设计模式

- **Multi-Agent**：6个专用Agent协作
- **策略模式**：规则优先 → LLM后备 → Fallback
- **观察者模式**：关键词监听触发
- **缓存模式**：三层缓存策略

### 性能指标

| 指标 | 数值 |
|------|------|
| 决策抽取 | < 10ms |
| 语义检索 | < 50ms |
| 缓存命中 | ~10ms |
| 同步成功率 | 95%+ |

---

## 🎯 优化亮点

### 解决的核心问题

| 问题 | 解决方案 | 效果 |
|------|---------|------|
| **上下文遗忘** | Context Engineer 加载最近7天决策 | ✅ AI 记住历史讨论 |
| **决策丢失** | 群聊自动监听，无需手动@ | ✅ 零感知记录 |
| **DDL 遗漏** | Heartbeat 每12h扫描推送 | ✅ 提前7天提醒 |
| **检索不准** | Chroma 向量语义搜索 | ✅ 理解"前端框架"="Vue3" |
| **数据孤岛** | 自动同步飞书多维表格 | ✅ 团队共享可见 |
| **API 超时** | 规则引擎优先（<10ms），LLM 后备 | ✅ 响应速度提升100倍 |
| **重复推送** | push_log 去重，每3天一次 | ✅ 避免骚扰用户 |
| **记忆膨胀** | Ebbinghaus 遗忘曲线自动归档 | ✅ 保持数据库精简 |

### 性能对比

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| 决策抽取耗时 | ~3s (LLM) | <10ms (规则) | **300x** |
| 语义检索耗时 | ~500ms (首次) | ~10ms (缓存) | **50x** |
| 向量检索耗时 | - | <50ms (Chroma) | **新增** |
| 同步成功率 | 60% | 95%+ | **58%** |
| 推送准确率 | - | 100% (去重) | **新增** |

---

## 📂 项目结构

```markdown
feishu-memory/
├── SKILL.md # OpenClaw Skill 定义
├── README.md # 本文档
├── memory.py # 核心存储引擎 (940行)
├── context_engineer.py # 上下文工程师 (182行)
├── heartbeat.py # 心跳推送引擎 (399行)
├── chat_auto_record.py # 群聊自动监听 (8KB)
├── daily_log.py # 每日日志生成器 (7.7KB)
├── scripts/
│ └── feishu_memory_cli.py # 统一 CLI 入口
├── config.json # 多维表格配置 
├── memory.db # SQLite 数据库
├── chroma_db/ # Chroma 向量库 
├── logs/ # 每日日志
│ ├── 2026-05-01.md
│ └── archive/ # 30天以上自动归档
├── image/ # 演示截图/GIF
│ ├── demo1.png
│ ├── demo1.gif
│ ├── demo2.png
│ ├── demo2.gif
│ ├── demo3.png
│ ├── demo3.gif
│ ├── demo4.png
│ └── demo4.gif
└── LICENSE
```
---

## 🐛 故障排查

| 问题 | 可能原因 | 解决方案 |
|------|---------|---------|
| Agent 说"将启动"但不执行 | Skill 未正确加载 | 检查 `SKILL.md` 权限配置 |
| 获取 token 失败 | appId/appSecret 错误 | 检查 openclaw.json 配置 |
| 同步失败 | Bitable 权限不足 | 确认应用有表格读写权限 |
| 向量检索无结果 | Chroma DB 未初始化 | 删除 `chroma_db/` 重启 |
| heartbeat 无推送 | decisions 表无 deadline | 检查记录时是否提取日期 |
| 自动记录未触发 | 消息不含关键词 | 添加新关键词到白名单 |

---

## 📈 路线图

- [ ] 支持更多飞书消息类型（图片、文件）
- [ ] 增加决策影响力评分
- [ ] 支持多人协作标注
- [ ] 导出为 Notion/Obsidian
- [ ] Web UI 管理界面
- [ ] 支持 Slack/Discord 等其他平台

---

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

---

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

---

## 🙏 致谢

- [OpenClaw](https://github.com/openclaw/openclaw) - 强大的 AI Agent 框架
- [ChromaDB](https://www.trychroma.com/) - 轻量级向量数据库
- [飞书开放平台](https://open.feishu.cn/) - 完善的 API 支持
- [Qwen](https://tongyi.aliyun.com/qianwen/) - 优秀的中文大模型

---

<div align="center">

**Made with ❤️ by Feishu Memory Team**

⭐ 如果这个项目对你有帮助，请给我们一个 Star！

[🔝 回到顶部](#-feishu-memory---飞书智能决策记忆系统)

</div>
