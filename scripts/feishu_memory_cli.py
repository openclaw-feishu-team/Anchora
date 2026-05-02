#!/usr/bin/env python3
"""
feishu-memory 的统一 CLI 入口
简化 Agent 调用，减少参数复杂度
"""
import argparse
import json
import sys
import os
from pathlib import Path

# 导入主模块
sys.path.insert(0, str(Path(__file__).parent.parent))
from memory import (
    extract_decision_structured, store_decision, query_decisions,
    recall_relevant_decisions, forget_expired,
    get_openclaw_config, get_feishu_account, get_tenant_access_token,
    sync_to_bitable, now_iso, db_conn
)
from context_engineer import store_decision_context


def cmd_record(args):
    """快速记录决策（一行命令），记录后自动同步到多维表格"""
    extracted = extract_decision_structured(args.text, args.project, args.maker)
    record = store_decision(
        project=args.project or extracted.get("project", "未分类"),
        decision=extracted.get("decision") or args.text,
        reasoning=extracted.get("reasoning", ""),
        conclusion=extracted.get("conclusion", ""),
        objections=extracted.get("objections", ""),
        decision_maker=args.maker or extracted.get("decision_maker", ""),
        chat_id=args.chat or "",
        deadline=extracted.get("deadline", ""),
    )
    # 自动存储上下文摘要（不阻塞，失败静默）
    try:
        store_decision_context(
            chat_id=args.chat or "",
            project=args.project or extracted.get("project", "未分类"),
            decision=extracted.get("decision") or args.text,
            reasoning=extracted.get("reasoning", ""),
            maker=args.maker or extracted.get("decision_maker", "")
        )
    except Exception:
        pass

    # 矛盾检测：如果有被覆盖的旧决策，提示冲突
    conflicts = []
    if record.get("superseded"):
        try:
            conn = db_conn()
            c = conn.cursor()
            c.execute("SELECT project, decision, reasoning, created_at FROM decisions WHERE id=?", (record["superseded"],))
            row = c.fetchone()
            conn.close()
            if row:
                conflicts.append({
                    "type": "superseded",
                    "message": f"新决策与旧决策冲突：[{row[0]}] {row[1]} ({row[3][:10]})",
                    "old_decision": row[1],
                    "old_reasoning": row[2],
                })
        except Exception:
            pass

    # ─── 自动同步到多维表格（不阻塞，失败静默）───
    sync_result = None
    try:
        cfg = get_openclaw_config()
        app_id, app_secret = get_feishu_account(cfg, "group")
        token = get_tenant_access_token(app_id, app_secret)
        config_path = Path(__file__).parent.parent / "config.json"
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

    output = {"status": "ok", "record": record, "auto_sync": sync_result}
    if conflicts:
        output["conflicts"] = conflicts
        output["warning"] = "检测到与历史决策冲突，请确认是否覆盖"
    print(json.dumps(output, ensure_ascii=False))


def cmd_query(args):
    """查询项目决策"""
    results = query_decisions(project=args.project, query_text=args.q, top_k=args.limit)
    print(json.dumps({"status": "ok", "count": len(results), "results": results}, ensure_ascii=False))


def _format_decision_card(r: dict) -> str:
    """将决策记录格式化为飞书卡片文本"""
    lines = [f"📋 【{r.get('project', '未分类')}】"]
    lines.append(f"决策：{r.get('decision', '')}")
    if r.get('reasoning'):
        lines.append(f"理由：{r.get('reasoning')}")
    if r.get('conclusion') and r.get('conclusion') != r.get('decision'):
        lines.append(f"结论：{r.get('conclusion')}")
    if r.get('objections'):
        lines.append(f"⚠️ 反对意见：{r.get('objections')}")
    if r.get('decision_maker'):
        lines.append(f"决策人：{r.get('decision_maker')}")
    lines.append(f"时间：{r.get('created_at', '')[:10]}")
    return "\n".join(lines)


def cmd_recall(args):
    """主动推送：根据消息检索相关决策，返回格式化卡片"""
    results = recall_relevant_decisions(message=args.message, project=args.project, top_k=args.limit)
    cards = [_format_decision_card(r) for r in results]
    print(json.dumps({
        "status": "ok",
        "count": len(results),
        "has_relevant": len(results) > 0,
        "decisions": results,
        "cards": cards,
        "suggestion": "检测到相关历史决策，建议推送决策卡片" if results else None,
    }, ensure_ascii=False))


def cmd_projects(_args):
    """列出所有项目"""
    import sqlite3
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT DISTINCT project FROM decisions WHERE superseded_by IS NULL OR superseded_by = '' ORDER BY project")
    projects = [row[0] for row in c.fetchall()]
    conn.close()
    print(json.dumps({"status": "ok", "projects": projects}, ensure_ascii=False))


def cmd_sync(args):
    """同步到多维表格"""
    cfg = get_openclaw_config()
    app_id, app_secret = get_feishu_account(cfg, args.account or "private1")
    token = get_tenant_access_token(app_id, app_secret)

    # 优先使用命令行参数，否则读取 config.json
    base_id = args.base
    table_id = args.table
    if not base_id or not table_id:
        config_path = Path(__file__).parent.parent / "config.json"
        if config_path.exists():
            local_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            bitable_cfg = local_cfg.get("bitable", {})
            base_id = base_id or bitable_cfg.get("base_id")
            table_id = table_id or bitable_cfg.get("table_id")

    if not base_id or not table_id:
        print(json.dumps({"status": "error", "message": "缺少 base_id 或 table_id，请通过参数传入或在 config.json 中配置"}, ensure_ascii=False))
        sys.exit(1)

    success = sync_to_bitable(token, base_id, table_id)
    print(json.dumps({"status": "ok", "synced": success}, ensure_ascii=False))


def main():
    # Windows 终端 UTF-8 编码支持
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(description="feishu-memory 快速 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # record: 最常用，参数最少
    p_rec = sub.add_parser("record", help="记录决策（自动抽取）")
    p_rec.add_argument("text", help="决策描述文本")
    p_rec.add_argument("--project", "-p", help="项目名称")
    p_rec.add_argument("--maker", "-m", help="决策人")
    p_rec.add_argument("--chat", "-c", help="飞书会话ID")
    p_rec.set_defaults(func=cmd_record)

    # query
    p_q = sub.add_parser("query", help="查询决策")
    p_q.add_argument("--project", "-p", required=True)
    p_q.add_argument("--q", help="语义查询")
    p_q.add_argument("--limit", "-l", type=int, default=5)
    p_q.set_defaults(func=cmd_query)

    # recall
    p_re = sub.add_parser("recall", help="主动推送检索")
    p_re.add_argument("message", help="新消息内容")
    p_re.add_argument("--project", "-p", help="限定项目")
    p_re.add_argument("--limit", "-l", type=int, default=3)
    p_re.set_defaults(func=cmd_recall)

    # projects
    p_proj = sub.add_parser("projects", help="列出项目")
    p_proj.set_defaults(func=cmd_projects)

    # sync
    p_sync = sub.add_parser("sync", help="同步到 Bitable")
    p_sync.add_argument("--base", help="Bitable base_id（默认读取 config.json）")
    p_sync.add_argument("--table", help="Bitable table_id（默认读取 config.json）")
    p_sync.add_argument("--account", default="private1")
    p_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
