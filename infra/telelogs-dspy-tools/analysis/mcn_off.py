import json, math
from collections import Counter, defaultdict
from pathlib import Path
D = Path("/tmp/claude-1000/-home-h2n-viettel-telelogs/e44d1b9f-dbf8-4e14-b0ed-5a04c5ca977d/scratchpad")

def load(name):
    out = {}
    for line in (D / name).read_text().splitlines():
        r = json.loads(line)
        cid = r["source_index"]
        out[cid] = (r["answer"], r["target"])
    return out

runs = {
    "specialist": "off864_tool_off864_s11_specialist.jsonl",
    "v21":        "off864_tool_off864_s11boot26_verified21.jsonl",
    "s4v2":       "off864_tool_off864_s4boot46_verified2.jsonl",
    "think":      "off864_tool_off864_s11boot26_think1.jsonl",
    "forced":     "off864_tool_off864_s11boot26_forced.jsonl",
}
P = {k: load(v) for k, v in runs.items()}
ids = sorted(set.intersection(*[set(p) for p in P.values()]))
gold = {i: P["specialist"][i][1] for i in ids}
print("common ids:", len(ids))

for k in P:
    acc = sum(P[k][i][0] == gold[i] for i in ids)
    print(f"  {k:11s} {acc}/{len(ids)} = {acc/len(ids)*100:.2f}%")

# precision-weighted ensemble, weights fitted on dev-96 (as shipped)
W = json.loads((D / "vote_weights.json").read_text()) if (D/"vote_weights.json").exists() else None

def mcnemar(a, b):
    n01 = sum(1 for i in ids if P[a][i][0] != gold[i] and P[b][i][0] == gold[i])
    n10 = sum(1 for i in ids if P[a][i][0] == gold[i] and P[b][i][0] != gold[i])
    n = n01 + n10
    if n == 0: return n10, n01, 1.0
    # exact two-sided binomial
    p = sum(math.comb(n, k) for k in range(0, min(n01, n10) + 1)) / 2**n * 2
    return n10, n01, min(1.0, p)

print("\nMcNemar (A wins : B wins, exact two-sided p)")
for a, b in [("specialist","v21"), ("specialist","forced"), ("specialist","s4v2"), ("specialist","think")]:
    w, l, p = mcnemar(a, b)
    print(f"  {a} vs {b:10s}  {w}:{l}  p={p:.3g}")
