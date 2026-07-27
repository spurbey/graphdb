def capability_label(cap: str) -> str:
    labels = {
        "semantic_search": "Semantic Search",
        "explain_coupling": "Co-Change Explanation",
        "commit_review": "Commit Impact Review",
    }
    return labels.get(cap, cap)


def generate(records: list[dict]) -> str:
    parts = []
    parts.append("=" * 72)
    parts.append("EVALUATION REPORT — MCP Tools vs Baseline")
    parts.append("=" * 72)
    parts.append("")

    for rec in records:
        parts.append(_format_task(rec))
        parts.append("")

    parts.append(_format_aggregate(records))
    parts.append("")
    parts.append("=" * 72)
    parts.append("END REPORT")
    parts.append("=" * 72)

    return "\n".join(parts)


def _format_task(rec: dict) -> str:
    tid = rec["task_id"]
    cap = rec.get("capability", "?")
    b = rec["baseline"]
    t = rec["treatment"]

    lines = []
    lines.append(f"-- {tid} ({capability_label(cap)}) --")
    lines.append("")

    # Step details
    if b.get("steps"):
        lines.append("  Baseline steps:")
        for s in b["steps"]:
            err = f" ERROR: {s.get('error', '')}" if s.get("error") else ""
            lines.append(f"    {s['step']:30s} {s['latency_s']:>6.2f}s  "
                         f"R:{s['tokens_read']:>6d}  W:{s['tokens_written']:>6d}  {s.get('result_summary','')}{err}")
        lines.append("")

    if t.get("steps"):
        lines.append("  Treatment steps:")
        for s in t["steps"]:
            err = f" ERROR: {s.get('error', '')}" if s.get("error") else ""
            lines.append(f"    {s['tool']:30s} {s['latency_s']:>6.2f}s  "
                         f"S:{s['tokens_sent']:>6d}  R:{s['tokens_received']:>6d}  {s.get('result_summary','')}{err}")
        lines.append("")

    # Comparison table
    b_tokens_read = b.get("total_tokens_read", 0)
    b_tokens_written = b.get("total_tokens_written", 0)
    b_tokens_total = b_tokens_read + b_tokens_written
    b_calls = len(b.get("steps", []))

    t_tokens_sent = t.get("total_tokens_sent", 0)
    t_tokens_received = t.get("total_tokens_received", 0)
    t_tokens_total = t_tokens_sent + t_tokens_received
    t_calls = len(t.get("steps", []))

    b_time = b.get("total_time_s", 0)
    t_time = t.get("total_time_s", 0)

    b_correct = _fmt_bool(rec.get("baseline_correct", False))
    t_correct = _fmt_bool(rec.get("treatment_correct", False))

    lines.append("  Metric                        Baseline    Treatment   Delta")
    lines.append("  " + "-" * 58)
    lines.append(f"  Tool calls                    {b_calls:>6d}      {t_calls:>6d}      {_delta(b_calls, t_calls)}")
    lines.append(f"  Tokens consumed               {b_tokens_total:>6d}      {t_tokens_total:>6d}      {_delta(b_tokens_total, t_tokens_total)}")
    lines.append(f"  Latency                       {b_time:>6.2f}s    {t_time:>6.2f}s    {_delta(b_time, t_time)}")
    lines.append(f"  Correct                       {b_correct:>6s}      {t_correct:>6s}      {'—' if b_correct == t_correct else ('[OK]' if t_correct == 'YES' else '[FAIL]')}")

    return "\n".join(lines)


def _format_aggregate(records: list[dict]) -> str:
    if not records:
        return "No records."

    n = len(records)
    total_b_calls = 0
    total_b_tokens = 0
    total_b_time = 0.0
    total_t_calls = 0
    total_t_tokens = 0
    total_t_time = 0.0
    correct_b = 0
    correct_t = 0

    for rec in records:
        b = rec["baseline"]
        t = rec["treatment"]
        total_b_calls += len(b.get("steps", []))
        total_b_tokens += b.get("total_tokens_read", 0) + b.get("total_tokens_written", 0)
        total_b_time += b.get("total_time_s", 0)
        total_t_calls += len(t.get("steps", []))
        total_t_tokens += t.get("total_tokens_sent", 0) + t.get("total_tokens_received", 0)
        total_t_time += t.get("total_time_s", 0)
        if rec.get("baseline_correct"):
            correct_b += 1
        if rec.get("treatment_correct"):
            correct_t += 1

    lines = []
    lines.append("-- AGGREGATE --")
    lines.append(f"  Tasks: {n}")
    lines.append("")
    lines.append("  Metric                        Baseline    Treatment   Delta")
    lines.append("  " + "-" * 58)
    lines.append(f"  Total tool calls              {total_b_calls:>6d}      {total_t_calls:>6d}      {_delta(total_b_calls, total_t_calls)}")
    lines.append(f"  Total tokens consumed         {total_b_tokens:>6d}      {total_t_tokens:>6d}      {_delta(total_b_tokens, total_t_tokens)}")
    lines.append(f"  Total latency                 {total_b_time:>6.2f}s    {total_t_time:>6.2f}s    {_delta(total_b_time, total_t_time)}")
    lines.append(f"  Correct answers               {correct_b}/{n}         {correct_t}/{n}         {'[OK]' if correct_t > correct_b else ('=' if correct_t == correct_b else '[FAIL]')}")

    return "\n".join(lines)


def _delta(a, b) -> str:
    if a == 0:
        return "—"
    pct = ((b - a) / a) * 100
    if pct < 0:
        return f"-{abs(pct):.0f}%"
    elif pct > 0:
        return f"+{pct:.0f}%"
    return "0%"


def _fmt_bool(v: bool) -> str:
    return "YES" if v else "NO"


def write_to_file(report: str, path: str = "eval/report/latest_report.txt"):
    from pathlib import Path
    Path(path).write_text(report, encoding="utf-8")
    print(f"Report written to {path}")
