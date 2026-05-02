#!/usr/bin/env python3
"""
feishu-memory 每日日志记录器

功能：
1. 每天把当天的决策记录、推送日志、群聊摘要写入 logs/YYYY-MM-DD.md
2. 提供 write 命令手动触发，或配合 cron 自动执行
3. 日志文件超过 30 天后自动归档到 logs/archive/

Usage:
  python daily_log.py write              # 写入今天的日志
  python daily_log.py archive            # 归档超过 30 天的日志
  python daily_log.py list               # 列出所有日志文件
"""
import argparse
import json
import sys
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent))
from memory import db_conn, now_iso

SCRIPT_DIR = Path(__file__).parent.resolve()
LOG_DIR = SCRIPT_DIR / "logs"
ARCHIVE_DIR = LOG_DIR / "archive"


def _ensure_dirs():
    LOG_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(exist_ok=True)


def _get_today_records():
    """获取今天创建或更新的决策记录"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = db_conn()
    c = conn.cursor()

    c.execute("""
        SELECT project, decision, reasoning, conclusion, objections, decision_maker, created_at, deadline
        FROM decisions
        WHERE (created_at LIKE ? OR updated_at LIKE ?)
          AND (superseded_by IS NULL OR superseded_by = '')
        ORDER BY created_at DESC
    """, (f"{today}%", f"{today}%"))

    cols = [d[0] for d in c.description]
    rows = c.fetchall()
    records = [{cols[i]: row[i] for i in range(len(cols))} for row in rows]

    # 获取今天的推送日志
    c.execute("""
        SELECT decision_id, push_type, pushed_at, content
        FROM push_log
        WHERE pushed_at LIKE ?
        ORDER BY pushed_at DESC
    """, (f"{today}%",))
    push_rows = c.fetchall()
    pushes = [{
        "decision_id": r[0], "push_type": r[1],
        "pushed_at": r[2], "content": r[3]
    } for r in push_rows]

    conn.close()
    return records, pushes


def _count_total_stats():
    """统计总数"""
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM decisions WHERE superseded_by IS NULL OR superseded_by = ''")
    total_decisions = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT project) FROM decisions WHERE superseded_by IS NULL OR superseded_by = ''")
    total_projects = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM push_log")
    total_pushes = c.fetchone()[0]
    conn.close()
    return total_decisions, total_projects, total_pushes


def write_daily_log():
    """写入今天的日志"""
    _ensure_dirs()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"{today}.md"

    records, pushes = _get_today_records()
    total_decisions, total_projects, total_pushes = _count_total_stats()

    lines = [
        f"# feishu-memory 每日日志 — {today}",
        "",
        f"生成时间：{now_iso()}",
        "",
        "## 今日统计",
        f"- 今日新增决策：{len(records)} 条",
        f"- 今日推送：{len(pushes)} 次",
        f"- 累计决策：{total_decisions} 条",
        f"- 累计项目：{total_projects} 个",
        f"- 累计推送：{total_pushes} 次",
        "",
    ]

    if records:
        lines.append("## 今日决策记录")
        lines.append("")
        for i, r in enumerate(records, 1):
            lines.append(f"### {i}. {r.get('project', '未分类')}")
            lines.append(f"- **决策**：{r.get('decision', '')}")
            if r.get('reasoning'):
                lines.append(f"- **理由**：{r.get('reasoning')}")
            if r.get('deadline'):
                lines.append(f"- **DDL**：{r.get('deadline')}")
            if r.get('decision_maker'):
                lines.append(f"- **负责人**：{r.get('decision_maker')}")
            lines.append(f"- **时间**：{r.get('created_at', '')}")
            lines.append("")
    else:
        lines.append("## 今日决策记录")
        lines.append("")
        lines.append("*今日无新增决策记录*")
        lines.append("")

    if pushes:
        lines.append("## 今日推送记录")
        lines.append("")
        for p in pushes:
            lines.append(f"- `{p['push_type']}` → {p['decision_id']} @ {p['pushed_at'][:16]}")
        lines.append("")
    else:
        lines.append("## 今日推送记录")
        lines.append("")
        lines.append("*今日无推送*")
        lines.append("")

    # 即将到达的关键节点
    conn = db_conn()
    c = conn.cursor()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    future = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%d")
    c.execute("""
        SELECT project, decision, deadline, decision_maker
        FROM decisions
        WHERE deadline IS NOT NULL AND deadline != ''
          AND deadline >= ? AND deadline <= ?
          AND (superseded_by IS NULL OR superseded_by = '')
        ORDER BY deadline
    """, (now_str, future))
    upcoming = c.fetchall()
    conn.close()

    if upcoming:
        lines.append("## 即将到达的关键节点（未来14天）")
        lines.append("")
        for row in upcoming:
            project, decision, deadline, maker = row
            try:
                ddl = datetime.strptime(deadline[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                days_left = (ddl - datetime.now(timezone.utc)).days
            except ValueError:
                days_left = "?"
            lines.append(f"- **{deadline}**（还剩 {days_left} 天）— {project}：{decision[:50]}...")
        lines.append("")

    lines.append("---")
    lines.append("*本日志由 feishu-memory daily_log.py 自动生成*")

    log_file.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "file": str(log_file), "records": len(records), "pushes": len(pushes)}


def archive_old_logs():
    """归档超过 30 天的日志"""
    _ensure_dirs()
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    archived = 0

    for f in LOG_DIR.glob("*.md"):
        try:
            file_date = datetime.strptime(f.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if file_date < cutoff:
                target = ARCHIVE_DIR / f.name
                f.rename(target)
                archived += 1
        except ValueError:
            continue

    return {"status": "ok", "archived": archived}


def list_logs():
    """列出所有日志文件"""
    _ensure_dirs()
    files = sorted(LOG_DIR.glob("*.md"), key=lambda x: x.stem, reverse=True)
    return [{"file": f.name, "date": f.stem, "size": f.stat().st_size} for f in files]


# ─── CLI ───


def cmd_write(_args):
    result = write_daily_log()
    print(json.dumps(result, ensure_ascii=False))


def cmd_archive(_args):
    result = archive_old_logs()
    print(json.dumps(result, ensure_ascii=False))


def cmd_list(_args):
    logs = list_logs()
    print(json.dumps({"status": "ok", "count": len(logs), "logs": logs}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="feishu-memory 每日日志")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("write", help="写入今天的日志").set_defaults(func=cmd_write)
    sub.add_parser("archive", help="归档超过 30 天的日志").set_defaults(func=cmd_archive)
    sub.add_parser("list", help="列出所有日志文件").set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
