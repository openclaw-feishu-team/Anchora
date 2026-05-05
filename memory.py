#!/usr/bin/env python3
# Windows 兼容：如果 python3 不存在，尝试 python
import sys
import os
if sys.platform == "win32" and sys.version_info.major < 3:
    os.execvp("python", ["python"] + sys.argv)

"""
飞书项目决策记忆 - 增强版
支持：SQLite 结构化存储 + Chroma 向量检索 + Ebbinghaus 遗忘 + 矛盾覆盖 + 主动推送

命令：
  add      - 添加/抽取决策记录（支持 LLM 结构化抽取）
  query    - 语义+关键词混合查询项目决策
  list     - 列出所有项目
  recall   - 根据新消息检索相关历史决策（主动推送）
  sync     - 同步到飞书多维表格
  forget   - 清理过期记忆

Usage:
  python memory.py add --raw "我们决定用Vue3，因为React学习成本高" --project "项目A" --decision-maker "张三" --chat-id "oc_xxx"
  python memory.py query --project "项目A" --q "为什么选Vue3"
  python memory.py recall --message "我们前端框架选什么好"
  python memory.py sync --account group
"""
import argparse
import json
import sys
import os
import sqlite3
import hashlib
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

# 路径控制：全部在 skill 目录下，不超过两层
SCRIPT_DIR = Path(__file__).parent.resolve()
DB_FILE = SCRIPT_DIR / "memory.db"
CONFIG_FILE = SCRIPT_DIR / "config.json"

# Chroma 向量库路径
CHROMA_DIR = SCRIPT_DIR / "chroma_db"

# 嵌入维度（qwen text-embedding-v3 是 1024 维）
EMBEDDING_DIM = 1024


def _get_api_key():
    """从 openclaw.json 获取 qwen API key"""
    try:
        cfg = get_openclaw_config()
        auth_path = Path(cfg.get("agents", {}).get("defaults", {}).get("workspace", Path.home() / ".openclaw" / "workspace")) / ".." / "agents" / "main" / "agent" / "auth-profiles.json"
        auth_path = auth_path.resolve()
        if auth_path.exists():
            auth = json.loads(auth_path.read_text(encoding="utf-8"))
            return auth.get("profiles", {}).get("qwen:default", {}).get("apiKey", "")
    except Exception:
        pass
    return ""


