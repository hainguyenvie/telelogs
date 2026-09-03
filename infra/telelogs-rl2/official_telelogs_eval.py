#!/usr/bin/env python3
"""Run the OFFICIAL ot-full harness — full4_eval.py — on the telelogs track only.

This is the harness the GSMA leaderboard number comes from: raw question as a
single user message, no system prompt, thinking enabled, temperature 0.6 /
top_p 0.95 / top_k 20 / seed 42, max_tokens 38000, and the score is the last
\\boxed{...}'s first integer. Nothing here reimplements any of that — the module
is imported and its own main() is called, so if the official harness changes,
this changes with it.

Three module globals are rebound before main() runs, and only three:

  MODEL      the served-model-name of the endpoint under test. full4_eval.py
             hardcodes "Qwen/Qwen3-8B" because it was written for the base
             server. Serving GRPO weights under that name is exactly the
             mistake that made v1's provenance unrecoverable, so the name is
             passed in instead and written into the run directory.
  ENDPOINT   where to send the requests.
  paths      RUN_DIR and the dashboard files, so this run cannot overwrite the
             base model's full4 results.

load_samples is replaced to read the telelogs parquet only and return empty
lists for the other three benchmarks. Empty is safe throughout: build_progress
guards every division on `if total else 0`, and main's scheduling loop skips a
benchmark once `index >= len(rows)`. The alternative — trimming BENCHMARKS —
would KeyError in main's hardcoded four-name tuple.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(os.environ["BENCH4_SCRIPTS"])
sys.path.insert(0, str(SCRIPTS))

import full4_eval as F  # noqa: E402

RUN_DIR = Path(os.environ["OFFICIAL_RUN_DIR"])
DATASET = Path(os.environ["OT_FULL_ROOT"])
TELELOGS = DATASET / "telelogs" / "test-00000-of-00001.parquet"
assert TELELOGS.is_file(), f"missing {TELELOGS}"

F.MODEL = os.environ["OFFICIAL_MODEL"]
F.ENDPOINT = os.environ["VLLM_CHAT_URL"]

F.RUN_DIR = RUN_DIR
F.RESULTS_PATH = RUN_DIR / "results.jsonl"
F.REPORT_PATH = RUN_DIR / "report.md"
F.DASHBOARD_DIR = RUN_DIR / "dashboard"
F.DATA_DIR = RUN_DIR / "dashboard" / "data"
F.ITEMS_DIR = RUN_DIR / "dashboard" / "data" / "items"
F.PROGRESS_PATH = F.DATA_DIR / "progress.json"
F.RECENT_PATH = F.DATA_DIR / "recent.json"


def load_telelogs_only() -> dict[str, list[dict[str, Any]]]:
    import pyarrow.parquet as pq

    rows = pq.read_table(TELELOGS).to_pylist()
    samples = []
    for index, row in enumerate(rows):
        item = dict(row)
        item.update(benchmark="telelogs", sample_index=index,
                    sample_id=f"telelogs-{index:05d}")
        samples.append(item)
    return {"telelogs": samples, "telemath": [], "teletables": [], "three_gpp": []}


F.load_samples = load_telelogs_only

RUN_DIR.mkdir(parents=True, exist_ok=True)
(RUN_DIR / "provenance.json").write_text(json.dumps({
    "harness": "full4_eval.py (official), telelogs track only",
    "harness_path": str(SCRIPTS / "full4_eval.py"),
    "served_model_name": F.MODEL,
    "endpoint": F.ENDPOINT,
    "parquet": str(TELELOGS),
    "workers": F.WORKERS,
    "max_tokens": F.MAX_TOKENS,
    "thinking_enabled": True,
}, indent=2), encoding="utf-8")

print(f"official telelogs eval -> {RUN_DIR}", flush=True)
print(f"  model={F.MODEL} endpoint={F.ENDPOINT} "
      f"workers={F.WORKERS} max_tokens={F.MAX_TOKENS}", flush=True)

F.main()
