#!/usr/bin/env bash
# Rebuild every file under data/oranbench/ from its public source.
#
# Nothing here is gated and nothing needs an HF token. All of data/ is
# gitignored by repo policy, so this is the only record of where it came from.
#
#   bash infra/oranbench/download.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA="$ROOT/data/oranbench"
PY="${PY:-$ROOT/.venv-srsran/bin/python3}"      # needs pyarrow only
HF="https://huggingface.co/datasets"
mkdir -p "$DATA/upstream/oran_bench_13k" "$DATA/corpus_oran_specs"

echo "== 1/5  the scored benchmark (GSMA/ot-full, config oranbench) =="
curl -sL --fail -o "$DATA/test-00000-of-00001.parquet" \
  "$HF/GSMA/ot-full/resolve/main/oranbench/test-00000-of-00001.parquet"
echo "1dafd3e4606c9e08add68797cdb0f202679dca4e4f6136c99a30bd1939f62778  $DATA/test-00000-of-00001.parquet" \
  | sha256sum -c -

echo "== 2/5  the 150-row ot-lite subset (what the task evaluates by default) =="
curl -sL --fail -o "$DATA/lite_test_oranbench.json" \
  "$HF/GSMA/ot-lite/resolve/main/test_oranbench.json"

echo "== 3/5  provenance: the upstream copy, and the 13,952-row parent =="
curl -sL --fail -o "$DATA/upstream/oranbench13k-train.parquet" \
  "$HF/prnshv/ORANBench/resolve/main/data/train-00000-of-00001.parquet"
cmp "$DATA/test-00000-of-00001.parquet" "$DATA/upstream/oranbench13k-train.parquet" \
  && echo "   upstream 'train' is byte-identical to GSMA 'test' -- as expected"
for f in fin_E fin_M fin_H; do
  curl -sL --fail -o "$DATA/upstream/oran_bench_13k/$f.json" \
    "https://raw.githubusercontent.com/prnshv/ORAN-Bench-13K/main/Benchmark/$f.json"
done

echo "== 4/5  the leaderboard snapshot =="
for f in leaderboard_scores.json leaderboard_scores.csv; do
  curl -sL --fail -o "$DATA/$f" "$HF/GSMA/leaderboard/resolve/main/$f"
done

echo "== 5/5  the O-RAN specification corpus (GSMA/oran, markdown only) =="
# The repo is 833 MB across 11,282 files, but 767 MB of that is extracted
# figures. The 169 marked/**/raw.md documents are the whole text corpus, 54 MB.
curl -sL --fail -o "$DATA/corpus_oran_specs/manifest.jsonl" "$HF/GSMA/oran/resolve/main/manifest.jsonl"
curl -sL --fail -o "$DATA/corpus_oran_specs/STATUS.md"      "$HF/GSMA/oran/resolve/main/STATUS.md"
"$PY" - "$DATA/corpus_oran_specs" <<'PYEOF'
import json, os, sys, urllib.parse, urllib.request, concurrent.futures as cf

root = sys.argv[1]
API = "https://huggingface.co/api/datasets/GSMA/oran/tree/main?recursive=true"
BASE = "https://huggingface.co/datasets/GSMA/oran/resolve/main/"

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()), r.headers.get("Link")

paths, url = [], API
while url:                                        # the tree API pages at 1,000
    page, link = get(url)
    paths += [f["path"] for f in page if f.get("type") == "file" and f["path"].endswith("/raw.md")]
    url = link.split(";")[0].strip("<> ") if link and 'rel="next"' in link else None
print(f"   {len(paths)} marked specification documents")

def fetch(p):
    out = os.path.join(root, p[len("marked/"):-len("/raw.md")] + ".md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    req = urllib.request.Request(BASE + urllib.parse.quote(p), headers={"User-Agent": "curl"})
    with urllib.request.urlopen(req, timeout=300) as r, open(out, "wb") as f:
        f.write(r.read())
    return os.path.getsize(out)

with cf.ThreadPoolExecutor(8) as ex:
    sizes = list(ex.map(fetch, paths))
print(f"   {len(sizes)} files, {sum(sizes)/1e6:.1f} MB")
PYEOF

echo
echo "== derive =="
"$PY" "$ROOT/infra/oranbench/profile_data.py" > /dev/null && echo "   data/oranbench/test.jsonl"
"$PY" "$ROOT/infra/oranbench/build_pool.py"   | tail -6
echo
echo "done. See infra/oranbench/README.md"
