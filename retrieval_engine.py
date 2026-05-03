#!/usr/bin/env python3
# 文件路径：skills/feishu-memory/retrieval_engine.py
# 修改类型：新增
# 依赖说明：标准库；复用 memory.py 中 SQLite/Chroma/LLM 配置，LLM 调用失败时降级为规则摘要
"""L0/L1/L2/L3 分层检索引擎。"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

sys.path.insert(0, str(Path(__file__).parent))
from memory import db_conn, get_embedding_for_texts, get_collection, get_openclaw_config, query_decisions, recall_relevant_decisions, init_db, _ensure_governance_columns

_HOT_CACHE = {"built_at": None, "items": []}


def _row_to_dict(cols, row):
    return {cols[i]: row[i] for i in range(len(cols))}


def _dedupe(results):
    seen = set()
    out = []
    for item in results:
        mid = item.get("id")
        if mid and mid not in seen:
            seen.add(mid)
            out.append(item)
    return out


def _build_hot_cache(project=None):
    """构建最近24h + 高频访问热缓存。"""
    try:
        init_db()
        _ensure_governance_columns()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
        conn = db_conn()
        c = conn.cursor()
        params = [cutoff, cutoff]
        project_clause = ""
        if project:
            project_clause = " AND d.project=?"
            params.append(project)
        c.execute(f"""
            SELECT d.*, COUNT(a.memory_id) AS access_count
            FROM decisions d
            LEFT JOIN access_log a ON a.memory_id=d.id AND a.accessed_at>=?
            WHERE (d.created_at>=? OR a.accessed_at IS NOT NULL)
              AND d.status='active'
              AND (d.superseded_by IS NULL OR d.superseded_by='')
              {project_clause}
            GROUP BY d.id
            ORDER BY access_count DESC, d.created_at DESC
            LIMIT 100
        """, tuple(params))
        cols = [d[0] for d in c.description]
        items = [_row_to_dict(cols, r) for r in c.fetchall()]
        conn.close()
        _HOT_CACHE["built_at"] = datetime.now(timezone.utc)
        _HOT_CACHE["items"] = items
    except Exception as exc:
        print(f"L0 热缓存构建失败: {exc}", file=sys.stderr)
        _HOT_CACHE["items"] = []


def _l0(query: str, project=None, top_k=5):
    """L0：热缓存命中。"""
    built_at = _HOT_CACHE.get("built_at")
    if not built_at or (datetime.now(timezone.utc) - built_at).total_seconds() > 300:
        _build_hot_cache(project)
    q = (query or "").lower()
    hits = []
    for item in _HOT_CACHE.get("items", []):
        if project and item.get("project") != project:
            continue
        text = f"{item.get('project','')} {item.get('decision','')} {item.get('reasoning','')}".lower()
        if not q or q in text or any(tok and tok in text for tok in q.split()):
            item = dict(item)
            item["_layer"] = "L0"
            hits.append(item)
        if len(hits) >= top_k:
            break
    return hits


def _l1(query: str, project=None, top_k=5):
    """L1：SQLite LIKE 精确/关键词匹配。"""
    try:
        init_db()
        _ensure_governance_columns()
        conn = db_conn()
        c = conn.cursor()
        conditions = ["status='active'", "(superseded_by IS NULL OR superseded_by='')"]
        params = []
        if project:
            conditions.append("project=?")
            params.append(project)
        if query:
            like = f"%{query}%"
            conditions.append("(decision LIKE ? OR reasoning LIKE ? OR conclusion LIKE ? OR evidence LIKE ?)")
            params.extend([like, like, like, like])
        c.execute(f"SELECT * FROM decisions WHERE {' AND '.join(conditions)} ORDER BY created_at DESC LIMIT ?", (*params, top_k))
        cols = [d[0] for d in c.description]
        rows = [_row_to_dict(cols, r) for r in c.fetchall()]
        conn.close()
        for r in rows:
            r["_layer"] = "L1"
        return rows
    except Exception as exc:
        print(f"L1 检索失败: {exc}", file=sys.stderr)
        return []


def _l2(query: str, project=None, top_k=5):
    """L2：Chroma 向量语义检索。"""
    try:
        results = recall_relevant_decisions(query, project=project, top_k=top_k)
        for r in results:
            r["_layer"] = "L2"
        return results
    except Exception as exc:
        print(f"L2 检索失败: {exc}", file=sys.stderr)
        return []


def _get_provider_api_key(cfg, provider_name="qwen"):
    try:
        root = Path(cfg.get("agents", {}).get("defaults", {}).get("workspace", ""))
        auth_path = (root / ".." / "agents" / "main" / "agent" / "auth-profiles.json").resolve()
        if auth_path.exists():
            auth = json.loads(auth_path.read_text(encoding="utf-8"))
            return auth.get("profiles", {}).get(f"{provider_name}:default", {}).get("apiKey", "")
    except Exception:
        pass
    return ""


def _l3(query: str, existing: list, project=None):
    """L3：LLM 综合归纳，不阻塞失败。"""
    summary = ""
    try:
        cfg = get_openclaw_config() or {}
        provider = (cfg.get("models", {}).get("providers", {}) or {}).get("qwen", {})
        api_key = _get_provider_api_key(cfg, "qwen")
        base_url = provider.get("baseUrl")
        model = (provider.get("models") or [{}])[0].get("id", "qwen-turbo-latest")
        if api_key and base_url:
            material = "\n".join([f"- [{r.get('project')}] {r.get('decision')} / {r.get('reasoning')}" for r in existing[:8]])
            prompt = f"基于以下历史决策，回答用户查询。若证据不足，请明确说明需要重新确认。\n项目：{project or '不限'}\n查询：{query}\n历史：\n{material}"
            resp = requests.post(f"{base_url}/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}, timeout=20)
            summary = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as exc:
        print(f"L3 LLM 归纳失败: {exc}", file=sys.stderr)
    if not summary:
        if existing:
            latest = existing[0]
            summary = f"基于历史决策，{latest.get('project', project or '该项目')} 曾倾向于：{latest.get('decision', '')}。如该决策较旧或证据不足，建议重新确认。"
        else:
            summary = "没有足够历史决策支撑结论，建议先补充或确认项目决策。"
    return {"_layer": "L3", "type": "synthesis", "project": project, "query": query, "answer": summary}


def layered_retrieve(query, project=None, top_k=5, max_layer="L3"):
    """执行 L0→L1→L2→L3 分层检索。"""
    order = ["L0", "L1", "L2", "L3"]
    max_idx = order.index(max_layer) if max_layer in order else 3
    results = []
    if max_idx >= 0:
        results.extend(_l0(query, project, top_k))
    if len(_dedupe(results)) < top_k and max_idx >= 1:
        results.extend(_l1(query, project, top_k))
    if len(_dedupe(results)) < top_k and max_idx >= 2:
        results.extend(_l2(query, project, top_k))
    results = _dedupe(results)[:top_k]
    synthesis = None
    if len(results) < top_k and max_idx >= 3:
        synthesis = _l3(query, results, project)
    return {"status": "ok", "query": query, "project": project, "count": len(results), "results": results, "synthesis": synthesis}


def main():
    parser = argparse.ArgumentParser(description="feishu-memory L0/L1/L2/L3 分层检索")
    parser.add_argument("query")
    parser.add_argument("--project", "-p")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--layer", choices=["L0", "L1", "L2", "L3"], default="L3")
    args = parser.parse_args()
    print(json.dumps(layered_retrieve(args.query, args.project, args.top_k, args.layer), ensure_ascii=False))


if __name__ == "__main__":
    main()
