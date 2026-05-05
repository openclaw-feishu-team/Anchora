#!/usr/bin/env python3
# 文件路径：skills/feishu-memory/dashboard.py
# 修改类型：新增
# 依赖说明：标准库 http.server；无需 Flask，可在 Windows + Python 3.10+ 运行
"""feishu-memory 本地管理后台。"""
import argparse
import html
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent))
from interactive_cards import reject_candidate
from memory import confirm_candidate, db_conn, now_iso

CSS = """
<style>
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;background:#f7f7f8;color:#222}a{color:#1455d9;text-decoration:none}.nav a{margin-right:14px}.card{background:white;border:1px solid #ddd;border-radius:10px;padding:16px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.stat{font-size:28px;font-weight:700}.muted{color:#666}.btn{display:inline-block;padding:6px 10px;border-radius:6px;border:1px solid #ccc;background:#fff;margin-right:6px}.primary{background:#1455d9;color:white}.danger{background:#c62828;color:white}table{width:100%;border-collapse:collapse;background:#fff}th,td{border-bottom:1px solid #eee;text-align:left;padding:8px;vertical-align:top}code{background:#eee;padding:2px 4px;border-radius:4px}input,select{padding:6px;margin-right:6px}</style>
"""


def _html_page(title, body):
    nav = '<div class="nav"><a href="/">概览</a><a href="/decisions">决策</a><a href="/review">待审核</a><a href="/audit">审计</a></div>'
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>{CSS}</head><body><h1>{html.escape(title)}</h1>{nav}{body}</body></html>".encode("utf-8")


def _query_dict(handler):
    return {k: v[0] for k, v in parse_qs(urlparse(handler.path).query).items()}


def _overview():
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM decisions"); total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM decisions WHERE status='active'"); active = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM decisions WHERE status='candidate'"); candidate = c.fetchone()[0]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM decisions WHERE created_at LIKE ?", (f"{today}%",)); today_count = c.fetchone()[0]
    future = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%d")
    c.execute("SELECT project, decision, deadline FROM decisions WHERE deadline IS NOT NULL AND deadline!='' AND deadline<=? AND deadline>=? ORDER BY deadline LIMIT 10", (future, today))
    upcoming = c.fetchall(); conn.close()
    body = f"""
    <div class='grid'>
      <div class='card'><div class='stat'>{total}</div><div class='muted'>总决策</div></div>
      <div class='card'><div class='stat'>{active}</div><div class='muted'>active</div></div>
      <div class='card'><div class='stat'>{candidate}</div><div class='muted'>candidate</div></div>
      <div class='card'><div class='stat'>{today_count}</div><div class='muted'>今日新增</div></div>
    </div><div class='card'><h2>即将到期 DDL</h2><ul>
    """
    body += "".join([f"<li><b>{html.escape(r[2] or '')}</b> {html.escape(r[0] or '')}: {html.escape((r[1] or '')[:80])}</li>" for r in upcoming]) or "<li>暂无</li>"
    body += "</ul></div>"
    return body


def _decisions(qs):
    project = qs.get("project", ""); status = qs.get("status", ""); keyword = qs.get("keyword", "")
    conditions = []; params = []
    if project: conditions.append("project=?"); params.append(project)
    if status: conditions.append("status=?"); params.append(status)
    if keyword:
        conditions.append("(decision LIKE ? OR reasoning LIKE ? OR evidence LIKE ?)"); params.extend([f"%{keyword}%"]*3)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    conn = db_conn(); c = conn.cursor()
    c.execute(f"SELECT id, project, decision, status, deadline, created_at FROM decisions{where} ORDER BY created_at DESC LIMIT 200", tuple(params))
    rows = c.fetchall(); conn.close()
    form = f"""
    <form><input name='project' placeholder='project' value='{html.escape(project)}'><input name='keyword' placeholder='keyword' value='{html.escape(keyword)}'><select name='status'><option value=''>all</option><option {'selected' if status=='active' else ''}>active</option><option {'selected' if status=='candidate' else ''}>candidate</option><option {'selected' if status=='rejected' else ''}>rejected</option></select><button>过滤</button></form>
    """
    table = "<table><tr><th>ID</th><th>项目</th><th>决策</th><th>状态</th><th>DDL</th><th>时间</th></tr>"
    for r in rows:
        table += f"<tr><td><a href='/decisions/{html.escape(r[0])}'><code>{html.escape(r[0])}</code></a></td><td>{html.escape(r[1] or '')}</td><td>{html.escape((r[2] or '')[:120])}</td><td>{html.escape(r[3] or '')}</td><td>{html.escape(r[4] or '')}</td><td>{html.escape(r[5] or '')}</td></tr>"
    return form + table + "</table>"


