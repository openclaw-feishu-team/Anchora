#!/usr/bin/env python3
"""
feishu-memory 定向 Benchmark Runner（独立版）

目标：
1) 不改动原有 benchmark.py
2) 覆盖五大痛点
3) 强化硬性指标：抗干扰、矛盾更新（以及 case 驱动的重复决策召回等汇总）

用例 JSON：`cases/*_cases.json` 根节点为 case 数组。
设计理念与字段释义见 docs/BENCHMARK_CASES_REFERENCE.md（仍兼容带 `cases` 键的包裹格式）。
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


SCRIPT_DIR = Path(__file__).parent.resolve()
SKILL_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = SCRIPT_DIR / "outputs"
DEFAULT_CASES = SCRIPT_DIR / "cases"
PROJECT_PREFIX = "_benchmark_sry_"

sys.path.insert(0, str(SKILL_DIR))

from memory import (  # noqa: E402
    _ensure_governance_columns,
    db_conn,
    extract_decision_structured,
    init_db,
    now_iso,
    query_decisions,
    recall_relevant_decisions,
    store_decision,
)

from chat_auto_record import auto_record_message  # noqa: E402


@dataclass
class CheckItem:
    name: str
    passed: bool
    detail: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _strip_query_prefix(text: str) -> str:
    return text.replace("/recall ", "").replace("/query ", "").strip()


def _project_for_case(case: Dict[str, Any]) -> str:
    return case.get("project") or f"{PROJECT_PREFIX}{case.get('case_id', 'unknown')}"


def _normalize_event_text(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("content", ""))
    return str(event)


def _decision_text(text: str, project: str) -> Dict[str, Any]:
    extracted = extract_decision_structured(text, project=project)
    return {
        "decision": extracted.get("decision") or text,
        "reasoning": extracted.get("reasoning", ""),
        "conclusion": extracted.get("conclusion", ""),
        "objections": extracted.get("objections", ""),
    }


def _store_event_as_decision(project: str, text: str, confidence: float = 0.9) -> Dict[str, Any]:
    parsed = _decision_text(text, project=project)
    return store_decision(
        project=project,
        decision=parsed["decision"],
        reasoning=parsed["reasoning"],
        conclusion=parsed["conclusion"],
        objections=parsed["objections"],
        chat_id="oc_benchmark_sry",
        evidence=text,
        confidence=confidence,
    )


def _backdate_record(mem_id: str, days_ago: int) -> None:
    target = (_utc_now() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    conn = db_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE decisions SET created_at=?, updated_at=?, last_accessed=? WHERE id=?",
        (target, target, target, mem_id),
    )
    conn.commit()
    conn.close()


def _rows_for_project(project: str) -> List[Dict[str, Any]]:
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM decisions WHERE project=? ORDER BY created_at DESC", (project,))
    cols = [d[0] for d in c.description]
    rows = [{cols[i]: row[i] for i in range(len(cols))} for row in c.fetchall()]
    conn.close()
    return rows


def _rank_of_value(results: List[Dict[str, Any]], expected_value: str) -> Optional[int]:
    if not expected_value:
        return None
    for idx, r in enumerate(results, start=1):
        decision = str(r.get("decision", ""))
        if expected_value in decision:
            return idx
    return None


def _forbidden_leaked(results: List[Dict[str, Any]], forbidden_values: List[str], top_n: int) -> bool:
    if not forbidden_values:
        return False
    top = results[: max(1, top_n)]
    for r in top:
        decision = str(r.get("decision", ""))
        for forbidden in forbidden_values:
            if forbidden and forbidden in decision:
                return True
    return False


def _clean_benchmark_data(prefix: str = PROJECT_PREFIX) -> None:
    conn = db_conn()
    c = conn.cursor()

    c.execute("SELECT id FROM decisions WHERE project LIKE ?", (f"{prefix}%",))
    ids = [row[0] for row in c.fetchall()]
    if ids:
        placeholders = ",".join(["?"] * len(ids))
        c.execute(f"DELETE FROM access_log WHERE memory_id IN ({placeholders})", tuple(ids))
        c.execute(f"DELETE FROM audit_log WHERE memory_id IN ({placeholders})", tuple(ids))
        c.execute(
            f"DELETE FROM memory_edges WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})",
            tuple(ids + ids),
        )
    c.execute("DELETE FROM decisions WHERE project LIKE ?", (f"{prefix}%",))
    conn.commit()
    conn.close()


def _safe_table_count(table_name: str) -> Optional[int]:
    """安全读取表行数，表不存在时返回 None。"""
    conn = db_conn()
    c = conn.cursor()
    try:
        c.execute(f"SELECT COUNT(*) FROM {table_name}")
        return int(c.fetchone()[0])
    except Exception:
        return None
    finally:
        conn.close()


def _db_snapshot(prefix: str = PROJECT_PREFIX) -> Dict[str, Any]:
    """
    生成数据库快照（用于可视化运行前后变化）。
    注意：该快照在 benchmark 清理前采集，便于观察测试数据写入效果。
    """
    tables = [
        "decisions",
        "access_log",
        "audit_log",
        "memory_edges",
        "embed_cache",
        "push_log",
        "decision_contexts",
        "knowledge_triples",
    ]
    table_counts = {name: _safe_table_count(name) for name in tables}

    conn = db_conn()
    c = conn.cursor()
    benchmark_status: Dict[str, int] = {}
    benchmark_projects_top: List[Dict[str, Any]] = []
    benchmark_decisions = 0
    try:
        c.execute("SELECT COUNT(*) FROM decisions WHERE project LIKE ?", (f"{prefix}%",))
        benchmark_decisions = int(c.fetchone()[0])

        c.execute(
            """
            SELECT COALESCE(status, '') AS status, COUNT(*) AS cnt
            FROM decisions
            WHERE project LIKE ?
            GROUP BY COALESCE(status, '')
            ORDER BY cnt DESC
            """,
            (f"{prefix}%",),
        )
        for status, cnt in c.fetchall():
            benchmark_status[str(status or "")] = int(cnt)

        c.execute(
            """
            SELECT project, COUNT(*) AS cnt
            FROM decisions
            WHERE project LIKE ?
            GROUP BY project
            ORDER BY cnt DESC, project ASC
            LIMIT 10
            """,
            (f"{prefix}%",),
        )
        benchmark_projects_top = [{"project": str(p), "count": int(n)} for p, n in c.fetchall()]
    finally:
        conn.close()

    return {
        "captured_at": now_iso(),
        "table_counts": table_counts,
        "benchmark_scope": {
            "project_prefix": prefix,
            "decisions_count": benchmark_decisions,
            "status_distribution": benchmark_status,
            "top_projects": benchmark_projects_top,
        },
    }


def _db_snapshot_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """计算快照差异（after - before）。"""
    before_counts = before.get("table_counts", {})
    after_counts = after.get("table_counts", {})
    keys = sorted(set(before_counts.keys()) | set(after_counts.keys()))

    table_delta: Dict[str, Optional[int]] = {}
    for key in keys:
        b = before_counts.get(key)
        a = after_counts.get(key)
        if b is None or a is None:
            table_delta[key] = None
        else:
            table_delta[key] = int(a) - int(b)

    b_scope = before.get("benchmark_scope", {})
    a_scope = after.get("benchmark_scope", {})
    b_status = b_scope.get("status_distribution", {})
    a_status = a_scope.get("status_distribution", {})
    status_keys = sorted(set(b_status.keys()) | set(a_status.keys()))
    status_delta = {k: int(a_status.get(k, 0)) - int(b_status.get(k, 0)) for k in status_keys}

    return {
        "table_counts_delta": table_delta,
        "benchmark_scope_delta": {
            "project_prefix": a_scope.get("project_prefix") or b_scope.get("project_prefix"),
            "decisions_count_delta": int(a_scope.get("decisions_count", 0)) - int(b_scope.get("decisions_count", 0)),
            "status_distribution_delta": status_delta,
        },
    }


def _run_case_decision_capture(case: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[CheckItem] = []
    text = str(case.get("text", ""))
    expected_candidate = bool(case.get("expected_candidate", True))
    expected = case.get("expected_output", {})
    project = _project_for_case(case)

    dry = auto_record_message("oc_benchmark_sry", text, sender="benchmark", dry_run=True)
    is_recordable = dry.get("status") == "dry_run" and bool(dry.get("would_record"))

    if expected_candidate:
        checks.append(CheckItem("candidate_触发", is_recordable, f"dry_run={dry.get('status')}"))
        if is_recordable:
            rec = _store_event_as_decision(project, text)
            expected_kw = str(expected.get("reason_keyword", ""))
            if expected_kw:
                checks.append(
                    CheckItem(
                        "reason_keyword_命中",
                        expected_kw in str(rec.get("reasoning", "")),
                        f"期望={expected_kw} 实际={rec.get('reasoning', '')[:80]}",
                    )
                )
    else:
        checks.append(CheckItem("negative_不过滤失败", not is_recordable, f"dry_run={dry.get('status')}"))

    return {"checks": checks, "extra": {"dry_run": dry}}


def _run_case_anti_interference(case: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[CheckItem] = []
    project = _project_for_case(case)
    expected = case.get("expected_output", {})
    top_k = int(case.get("top_k", 5))

    anchor = str(case.get("anchor_event", ""))
    rec = _store_event_as_decision(project, anchor)
    days_ago = int(case.get("simulate_days_ago", 0))
    if days_ago > 0:
        _backdate_record(rec["id"], days_ago)

    distractor_events = _to_list(case.get("distractor_events"))
    for evt in distractor_events:
        _store_event_as_decision(project, _normalize_event_text(evt))

    noise_events = _to_list(case.get("noise_events"))
    noise_inputs = 0
    for evt in noise_events:
        noise_inputs += 1
        auto_record_message("oc_benchmark_sry", _normalize_event_text(evt), sender="noise", dry_run=True)

    query = _strip_query_prefix(str(case.get("query", "")))
    start = time.perf_counter()
    results = query_decisions(project=project, query_text=query, top_k=top_k, status_filter="all")
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    expected_value = str(expected.get("expected_active_value", ""))
    rank = _rank_of_value(results, expected_value)
    max_rank = int(expected.get("max_rank", 3))
    checks.append(CheckItem("抗干扰_命中", rank is not None and rank <= max_rank, f"rank={rank}, max_rank={max_rank}"))

    forbidden_values = [str(x) for x in _to_list(expected.get("forbidden_values"))]
    leaked = _forbidden_leaked(results, forbidden_values, top_n=3)
    checks.append(CheckItem("抗干扰_旧值不泄漏", not leaked, f"forbidden={forbidden_values}"))

    min_noise = int(case.get("min_noise_inputs", 20))
    checks.append(CheckItem("抗干扰_噪声规模", noise_inputs >= min_noise, f"noise={noise_inputs}, min={min_noise}"))

    return {
        "checks": checks,
        "extra": {
            "latency_ms": latency_ms,
            "rank": rank,
            "query": query,
            "noise_inputs": noise_inputs,
            "stored_rows": len(_rows_for_project(project)),
            "top_results": [str(r.get("decision", "")) for r in results[:3]],
        },
    }


def _run_case_decision_aggregation(case: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[CheckItem] = []
    project = _project_for_case(case)
    expected = case.get("expected_output", {})
    events = _to_list(case.get("events"))

    for evt in events:
        text = _normalize_event_text(evt)
        if text.startswith("/decide "):
            text = text.replace("/decide ", "", 1)
        _store_event_as_decision(project, text)

    query = _strip_query_prefix(str(case.get("query", "")))
    start = time.perf_counter()
    results = query_decisions(project=project, query_text=query, top_k=5, status_filter="all")
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    expected_value = str(expected.get("expected_active_value", ""))
    rank = _rank_of_value(results, expected_value)
    checks.append(CheckItem("聚合_结论命中", rank is not None and rank <= 3, f"rank={rank}"))

    evidence_hits = 0
    for r in results:
        for kw in _to_list(expected.get("evidence_keywords")):
            kw = str(kw)
            if kw and (kw in str(r.get("reasoning", "")) or kw in str(r.get("evidence", ""))):
                evidence_hits += 1
    min_sources = int(expected.get("source_count_min", 2))
    checks.append(CheckItem("聚合_证据命中", evidence_hits >= min_sources, f"hits={evidence_hits}, min={min_sources}"))

    return {
        "checks": checks,
        "extra": {"latency_ms": latency_ms, "rank": rank, "evidence_hits": evidence_hits},
    }


def _run_case_context_invalidated(case: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[CheckItem] = []
    project = _project_for_case(case)
    expected = case.get("expected_output", {})

    initial_event = str(case.get("initial_event", ""))
    invalidating_event = str(case.get("invalidating_event", ""))
    updated_event = str(case.get("updated_event", ""))

    rec_old = _store_event_as_decision(project, initial_event)
    _store_event_as_decision(project, invalidating_event)
    rec_new = _store_event_as_decision(project, updated_event)

    query = _strip_query_prefix(str(case.get("query", "")))
    start = time.perf_counter()
    results = query_decisions(project=project, query_text=query, top_k=5, status_filter="all")
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    expected_new = str(expected.get("expected_active_value", ""))
    new_rank = _rank_of_value(results, expected_new)
    checks.append(CheckItem("上下文失效_新结论可检索", new_rank is not None and new_rank <= 3, f"rank={new_rank}"))

    forbidden = [str(x) for x in _to_list(expected.get("forbidden_values"))]
    old_leak = _forbidden_leaked(results, forbidden, top_n=1)
    checks.append(CheckItem("上下文失效_旧值不在Top1", not old_leak, f"forbidden={forbidden}"))

    rows = _rows_for_project(project)
    has_supersede = any(str(r.get("superseded_by") or "").strip() for r in rows)
    checks.append(CheckItem("上下文失效_存在覆盖关系", has_supersede, f"rows={len(rows)}"))

    return {
        "checks": checks,
        "extra": {
            "latency_ms": latency_ms,
            "old_id": rec_old.get("id"),
            "new_id": rec_new.get("id"),
            "rows": len(rows),
        },
    }


def _run_case_contradiction_update(case: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[CheckItem] = []
    project = _project_for_case(case)
    expected = case.get("expected_output", {})

    old_event = str(case.get("old_event", ""))
    new_event = str(case.get("new_event", ""))
    rec_old = _store_event_as_decision(project, old_event)
    rec_new = _store_event_as_decision(project, new_event)

    query = _strip_query_prefix(str(case.get("query", "")))
    start = time.perf_counter()
    results = query_decisions(project=project, query_text=query, top_k=5, status_filter="all")
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    expected_new = str(expected.get("expected_active_value", ""))
    rank = _rank_of_value(results, expected_new)
    checks.append(CheckItem("矛盾更新_新值命中", rank is not None and rank <= 3, f"rank={rank}"))

    forbidden = [str(x) for x in _to_list(expected.get("forbidden_values"))]
    old_leak = _forbidden_leaked(results, forbidden, top_n=1)
    checks.append(CheckItem("矛盾更新_旧值不在Top1", not old_leak, f"forbidden={forbidden}"))

    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT superseded_by FROM decisions WHERE id=?", (rec_old.get("id"),))
    row = c.fetchone()
    conn.close()
    superseded_ok = bool(row and row[0] == rec_new.get("id"))
    checks.append(CheckItem("矛盾更新_版本链成立", superseded_ok, f"old={rec_old.get('id')} -> new={rec_new.get('id')}"))

    return {
        "checks": checks,
        "extra": {
            "latency_ms": latency_ms,
            "rank": rank,
            "old_leak": old_leak,
            "old_id": rec_old.get("id"),
            "new_id": rec_new.get("id"),
        },
    }


def _run_case_repeat_push(case: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[CheckItem] = []
    project = _project_for_case(case)
    expected = case.get("expected_output", {})
    for evt in _to_list(case.get("events")):
        _store_event_as_decision(project, _normalize_event_text(evt))

    trigger = str(case.get("trigger_message", ""))
    start = time.perf_counter()
    recalled = recall_relevant_decisions(trigger, project=project, top_k=3)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    expected_reminder = bool(expected.get("expected_reminder", True))
    expected_active_value = str(expected.get("expected_active_value", ""))
    hit = _rank_of_value(recalled, expected_active_value) is not None if expected_active_value else bool(recalled)

    if expected_reminder:
        checks.append(CheckItem("重复决策_应推送命中", hit, f"recalled={len(recalled)}"))
    else:
        checks.append(CheckItem("重复决策_不应推送", not hit, f"recalled={len(recalled)}"))

    return {"checks": checks, "extra": {"latency_ms": latency_ms, "recalled_count": len(recalled)}}


HANDLERS = {
    "decision_capture": _run_case_decision_capture,
    "anti_interference": _run_case_anti_interference,
    "decision_aggregation": _run_case_decision_aggregation,
    "context_invalidated": _run_case_context_invalidated,
    "contradiction_update": _run_case_contradiction_update,
    "decision_repeat_push": _run_case_repeat_push,
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_cases_array(data: Any) -> List[Dict[str, Any]]:
    """兼容纯数组，或带文档包装的 {\"cases\": [...] }。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        cases = data.get("cases")
        if isinstance(cases, list):
            return cases
    return []


def _load_cases_from_path(cases_path: Path) -> List[Dict[str, Any]]:
    """
    读取用例：
    - 传入文件：读取 JSON，支持根数组或 {\"cases\": [...] }
    - 传入目录：读取目录下所有 *_cases.json（按文件名排序）并合并
    """
    if cases_path.is_file():
        data = _load_json(cases_path)
        return _extract_cases_array(data)

    if not cases_path.exists() or not cases_path.is_dir():
        return []

    merged: List[Dict[str, Any]] = []
    for file_path in sorted(cases_path.glob("*_cases.json")):
        data = _load_json(file_path)
        merged.extend(_extract_cases_array(data))
    return merged


def _case_status(checks: List[CheckItem]) -> str:
    failed = [c for c in checks if not c.passed]
    if not checks:
        return "failed"
    if not failed:
        return "passed"
    if len(failed) < len(checks):
        return "partial"
    return "failed"


def _score(case_points: float, checks: List[CheckItem]) -> float:
    if not checks:
        return 0.0
    passed = sum(1 for c in checks if c.passed)
    return round(case_points * passed / len(checks), 2)


def _aggregate_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_cases = len(results)
    passed_cases = sum(1 for r in results if r.get("status") == "passed")
    partial_cases = sum(1 for r in results if r.get("status") == "partial")

    latencies = [r.get("extra", {}).get("latency_ms") for r in results if r.get("extra", {}).get("latency_ms") is not None]
    avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else None

    anti_cases = [r for r in results if r.get("case_type") == "anti_interference"]
    anti_r1 = 0
    anti_r3 = 0
    for r in anti_cases:
        rank = r.get("extra", {}).get("rank")
        if rank == 1:
            anti_r1 += 1
        if rank is not None and rank <= 3:
            anti_r3 += 1

    conflict_cases = [r for r in results if r.get("case_type") == "contradiction_update"]
    conflict_ok = 0
    stale_leak = 0
    for r in conflict_cases:
        checks = r.get("checks", [])
        by_name = {c["name"]: c["passed"] for c in checks}
        if by_name.get("矛盾更新_新值命中") and by_name.get("矛盾更新_版本链成立"):
            conflict_ok += 1
        if not by_name.get("矛盾更新_旧值不在Top1", True):
            stale_leak += 1

    repeat_cases = [r for r in results if r.get("case_type") == "decision_repeat_push"]
    repeat_hit = 0
    for r in repeat_cases:
        checks = r.get("checks", [])
        if checks and checks[0].get("passed"):
            repeat_hit += 1

    return {
        "case_count": total_cases,
        "case_pass_rate": round(passed_cases / total_cases, 4) if total_cases else 0.0,
        "case_partial_rate": round(partial_cases / total_cases, 4) if total_cases else 0.0,
        "avg_latency_ms": avg_latency,
        "anti_interference_recall_at_1": round(anti_r1 / len(anti_cases), 4) if anti_cases else None,
        "anti_interference_recall_at_3": round(anti_r3 / len(anti_cases), 4) if anti_cases else None,
        "contradiction_overwrite_success_rate": round(conflict_ok / len(conflict_cases), 4) if conflict_cases else None,
        "stale_leakage_rate": round(stale_leak / len(conflict_cases), 4) if conflict_cases else None,
        "repeat_push_hit_rate": round(repeat_hit / len(repeat_cases), 4) if repeat_cases else None,
    }


def _render_markdown(report: Dict[str, Any]) -> str:
    summary = report.get("summary", {})
    metrics = report.get("metrics", {})

    lines = []
    lines.append("# Benchmark Report（benchmark_sry）")
    lines.append("")
    lines.append(f"- 生成时间：{report.get('generated_at')}")
    lines.append(f"- 用例文件：`{report.get('cases_file')}`")
    lines.append("")
    lines.append("## 总体结果")
    lines.append("")
    lines.append(f"- 总用例数：{summary.get('total_cases')}")
    lines.append(f"- 通过率：{metrics.get('case_pass_rate')}")
    lines.append(f"- 部分通过率：{metrics.get('case_partial_rate')}")
    lines.append(f"- 平均耗时（ms）：{metrics.get('avg_latency_ms')}")
    lines.append("")
    lines.append("## 核心指标")
    lines.append("")
    lines.append(f"- 抗干扰 Recall@1：{metrics.get('anti_interference_recall_at_1')}")
    lines.append(f"- 抗干扰 Recall@3：{metrics.get('anti_interference_recall_at_3')}")
    lines.append(f"- 矛盾覆盖成功率：{metrics.get('contradiction_overwrite_success_rate')}")
    lines.append(f"- 旧值泄漏率：{metrics.get('stale_leakage_rate')}")
    lines.append(f"- 重复决策召回命中占比：{metrics.get('repeat_push_hit_rate')}")
    lines.append("")

    db_vis = report.get("db_change_preview", {})
    if db_vis:
        lines.append("## memory.db 变化可视化（清理前）")
        lines.append("")
        lines.append(f"- 采集窗口：{db_vis.get('before', {}).get('captured_at')} -> {db_vis.get('after', {}).get('captured_at')}")
        lines.append(f"- project 前缀：{db_vis.get('after', {}).get('benchmark_scope', {}).get('project_prefix')}")
        lines.append("")
        lines.append("- 表行数变化（after - before）：")
        table_delta = db_vis.get("delta", {}).get("table_counts_delta", {})
        for table_name in sorted(table_delta.keys()):
            lines.append(f"  - {table_name}: {table_delta.get(table_name)}")
        lines.append("")
        scope_after = db_vis.get("after", {}).get("benchmark_scope", {})
        lines.append(f"- 测试前缀决策总数（清理前）：{scope_after.get('decisions_count')}")
        lines.append("- 测试前缀状态分布（清理前）：")
        for k, v in scope_after.get("status_distribution", {}).items():
            lines.append(f"  - {k or '(empty)'}: {v}")
        lines.append("- 测试前缀项目 Top（清理前）：")
        for row in scope_after.get("top_projects", []):
            lines.append(f"  - {row.get('project')}: {row.get('count')}")
        lines.append("")

    lines.append("## 分用例结果")
    lines.append("")
    for row in report.get("results", []):
        lines.append(
            f"- `{row.get('case_id')}` | {row.get('case_type')} | status={row.get('status')} | "
            f"score={row.get('earned_points')}/{row.get('points')}"
        )
        for ck in row.get("checks", []):
            mark = "PASS" if ck.get("passed") else "FAIL"
            lines.append(f"  - [{mark}] {ck.get('name')}: {ck.get('detail')}")
    lines.append("")
    return "\n".join(lines)


def run_benchmark(cases_file: Path, tag: str = "") -> Dict[str, Any]:
    init_db()
    _ensure_governance_columns()
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

    cases = _load_cases_from_path(cases_file)

    _clean_benchmark_data()
    db_before = _db_snapshot(PROJECT_PREFIX)

    results = []
    total_points = 0.0
    earned_points = 0.0

    for case in cases:
        case_id = case.get("case_id", "unknown")
        case_type = case.get("case_type", "")
        points = float(case.get("points", 10))
        total_points += points
        start = time.perf_counter()

        handler = HANDLERS.get(case_type)
        if not handler:
            results.append(
                {
                    "case_id": case_id,
                    "case_type": case_type,
                    "pain_point": case.get("pain_point", ""),
                    "status": "skipped",
                    "points": points,
                    "earned_points": 0,
                    "checks": [],
                    "extra": {"reason": f"未实现的 case_type: {case_type}"},
                }
            )
            continue

        try:
            out = handler(case)
            checks = out.get("checks", [])
            status = _case_status(checks)
            score = _score(points, checks)
            earned_points += score
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            extra = dict(out.get("extra", {}))
            extra["runner_elapsed_ms"] = elapsed_ms

            results.append(
                {
                    "case_id": case_id,
                    "case_type": case_type,
                    "pain_point": case.get("pain_point", ""),
                    "status": status,
                    "points": points,
                    "earned_points": score,
                    "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks],
                    "extra": extra,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "case_id": case_id,
                    "case_type": case_type,
                    "pain_point": case.get("pain_point", ""),
                    "status": "error",
                    "points": points,
                    "earned_points": 0,
                    "checks": [],
                    "extra": {"error": str(exc)},
                }
            )

    metrics = _aggregate_metrics(results)
    db_after = _db_snapshot(PROJECT_PREFIX)
    db_delta = _db_snapshot_delta(db_before, db_after)
    report = {
        "status": "ok",
        "generated_at": now_iso(),
        "tag": tag or "",
        "cases_file": str(cases_file),
        "summary": {
            "total_cases": len(cases),
            "total_points": round(total_points, 2),
            "earned_points": round(earned_points, 2),
            "score_percent": round(earned_points / total_points * 100, 2) if total_points else 0.0,
        },
        "metrics": metrics,
        "db_change_preview": {
            "before": db_before,
            "after": db_after,
            "delta": db_delta,
            "note": "该区块在 benchmark 结束且清理前采集，用于可视化 memory.db 的测试写入变化。",
        },
        "results": results,
    }

    ts = _utc_now().strftime("%Y-%m-%d_%H-%M-%S")
    tag_suffix = f"_{tag}" if tag else ""
    json_path = OUTPUT_DIR / f"benchmark_sry_report_{ts}{tag_suffix}.json"
    md_path = OUTPUT_DIR / f"benchmark_sry_report_{ts}{tag_suffix}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    report["output"] = {"json": str(json_path), "markdown": str(md_path)}

    _clean_benchmark_data()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="benchmark_sry 独立 Runner")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="运行 benchmark 并导出报告")
    p_run.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="用例文件路径")
    p_run.add_argument("--tag", type=str, default="", help="报告标签")

    args = parser.parse_args()
    if args.command == "run":
        report = run_benchmark(args.cases, tag=args.tag)
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
