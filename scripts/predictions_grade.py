#!/usr/bin/env python3
"""Grade every matured claim and rewrite the PREDICTIONS.md scoreboard.

    python scripts/predictions_grade.py

Run daily by the workflow after the logger. A claim matures when its
symbol's price history contains ``horizon_sessions`` sessions after the
claim's as_of date (event claims mature once the predicted reaction
session has a bar). Grades are appended to history/predictions_grades.jsonl
— one line per claim, ever — and the scoreboard between the registry
markers in PREDICTIONS.md is rewritten from ALL grades on disk, so the
scoreboard is a pure function of the two JSONL files and never drifts
from them.

Grading uses only what the claim stored. If a company moved its earnings
date after a claim was logged, the grade measures the session the claim
named — which scores AGAINST the registry, and is preferable to letting
the grader quietly re-decide what was predicted.
"""
from __future__ import annotations

import json
import statistics
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
warnings.filterwarnings("ignore")

from market_desk.fetch import fetch_history              # noqa: E402
from market_desk.predictions import (                    # noqa: E402
    grade_book, grade_event, grade_range, grade_regime,
)

REGISTRY = REPO_ROOT / "history" / "predictions.jsonl"
GRADES = REPO_ROOT / "history" / "predictions_grades.jsonl"
DOC = REPO_ROOT / "PREDICTIONS.md"
BEGIN = "<!-- prediction-registry:begin -->"
END = "<!-- prediction-registry:end -->"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def session_after(dates: list[str], as_of: str, n: int):
    """Index of the n-th session strictly after as_of, or None."""
    later = [i for i, d in enumerate(dates) if d > as_of]
    return later[n - 1] if len(later) >= n else None


def grade_one(claim: dict, bars_by_symbol: dict) -> dict | None:
    kind = claim["kind"]
    if kind == "book":
        rets = {}
        for s in claim["weights"]:
            bs = bars_by_symbol.get(s)
            if not bs:
                return None
            dates = [b.date for b in bs]
            j = session_after(dates, claim["as_of"], claim["horizon_sessions"])
            base = next((i for i, d in enumerate(dates)
                         if d == claim["as_of"]), None)
            if j is None or base is None or bs[base].close <= 0:
                return None
            rets[s] = bs[j].close / bs[base].close - 1.0
        return grade_book(claim, rets)

    bs = bars_by_symbol.get(claim["symbol"])
    if not bs:
        return None
    dates = [b.date for b in bs]

    if kind == "event":
        try:
            j = dates.index(claim["session"])
        except ValueError:
            # predicted session not a trading day yet (or holiday-shifted);
            # grade once a later session exists, against the next real bar
            later = [i for i, d in enumerate(dates) if d > claim["session"]]
            if not later:
                return None
            j = later[0]
        if j == 0 or bs[j - 1].close <= 0:
            return None
        return grade_event(claim, abs(bs[j].close / bs[j - 1].close - 1.0))

    base = next((i for i, d in enumerate(dates) if d == claim["as_of"]), None)
    j = session_after(dates, claim["as_of"], claim["horizon_sessions"])
    if base is None or j is None:
        return None
    if kind == "range":
        return grade_range(claim, bs[j].close)
    if kind == "regime":
        if bs[base].close <= 0:
            return None
        return grade_regime(claim, abs(bs[j].close / bs[base].close - 1.0))
    return None


def scoreboard(claims: list[dict], grades: list[dict]) -> str:
    graded = {g["id"] for g in grades}
    pending = sum(1 for c in claims if c["id"] not in graded)
    by_kind: dict[str, list[dict]] = {}
    for g in grades:
        by_kind.setdefault(g["kind"], []).append(g)

    lines = [BEGIN,
             "## Live record — every claim graded after the fact",
             "",
             "Maintained automatically: `predictions_log.py` writes each "
             "day's claims to `history/predictions.jsonl` before their "
             "windows open; `predictions_grade.py` scores them when the "
             "windows close. No backtest below — only claims made on the "
             "record and what then happened.",
             ""]
    if not grades:
        lines.append(f"*No claims have matured yet; {pending} pending.*")
    else:
        lines.append("| claim type | graded | result | claimed |")
        lines.append("|---|---|---|---|")
        for kind in ("range", "book", "event", "regime"):
            gs = by_kind.get(kind, [])
            if not gs:
                continue
            n = len(gs)
            if kind in ("range", "book"):
                hits = sum(1 for g in gs if g["hit"])
                lines.append(f"| {kind} (80% bands) | {n} | "
                             f"{hits / n * 100:.0f}% inside | 80% |")
            elif kind == "event":
                ratios = [g["ratio"] for g in gs if g["ratio"]]
                med = statistics.median(ratios) if ratios else float("nan")
                beat = sum(1 for g in gs if g["beat_baseline"])
                lines.append(f"| event size | {n} | median realized/typical "
                             f"{med:.2f}; {beat / n * 100:.0f}% beat ordinary | "
                             f"~1.00; most |")
            elif kind == "regime":
                ok = sum(1 for g in gs if g["correct"])
                lines.append(f"| regime persistence | {n} | "
                             f"{ok / n * 100:.0f}% correct | >50% |")
        lines.append("")
        lines.append(f"*{pending} claims pending. Dates and sessions are "
                     f"approximations that grade against the registry, so "
                     f"this record understates rather than flatters.*")
    lines.append(END)
    return "\n".join(lines)


def main() -> int:
    claims = read_jsonl(REGISTRY)
    grades = read_jsonl(GRADES)
    done = {g["id"] for g in grades}
    todo = [c for c in claims if c["id"] not in done]
    print(f"{len(claims)} claims, {len(grades)} graded, {len(todo)} candidates")

    new: list[dict] = []
    if todo:
        symbols = sorted({s for c in todo
                          for s in (c.get("weights") or {c.get("symbol")})
                          if s})
        bars, _ = fetch_history(symbols, "6mo")
        for c in todo:
            g = grade_one(c, bars)
            if g is not None:
                g["kind"] = c["kind"]
                new.append(g)
        if new:
            GRADES.parent.mkdir(parents=True, exist_ok=True)
            with GRADES.open("a") as fh:
                for g in new:
                    fh.write(json.dumps(g) + "\n")
    print(f"graded {len(new)} newly matured claims")

    grades = read_jsonl(GRADES)
    block = scoreboard(claims, grades)
    doc = DOC.read_text()
    if BEGIN in doc and END in doc:
        head, rest = doc.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        doc = head + block + tail
    else:
        anchor = "## Predictions that are genuinely defensible"
        doc = doc.replace(anchor, block + "\n\n" + anchor, 1)
    DOC.write_text(doc)
    print("scoreboard rewritten in PREDICTIONS.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