def _decision_detail(mem_id):
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT * FROM decisions WHERE id=?", (mem_id,)); row = c.fetchone(); cols = [d[0] for d in c.description] if c.description else []
    c.execute("SELECT action, actor, details, created_at FROM audit_log WHERE memory_id=? ORDER BY created_at DESC", (mem_id,)); audits = c.fetchall()
    c.execute("SELECT subject, predicate, object FROM knowledge_triples WHERE memory_id=?", (mem_id,)); triples = c.fetchall()
    conn.close()
    if not row: return "<div class='card'>未找到</div>"
    rec = {cols[i]: row[i] for i in range(len(cols))}
    body = "<div class='card'>" + "".join([f"<p><b>{html.escape(k)}</b>: {html.escape(str(v or ''))}</p>" for k, v in rec.items()]) + "</div>"
    body += "<div class='card'><h2>审计历史</h2><ul>" + "".join([f"<li>{html.escape(a[3] or '')} — {html.escape(a[0] or '')} by {html.escape(a[1] or '')}: {html.escape(a[2] or '')}</li>" for a in audits]) + "</ul></div>"
    body += "<div class='card'><h2>关系图谱</h2><ul>" + "".join([f"<li>{html.escape(t[0] or '')} — {html.escape(t[1] or '')} → {html.escape(t[2] or '')}</li>" for t in triples]) + "</ul></div>"
    return body


def _review():
    conn = db_conn(); c = conn.cursor(); c.execute("SELECT id, project, decision, evidence, created_at FROM decisions WHERE status='candidate' ORDER BY created_at DESC"); rows = c.fetchall(); conn.close()
    body = "<table><tr><th>ID</th><th>项目</th><th>决策</th><th>证据</th><th>操作</th></tr>"
    for r in rows:
        body += f"<tr><td><code>{html.escape(r[0])}</code></td><td>{html.escape(r[1] or '')}</td><td>{html.escape(r[2] or '')}</td><td>{html.escape((r[3] or '')[:100])}</td><td><a class='btn primary' href='/action?op=confirm&id={html.escape(r[0])}'>确认</a><a class='btn danger' href='/action?op=reject&id={html.escape(r[0])}'>驳回</a></td></tr>"
    return body + "</table>"


def _audit():
    conn = db_conn(); c = conn.cursor(); c.execute("SELECT memory_id, action, actor, details, created_at FROM audit_log ORDER BY created_at DESC LIMIT 200"); rows = c.fetchall(); conn.close()
    body = "<table><tr><th>Memory</th><th>Action</th><th>Actor</th><th>Details</th><th>Time</th></tr>"
    for r in rows:
        body += f"<tr><td><code>{html.escape(r[0] or '')}</code></td><td>{html.escape(r[1] or '')}</td><td>{html.escape(r[2] or '')}</td><td>{html.escape(r[3] or '')}</td><td>{html.escape(r[4] or '')}</td></tr>"
    return body + "</table>"


def _send_json(handler, data, status=200):
    payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(payload)


def _api_overview():
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM decisions"); total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM decisions WHERE status='active'"); active = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM decisions WHERE status='candidate'"); candidate = c.fetchone()[0]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM decisions WHERE created_at LIKE ?", (f"{today}%",)); today_count = c.fetchone()[0]
    future = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%d")
    c.execute("SELECT project, decision, deadline FROM decisions WHERE deadline IS NOT NULL AND deadline!='' AND deadline<=? AND deadline>=? ORDER BY deadline LIMIT 10", (future, today))
    upcoming = [{"project": r[0], "decision": r[1], "deadline": r[2]} for r in c.fetchall()]
    conn.close()
    return {"total": total, "active": active, "candidate": candidate, "today": today_count, "upcoming": upcoming}


def _api_decisions(qs):
    project = qs.get("project", ""); status = qs.get("status", ""); keyword = qs.get("keyword", "")
    conditions = []; params = []
    if project: conditions.append("project=?"); params.append(project)
    if status: conditions.append("status=?"); params.append(status)
    if keyword:
        conditions.append("(decision LIKE ? OR reasoning LIKE ? OR evidence LIKE ?)"); params.extend([f"%{keyword}%"]*3)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    conn = db_conn(); c = conn.cursor()
    c.execute(f"SELECT id, project, decision, status, deadline, created_at FROM decisions{where} ORDER BY created_at DESC LIMIT 200", tuple(params))
    rows = [{"id": r[0], "project": r[1], "decision": r[2], "status": r[3], "deadline": r[4], "created_at": r[5]} for r in c.fetchall()]
    conn.close()
    return {"rows": rows}


