#!/usr/bin/env python3
"""Print the score of a verify run, next to the band it has to be read against.

A single number out of an eval means nothing here without the spread it was
drawn from: this exact adapter and program scored 814, 812, 811 and 807 out of
864 across four H200 runs, every pair indistinguishable. Printing the run's
number alone invites reading a 3-row difference as a regression.
"""
from __future__ import annotations

import json
import pathlib
import statistics
import sys

H200_RUNS = (814, 812, 811, 807)   # job 34, job 53, job 51A, job 52


def main() -> None:
    out = pathlib.Path(sys.argv[1])
    path = out / "results.jsonl"
    if not path.is_file():
        print(f"no results.jsonl under {out}")
        raise SystemExit(1)

    rows = {}
    for line in path.open(encoding="utf-8"):
        if line.strip():
            row = json.loads(line)
            rows[row["sample_id"]] = row      # last write wins, matching a resume

    total = len(rows)
    correct = sum(bool(r["correct"]) for r in rows.values())
    errors = sum(bool(r.get("error")) for r in rows.values())
    unboxed = sum(1 for r in rows.values() if not r.get("parsed_answer"))

    print()
    print(f"OFFICIAL telelogs, boxed-int scorer : {correct}/{total} = {correct / total * 100:.2f}%")
    print(f"  errors {errors}   no-boxed {unboxed}")
    print()
    lo, hi = min(H200_RUNS), max(H200_RUNS)
    mean = statistics.mean(H200_RUNS)
    print("Measured on H200 (SM 9.0), same adapter, same compiled program:")
    print(f"  {' / '.join(str(v) for v in H200_RUNS)} out of 864"
          f"  ->  mean {mean / 864 * 100:.2f}%, sd {statistics.stdev(H200_RUNS):.2f} rows,"
          f" range {(hi - lo) / 864 * 100:.2f}pp")
    print("  every pair indistinguishable (p 0.30-1.00); 24-37 rows change answer per rerun.")
    print()
    if lo <= correct <= hi:
        print(f"-> {correct} sits inside the measured band [{lo}, {hi}]. The stack matches.")
    else:
        side = "above" if correct > hi else "below"
        print(f"-> {correct} is {side} the measured band [{lo}, {hi}].")
        print("   A different GPU architecture does shift the numerics, so a small excursion")
        print("   is expected; a large one means something in the stack differs. Check the")
        print("   vLLM version, the model directory, and that program.json loaded.")


if __name__ == "__main__":
    main()
