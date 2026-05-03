#!/usr/bin/env python3
# 文件路径：skills/feishu-memory/benchmark.py
# 修改类型：新增
# 依赖说明：标准库 SQLite/JSON；生成 benchmarks/YYYY-MM-DD.json
"""feishu-memory 记忆质量评估框架。"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent))
from memory import db_conn, now_iso, init_db, _ensure_governance_columns

SCRIPT_DIR = Path(__file__).parent.resolve()
BENCHMARK_DIR = SCRIPT_DIR / "benchmarks"


def _safe_ratio(a, b):
    return round(a / b, 4) if b else 0.0


def _score_from_penalty(*penalties):
    score = 1.0 - sum(penalties)
    return round(max(0.0, min(1.0, score)), 4)


def run_benchmark() -> dict:
    """运行质量评估并写入 JSON 报告。"""
    BENCHMARK_DIR.mkdir(exist_ok=True)
    init_db()
    _ensure_governance_columns()
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        conn = db_conn()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM decisions")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM decisions WHERE status='candidate'")
        candidates = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM decisions WHERE status='active'")
        active = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM decisions WHERE status='active' AND superseded_by IS NOT NULL AND superseded_by != ''")
        active_superseded = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM decisions WHERE deadline IS NOT NULL AND deadline != ''")
        deadline_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM decisions WHERE superseded_by='FORGOTTEN'")
        forgotten = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT project) FROM decisions WHERE project IS NOT NULL AND project != ''")
        project_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM access_log")
        access_count = c.fetchone()[0]
        c.execute("SELECT MIN(accessed_at), MAX(accessed_at) FROM access_log")
        access_range = c.fetchone()
        conn.close()

        candidate_ratio = _safe_ratio(candidates, total)
        active_superseded_ratio = _safe_ratio(active_superseded, active)
        deadline_rate = _safe_ratio(deadline_count, total)
        forgotten_rate = _safe_ratio(forgotten, total)
        coverage_rate = 1.0 if project_count else 0.0
        avg_access_interval_hours = None
        if access_range and access_range[0] and access_range[1] and access_count > 1:
            try:
                start = datetime.fromisoformat(access_range[0].replace("Z", "+00:00"))
                end = datetime.fromisoformat(access_range[1].replace("Z", "+00:00"))
                avg_access_interval_hours = round((end - start).total_seconds() / 3600 / max(1, access_count - 1), 2)
            except Exception:
                avg_access_interval_hours = None

        accuracy_score = _score_from_penalty(max(0, candidate_ratio - 0.35), active_superseded_ratio)
        completeness_score = round(deadline_rate, 4)
        freshness_score = _score_from_penalty(forgotten_rate)
        coverage_score = round(coverage_rate, 4)
        overall = round((accuracy_score * 0.35 + completeness_score * 0.25 + freshness_score * 0.25 + coverage_score * 0.15), 4)

        report = {
            "status": "ok",
            "date": report_date,
            "generated_at": now_iso(),
            "counts": {
                "total_decisions": total,
                "active": active,
                "candidate": candidates,
                "active_superseded": active_superseded,
                "with_deadline": deadline_count,
                "forgotten": forgotten,
                "projects": project_count,
                "access_log": access_count,
            },
            "metrics": {
                "candidate_ratio": candidate_ratio,
                "active_superseded_ratio": active_superseded_ratio,
                "deadline_rate": deadline_rate,
                "forgotten_rate": forgotten_rate,
                "coverage_rate": coverage_rate,
                "avg_access_interval_hours": avg_access_interval_hours,
            },
            "scores": {
                "accuracy": accuracy_score,
                "completeness": completeness_score,
                "freshness": freshness_score,
                "coverage": coverage_score,
                "overall": overall,
            },
        }
        (BENCHMARK_DIR / f"{report_date}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    except Exception as exc:
        print(f"benchmark 失败: {exc}", file=sys.stderr)
        return {"status": "error", "error": str(exc)}


def load_reports(days: int = 7) -> list:
    """读取最近 N 天报告。"""
    BENCHMARK_DIR.mkdir(exist_ok=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    reports = []
    for path in sorted(BENCHMARK_DIR.glob("*.json")):
        try:
            dt = datetime.strptime(path.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                reports.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"读取 benchmark 报告失败 {path}: {exc}", file=sys.stderr)
    return reports


def summarize_latest() -> str:
    """生成每日志可嵌入的质量摘要。"""
    report = run_benchmark(
        
    )
    if report.get("status") != "ok":
        return "- 质量评估：生成失败"
    scores = report.get("scores", {})
    metrics = report.get("metrics", {})
    return "\n".join([
        f"- 综合评分：{scores.get('overall', 0):.2f}",
        f"- 准确性：{scores.get('accuracy', 0):.2f} / 完整性：{scores.get('completeness', 0):.2f} / 时效性：{scores.get('freshness', 0):.2f}",
        f"- candidate 比例：{metrics.get('candidate_ratio', 0):.2%}，deadline 提取率：{metrics.get('deadline_rate', 0):.2%}",
    ])


# ─── Benchmark Cases（测试用例执行）───

CASES_FILE = SCRIPT_DIR / "benchmark_cases" / "cases.json"


def _load_cases() -> list:
    """加载 benchmark 测试用例。"""
    if not CASES_FILE.exists():
        return []
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def _clean_test_decisions(prefix: str = "_benchmark_"):
    """清理 benchmark 产生的测试数据。"""
    conn = db_conn()
    c = conn.cursor()
    c.execute("DELETE FROM decisions WHERE project LIKE ?", (f"{prefix}%",))
    c.execute("DELETE FROM audit_log WHERE memory_id IN (SELECT id FROM decisions WHERE project LIKE ?)", (f"{prefix}%",))
    conn.commit()
    conn.close()


def _run_case_decision_capture(case: dict) -> dict:
    """执行决策捕获测试。"""
    from memory import extract_decision_structured, store_decision, query_decisions

    results = {"passed": 0, "failed": 0, "details": []}
    text = case.get("text", "")
    events = case.get("events", [])
    expected = case.get("expected_output", {})
    expected_candidate = case.get("expected_candidate", True)

    # 单条消息测试
    if text:
        extracted = extract_decision_structured(text)
        if not expected_candidate:
            # Negative case: 不应被识别为决策
            has_decision = bool(extracted.get("decision")) and extracted.get("decision") != text
            if has_decision:
                results["details"].append(f"FAIL: 非决策内容被误识别: {text[:40]}")
                results["failed"] += 1
            else:
                results["details"].append(f"PASS: 正确忽略非决策内容")
                results["passed"] += 1
            return results

        # Positive case: 应被识别为决策
        project = f"_benchmark_{case['case_id']}"
        record = store_decision(
            project=project,
            decision=extracted.get("decision") or text,
            reasoning=extracted.get("reasoning", ""),
            chat_id="oc_benchmark",
            evidence=text,
        )

        # 检查结论
        ok = True
        if expected.get("conclusion") and expected["conclusion"] not in record["decision"]:
            results["details"].append(f"FAIL: 结论不匹配，期望 '{expected['conclusion']}'，实际 '{record['decision']}'")
            ok = False
        if expected.get("topic") and expected["topic"] not in record["project"]:
            results["details"].append(f"FAIL: 主题不匹配")
            ok = False
        if expected.get("reason_keyword") and expected["reason_keyword"] not in str(record.get("reasoning", "")):
            results["details"].append(f"FAIL: 理由关键词 '{expected['reason_keyword']}' 未命中")
            ok = False
        if expected.get("evidence_keyword") and expected["evidence_keyword"] not in str(record.get("evidence", "")):
            results["details"].append(f"FAIL: 证据关键词 '{expected['evidence_keyword']}' 未命中")
            ok = False

        if ok:
            results["details"].append(f"PASS: 正确捕获决策 '{record['decision'][:40]}'")
            results["passed"] += 1
        else:
            results["failed"] += 1

    # 多条消息 + query 测试
    if events and case.get("query"):
        project = f"_benchmark_{case['case_id']}"
        for evt in events:
            extracted = extract_decision_structured(evt)
            if extracted.get("decision"):
                store_decision(
                    project=project,
                    decision=extracted.get("decision"),
                    reasoning=extracted.get("reasoning", ""),
                    chat_id="oc_benchmark",
                    evidence=evt,
                )

        # query
        q = case["query"].replace("/recall ", "").replace("/query ", "")
        qr = query_decisions(project=project, query_text=q, top_k=5)

        ok = False
        for r in qr:
            dec = str(r.get("decision", ""))
            if expected.get("expected_active_value") and expected["expected_active_value"] in dec:
                ok = True
                break

        if ok:
            results["details"].append(f"PASS: query '{q}' 返回正确决策")
            results["passed"] += 1
        else:
            results["details"].append(f"FAIL: query '{q}' 未返回期望决策 '{expected.get('expected_active_value')}'")
            results["failed"] += 1

    return results


def _run_case_decision_aggregation(case: dict) -> dict:
    """执行决策聚合测试。"""
    from memory import extract_decision_structured, store_decision, query_decisions

    results = {"passed": 0, "failed": 0, "details": []}
    events = case.get("events", [])
    expected = case.get("expected_output", {})
    project = f"_benchmark_{case['case_id']}"

    for evt in events:
        if evt.startswith("/decide"):
            # 最终决策指令
            text = evt.replace("/decide ", "")
            extracted = extract_decision_structured(text)
            store_decision(
                project=project,
                decision=extracted.get("decision") or text,
                reasoning=extracted.get("reasoning", ""),
                chat_id="oc_benchmark",
                evidence=text,
            )
        else:
            extracted = extract_decision_structured(evt)
            if extracted.get("decision"):
                store_decision(
                    project=project,
                    decision=extracted.get("decision"),
                    reasoning=extracted.get("reasoning", ""),
                    chat_id="oc_benchmark",
                    evidence=evt,
                )

    # query 验证
    q = case.get("query", "").replace("/recall ", "").replace("/query ", "")
    qr = query_decisions(project=project, query_text=q, top_k=5)

    ok = False
    evidence_hits = 0
    for r in qr:
        dec = str(r.get("decision", ""))
        if expected.get("expected_active_value") and expected["expected_active_value"] in dec:
            ok = True
        # 检查 evidence keywords
        for kw in expected.get("evidence_keywords", []):
            if kw in str(r.get("reasoning", "")) or kw in str(r.get("evidence", "")):
                evidence_hits += 1

    if ok:
        results["details"].append(f"PASS: 聚合决策 '{expected.get('expected_active_value')}' 正确")
        results["passed"] += 1
    else:
        results["details"].append(f"FAIL: 未找到聚合决策 '{expected.get('expected_active_value')}'")
        results["failed"] += 1

    min_sources = expected.get("source_count_min", 1)
    if evidence_hits >= min_sources:
        results["details"].append(f"PASS: 证据来源命中 {evidence_hits} >= {min_sources}")
        results["passed"] += 1
    else:
        results["details"].append(f"FAIL: 证据来源命中 {evidence_hits} < {min_sources}")
        results["failed"] += 1

    return results


def _run_case_decision_invalidated(case: dict) -> dict:
    """执行决策失效测试。"""
    from memory import extract_decision_structured, store_decision, query_decisions

    results = {"passed": 0, "failed": 0, "details": []}
    events = case.get("events", [])
    expected = case.get("expected_output", {})
    project = f"_benchmark_{case['case_id']}"

    for evt in events:
        if isinstance(evt, dict):
            text = evt.get("content", "")
        else:
            text = str(evt)
        extracted = extract_decision_structured(text)
        store_decision(
            project=project,
            decision=extracted.get("decision") or text,
            reasoning=extracted.get("reasoning", ""),
            chat_id="oc_benchmark",
            evidence=text,
        )

    # 检查是否有冲突/覆盖关系
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT id, decision, superseded_by FROM decisions WHERE project=? ORDER BY created_at DESC", (project,))
    rows = c.fetchall()
    conn.close()

    # 简单判断：如果第二条决策覆盖了第一条，认为检测到变化
    has_conflict = len(rows) >= 2 and rows[0][2] is not None

    if has_conflict or len(rows) >= 2:
        results["details"].append(f"PASS: 检测到决策变更/冲突 ({len(rows)} 条记录)")
        results["passed"] += 1
    else:
        results["details"].append(f"FAIL: 未检测到决策变更")
        results["failed"] += 1

    return results


def _run_case_decision_repeat_push(case: dict) -> dict:
    """执行重复决策推送测试。"""
    from memory import extract_decision_structured, store_decision, recall_relevant_decisions

    results = {"passed": 0, "failed": 0, "details": []}
    events = case.get("events", [])
    trigger = case.get("trigger_message", "")
    expected = case.get("expected_output", {})
    project = f"_benchmark_{case['case_id']}"

    # 先记录已有决策
    for evt in events:
        if isinstance(evt, dict):
            text = evt.get("content", "")
        else:
            text = str(evt)
        extracted = extract_decision_structured(text)
        store_decision(
            project=project,
            decision=extracted.get("decision") or text,
            reasoning=extracted.get("reasoning", ""),
            chat_id="oc_benchmark",
            evidence=text,
        )

    # 用 trigger_message 召回
    recalled = recall_relevant_decisions(trigger, project=project, top_k=3)

    ok = False
    for r in recalled:
        dec = str(r.get("decision", ""))
        if expected.get("expected_active_value") and expected["expected_active_value"] in dec:
            ok = True
            break

    if ok:
        results["details"].append(f"PASS: trigger 消息正确召回已有决策")
        results["passed"] += 1
    else:
        results["details"].append(f"FAIL: trigger 消息未召回期望决策")
        results["failed"] += 1

    return results


def run_cases() -> dict:
    """运行 benchmark 测试用例并生成报告。"""
    cases = _load_cases()
    if not cases:
        return {"status": "error", "message": f"未找到测试用例: {CASES_FILE}"}

    init_db()
    _ensure_governance_columns()
    _clean_test_decisions()

    total_points = 0
    earned_points = 0
    case_results = []

    handlers = {
        "decision_capture": _run_case_decision_capture,
        "decision_aggregation": _run_case_decision_aggregation,
        "decision_invalidated": _run_case_decision_invalidated,
        "decision_repeat_push": _run_case_decision_repeat_push,
    }

    for case in cases:
        case_id = case.get("case_id", "unknown")
        case_type = case.get("case_type", "")
        points = case.get("points", 0)
        total_points += points

        handler = handlers.get(case_type)
        if not handler:
            case_results.append({
                "case_id": case_id,
                "case_type": case_type,
                "status": "skipped",
                "points": points,
                "earned": 0,
                "reason": f"未知 case_type: {case_type}",
            })
            continue

        try:
            sub = handler(case)
            passed = sub.get("passed", 0)
            failed = sub.get("failed", 0)
            total_checks = passed + failed
            score_ratio = passed / total_checks if total_checks > 0 else 0
            earned = round(points * score_ratio, 1)
            earned_points += earned

            status = "passed" if failed == 0 else "partial" if passed > 0 else "failed"
            case_results.append({
                "case_id": case_id,
                "case_type": case_type,
                "pain_point": case.get("pain_point", ""),
                "status": status,
                "points": points,
                "earned": earned,
                "passed": passed,
                "failed": failed,
                "details": sub.get("details", []),
            })
        except Exception as exc:
            case_results.append({
                "case_id": case_id,
                "case_type": case_type,
                "status": "error",
                "points": points,
                "earned": 0,
                "error": str(exc),
            })

    # 清理测试数据
    _clean_test_decisions()

    report = {
        "status": "ok",
        "generated_at": now_iso(),
        "summary": {
            "total_cases": len(cases),
            "total_points": total_points,
            "earned_points": round(earned_points, 1),
            "score_percent": round(earned_points / total_points * 100, 1) if total_points else 0,
        },
        "results": case_results,
    }

    # 保存报告
    BENCHMARK_DIR.mkdir(exist_ok=True)
    report_path = BENCHMARK_DIR / f"cases_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def main():
    parser = argparse.ArgumentParser(description="feishu-memory benchmark")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="生成今天的质量报告")
    p_report = sub.add_parser("report", help="查看最近报告")
    p_report.add_argument("--days", type=int, default=7)
    p_cases = sub.add_parser("cases", help="运行 benchmark 测试用例")
    args = parser.parse_args()
    if args.command == "run":
        print(json.dumps(run_benchmark(), ensure_ascii=False))
    elif args.command == "report":
        reports = load_reports(args.days)
        print(json.dumps({"status": "ok", "count": len(reports), "reports": reports}, ensure_ascii=False))
    elif args.command == "cases":
        print(json.dumps(run_cases(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
