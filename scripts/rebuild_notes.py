#!/usr/bin/env python3
"""Regenerate docs/data/notes.json from analysis/*.md.

Split out from the main refresh because of an ordering constraint: the
daily workflow builds every payload FIRST, then has Claude write the
session's note. A notes.json produced during the refresh therefore
predates the note by one step, and the dashboard's Analysis tab would
always be a day behind. This runs after the note is written and before
the Pages artifact is uploaded.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import json                                            # noqa: E402
from market_desk.build import DATA_DIR, collect_notes  # noqa: E402

notes = collect_notes()
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "notes.json").write_text(json.dumps({"notes": notes}, separators=(",", ":")))
print(f"notes.json: {len(notes)} note(s)")
