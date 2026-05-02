#!/usr/bin/env python3
"""
feishu-memory 群聊自动监听与记录

功能：
1. scan — 扫描指定群聊的最近消息，自动提取项目决策并记录
2. auto — 分析单条消息文本，判断是否包含决策/DDL/里程碑信息，自动执行 record
3. sync — 将本地记录自动同步到多维表格

无需用户 @ 触发。只要群聊中出现项目相关信息，即可自动记录。

Usage:
  python chat_auto_record.py auto "{chat_id}" "{消息内容}"
  python chat_auto_record.py scan "{chat_id}" --limit 20
"""
import argparse
import json
import sys
import os
import sqlite3
import re
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
from memory import (
    init_db, store_decision, extract_decision_structured, db_conn, now_iso,
    get_openclaw_config, get_feishu_account, get_tenant_access_token,
    sync_to_bitable
)

SCRIPT_DIR = Path(__file__).parent.resolve()


# ─── 关键词触发规则 ───

# 决策触发词
DECISION_TRIGGERS = [
    "决定", "确定", "结论", "拍板", "选定", "选用", "采用", "敲定",
    "确定用", "决定用", "选用", "选型", "方案", "架构", "设计",
    "需求变更", "变更", "调整", "修改", "更新", "迭代",
]

# DDL/时间节点触发词
DDL_TRIGGERS = [
    "上线", "截止", "ddl", "deadline", "里程碑", "节点", "交付",
    "发布", "提测", "评审", "验收", "发版", "投产", "交付日期",
    "完成时间", "预计", "计划", "排期", "时间",
]

# 项目相关触发词
PROJECT_TRIGGERS = [
    "项目", "产品", "模块", "功能", "系统", "平台", "服务",
]


def _should_auto_record(text: str) -> bool:
    """
    判断一条消息是否需要自动记录。
    触发条件：包含决策词 或 (包含DDL词 且 包含项目词)
    """
    text_lower = text.lower()
    has_decision = any(w in text for w in DECISION_TRIGGERS)
    has_ddl = any(w in text_lower for w in DDL_TRIGGERS)
    has_project = any(w in text for w in PROJECT_TRIGGERS)

    return has_decision or (has_ddl and has_project)


def _extract_project_from_chat(text: str, default: str = "未分类") -> str:
    """从群聊消息中提取项目名称"""
    # 优先匹配 "项目X" 或 "XX项目"
    m = re.search(r"([\w\u4e00-\u9fff]+项目|[\w\u4e00-\u9fff]+项目组)", text)
    if m:
        return m.group(1)
    m = re.search(r"项目\s*([\w\u4e00-\u9fff]+)", text)
    if m:
        return f"项目{m.group(1)}"
    return default


def _extract_deadline(text: str) -> str:
    """提取消息中的日期"""
    # 2026年10月31日
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})[日号]', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 2026-10-31
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
    return ""


def _update_decision_deadline(record_id: str, deadline: str):
    """更新决策记录的 deadline 字段"""
    conn = db_conn()
    c = conn.cursor()
    # 确保字段存在
    try:
        c.execute("SELECT deadline FROM decisions LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE decisions ADD COLUMN deadline TEXT")
    c.execute("UPDATE decisions SET deadline=? WHERE id=?", (deadline, record_id))
    conn.commit()
    conn.close()


def auto_record_message(chat_id: str, text: str, sender: str = "", dry_run: bool = False):
    """
    分析群聊消息，自动记录决策。
    返回操作结果。
    """
    if not _should_auto_record(text):
        return {"status": "skipped", "reason": "消息不包含决策或项目节点信息"}

    init_db()

    # 提取结构化信息
    project = _extract_project_from_chat(text)
    extracted = extract_decision_structured(text, project, sender)
    project = project or extracted.get("project", "未分类")

    # 提取 deadline
    deadline = _extract_deadline(text)

    if dry_run:
        return {
            "status": "dry_run",
            "would_record": True,
            "project": project,
            "decision": extracted.get("decision", text),
            "reasoning": extracted.get("reasoning", ""),
            "deadline": deadline,
            "chat_id": chat_id,
        }

    # 执行记录
    record = store_decision(
        project=project,
        decision=extracted.get("decision") or text,
        reasoning=extracted.get("reasoning", ""),
        conclusion=extracted.get("conclusion", ""),
        objections=extracted.get("objections", ""),
        decision_maker=sender or extracted.get("decision_maker", ""),
        chat_id=chat_id,
    )

    # 更新 deadline
    if deadline:
        _update_decision_deadline(record.get("id"), deadline)
        record["deadline"] = deadline

    # 自动同步到多维表格
    sync_result = None
    try:
        cfg = get_openclaw_config()
        app_id, app_secret = get_feishu_account(cfg, "group")
        token = get_tenant_access_token(app_id, app_secret)
        config_path = SCRIPT_DIR / "config.json"
        if config_path.exists():
            local_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            bitable_cfg = local_cfg.get("bitable", {})
            base_id = bitable_cfg.get("base_id")
            table_id = bitable_cfg.get("table_id")
            if base_id and table_id:
                synced = sync_to_bitable(token, base_id, table_id)
                sync_result = {"synced": synced}
    except Exception as e:
        sync_result = {"error": str(e)}

    return {
        "status": "recorded",
        "record": record,
        "auto_sync": sync_result,
    }


def scan_chat_history(chat_id: str, limit: int = 20):
    """
    扫描群聊历史消息（通过飞书 API 拉取）。
    注意：这需要群聊 bot 有消息读取权限，且 chat_id 有效。
    当前为简化实现，返回提示信息。
    """
    # 实际实现需要使用飞书 IM API 拉取历史消息
    # lark-cli 方式: lark-cli.exe im message list --container-chat-id <chat_id> --page-size <limit>
    return {
        "status": "info",
        "message": "群聊历史扫描需要通过飞书 IM API 或 lark-cli 实现。建议使用 'auto' 命令在收到每条消息时实时处理。",
        "suggestion": f"python chat_auto_record.py auto \"{chat_id}\" \"消息内容\"",
    }


# ─── CLI ───


def cmd_auto(args):
    result = auto_record_message(args.chat_id, args.text, args.sender, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False))


def cmd_scan(args):
    result = scan_chat_history(args.chat_id, args.limit)
    print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="feishu-memory 群聊自动监听与记录")
    sub = parser.add_subparsers(dest="command", required=True)

    p_auto = sub.add_parser("auto", help="分析单条消息并自动记录")
    p_auto.add_argument("chat_id", help="飞书会话ID")
    p_auto.add_argument("text", help="消息内容")
    p_auto.add_argument("--sender", "-s", default="", help="发送者名称")
    p_auto.add_argument("--dry-run", action="store_true", help="模拟运行")
    p_auto.set_defaults(func=cmd_auto)

    p_scan = sub.add_parser("scan", help="扫描群聊历史消息")
    p_scan.add_argument("chat_id", help="飞书会话ID")
    p_scan.add_argument("--limit", "-l", type=int, default=20, help="扫描消息数量")
    p_scan.set_defaults(func=cmd_scan)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
