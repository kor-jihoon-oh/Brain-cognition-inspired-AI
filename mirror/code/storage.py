"""Write atomic artifacts and aggregate attempts into round and problem reports."""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
import math
import os
from pathlib import Path
import re
import uuid

try:
    from .serialization import UNDEFINED, js_dumps, json_loads, js_truthy, js_string
    from .optics import boardForRound
except ImportError:
    from serialization import UNDEFINED, js_dumps, json_loads, js_truthy, js_string
    from optics import boardForRound


def _get(value, key):
    return value.get(key, UNDEFINED) if isinstance(value, dict) else UNDEFINED


def _path(value, *keys):
    for key in keys:
        value = _get(value, key)
    return value


def _nullish(value, fallback):
    return fallback if value is None or value is UNDEFINED else value


def _stringify(value):
    return UNDEFINED if value is UNDEFINED else js_dumps(value)


def _to_fixed(value, digits):
    """Round a binary64 value to decimal places, with ties away from zero."""
    number = float(value)
    if not math.isfinite(number) or abs(number) >= 1e21:
        return js_string(number)
    negative = number < 0
    with localcontext() as context:
        context.prec = 1100
        rounded = Decimal.from_float(abs(number)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
        return ("-" if negative else "") + format(rounded, f".{digits}f")


def _date_parse_ms(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    except (TypeError, ValueError, OverflowError):
        return float("nan")


def readJSON(file):
    return json_loads(Path(file).read_text(encoding="utf-8").removeprefix("\ufeff"))


def atomicWrite(file, text):
    file = os.fspath(file)
    Path(file).parent.mkdir(parents=True, exist_ok=True)
    temporary = file + "." + str(uuid.uuid4()) + ".tmp"
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    # A binary descriptor keeps saved bytes independent of platform newline rules.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o666)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, file)


def writeJSON(file, x):
    atomicWrite(file, js_dumps(x, indent=2) + "\n")


def readCalls(folder):
    directory = Path(folder) / "calls"
    return [readJSON(directory / name) for name in sorted(os.listdir(directory)) if re.fullmatch(r"[0-9]{5}\.json", name)] if directory.exists() else []


def _sum(rows, f):
    total = 0.0
    for row in rows:
        total = total + _nullish(f(row), 0)
    return total


def _mean(values):
    return _sum(values, lambda value: value) / len(values) if values else None


_exportCache = {}


def _group(rows, key):
    out = {}
    for row in rows:
        out.setdefault(key(row), []).append(row)
    return out


def _roundKey(x):
    return f"{js_string(x['problem'])}:{js_string(x['round'])}"


def _appendDurably(file, text):
    if not text:
        return
    descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o666)
    try:
        view = memoryview(text.encode("utf-8"))
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def resources(calls):
    unknownCost = sum(_path(c, "cost", "estimated_cost_usd") in (None, UNDEFINED) for c in calls)
    unknownTime = sum(_get(c, "api_seconds") in (None, UNDEFINED) for c in calls)
    knownCost = _sum(calls, lambda c: _path(c, "cost", "estimated_cost_usd"))
    knownTime = _sum(calls, lambda c: _get(c, "api_seconds"))
    starts = sorted(_get(c, "started_at") for c in calls if js_truthy(_get(c, "started_at")))
    ends = sorted(_get(c, "finished_at") for c in calls if js_truthy(_get(c, "finished_at")))
    first, last = (starts[0] if starts else None), (ends[-1] if ends else None)

    def total_tokens(c):
        usage = _path(c, "response", "usage")
        total = _get(usage, "total_tokens")
        if total is not None and total is not UNDEFINED:
            return total
        if js_truthy(usage):
            a, b = _get(usage, "input_tokens"), _get(usage, "output_tokens")
            return float("nan") if a is UNDEFINED or b is UNDEFINED else (0 if a is None else a) + (0 if b is None else b)
        return None

    return {
        "api_calls": len(calls), "api_seconds": None if unknownTime else knownTime,
        "known_api_seconds": knownTime, "unknown_time_calls": unknownTime,
        "first_request_at": first, "last_response_at": last,
        "wall_elapsed_seconds_including_pauses": (_date_parse_ms(last) - _date_parse_ms(first)) / 1000 if js_truthy(first) and js_truthy(last) and all(js_truthy(_get(c, "finished_at")) for c in calls) else None,
        "estimated_cost_usd": None if unknownCost else knownCost,
        "known_cost_subtotal_usd": knownCost, "unpriced_calls": unknownCost,
        "known_input_tokens": _sum(calls, lambda c: _path(c, "response", "usage", "input_tokens")),
        "known_output_tokens": _sum(calls, lambda c: _path(c, "response", "usage", "output_tokens")),
        "known_cached_input_tokens": _sum(calls, lambda c: _path(c, "response", "usage", "input_tokens_details", "cached_tokens")),
        "known_cache_write_tokens": _sum(calls, lambda c: _path(c, "response", "usage", "input_tokens_details", "cache_write_tokens")),
        "known_total_tokens": _sum(calls, total_tokens),
        "calls_without_usage": sum(not js_truthy(_path(c, "response", "usage")) for c in calls),
        "known_reasoning_tokens": _sum(calls, lambda c: _path(c, "response", "usage", "output_tokens_details", "reasoning_tokens")),
    }


def _table(folder, name, rows):
    if not rows:
        return
    keys = list(dict.fromkeys(key for row in rows for key in row))

    def escape(x):
        return '"' + js_string(_nullish(x, "")).replace('"', '""') + '"'

    text = "\ufeff" + "\r\n".join(",".join(escape(cell) for cell in row) for row in [keys] + [[r.get(k, UNDEFINED) for k in keys] for r in rows]) + "\r\n"
    atomicWrite(Path(folder) / (name + ".csv"), text)


def exportReports(folder, s, calls, config):
    folder = os.fspath(folder)
    target = Path(folder)
    cached = _exportCache.get(folder)
    incremental = cached is not None and cached["state"] is s and cached["attempts"] <= len(s["attempts"]) and (target / "attempts.jsonl").exists()
    previousCount = cached["attempts"] if incremental else 0
    attemptsByRound = _group(s["attempts"], _roundKey)
    attemptsByProblem = _group(s["attempts"], lambda x: x["problem"])
    callsByRound = _group(calls, _roundKey)
    callsByProblem = _group(calls, lambda x: x["problem"])
    endedByRound = {_roundKey(x): x for x in s["rounds"]}
    skippedByRound = {_roundKey(x): x for x in s["skippedRounds"]}
    rounds = []
    for p in s["dataset"]["problems"]:
        for r in range(1, config["rounds"] + 1):
            key = f"{js_string(p['problem_number'])}:{r}"
            aa, rr = attemptsByRound.get(key, []), endedByRound.get(key, UNDEFINED)
            skipped = skippedByRound.get(key, UNDEFINED)
            cc = callsByRound.get(key, [])
            res = resources(cc)
            triangles = sum(js_truthy(_get(a, "previous_rule_error")) for a in aa)
            has_round, is_skipped = js_truthy(rr), js_truthy(skipped)
            eligible = has_round and js_truthy(_get(rr, "previous_round_had_success"))
            correct = sum(js_truthy(_get(a, "correct")) for a in aa)
            board = boardForRound(p, r - 1)
            rounds.append({
                "problem": p["problem_number"], "round": r,
                "source_position": js_dumps(board["source"]["position"]),
                "filter_positions": js_dumps(board["filters"]), "sensor_positions": js_dumps(board["sensors"]),
                "status": "skipped" if is_skipped else ("success" if js_truthy(_get(rr, "reached_k")) else "failed") if has_round else "in_progress" if cc else "not_started",
                "skipped": is_skipped, "skip_reason": _nullish(_get(skipped, "reason"), None), "blocked_by_round": _nullish(_get(skipped, "blocked_by_round"), None),
                "attempts": len(aa), "correct_count": None if is_skipped else correct,
                "previous_rule_error_count": None if is_skipped else triangles,
                "incorrect_count": None if is_skipped else sum(not js_truthy(_get(a, "correct")) for a in aa),
                "ordinary_error_count": None if is_skipped else sum(not js_truthy(_get(a, "correct")) and not js_truthy(_get(a, "previous_rule_error")) for a in aa),
                "symbols": "".join("O" if js_truthy(_get(a, "correct")) else "△" if js_truthy(_get(a, "previous_rule_error")) else "X" for a in aa),
                "accuracy_percent": 100 * correct / len(aa) if has_round and aa else float("nan") if has_round else None,
                "per_eligible": False if is_skipped else _nullish(_get(rr, "previous_round_had_success"), _nullish(_get(aa[0] if aa else UNDEFINED, "per_eligible"), None)),
                "per_numerator": triangles if eligible else None, "per_denominator": len(aa) if eligible else None,
                "per_percent": 100 * triangles / len(aa) if eligible and aa else float("nan") if eligible else None,
                "reached_k": None if is_skipped else _nullish(_get(rr, "reached_k"), False),
                "had_full_success": None if is_skipped else any(js_truthy(_get(a, "correct")) for a in aa),
                "end_reason": _nullish(_get(rr, "end_reason"), None),
                **res, "successful_round_api_seconds": res["api_seconds"] if js_truthy(_get(rr, "reached_k")) else None,
            })
    roundsByProblem = _group(rounds, lambda x: x["problem"])
    problems = []
    for p in s["dataset"]["problems"]:
        rr, cc = roundsByProblem.get(p["problem_number"], []), callsByProblem.get(p["problem_number"], [])
        completed = sum(r["status"] in ("success", "failed") for r in rr)
        skipped = sum(r["status"] == "skipped" for r in rr)
        problems.append({
            "problem": p["problem_number"], "problem_id": p["problem_id"],
            "status": "completed" if completed + skipped == config["rounds"] else "in_progress" if cc else "not_started",
            "skipped_rounds": skipped, "stopped_after_round": next((r["round"] for r in rr if r["status"] == "failed" and r["correct_count"] == 0), None),
            "completed_rounds": completed, "successful_rounds": sum(r["status"] == "success" for r in rr), "failed_rounds": sum(r["status"] == "failed" for r in rr),
            "first_round_accuracy_percent": rr[0]["accuracy_percent"], "per_eligible_rounds": sum(r["per_percent"] is not None for r in rr),
            "per_percent": _mean([r["per_percent"] for r in rr if r["per_percent"] is not None]),
            "attempts": _sum(rr, lambda r: r["attempts"]), "correct_count": _sum(rr, lambda r: r["correct_count"]), "incorrect_count": _sum(rr, lambda r: r["incorrect_count"]),
            "previous_rule_error_count": _sum(rr, lambda r: r["previous_rule_error_count"]), "ordinary_error_count": _sum(rr, lambda r: r["ordinary_error_count"]), **resources(cc),
        })
    defined = [p for p in problems if p["status"] == "completed" and p["per_percent"] is not None]
    completedIds = {p["problem"] for p in problems if p["status"] == "completed"}
    roundAverages = []
    for i in range(config["rounds"]):
        rr = [r for r in rounds if r["round"] == i + 1 and r["problem"] in completedIds and not r["skipped"]]
        column = "accuracy_percent" if i == 0 else "per_percent"
        values = [r[column] for r in rr if r[column] is not None]
        roundAverages.append({"round": i + 1, "metric": "R1 Accuracy" if i == 0 else "PER", "included_problems": len(values), "mean_percent": _mean(values)})
    summary = {
        **{k: _get(config, k) for k in ("protocol", "model", "reasoning_effort", "k", "T", "source_dataset_problems", "selection")},
        "status": "completed" if s["done"] else "in_progress", "planned_problems": len(problems), "completed_problems": sum(p["status"] == "completed" for p in problems),
        "completed_rounds": len(s["rounds"]), "skipped_rounds": len(s["skippedRounds"]), "successful_rounds": sum(js_truthy(_get(r, "reached_k")) for r in s["rounds"]),
        "failed_rounds": sum(not js_truthy(_get(r, "reached_k")) for r in s["rounds"]), "attempts": len(s["attempts"]), **resources(calls),
        "correct_count": sum(js_truthy(_get(a, "correct")) for a in s["attempts"]), "incorrect_count": sum(not js_truthy(_get(a, "correct")) for a in s["attempts"]),
        "previous_rule_error_count": sum(js_truthy(_get(a, "previous_rule_error")) for a in s["attempts"]),
        "ordinary_error_count": sum(not js_truthy(_get(a, "correct")) and not js_truthy(_get(a, "previous_rule_error")) for a in s["attempts"]),
        "mean_completed_problem_api_seconds": _mean([p["api_seconds"] for p in problems if p["status"] == "completed" and p["api_seconds"] is not None]),
        "mean_completed_problem_estimated_cost_usd": _mean([p["estimated_cost_usd"] for p in problems if p["status"] == "completed" and p["estimated_cost_usd"] is not None]),
        "completed_problems_with_unpriced_calls": sum(p["status"] == "completed" and p["unpriced_calls"] > 0 for p in problems),
        "successful_round_mean_api_seconds": _mean([r["api_seconds"] for r in rounds if js_truthy(r["reached_k"]) and r["api_seconds"] is not None]),
        "per_included_completed_problems": len(defined), "per_percent": _mean([p["per_percent"] for p in defined]),
        "first_round_accuracy_percent": _mean([p["first_round_accuracy_percent"] for p in problems if p["first_round_accuracy_percent"] is not None]),
        **{k: _get(config, k) for k in ("per_definition", "per_reference_board", "source_policy", "board_policy")},
        "round_averages_completed_problems": roundAverages,
        "per_aggregation": "Mean of eligible completed round PERs per problem, then mean of defined completed problem PERs. R1 and skipped rounds excluded.",
        **{k: _get(config, k) for k in ("feedback", "model_input_language", "board_delivery", "sensor_feedback_definition", "feedback_received_colors", "feedback_rule_violation_reason", "after_failed_round")},
        "units": {"time": "seconds", "cost": "USD", "tokens": "API reported token counts"},
        "cost_note": "API usage 및 pricing-snapshot.json 단가에 따른 추정액. 청구서 금액이 아님. 미확인 비용은 0원으로 취급하지 않음",
        "history_note": "모든 시도에 현재 전체 배치를 영어 안내문과 함께 제공. 문제 안에서 모든 응답 항목과 피드백 유지, 문제 사이에는 초기화. 라운드/시도 번호 및 숨겨진 정보는 전송하지 않음",
        "time_note": "api_seconds는 API 요청 소요시간 합계(중단/실패 요청 시간 포함, 응답 대기 외의 수동 중단/파일 저장시간 제외). wall_elapsed_seconds_including_pauses는 첫 요청~마지막 응답의 경과시간(재개 전 대기 포함). 시간 단위는 초. 성공 라운드는 reached_k=true로 식별",
    }
    writeJSON(target / "summary.json", summary)
    writeJSON(target / "rounds.json", rounds)
    writeJSON(target / "problems.json", problems)
    newAttempts = s["attempts"][previousCount:]
    jsonl = "\n".join(js_dumps(a) for a in newAttempts) + ("\n" if newAttempts else "")
    if incremental:
        _appendDurably(target / "attempts.jsonl", jsonl)
    else:
        atomicWrite(target / "attempts.jsonl", jsonl)
    attemptRows = []
    for a in s["attempts"]:
        row = {k: _get(a, k) for k in ("problem", "round", "attempt", "correct", "symbol", "experiment_submission", "problem_submission", "round_incorrect_so_far", "problem_incorrect_so_far", "started_at", "finished_at")}
        row.update({
            "S1_result": _path(a, "sensor_results", "S1"), "S2_result": _path(a, "sensor_results", "S2"), "valid": _get(a, "valid"), "validation_error": _get(a, "validation_error"),
            "current_source": _stringify(_get(a, "current_source")), "current_board": _stringify(_get(a, "current_board")),
            **{k: _get(a, k) for k in ("previous_full_success", "previous_rule_accepts", "previous_rule_error", "per_eligible")},
            "current_received_colors": js_dumps(_nullish(_path(a, "optics", "received_colors"), None)),
            "previous_received_colors": js_dumps(_nullish(_path(a, "previous_optics", "received_colors"), None)),
            "current_constraints": _stringify(_get(a, "current_constraints")), "previous_constraints": _stringify(_get(a, "previous_constraints")),
            "rule_satisfied": _path(a, "current_evaluation", "rule_satisfied"), "feedback_received_colors": _stringify(_get(a, "feedback_received_colors")),
            "current_target": _stringify(_get(a, "current_target")), "previous_target": _stringify(_get(a, "previous_target")),
            **{k: _get(a, k) for k in ("feedback", "next_model_input", "consecutive_correct", "round_end_reason", "problem_ended", "problem_end_reason")},
            "layout": _stringify(_get(a, "layout")), "raw_output": _get(a, "raw_output"), "api_seconds": _get(a, "api_seconds"), "estimated_cost_usd": _path(a, "cost", "estimated_cost_usd"),
            "input_tokens": _path(a, "usage", "input_tokens"), "output_tokens": _path(a, "usage", "output_tokens"), "reasoning_tokens": _path(a, "usage", "output_tokens_details", "reasoning_tokens"),
            "cached_input_tokens": _path(a, "usage", "input_tokens_details", "cached_tokens"), "cache_write_tokens": _path(a, "usage", "input_tokens_details", "cache_write_tokens"),
            **{k: _get(a, k) for k in ("call_id", "response_id", "served_model")},
        })
        attemptRows.append(row)
    _table(target, "attempts", attemptRows)
    _table(target, "rounds", rounds)
    _table(target, "problems", problems)

    def pct(v):
        return "—" if v is None else _to_fixed(v, 2) + "%"

    def escape(value):
        return js_string(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    resultRows = []
    for p in problems:
        cells = []
        for r in (r for r in rounds if r["problem"] == p["problem"]):
            if r["skipped"] or r["status"] == "not_started":
                cells.append('<td class="skipped">' + ("생략" if r["skipped"] else "미실행") + "</td>")
                continue
            metric = ("Accuracy " + pct(r["accuracy_percent"]) + f" ({js_string(r['correct_count'])}/{js_string(r['attempts'])})") if r["round"] == 1 else "PER " + pct(r["per_percent"]) + (f" ({js_string(r['per_numerator'])}/{js_string(r['per_denominator'])})" if js_truthy(r["per_denominator"]) else "")
            cells.append("<td>" + escape(r["status"]) + f"<br>○ {js_string(r['correct_count'])} / △ {js_string(r['previous_rule_error_count'])} / × {js_string(r['ordinary_error_count'])}<br>" + metric + "</td>")
        resultRows.append("<tr><th>" + js_string(p["problem"]) + "</th>" + "".join(cells) + "<td>" + pct(p["per_percent"]) + (" (진행 중)" if p["status"] == "in_progress" else "") + "</td></tr>")
    html = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>실험 결과</title><style>body{font-family:Malgun Gothic,sans-serif;margin:24px;color:#17232b}table{border-collapse:collapse}th,td{padding:12px;border:1px solid #ccd4dc;line-height:1.7}.skipped{background:#eee;color:#666}.scroll{overflow:auto}p{line-height:1.7}</style><h1>거울·필터 실험 결과</h1><p>완료 문제 평균 PER: ' + pct(summary["per_percent"]) + ' · 계산 가능 완료 문제 ' + str(len(defined)) + '개<br>API 시간 합계: ' + (_to_fixed(summary["api_seconds"], 2) if summary["api_seconds"] is not None else '미확인') + '초 · 추정 비용: ' + ('미확인' if summary["estimated_cost_usd"] is None else _to_fixed(summary["estimated_cost_usd"], 6) + ' USD') + '</p><div class="scroll"><table><thead><tr><th>문제</th>' + ''.join('<th>R' + str(i + 1) + '</th>' for i in range(config["rounds"])) + '<th>문제 PER</th></tr></thead><tbody>' + ''.join(resultRows) + '<tr><th>완료 문제 평균</th>' + ''.join('<td>' + r["metric"] + ' ' + pct(r["mean_percent"]) + '<br>n=' + str(r["included_problems"]) + '</td>' for r in roundAverages) + '<td>' + pct(summary["per_percent"]) + '</td></tr></tbody></table></div><p>○ 현재 전체 성공 · △ 현재 유효한 오답이며 직전 규칙 만족 (이전 목표 색 무관, 현재 광원·필터·센서 배치 기준) · × 나머지 오답<br>PER은 실제 판정 시도를 분모로 계산합니다. 미실행·생략·계산 불가는 0%로 취급하지 않습니다. 진행 중 문제는 완료 문제 평균에서 제외합니다.</p></html>'
    atomicWrite(target / "RESULTS.html", html)
    callRows = []
    for c in calls:
        row = {k: _get(c, k) for k in ("call_id", "problem", "round", "attempt", "status", "started_at", "finished_at", "api_seconds", "http_status")}
        row.update({"response_id": _path(c, "response", "id"), "request_id": _get(c, "request_id"), "served_model": _path(c, "response", "model")})
        cost = _get(c, "cost")
        if isinstance(cost, dict):
            row.update(cost)
        callRows.append(row)
    _table(target, "api-usage-cost", callRows)
    for p in s["dataset"]["problems"]:
        if incremental and not any(a["problem"] == p["problem_number"] for a in newAttempts):
            continue
        aa = attemptsByProblem.get(p["problem_number"], [])
        if not aa:
            continue
        pc = callsByProblem.get(p["problem_number"], [])
        parts = [f"문제 {js_string(p['problem_number'])} | 최초 모델 입력", _nullish(_get(pc[0] if pc else UNDEFINED, "new_input"), "")]
        for a in aa:
            seconds, cost = _get(a, "api_seconds"), _path(a, "cost", "estimated_cost_usd")
            timing = "?" if seconds in (None, UNDEFINED) else _to_fixed(seconds, 3)
            price = "미확인" if cost in (None, UNDEFINED) else _to_fixed(cost, 8)
            parts.append("\n" + "=" * 55 + f"\n라운드 {js_string(a['round'])} | 시도 {js_string(a['attempt'])}\n모델 응답\n{js_string(_get(a, 'raw_output'))}\n모델에게 보낸 피드백\n{js_string(_nullish(_get(a, 'next_model_input'), _get(a, 'feedback')))}\nAPI {timing}초 | 추정 비용 ${price}")
        atomicWrite(target / "transcripts" / (p["problem_id"] + ".txt"), "\n".join(parts) + "\n")
    writeJSON(target / "progress.json", {
        "done": s["done"], "next_problem": None if s["done"] else s["dataset"]["problems"][s["problemIndex"]]["problem_number"],
        "next_round": None if s["done"] else s["roundIndex"] + 1, "next_attempt": None if s["done"] else s["attempt"], "consecutive_correct": s["streak"],
        "saved_api_calls": len(calls), "saved_attempts": len(s["attempts"]), "updated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    })
    _exportCache[folder] = {"state": s, "attempts": len(s["attempts"])}
    return summary
