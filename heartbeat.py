#!/usr/bin/env python3
"""
feishu-memory 心跳推送引擎（Heartbeat）

功能：
1. 扫描数据库中所有带 deadline 的决策/项目节点
2. DDL 前一周，每三天主动推送一次提醒卡片
3. 每 12 小时遍历一次数据库
4. 推送过的节点记录到 push_log 表，避免重复推送

Usage:
  python heartbeat.py check            # 立即检查并推送需要提醒的 DDL
  python heartbeat.py list             # 列出未来 30 天内的所有关键节点
  python heartbeat.py dry-run          # 模拟运行，只输出不推送
"""
import argparse
import json
import sys
import os
import sqlite3
import re
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent))
from memory import init_db, db_conn, now_iso, get_openclaw_config, get_feishu_account, get_tenant_access_token

SCRIPT_DIR = Path(__file__).parent.resolve()
DB_FILE = SCRIPT_DIR / "memory.db"
LOG_DIR = SCRIPT_DIR / "logs"

# ─── 数据库 DDL 字段初始化 ───


def _ensure_deadline_column():
    """确保 decisions 表有 deadline 字段"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("SELECT deadline FROM decisions LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE decisions ADD COLUMN deadline TEXT")
        conn.commit()
    conn.close()


def _ensure_push_log_table():
    """创建推送日志表，避免重复推送"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS push_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id TEXT NOT NULL,
            push_type TEXT NOT NULL,   -- 'ddl_reminder', 'heartbeat'
            pushed_at TEXT NOT NULL,
            content TEXT,
            UNIQUE(decision_id, push_type)
        )
    ''')
    conn.commit()
    conn.close()


# ─── DDL 提取 ───


def extract_deadline(text: str) -> str:
    """
    从文本中提取日期/DDL。
    支持格式：2026年10月31日、2026-10-31、10月31日上线、ddl:2026-05-01 等
    返回 ISO 格式日期字符串或空字符串
    """
    text = text.strip()

    # 模式 1: 2026年10月31日 / 2026年10月31号
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})[日号]', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 模式 2: 2026-10-31 / 2026/10/31
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # 简单验证
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year}-{month:02d}-{day:02d}"

    # 模式 3: 10月31日 / 10月31号（默认今年）
    m = re.search(r'(\d{1,2})月(\d{1,2})[日号]', text)
    if m:
        year = datetime.now(timezone.utc).year
        return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # 模式 4: DDL/截止/上线/交付 后面跟日期
    m = re.search(r'(?:ddl|截止|上线|交付|deadline|里程碑|节点)[\s:：]*(\d{4}-\d{2}-\d{2})', text, re.I)
    if m:
        return m.group(1)

    return ""


# ─── 推送逻辑 ───


def _format_ddl_card(record: dict, days_left: int) -> str:
    """格式化 DDL 提醒卡片"""
    project = record.get("project", "未分类")
    decision = record.get("decision", "")
    deadline = record.get("deadline", "")
    maker = record.get("decision_maker", "")

    urgency = ""
    if days_left <= 3:
        urgency = "🔴 紧急"
    elif days_left <= 7:
        urgency = "🟠 预警"
    else:
        urgency = "🟡 提醒"

    lines = [
        f"{urgency} 项目节点提醒",
        f"",
        f"📋 项目：{project}",
        f"📝 内容：{decision[:80]}...",
        f"📅 DDL：{deadline}",
        f"⏰ 剩余：{days_left} 天",
    ]
    if maker:
        lines.append(f"👤 负责人：{maker}")
    lines.append("")
    lines.append("—— 来自 feishu-memory 心跳推送")

    return "\n".join(lines)


def _get_push_history(decision_id: str, push_type: str) -> list:
    """查询某条决策的推送历史"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT pushed_at FROM push_log WHERE decision_id=? AND push_type=? ORDER BY pushed_at",
        (decision_id, push_type)
    )
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


def _should_push_ddl(record: dict) -> bool:
    """
    判断一条带 deadline 的决策是否需要推送。
    规则：DDL 前一周开始，每三天推送一次。
    """
    deadline_str = record.get("deadline", "")
    if not deadline_str:
        return False

    try:
        deadline = datetime.strptime(deadline_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False

    now = datetime.now(timezone.utc)
    days_left = (deadline - now).days

    # 只关注未来 7 天内的 DDL
    if days_left < 0 or days_left > 7:
        return False

    decision_id = record.get("id", "")
    history = _get_push_history(decision_id, "ddl_reminder")

    if not history:
        return True  # 从未推送过，需要推送

    # 检查最近推送时间是否超过 3 天
    last_push = datetime.fromisoformat(history[-1].replace("Z", "+00:00"))
    days_since_last_push = (now - last_push).days
    return days_since_last_push >= 3


def _mark_pushed(decision_id: str, push_type: str, content: str = ""):
    """记录已推送"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO push_log (decision_id, push_type, pushed_at, content) VALUES (?, ?, ?, ?)",
        (decision_id, push_type, now_iso(), content)
    )
    conn.commit()
    conn.close()


# ─── 飞书消息推送 ───


def send_feishu_card(token: str, chat_id: str, content: str) -> dict:
    """发送文本卡片到飞书群聊"""
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    headers = {"Authorization": f"Bearer {token}"}

    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": content}, ensure_ascii=False)
    }

    try:
        resp = requests.post(url, headers=headers, json=body, params={"receive_id_type": "chat_id"})
        data = resp.json()
        return {"status": "ok" if data.get("code") == 0 else "error", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─── 核心检查逻辑 ───


def check_ddl_pushes(dry_run: bool = False):
    """
    检查所有带 deadline 的决策，推送需要提醒的。
    返回推送结果列表。
    """
    _ensure_deadline_column()
    _ensure_push_log_table()

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT id, project, decision, deadline, decision_maker, chat_id
        FROM decisions
        WHERE deadline IS NOT NULL AND deadline != ''
          AND (superseded_by IS NULL OR superseded_by = '')
        ORDER BY deadline
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        return [{"status": "info", "message": "没有找到带 deadline 的决策记录"}]

    results = []
    now = datetime.now(timezone.utc)

    # 获取 token（使用 group 账号）
    cfg = get_openclaw_config()
    app_id, app_secret = get_feishu_account(cfg, "group")
    token = None
    if app_id and app_secret:
        try:
            token = get_tenant_access_token(app_id, app_secret)
        except Exception as e:
            results.append({"status": "error", "message": f"获取飞书 token 失败: {e}"})
            return results

    for row in rows:
        rid, project, decision, deadline, maker, chat_id = row
        record = {
            "id": rid, "project": project, "decision": decision,
            "deadline": deadline, "decision_maker": maker, "chat_id": chat_id
        }

        try:
            deadline_dt = datetime.strptime(deadline[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        days_left = (deadline_dt - now).days

        if _should_push_ddl(record):
            card = _format_ddl_card(record, days_left)
            chat_target = chat_id or "oc_69d82d60aba270c50df63fb6b69a9713"

            if dry_run:
                results.append({
                    "status": "dry_run",
                    "decision_id": rid,
                    "project": project,
                    "deadline": deadline,
                    "days_left": days_left,
                    "card": card,
                    "target_chat": chat_target,
                })
            else:
                if token and chat_target:
                    push_result = send_feishu_card(token, chat_target, card)
                    if push_result.get("status") == "ok":
                        _mark_pushed(rid, "ddl_reminder", card[:200])
                        results.append({
                            "status": "pushed",
                            "decision_id": rid,
                            "project": project,
                            "deadline": deadline,
                            "days_left": days_left,
                        })
                    else:
                        results.append({
                            "status": "push_failed",
                            "decision_id": rid,
                            "error": push_result,
                        })
                else:
                    results.append({
                        "status": "skipped",
                        "decision_id": rid,
                        "reason": "无 token 或 chat_id",
                    })

    if not results:
        return [{"status": "info", "message": "所有 DDL 均已按时推送，无新增提醒"}]

    return results


def list_upcoming_nodes(days: int = 30):
    """列出未来 N 天内的所有关键节点"""
    _ensure_deadline_column()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    c.execute("""
        SELECT id, project, decision, deadline, decision_maker, chat_id
        FROM decisions
        WHERE deadline IS NOT NULL AND deadline != ''
          AND deadline >= ? AND deadline <= ?
          AND (superseded_by IS NULL OR superseded_by = '')
        ORDER BY deadline
    """, (now_str, cutoff))

    rows = c.fetchall()
    conn.close()

    nodes = []
    for row in rows:
        rid, project, decision, deadline, maker, chat_id = row
        try:
            ddl = datetime.strptime(deadline[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_left = (ddl - datetime.now(timezone.utc)).days
        except ValueError:
            days_left = None

        history = _get_push_history(rid, "ddl_reminder")
        nodes.append({
            "id": rid,
            "project": project,
            "decision": decision[:60],
            "deadline": deadline,
            "days_left": days_left,
            "maker": maker,
            "pushed_count": len(history),
            "last_pushed": history[-1] if history else None,
        })

    return nodes


# ─── CLI ───


def cmd_check(args):
    results = check_ddl_pushes(dry_run=args.dry_run)
    print(json.dumps({"status": "ok", "pushes": results}, ensure_ascii=False))


def cmd_list(args):
    nodes = list_upcoming_nodes(days=args.days)
    print(json.dumps({"status": "ok", "count": len(nodes), "nodes": nodes}, ensure_ascii=False))


def main():
    # Windows 终端 UTF-8 编码支持
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(description="feishu-memory 心跳推送引擎")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="检查并推送 DDL 提醒")
    p_check.add_argument("--dry-run", action="store_true", help="模拟运行，不实际发送")
    p_check.set_defaults(func=cmd_check)

    p_list = sub.add_parser("list", help="列出即将到达的关键节点")
    p_list.add_argument("--days", type=int, default=30, help="查看未来 N 天")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