def _api_decision_detail(mem_id):
    conn = db_conn(); c = conn.cursor()
    c.execute("SELECT * FROM decisions WHERE id=?", (mem_id,)); row = c.fetchone(); cols = [d[0] for d in c.description] if c.description else []
    c.execute("SELECT action, actor, details, created_at FROM audit_log WHERE memory_id=? ORDER BY created_at DESC", (mem_id,)); audits = [{"action": a[0], "actor": a[1], "details": a[2], "created_at": a[3]} for a in c.fetchall()]
    c.execute("SELECT subject, predicate, object FROM knowledge_triples WHERE memory_id=?", (mem_id,)); triples = [{"subject": t[0], "predicate": t[1], "object": t[2]} for t in c.fetchall()]
    conn.close()
    if not row: return {"error": "not found"}
    rec = {cols[i]: row[i] for i in range(len(cols))}
    return {"record": rec, "audits": audits, "triples": triples}


def _api_review():
    conn = db_conn(); c = conn.cursor(); c.execute("SELECT id, project, decision, evidence, created_at FROM decisions WHERE status='candidate' ORDER BY created_at DESC"); rows = [{"id": r[0], "project": r[1], "decision": r[2], "evidence": r[3], "created_at": r[4]} for r in c.fetchall()]; conn.close(); return {"rows": rows}


def _api_audit():
    conn = db_conn(); c = conn.cursor(); c.execute("SELECT memory_id, action, actor, details, created_at FROM audit_log ORDER BY created_at DESC LIMIT 200"); rows = [{"memory_id": r[0], "action": r[1], "actor": r[2], "details": r[3], "created_at": r[4]} for r in c.fetchall()]; conn.close(); return {"rows": rows}


def _api_action(qs):
    mem_id = qs.get("id", ""); op = qs.get("op", "")
    if not mem_id or op not in ("confirm", "reject"): return {"error": "invalid params"}
    result = confirm_candidate(mem_id, "dashboard") if op == "confirm" else reject_candidate(mem_id, "dashboard")
    return {"result": result}


class DashboardHandler(BaseHTTPRequestHandler):
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        try:
            path = urlparse(self.path).path
            qs = _query_dict(self)
            # JSON API routes
            if path == "/api/overview":
                _send_json(self, _api_overview()); return
            elif path == "/api/decisions":
                _send_json(self, _api_decisions(qs)); return
            elif path.startswith("/api/decisions/"):
                _send_json(self, _api_decision_detail(path.rsplit("/",1)[-1])); return
            elif path == "/api/review":
                _send_json(self, _api_review()); return
            elif path == "/api/audit":
                _send_json(self, _api_audit()); return
            elif path == "/api/action":
                _send_json(self, _api_action(qs)); return
            # HTML routes
            if path == "/": body = _overview(); title = "feishu-memory 概览"
            elif path == "/decisions": body = _decisions(qs); title = "决策列表"
            elif path.startswith("/decisions/"): body = _decision_detail(path.rsplit("/",1)[-1]); title = "决策详情"
            elif path == "/review": body = _review(); title = "待审核队列"
            elif path == "/audit": body = _audit(); title = "审计日志"
            elif path == "/action":
                mem_id = qs.get("id", ""); op = qs.get("op", "")
                result = confirm_candidate(mem_id, "dashboard") if op == "confirm" else reject_candidate(mem_id, "dashboard")
                body = f"<div class='card'><pre>{html.escape(json.dumps(result, ensure_ascii=False, indent=2))}</pre><a href='/review'>返回审核</a></div>"; title = "操作结果"
            else: body = "<div class='card'>404</div>"; title = "404"
            data = _html_page(title, body)
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self._cors_headers(); self.end_headers(); self.wfile.write(data)
        except Exception as exc:
            print(f"dashboard 请求失败: {exc}", file=sys.stderr)
            _send_json(self, {"error": str(exc)}, status=500)

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            qs = _query_dict(self)
            if path == "/api/action":
                _send_json(self, _api_action(qs)); return
            _send_json(self, {"error": "not found"}, status=404)
        except Exception as exc:
            print(f"dashboard POST 失败: {exc}", file=sys.stderr)
            _send_json(self, {"error": str(exc)}, status=500)

    def log_message(self, *_args): return


def run(port=8080):
    print(f"feishu-memory dashboard: http://127.0.0.1:{port}")
    HTTPServer(("127.0.0.1", port), DashboardHandler).serve_forever()


def main():
    parser = argparse.ArgumentParser(description="feishu-memory dashboard")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(); run(args.port)


if __name__ == "__main__":
    main()
