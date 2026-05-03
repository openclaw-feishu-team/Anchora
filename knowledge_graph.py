#!/usr/bin/env python3
# 文件路径：skills/feishu-memory/knowledge_graph.py
# 修改类型：新增
# 依赖说明：标准库 SQLite；未依赖 cognee，使用轻量 triples 表实现简化知识图谱
"""feishu-memory 简化知识图谱。"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from memory import db_conn, now_iso

TECH_KEYWORDS = ["Vue3", "Vue", "React", "Python", "Django", "Flask", "FastAPI", "SQLite", "Chroma", "MySQL", "PostgreSQL", "Redis", "Docker", "Kubernetes", "OpenClaw", "飞书", "Lark", "TypeScript", "JavaScript", "Node", "LLM"]


def ensure_kg_tables():
    """确保知识图谱 triples 表存在。"""
    conn = db_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_triples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            subject_type TEXT,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            object_type TEXT,
            memory_id TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(subject, predicate, object, memory_id)
        )
    """)
    conn.commit()
    conn.close()


def _add_triple(c, subject, subject_type, predicate, obj, object_type, memory_id):
    if not subject or not obj:
        return
    c.execute("""
        INSERT OR IGNORE INTO knowledge_triples
        (subject, subject_type, predicate, object, object_type, memory_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (subject, subject_type, predicate, obj, object_type, memory_id, now_iso()))


def extract_entities(record: dict) -> dict:
    """使用规则抽取实体。"""
    text = f"{record.get('decision','')} {record.get('reasoning','')} {record.get('conclusion','')}"
    techs = [kw for kw in TECH_KEYWORDS if kw.lower() in text.lower()]
    return {
        "project": record.get("project") or "未分类",
        "decision": record.get("decision") or "",
        "person": record.get("decision_maker") or "",
        "deadline": record.get("deadline") or "",
        "technologies": sorted(set(techs)),
    }


def index_decision(record: dict) -> dict:
    """将单条决策写入知识图谱。"""
    try:
        ensure_kg_tables()
        ents = extract_entities(record)
        mem_id = record.get("id")
        conn = db_conn()
        c = conn.cursor()
        project = ents["project"]
        decision = ents["decision"]
        _add_triple(c, decision, "Decision", "BELONGS_TO", project, "Project", mem_id)
        if ents["person"]:
            _add_triple(c, decision, "Decision", "DECIDED_BY", ents["person"], "Person", mem_id)
        if ents["deadline"]:
            _add_triple(c, decision, "Decision", "HAS_DEADLINE", ents["deadline"], "Deadline", mem_id)
        for tech in ents["technologies"]:
            _add_triple(c, decision, "Decision", "USES", tech, "Technology", mem_id)
        if record.get("superseded"):
            _add_triple(c, decision, "Decision", "SUPERSEDES", record.get("superseded"), "Decision", mem_id)
        conn.commit()
        conn.close()
        return {"status": "ok", "memory_id": mem_id}
    except Exception as exc:
        print(f"知识图谱写入失败: {exc}", file=sys.stderr)
        return {"status": "error", "error": str(exc)}


def rebuild_graph() -> dict:
    """重建全部知识图谱。"""
    ensure_kg_tables()
    conn = db_conn()
    c = conn.cursor()
    c.execute("DELETE FROM knowledge_triples")
    c.execute("SELECT * FROM decisions")
    cols = [d[0] for d in c.description]
    rows = [{cols[i]: r[i] for i in range(len(cols))} for r in c.fetchall()]
    conn.commit()
    conn.close()
    count = 0
    for row in rows:
        if index_decision(row).get("status") == "ok":
            count += 1
    return {"status": "ok", "indexed": count}


def kg_query(question: str) -> dict:
    """解析简单中文问题并查询 triples。"""
    ensure_kg_tables()
    q = question.strip()
    conn = db_conn()
    c = conn.cursor()
    results = []
    try:
        project_match = re.search(r"([A-Za-z0-9\u4e00-\u9fff]*项目[A-Za-z0-9\u4e00-\u9fff]*)", q)
        project = project_match.group(1) if project_match else None
        if "决策人" in q or "谁" in q:
            sql = """
                SELECT DISTINCT kt.object, kt.memory_id, kt.subject
                FROM knowledge_triples kt
                JOIN knowledge_triples bp ON bp.subject=kt.subject AND bp.predicate='BELONGS_TO'
                WHERE kt.predicate='DECIDED_BY'
            """
            params = []
            if project:
                sql += " AND bp.object LIKE ?"
                params.append(f"%{project}%")
            c.execute(sql, params)
            results = [{"person": r[0], "memory_id": r[1], "decision": r[2]} for r in c.fetchall()]
        elif "技术" in q or "用了" in q or "有关" in q:
            sql = """
                SELECT DISTINCT kt.object, kt.memory_id, kt.subject
                FROM knowledge_triples kt
                LEFT JOIN knowledge_triples bp ON bp.subject=kt.subject AND bp.predicate='BELONGS_TO'
                WHERE kt.predicate='USES'
            """
            params = []
            if project:
                sql += " AND bp.object LIKE ?"
                params.append(f"%{project}%")
            for tech in TECH_KEYWORDS:
                if tech.lower() in q.lower():
                    sql += " AND kt.object LIKE ?"
                    params.append(f"%{tech}%")
                    break
            c.execute(sql, params)
            results = [{"technology": r[0], "memory_id": r[1], "decision": r[2]} for r in c.fetchall()]
        else:
            like = f"%{q}%"
            c.execute("SELECT subject, predicate, object, memory_id FROM knowledge_triples WHERE subject LIKE ? OR object LIKE ? LIMIT 20", (like, like))
            results = [{"subject": r[0], "predicate": r[1], "object": r[2], "memory_id": r[3]} for r in c.fetchall()]
    except Exception as exc:
        print(f"kg_query 失败: {exc}", file=sys.stderr)
    finally:
        conn.close()
    return {"status": "ok", "question": question, "count": len(results), "results": results}


def main():
    parser = argparse.ArgumentParser(description="feishu-memory 知识图谱")
    sub = parser.add_subparsers(dest="command", required=True)
    p_query = sub.add_parser("query")
    p_query.add_argument("question")
    sub.add_parser("rebuild")
    args = parser.parse_args()
    if args.command == "query":
        print(json.dumps(kg_query(args.question), ensure_ascii=False))
    elif args.command == "rebuild":
        print(json.dumps(rebuild_graph(), ensure_ascii=False))


if __name__ == "__main__":
    main()