def _embed_with_api(texts):
    """调用 qwen embedding API，带本地缓存"""
    api_key = _get_api_key()
    if not api_key:
        return None

    # 检查缓存
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS embed_cache (
            text_hash TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            embedding TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')

    results = []
    uncached_texts = []
    uncached_indices = []

    for i, text in enumerate(texts):
        text_hash = hashlib.sha256(str(text).encode()).hexdigest()[:16]
        c.execute("SELECT embedding FROM embed_cache WHERE text_hash=?", (text_hash,))
        row = c.fetchone()
        if row:
            results.append((i, json.loads(row[0])))
        else:
            uncached_texts.append(str(text))
            uncached_indices.append(i)
            results.append((i, None))

    # 批量调用 API（最多 25 条/批）
    if uncached_texts:
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            resp = requests.post(url, headers=headers, json={
                "model": "text-embedding-v3",
                "input": uncached_texts,
            }, timeout=30)
            data = resp.json()
            if "data" in data:
                for idx_in_batch, item in enumerate(data["data"]):
                    embedding = item["embedding"]
                    original_idx = uncached_indices[idx_in_batch]
                    text = uncached_texts[idx_in_batch]
                    text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
                    c.execute(
                        "INSERT OR REPLACE INTO embed_cache (text_hash, text, embedding, created_at) VALUES (?, ?, ?, ?)",
                        (text_hash, text, json.dumps(embedding), now_iso())
                    )
                    results[original_idx] = (original_idx, embedding)
            conn.commit()
        except Exception as e:
            print(f"Embedding API 调用失败: {e}", file=sys.stderr)
            conn.close()
            return None

    conn.close()
    # 按原始顺序返回
    return [emb for _, emb in sorted(results, key=lambda x: x[0])]


def get_embedder():
    """获取嵌入函数。优先 API embedding（效果好，有缓存），失败则 fallback"""
    def api_embed(texts):
        result = _embed_with_api(texts)
        if result is not None:
            return result
        # API 失败，fallback
        return simple_embed(texts)

    # Fallback：简单字符 n-gram 哈希嵌入
    def simple_embed(texts):
        results = []
        for text in texts:
            vec = [0.0] * EMBEDDING_DIM
            text = str(text).lower()
            for i in range(len(text) - 2):
                idx = hash(text[i:i+3]) % EMBEDDING_DIM
                vec[idx] += 1.0
            norm = sum(x*x for x in vec) ** 0.5
            if norm > 0:
                vec = [x/norm for x in vec]
            results.append(vec)
        return results

    return api_embed


def get_chroma_client():
    """获取 Chroma 客户端"""
    import chromadb
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def get_collection():
    """获取决策向量集合（纯存储，embedding 由调用方预计算传入）"""
    client = get_chroma_client()
    collection = client.get_or_create_collection(name="decisions")
    return collection


def get_embedding_for_texts(texts):
    """批量获取文本的 embedding（API + 缓存）"""
    embed_fn = get_embedder()
    return embed_fn(texts)


# ─── SQLite 数据库操作 ───

def init_db():
    """初始化 SQLite 表结构"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 决策主表
    c.execute('''
        CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY,
            project TEXT NOT NULL,
            decision TEXT NOT NULL,
            reasoning TEXT,
            conclusion TEXT,
            objections TEXT,
            decision_maker TEXT,
            chat_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            ttl INTEGER DEFAULT 2592000,
            last_accessed TEXT,
            version INTEGER DEFAULT 1,
            superseded_by TEXT,
            embedding_id TEXT
        )
    ''')
    # 关系表（Supersedes 等）
    c.execute('''
        CREATE TABLE IF NOT EXISTS memory_edges (
            from_id TEXT NOT NULL,
            to_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (from_id, to_id, edge_type)
        )
    ''')
    # 访问日志（用于遗忘计算）
    c.execute('''
        CREATE TABLE IF NOT EXISTS access_log (
            memory_id TEXT NOT NULL,
            accessed_at TEXT NOT NULL
        )
    ''')
    # 审计日志（治理层：记录每次操作）
    c.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT NOT NULL,
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()


def db_conn():
    return sqlite3.connect(DB_FILE)


def _ensure_governance_columns():
    """确保 governance 相关列存在（兼容旧数据库）
    旧数据默认回填为 active，避免查询丢失历史记录。"""
    conn = db_conn()
    c = conn.cursor()
    cols_to_add = [
        ("status", "TEXT DEFAULT 'candidate'"),
        ("evidence", "TEXT"),
        ("confidence", "REAL DEFAULT 0.8"),
    ]
    for col_name, col_def in cols_to_add:
        try:
            c.execute(f"SELECT {col_name} FROM decisions LIMIT 1")
        except sqlite3.OperationalError:
            c.execute(f"ALTER TABLE decisions ADD COLUMN {col_name} {col_def}")
            if col_name == "status":
                c.execute("UPDATE decisions SET status='active' WHERE status IS NULL")
            if col_name == "confidence":
                c.execute("UPDATE decisions SET confidence=0.8 WHERE confidence IS NULL")
    conn.commit()
    conn.close()


def generate_id(*parts):
    """生成确定性 ID"""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]
    return h


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# ─── 记忆提取（结构化） ───

def extract_decision_structured(raw_text: str, project: str = "", maker: str = "", use_llm: bool = False) -> dict:
    """
    从原始文本中提取结构化决策信息。
    默认使用快速规则抽取（避免 LLM 调用超时）。
    仅在明确指定 use_llm=True 时才调用 LLM。
    """
    # 快速路径：优先规则抽取（< 10ms）
    text = raw_text.strip()
    result = {
        "project": project or _extract_project(text),
        "decision": text,
        "reasoning": "",
        "conclusion": text,
        "objections": "",
        "decision_maker": maker or "",
        "deadline": "",
    }

    # 简单规则：提取"因为/原因是"后面的内容作为 reasoning
    reason_match = re.search(r"(?:因为|原因是|理由是|考虑到)[：:]?\s*(.+?)(?:[。；]|$)", text)
    if reason_match:
        result["reasoning"] = reason_match.group(1).strip()
    else:
        # 口语化理由提取：取第一个逗号后的补充内容作为 reasoning
        # 适用于 "决策声明，理由1，理由2" 的口语化表达
        if "，" in text:
            parts = text.split("，", 1)
            if len(parts) > 1:
                reasoning_text = parts[1].strip("，。； ")
                # 过滤掉末尾的语气词（如"就它吧"、"算了"）
                reasoning_text = re.sub(r"(?:就它吧|就这个吧|算了|吧)$", "", reasoning_text).strip("，。； ")
                if reasoning_text:
                    result["reasoning"] = reasoning_text

    if "观察" in text and "观察" not in result.get("reasoning", ""):
        observe_match = re.search(r"([^，。；]*观察[^，。；]*)", text)
        if observe_match:
            result["reasoning"] = observe_match.group(1).strip("，。； ")

    # 提取"但是/反对/不过"后面的内容作为 objections
    obj_match = re.search(r"(?:但是|反对|不过|然而)[：:]?\s*(.+?)(?:[。；]|$)", text)
    if obj_match:
        result["objections"] = obj_match.group(1).strip()

    # 提取"结论是/决定"后面的内容作为 decision（显性决策词）
    dec_match = re.search(r"(?:决定|结论|确定)[：:]?\s*(.+?)(?:[。；]|$)", text)
    if dec_match:
        result["decision"] = dec_match.group(1).strip()
        result["conclusion"] = dec_match.group(1).strip()
    else:
        # 口语化决策提取：识别"动作+对象"的核心决策句
        # 模式："[前缀]，[动作][对象]吧" 或 "[动作]到[对象]"
        # 尝试提取包含技术名词/动作词的子句作为核心 decision
        action_pattern = re.search(
            r"(?:换|改|用|走|选|采用|迁移|切到|继续用|统一走|发|通知|保留|执行|归档|响应|上线|发布|投产)" +
            r"[^，。；]*(?:[A-Z][a-zA-Z0-9]*|[\u4e00-\u9fff]{2,})",
            text
        )
        if action_pattern:
            core = action_pattern.group(0).strip("，。； ")
            if len(core) >= 4:
                result["decision"] = core
                result["conclusion"] = core

    # 提取 deadline（日期）
    result["deadline"] = _extract_deadline_from_text(text)

    # 仅在明确要求且规则抽取结果不完整时，才尝试 LLM
    if use_llm and (not result["reasoning"] or not result["project"]):
        try:
            llm_result = _try_llm_extract(raw_text)
            if llm_result:
                # 合并 LLM 结果，但不覆盖已有值
                for key in ["project", "decision", "reasoning", "conclusion", "objections"]:
                    if not result.get(key) and llm_result.get(key):
                        result[key] = llm_result[key]
        except Exception:
            pass  # LLM 失败不影响规则结果

    return result


def _extract_deadline_from_text(text: str) -> str:
    """从文本中提取日期/DDL"""
    # 2026年10月31日 / 2026年10月31号
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})[日号]', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 2026-10-31 / 2026/10/31
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y}-{mo:02d}-{d:02d}"
    # 10月31日
    m = re.search(r'(\d{1,2})月(\d{1,2})[日号]', text)
    if m:
        year = datetime.now(timezone.utc).year
        return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    # DDL/截止/上线 后面跟日期
    m = re.search(r'(?:ddl|截止|上线|交付|deadline|里程碑|节点)[\s:：]*(\d{4}-\d{2}-\d{2})', text, re.I)
    if m:
        return m.group(1)
    return ""


def _extract_project(text: str) -> str:
    """从文本中提取项目名称"""
    # 匹配 "项目X" 或 "XX项目"
    m = re.search(r"([\w\u4e00-\u9fff]+项目|[\w\u4e00-\u9fff]+项目组)", text)
    if m:
        return m.group(1)
    m = re.search(r"项目\s*([\w\u4e00-\u9fff]+)", text)
    if m:
        return f"项目{m.group(1)}"
    return "未分类"


def _try_llm_extract(text: str) -> dict:
    """尝试调用本地 LLM 进行结构化抽取"""
    try:
        cfg = get_openclaw_config()
        if not cfg:
            return None

        # 读取模型配置
        model_cfg = cfg.get("models", {})
        providers = model_cfg.get("providers", {})

        # 优先使用 qwen
        provider = providers.get("qwen") or list(providers.values())[0]
        if not provider:
            return None

        base_url = provider.get("baseUrl", "")
        api_key = _get_provider_api_key(cfg, "qwen")

        if not base_url or not api_key:
            return None

        prompt = f"""从以下文本中提取结构化决策信息，返回 JSON：
{{
  "project": "项目名称",
  "decision": "决策内容（一句话）",
  "reasoning": "理由/原因",
  "conclusion": "结论",
  "objections": "反对意见（如有）"
}}

文本：{text}

只返回 JSON，不要其他内容。"""

        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "qwen-turbo-1101",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=30
        )
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        # 提取 JSON
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            return json.loads(json_match.group())
    except Exception:
        pass
    return None


def _get_provider_api_key(cfg, provider_name):
    """从 openclaw.json 获取 API key"""
    auth = cfg.get("auth", {})
    profiles = auth.get("profiles", {})
    key = f"{provider_name}:default"
    if key in profiles:
        # 读取 credentials 中的实际 key
        creds = cfg.get("credentials", {})
        cred_key = profiles[key].get("credential")
        if cred_key and cred_key in creds:
            return creds[cred_key].get("apiKey", "")
    return ""


# ─── 核心存储 ───

def _sensitive_keywords():
    """敏感关键词列表"""
    return ["密码", "secret", "token", "api_key", "薪资", "工资", "salary", "身份证号", "手机号"]


def review_policy(record: dict, has_conflict: bool = False) -> str:
    """
    审查策略：决定新记忆是 candidate 还是 active
    - 低置信度 (<0.5) → candidate
    - 含敏感词 → candidate
    - 与现有 active 决策冲突 → candidate
    - 否则 → active（自动确认）
    """
    confidence = record.get("confidence", 0.8)
    text = f"{record.get('decision', '')} {record.get('reasoning', '')}"
    if confidence < 0.5:
        return "candidate"
    if any(kw in text for kw in _sensitive_keywords()):
        return "candidate"
    if has_conflict:
        return "candidate"
    return "active"


def write_audit_log(memory_id: str, action: str, actor: str = "system", details: str = ""):
    """写入审计日志"""
    init_db()
    conn = db_conn()
    c = conn.cursor()
    c.execute('''
        INSERT INTO audit_log (memory_id, action, actor, details, created_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (memory_id, action, actor, details, now_iso()))
    conn.commit()
    conn.close()


def confirm_candidate(mem_id: str, actor: str = "system") -> dict:
    """将 candidate 状态的记忆确认为 active"""
    init_db()
    _ensure_governance_columns()
    conn = db_conn()
    c = conn.cursor()
    c.execute("UPDATE decisions SET status='active', updated_at=? WHERE id=? AND status='candidate'",
              (now_iso(), mem_id))
    changed = c.rowcount
    conn.commit()
    conn.close()
    if changed:
        write_audit_log(mem_id, "confirm", actor, "Candidate → Active")
        return {"status": "ok", "id": mem_id, "action": "confirmed"}
    return {"status": "noop", "id": mem_id, "message": "记录不存在或已经是 active"}


def store_decision(project, decision, reasoning="", conclusion="", objections="",
                   decision_maker="", chat_id="", ttl=2592000, deadline="",
                   evidence="", confidence=0.8) -> dict:
    """
    存储一条决策记录到 SQLite + Chroma
    ttl: 默认 30 天（秒）
    deadline: YYYY-MM-DD 格式，用于心跳推送
    evidence: 证据链（原始消息内容或来源）
    confidence: 置信度 0-1
    """
    init_db()
    _ensure_governance_columns()

    # 确保 deadline 字段存在（兼容旧数据库）
    conn = db_conn()
    c = conn.cursor()
    try:
        c.execute("SELECT deadline FROM decisions LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE decisions ADD COLUMN deadline TEXT")
        conn.commit()
    conn.close()

    mem_id = generate_id(project, decision, now_iso())
    created = now_iso()

    # 检查同一项目是否有相似决策（Jaccard + 语义相似度综合判定）
    conn = db_conn()
    c = conn.cursor()
    # 只查未被覆盖的 active 决策
    c.execute("SELECT id, decision FROM decisions WHERE project=? AND status='active' AND (superseded_by IS NULL OR superseded_by = '')", (project,))
    existing = c.fetchall()

    # 如果内容高度相似，标记为版本更新
    # 策略三合一：Jaccard > 0.15 或 token 语义相似度 > 0.15 或 embedding 余弦 > 0.65
    # 口语化文本表面差异大但 embedding 语义相近，需 embedding 兜底
    superseded = None
    has_conflict = False
    for eid, edecision in existing:
        jaccard_sim = _similarity(decision, edecision)
        semantic_sim = _semantic_similarity(decision, edecision)
        # 只有在共享至少一个主题词时才调用 embedding，避免无关文本被误判
        embed_sim = 0.0
        if jaccard_sim < 0.15 and semantic_sim < 0.15 and _has_common_topic(decision, edecision):
            embed_sim = _embedding_cosine(decision, edecision)
        if jaccard_sim > 0.15 or semantic_sim > 0.15 or embed_sim > 0.65:
            superseded = eid
            has_conflict = True
            break

    # 审查策略：决定状态
    review_input = {
        "decision": decision,
        "reasoning": reasoning,
        "confidence": confidence,
    }
    status = review_policy(review_input, has_conflict=has_conflict)

    # 插入新记录
    c.execute('''
        INSERT INTO decisions
        (id, project, decision, reasoning, conclusion, objections, decision_maker,
         chat_id, created_at, updated_at, ttl, last_accessed, version, superseded_by, embedding_id, deadline, status, evidence, confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (mem_id, project, decision, reasoning, conclusion, objections, decision_maker,
          chat_id, created, created, ttl, created, 1, None, mem_id, deadline or None, status, evidence, confidence))

    # 如果有旧版本，建立 Supersedes 关系
    if superseded:
        c.execute("UPDATE decisions SET superseded_by=? WHERE id=?", (mem_id, superseded))
        c.execute('''
            INSERT OR REPLACE INTO memory_edges (from_id, to_id, edge_type, created_at)
            VALUES (?, ?, 'Supersedes', ?)
        ''', (mem_id, superseded, created))

    conn.commit()
    conn.close()

    # 写入审计日志
    write_audit_log(mem_id, "create", "system", f"status={status}, conflict={has_conflict}, evidence={evidence[:50] if evidence else 'none'}")

    # 写入 Chroma 向量库（预计算 embedding）
    try:
        collection = get_collection()
        doc = f"项目：{project}\n决策：{decision}\n理由：{reasoning}\n结论：{conclusion}\n反对：{objections}"
        embeddings = get_embedding_for_texts([doc])
        collection.add(
            ids=[mem_id],
            documents=[doc],
            embeddings=embeddings,
            metadatas=[{
                "project": project,
                "decision_maker": decision_maker,
                "created_at": created,
                "chat_id": chat_id,
                "status": status,
            }]
        )
    except Exception as e:
        print(f"向量存储警告: {e}", file=sys.stderr)

    result = {
        "id": mem_id,
        "project": project,
        "decision": decision,
        "reasoning": reasoning,
        "conclusion": conclusion,
        "objections": objections,
        "decision_maker": decision_maker,
        "chat_id": chat_id,
        "deadline": deadline,
        "created_at": created,
        "superseded": superseded,
        "status": status,
        "evidence": evidence,
        "confidence": confidence,
    }

    # 写入简化知识图谱（不阻塞）
    try:
        from knowledge_graph import index_decision
        index_decision(result)
    except Exception as e:
        print(f"知识图谱索引警告: {e}", file=sys.stderr)

    return result


def _similarity(a: str, b: str) -> float:
    """简单 Jaccard 相似度（字符级，适合短文本）"""
    sa = set(a.lower())
    sb = set(b.lower())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _semantic_similarity(a: str, b: str) -> float:
    """语义相似度：基于简单 token 重叠（project/技术名词/动作词）"""
    import re
    # 提取有意义的词（中文词、英文单词、技术名词）
    def _tokens(text: str) -> set:
        text = text.lower()
        # 中文连续字符
        zh = set(re.findall(r'[\u4e00-\u9fff]{2,}', text))
        # 英文单词（含技术名词）
        en = set(re.findall(r'[a-z][a-z0-9]*', text))
        return zh | en
    ta = _tokens(a)
    tb = _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _has_common_topic(a: str, b: str) -> bool:
    """判断两段文本是否共享至少一个有意义 token（避免无关文本被 embedding 误判）"""
    import re
    def _tokens(text: str) -> set:
        text = text.lower()
        zh = set(re.findall(r'[\u4e00-\u9fff]{2,}', text))
        en = set(re.findall(r'[a-z][a-z0-9]*', text))
        return zh | en
    return bool(_tokens(a) & _tokens(b))


def _embedding_cosine(a: str, b: str) -> float:
    """基于 embedding API 的余弦相似度（用于冲突检测兜底）"""
    try:
        # 短文本 embedding 不稳定，增加保护
        if len(a) < 5 or len(b) < 5:
            return 0.0
        emb = get_embedding_for_texts([a, b])
        v1, v2 = emb[0], emb[1]
        # 安全检查：排除 NaN/异常值
        if any(not isinstance(x, (int, float)) or x != x for x in v1 + v2):
            return 0.0
        dot = sum(x * y for x, y in zip(v1, v2))
        norm1 = sum(x * x for x in v1) ** 0.5
        norm2 = sum(x * x for x in v2) ** 0.5
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)
    except Exception:
        return 0.0


# ─── 查询与检索 ───

def _chroma_where(project=None, status_filter="active"):
    """构造兼容 Chroma 的 where 字典（扁平格式，避免 $and 兼容问题）。"""
    where = {}
    if project:
        where["project"] = project
    if status_filter != "all":
        where["status"] = status_filter
    return where if where else None


def query_decisions(project=None, query_text=None, include_superseded=False, top_k=10, status_filter="active"):
    """
    查询决策记录。
    支持：按项目过滤 + 语义检索 + 关键词 BM25 + 状态过滤
    status_filter: 'active' | 'candidate' | 'all'
    语义检索优先：Chroma 结果按相似度排序，SQLite 作为补充。
    """
    init_db()
    _ensure_governance_columns()
    conn = db_conn()
    c = conn.cursor()

    results = []
    seen_ids = set()

    # 1. Chroma 语义检索（如果提供了 query_text）
    if query_text:
        try:
            collection = get_collection()
            query_embedding = get_embedding_for_texts([query_text])
            chroma_where = _chroma_where(project, status_filter)
            if False and status_filter != "all" and chroma_where is not None:
                pass
            if False and status_filter != "all":
                pass
            chroma_results = collection.query(
                query_embeddings=query_embedding,
                n_results=min(top_k * 6, 100),
                where=chroma_where
            )
            ids = chroma_results["ids"][0] if chroma_results.get("ids") and chroma_results["ids"] else []
            distances = chroma_results["distances"][0] if chroma_results.get("distances") and chroma_results["distances"] else []
            id_to_score = {sid: 1.0 / (1.0 + d) for sid, d in zip(ids, distances)}

            if ids:
                placeholders = ",".join("?" * len(ids))
                conditions = [f"id IN ({placeholders})"]
                params = list(ids)
                if not include_superseded:
                    conditions.append("(superseded_by IS NULL OR superseded_by = '')")
                if status_filter != "all":
                    conditions.append("status = ?")
                    params.append(status_filter)

                c.execute(f"SELECT * FROM decisions WHERE {' AND '.join(conditions)}", tuple(params))
                cols = [d[0] for d in c.description]
                for row in c.fetchall():
                    rec = {cols[i]: row[i] for i in range(len(cols))}
                    rec["_semantic_score"] = id_to_score.get(rec["id"], 0)
                    results.append(rec)
                    seen_ids.add(rec["id"])
                # 按语义相似度降序
                results.sort(key=lambda x: x.get("_semantic_score", 0), reverse=True)
        except Exception as e:
            print(f"语义检索警告: {e}", file=sys.stderr)

    # 2. SQLite 关键词补充（如果语义结果不足）
    if len(results) < top_k:
        conditions = []
        params = []
        if not include_superseded:
            conditions.append("(superseded_by IS NULL OR superseded_by = '')")
        if status_filter != "all":
            conditions.append("status = ?")
            params.append(status_filter)
        if project:
            conditions.append("project = ?")
            params.append(project)
        if seen_ids:
            placeholders = ",".join("?" * len(seen_ids))
            conditions.append(f"id NOT IN ({placeholders})")
            params.extend(list(seen_ids))

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        c.execute(f"""
            SELECT * FROM decisions
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
        """, tuple(params + [top_k - len(results)]))

        cols = [d[0] for d in c.description]
        for row in c.fetchall():
            rec = {cols[i]: row[i] for i in range(len(cols))}
            rec["_semantic_score"] = 0.0
            results.append(rec)

    conn.close()
    return results[:top_k]


def _is_recall_related(message: str, record: dict) -> bool:
    """轻量二分类：判断新消息是否值得推送历史决策。"""
    decision_text = f"{record.get('project', '')} {record.get('decision', '')} {record.get('reasoning', '')} {record.get('evidence', '')}"
    if _has_common_topic(message, decision_text):
        return True
    msg = message.lower()
    dec = decision_text.lower()
    domain_pairs = [
        ("kong", "apisix"), ("implicit", "oauth2"), ("implicit", "pkce"),
        ("单机", "redis"), ("单机", "cluster"), ("咖啡", "redis"),
    ]
    for left, right in domain_pairs:
        if left in msg and right in dec:
            return left != "咖啡"
    if any(word in msg for word in ["方案", "重新聊", "要不要", "试下", "改", "换"]):
        return any(word in dec for word in ["采用", "用", "方案", "鉴权", "缓存", "网关"])
    return False


def _is_casual_chat(text: str) -> bool:
    """判断消息是否是明显的闲聊/生活话题（用于 P5 负例过滤）。"""
    casual_words = ["咖啡", "午饭", "吃饭", "天气", "地铁", "迟到", "外卖", "奶茶", "电影", "周末", "打球", "团建", "头像", "表情包", "拍照", "下雨", "迟到"]
    tech_markers = ["方案", "要不要", "重新聊", "试下", "改", "换", "采用", "用", "上线", "发布", "部署", "网关", "缓存", "鉴权", "数据库", "消息队列", "CI/CD", "Redis", "OAuth", "Kafka", "APISIX", "Kong", "ES", "PostgreSQL", "MySQL"]
    has_casual = any(w in text for w in casual_words)
    has_tech = any(w in text for w in tech_markers)
    return has_casual and not has_tech


def recall_relevant_decisions(message: str, project=None, top_k=3):
    """
    根据新消息检索相关的历史决策（主动推送用）。
    只返回 active 状态的决策。
    策略：信任 Chroma 语义排序，仅对明显闲聊消息过滤（避免负例泄漏）。
    """
    results = query_decisions(project=project, query_text=message, top_k=top_k * 2, status_filter="active")

    is_chat = _is_casual_chat(message)
    active_results = []
    for r in results:
        if _is_forgotten(r):
            continue
        # Chroma 结果：明显闲聊消息才过滤；其他（含技术讨论）信任语义排序
        if r.get("_semantic_score") is not None:
            if is_chat:
                continue
        else:
            # SQLite fallback：保守过滤
            sem_sim = _semantic_similarity(message, f"{r.get('decision', '')} {r.get('reasoning', '')}")
            if sem_sim < 0.03:
                continue
        active_results.append(r)
        if len(active_results) >= top_k:
            break

    _touch_memories([r["id"] for r in active_results])
    return active_results


# ─── 遗忘机制（Ebbinghaus） ───

def _is_forgotten(record: dict) -> bool:
    """判断一条记忆是否已遗忘"""
    try:
        ttl = int(record.get("ttl", 2592000))
        last_accessed = record.get("last_accessed") or record.get("created_at")
        last_dt = datetime.fromisoformat(last_accessed.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age = (now - last_dt).total_seconds()

        # Ebbinghaus 衰减：记忆强度 = e^(-age/ttl)
        import math
        strength = math.exp(-age / ttl)
        # 当强度低于阈值 0.1 时认为已遗忘
        return strength < 0.1
    except Exception:
        return False


def _touch_memories(mem_ids):
    """更新记忆的最后访问时间"""
    if not mem_ids:
        return
    conn = db_conn()
    c = conn.cursor()
    now = now_iso()
    for mid in mem_ids:
        c.execute("UPDATE decisions SET last_accessed=? WHERE id=?", (now, mid))
        c.execute("INSERT INTO access_log (memory_id, accessed_at) VALUES (?, ?)", (mid, now))
    conn.commit()
    conn.close()


def forget_expired():
    """清理已遗忘的记忆（软删除：标记 superseded_by='FORGOTTEN'）"""
    init_db()
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM decisions WHERE superseded_by IS NULL")
    cols = [d[0] for d in c.description]
    rows = c.fetchall()

    forgotten = 0
    now = now_iso()
    for row in rows:
        record = {cols[i]: row[i] for i in range(len(cols))}
        if _is_forgotten(record):
            c.execute("UPDATE decisions SET superseded_by='FORGOTTEN', updated_at=? WHERE id=?",
                      (now, record["id"]))
            forgotten += 1

    conn.commit()
    conn.close()
    return forgotten


# ─── 飞书配置 ───

def get_openclaw_config():
    config_paths = [
        Path(os.environ.get("OPENCLAW_CONFIG", "")),
        Path.home() / ".openclaw" / "openclaw.json",
        Path("D:/OpenClawData/.openclaw/openclaw.json"),
    ]
    for config_path in config_paths:
        if config_path.exists():
            try:
                return json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def get_feishu_account(cfg, account_name="default"):
    feishu_cfg = cfg.get("channels", {}).get("feishu", {})
    accounts = feishu_cfg.get("accounts", {})
    if account_name in accounts:
        acc = accounts[account_name]
        return acc.get("appId"), acc.get("appSecret")
    return feishu_cfg.get("appId"), feishu_cfg.get("appSecret")


def get_tenant_access_token(app_id, app_secret):
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": app_id, "app_secret": app_secret})
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取 token 失败: {data}")
    return data["tenant_access_token"]


# ─── 多维表格同步 ───

def _get_bitable_fields(token, base_id, table_id):
    """获取多维表格的字段列表，返回 {field_name: field_id}"""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_id}/tables/{table_id}/fields"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, params={"page_size": 100})
    data = resp.json()
    if data.get("code") != 0:
        return {}
    return {f["field_name"]: f["field_id"] for f in data.get("data", {}).get("items", [])}


def sync_to_bitable(token, base_id, table_id):
    init_db()
    _ensure_governance_columns()
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM decisions WHERE (superseded_by IS NULL OR superseded_by='FORGOTTEN') AND status='active'")
    cols = [d[0] for d in c.description]
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("本地没有决策记录，无需同步")
        return 0

    # 获取目标表格的字段结构
    fields = _get_bitable_fields(token, base_id, table_id)
    if not fields:
        print("无法获取表格字段信息，请检查 base_id 和 table_id", file=sys.stderr)
        return 0

    # 判断表格是否有标准字段，还是只有"文本"字段
    has_standard_fields = any(k in fields for k in ["项目名", "决策内容", "理由"])
    text_field = fields.get("文本") or fields.get("内容") or list(fields.keys())[0]

    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_id}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    success = 0
    failed = 0
    for row in rows:
        record = {cols[i]: row[i] for i in range(len(cols))}

        if has_standard_fields:
            # 智能字段映射：根据表格实际字段名匹配
            body_fields = {}
            field_aliases = {
                "project": ["项目名", "决策项目", "项目名称", "项目"],
                "decision": ["决策内容", "内容", "决策"],
                "reasoning": ["理由", "原因", "决策理由"],
                "conclusion": ["结论", "决策结论"],
                "objections": ["反对意见", "异议"],
                "created_at": ["时间", "创建时间", "日期"],
                "decision_maker": ["决策人", "创建人", "负责人", "决策人"],
                "chat_id": ["会话ID", "聊天ID"],
                "version": ["版本", "version"],
            }
            values = {
                "project": record.get("project", ""),
                "decision": record.get("decision", ""),
                "reasoning": record.get("reasoning", ""),
                "conclusion": record.get("conclusion", ""),
                "objections": record.get("objections", ""),
                "created_at": record.get("created_at", ""),
                "decision_maker": record.get("decision_maker", ""),
                "chat_id": record.get("chat_id", ""),
                "version": str(record.get("version", 1)),
            }
            for key, aliases in field_aliases.items():
                for alias in aliases:
                    if alias in fields:
                        body_fields[alias] = values[key]
                        break
        else:
            # 表格只有简单字段（如"文本"），将所有信息合并为一段文本
            parts = [
                f"项目：{record.get('project', '')}",
                f"决策：{record.get('decision', '')}",
            ]
            if record.get("reasoning"):
                parts.append(f"理由：{record.get('reasoning')}")
            if record.get("conclusion"):
                parts.append(f"结论：{record.get('conclusion')}")
            if record.get("objections"):
                parts.append(f"反对：{record.get('objections')}")
            if record.get("decision_maker"):
                parts.append(f"决策人：{record.get('decision_maker')}")
            parts.append(f"时间：{record.get('created_at', '')}")
            body_fields = {text_field: "\n".join(parts)}

        body = {"fields": body_fields}
        resp = requests.post(url, headers=headers, json=body)
        data = resp.json()
        if data.get("code") == 0:
            success += 1
        else:
            failed += 1
            print(f"同步失败: {record.get('id')} -> {data}", file=sys.stderr)

    print(f"同步完成: 成功 {success} 条, 失败 {failed} 条")
    return success


# ─── 命令处理 ───

def cmd_add(args):
    # 结构化抽取（默认规则抽取，避免 LLM 超时）
    extracted = extract_decision_structured(
        args.raw or args.decision,
        args.project,
        args.decision_maker,
        use_llm=getattr(args, 'use_llm', False)
    )

    project = args.project or extracted.get("project", "未分类")
    decision = extracted.get("decision") or args.decision or args.raw or ""
    reasoning = extracted.get("reasoning", "")
    conclusion = extracted.get("conclusion", "")
    objections = extracted.get("objections", "")
    maker = args.decision_maker or extracted.get("decision_maker", "")
    evidence = getattr(args, 'evidence', '') or ''
    confidence = getattr(args, 'confidence', 0.8) or 0.8

    record = store_decision(
        project=project,
        decision=decision,
        reasoning=reasoning,
        conclusion=conclusion,
        objections=objections,
        decision_maker=maker,
        chat_id=args.chat_id or "",
        ttl=args.ttl,
        evidence=evidence,
        confidence=confidence,
    )
    print(json.dumps({"status": "ok", "record": record}, ensure_ascii=False))


def cmd_query(args):
    results = query_decisions(
        project=args.project,
        query_text=args.q,
        include_superseded=args.include_superseded,
        top_k=args.top_k,
        status_filter=getattr(args, 'status', 'active'),
    )
    # 更新访问时间
    _touch_memories([r["id"] for r in results])
    print(json.dumps({"status": "ok", "count": len(results), "results": results}, ensure_ascii=False))


def cmd_list(_args):
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT DISTINCT project FROM decisions WHERE superseded_by IS NULL ORDER BY project")
    projects = [row[0] for row in c.fetchall()]
    conn.close()
    print(json.dumps({"status": "ok", "count": len(projects), "projects": projects}, ensure_ascii=False))


def cmd_recall(args):
    results = recall_relevant_decisions(
        message=args.message,
        project=args.project,
        top_k=args.top_k,
    )
    print(json.dumps({
        "status": "ok",
        "count": len(results),
        "message": args.message,
        "relevant_decisions": results,
    }, ensure_ascii=False))


def cmd_sync(args):
    cfg = get_openclaw_config()
    if not cfg:
        print(json.dumps({"status": "error", "message": "未找到 openclaw.json"}, ensure_ascii=False))
        sys.exit(1)

    # 智能账号回退
    feishu_cfg = cfg.get("channels", {}).get("feishu", {})
    accounts = feishu_cfg.get("accounts", {})
    if args.account not in accounts:
        default_account = feishu_cfg.get("defaultAccount", "")
        if default_account and default_account in accounts:
            args.account = default_account
        else:
            available = [k for k in accounts.keys() if k != "default"]
            if available:
                args.account = available[0]

    app_id, app_secret = get_feishu_account(cfg, args.account)
    if not app_id or not app_secret:
        print(json.dumps({"status": "error", "message": f"未找到飞书账号: {args.account}"}, ensure_ascii=False))
        sys.exit(1)

    token = get_tenant_access_token(app_id, app_secret)

    base_id = args.base_id
    table_id = args.table_id
    if not base_id or not table_id:
        local_cfg = {}
        if CONFIG_FILE.exists():
            local_cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        bitable_cfg = local_cfg.get("bitable", {})
        base_id = base_id or bitable_cfg.get("base_id")
        table_id = table_id or bitable_cfg.get("table_id")

    if not base_id or not table_id:
        print(json.dumps({
            "status": "error",
            "message": "缺少 base_id 或 table_id",
            "hint": "请通过参数传入，或在 config.json 中配置 bitable.base_id 和 bitable.table_id"
        }, ensure_ascii=False))
        sys.exit(1)

    success = sync_to_bitable(token, base_id, table_id)
    print(json.dumps({"status": "ok", "synced": success}, ensure_ascii=False))


def cmd_forget(_args):
    count = forget_expired()
    print(json.dumps({"status": "ok", "forgotten_count": count}, ensure_ascii=False))


def cmd_review(args):
    """列出待审核的 candidate 记忆"""
    results = query_decisions(
        project=args.project,
        query_text=args.q,
        include_superseded=False,
        top_k=args.top_k,
        status_filter="candidate",
    )
    print(json.dumps({"status": "ok", "count": len(results), "results": results}, ensure_ascii=False))


def cmd_confirm(args):
    """确认一条 candidate 记忆为 active"""
    result = confirm_candidate(args.id, actor=getattr(args, 'actor', 'admin'))
    print(json.dumps(result, ensure_ascii=False))


# ─── 主入口 ───

def main():
    parser = argparse.ArgumentParser(description="飞书项目决策记忆 - 增强版")
    sub = parser.add_subparsers(dest="command", required=True)

    # add
    p_add = sub.add_parser("add", help="添加决策记录（默认规则抽取，避免 LLM 超时）")
    p_add.add_argument("--raw", help="原始文本，自动抽取结构化信息")
    p_add.add_argument("--project", help="项目名称")
    p_add.add_argument("--decision", help="决策内容")
    p_add.add_argument("--decision-maker", help="决策人")
    p_add.add_argument("--chat-id", default="", help="飞书会话ID")
    p_add.add_argument("--ttl", type=int, default=2592000, help="记忆存活时间（秒，默认30天）")
    p_add.add_argument("--use-llm", action="store_true", help="启用 LLM 结构化抽取（可能较慢）")
    p_add.add_argument("--evidence", default="", help="证据链（原始消息来源）")
    p_add.add_argument("--confidence", type=float, default=0.8, help="置信度 0-1（默认0.8）")
    p_add.set_defaults(func=cmd_add)

    # query
    p_query = sub.add_parser("query", help="查询项目决策")
    p_query.add_argument("--project", help="项目名称")
    p_query.add_argument("--q", help="语义查询文本")
    p_query.add_argument("--include-superseded", action="store_true", help="包含已被覆盖的决策")
    p_query.add_argument("--top-k", type=int, default=10, help="返回数量")
    p_query.add_argument("--status", default="active", help="状态过滤：active | candidate | all（默认active）")
    p_query.set_defaults(func=cmd_query)

    # list
    p_list = sub.add_parser("list", help="列出所有项目")
    p_list.set_defaults(func=cmd_list)

    # recall
    p_recall = sub.add_parser("recall", help="根据消息检索相关历史决策（主动推送）")
    p_recall.add_argument("--message", required=True, help="新消息内容")
    p_recall.add_argument("--project", help="限定项目")
    p_recall.add_argument("--top-k", type=int, default=3, help="返回数量")
    p_recall.set_defaults(func=cmd_recall)

    # sync
    p_sync = sub.add_parser("sync", help="同步到飞书多维表格")
    p_sync.add_argument("--account", default="private1", help="飞书账号名 (default: private1, 群聊用 group)")
    p_sync.add_argument("--base-id", help="Bitable base_id")
    p_sync.add_argument("--table-id", help="Bitable table_id")
    p_sync.set_defaults(func=cmd_sync)

    # forget
    p_forget = sub.add_parser("forget", help="清理过期记忆")
    p_forget.set_defaults(func=cmd_forget)

    # review
    p_review = sub.add_parser("review", help="列出待审核的 candidate 记忆")
    p_review.add_argument("--project", help="项目名称")
    p_review.add_argument("--q", help="语义查询文本")
    p_review.add_argument("--top-k", type=int, default=10, help="返回数量")
    p_review.set_defaults(func=cmd_review)

    # confirm
    p_confirm = sub.add_parser("confirm", help="确认 candidate 记忆为 active")
    p_confirm.add_argument("id", help="记忆ID")
    p_confirm.add_argument("--actor", default="admin", help="审核人")
    p_confirm.set_defaults(func=cmd_confirm)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
