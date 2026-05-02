#!/usr/bin/env python3
"""
feishu-memory 上下文工程师

解决飞书场景下的上下文遗忘问题：
1. 维护项目决策历史上下文
2. 加载相关决策记忆
3. 生成带上下文的回复

Usage:
  python context_engineer.py build --chat-id "oc_xxx" --project "项目A" --query "为什么选Vue3"
  python context_engineer.py store --chat-id "oc_xxx" --project "项目A" --decision "用Vue3" --reasoning "React学习成本高"
"""
import argparse
import json
import sys
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = Path(__file__).parent.resolve()
DB_FILE = SCRIPT_DIR / "memory.db"


def db_conn():
    return sqlite3.connect(DB_FILE)


def get_project_context(chat_id: str, project: str = None, query: str = None) -> dict:
    """
    加载项目决策上下文
    返回: {"system_prompt": "...", "recent_decisions": [...], "related_decisions": [...]}
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # 1. 加载该 chat 下的所有项目
    c.execute("""
        SELECT DISTINCT project FROM decisions
        WHERE chat_id=? AND (superseded_by IS NULL OR superseded_by = '')
        ORDER BY project
    """, (chat_id,))
    projects = [row[0] for row in c.fetchall()]

    # 2. 加载最近决策（最近7天）
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    c.execute("""
        SELECT project, decision, reasoning, conclusion, objections, decision_maker, created_at
        FROM decisions
        WHERE chat_id=? AND created_at >= ? AND (superseded_by IS NULL OR superseded_by = '')
        ORDER BY created_at DESC LIMIT 10
    """, (chat_id, since))
    recent = []
    cols = [d[0] for d in c.description]
    for row in c.fetchall():
        recent.append({cols[i]: row[i] for i in range(len(cols))})

    # 3. 如果指定了 project，加载该项目的所有决策
    project_decisions = []
    if project:
        c.execute("""
            SELECT project, decision, reasoning, conclusion, objections, decision_maker, created_at
            FROM decisions
            WHERE project=? AND chat_id=? AND (superseded_by IS NULL OR superseded_by = '')
            ORDER BY created_at DESC
        """, (project, chat_id))
        for row in c.fetchall():
            project_decisions.append({cols[i]: row[i] for i in range(len(cols))})

    conn.close()

    # 4. 构建 system prompt
    prompt_parts = [
        "你是飞书项目决策助手，负责记录和查询项目决策。",
        f"当前会话（{chat_id}）涉及项目：{', '.join(projects) if projects else '暂无'}",
    ]

    if recent:
        prompt_parts.append("\n最近决策（7天内）：")
        for d in recent[:5]:
            prompt_parts.append(
                f"- [{d['project']}] {d['decision'][:50]}... "
                f"({d['decision_maker']}, {d['created_at'][:10]})"
            )

    if project_decisions:
        prompt_parts.append(f"\n项目「{project}」的全部决策：")
        for d in project_decisions[:10]:
            prompt_parts.append(f"- {d['created_at'][:10]}: {d['decision'][:80]}...")
            if d['reasoning']:
                prompt_parts.append(f"  理由：{d['reasoning'][:60]}...")
            if d['objections']:
                prompt_parts.append(f"  反对：{d['objections'][:60]}...")

    prompt_parts.append(
        "\n回复原则："
        "\n1. 保持项目决策的连续性，记住之前的讨论"
        "\n2. 新决策与旧决策冲突时，提示用户是否覆盖"
        "\n3. 查询时优先返回最近、最相关的决策"
        "\n4. 不要重复询问已记录的信息"
    )

    return {
        "system_prompt": "\n".join(prompt_parts),
        "context": {
            "chat_id": chat_id,
            "projects": projects,
            "recent_count": len(recent),
            "project_decisions_count": len(project_decisions),
        }
    }


def store_decision_context(chat_id: str, project: str, decision: str, reasoning: str = "", maker: str = ""):
    """存储决策上下文摘要（用于快速检索）"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # 创建上下文摘要表（如果不存在）
    c.execute('''
        CREATE TABLE IF NOT EXISTS decision_contexts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            project TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')

    now = datetime.now(timezone.utc).isoformat()
    summary = f"项目{project}决定{decision}"
    if reasoning:
        summary += f"，因为{reasoning}"
    if maker:
        summary += f"，决策人{maker}"

    c.execute('''
        INSERT INTO decision_contexts (chat_id, project, summary, created_at)
        VALUES (?, ?, ?, ?)
    ''', (chat_id, project, summary, now))

    conn.commit()
    conn.close()
    return {"status": "ok", "summary": summary}


def cmd_build(args):
    result = get_project_context(args.chat_id, args.project, args.query)
    print(json.dumps(result, ensure_ascii=False))


def cmd_store(args):
    result = store_decision_context(args.chat_id, args.project, args.decision, args.reasoning, args.maker)
    print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="feishu-memory 上下文工程师")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="构建项目决策上下文")
    p_build.add_argument("--chat-id", required=True, help="飞书会话ID")
    p_build.add_argument("--project", "-p", help="项目名称")
    p_build.add_argument("--query", "-q", help="查询文本")
    p_build.set_defaults(func=cmd_build)

    p_store = sub.add_parser("store", help="存储决策上下文摘要")
    p_store.add_argument("--chat-id", required=True)
    p_store.add_argument("--project", required=True)
    p_store.add_argument("--decision", required=True)
    p_store.add_argument("--reasoning", default="")
    p_store.add_argument("--maker", default="")
    p_store.set_defaults(func=cmd_store)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
