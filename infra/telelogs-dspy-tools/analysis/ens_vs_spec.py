import json, math
from collections import defaultdict
from pathlib import Path
D = Path("/tmp/claude-1000/-home-h2n-viettel-telelogs/e44d1b9f-dbf8-4e14-b0ed-5a04c5ca977d/scratchpad")

def load(name):
    out = {}
    for line in (D / name).read_text().splitlines():
        r = json.loads(line)
        out[r["source_index"]] = (r["answer"], r["target"])
    return out

VOTERS = ["v21", "s4v2", "think"]
dev = {
    "v21":  load("dev96_tool_dev96_s11boot26_verified21.jsonl"),
    "s4v2": load("dev96_tool_dev96_s4boot46_verified2.jsonl"),
    "think":load("dev96_tool_dev96_s11boot26_think.jsonl"),
}
off = {
    "v21":  load("off864_tool_off864_s11boot26_verified21.jsonl"),
    "s4v2": load("off864_tool_off864_s4boot46_verified2.jsonl"),
    "think":load("off864_tool_off864_s11boot26_think1.jsonl"),
}
spec = load("off864_tool_off864_s11_specialist.jsonl")

# weights fitted on dev-96 only: w(voter,class) = (correct+1)/(votes+2)
w = {}
for v in VOTERS:
    votes, corr = defaultdict(int), defaultdict(int)
    for i, (a, t) in dev[v].items():
        votes[a] += 1
        if a == t: corr[a] += 1
    for c in [f"C{k}" for k in range(1, 9)]:
        w[(v, c)] = (corr[c] + 1) / (votes[c] + 2)

def vote(preds):
    score = defaultdict(float)
    for v in VOTERS:
        a = preds[v]
        if not a: continue                      # a failed ballot casts no vote
        score[a] += w.get((v, a), 0.5)
    if not score: return preds["v21"]
    best = max(score.values())
    top = sorted(c for c, s in score.items() if abs(s - best) < 1e-12)
    return preds["v21"] if len(top) > 1 else top[0]   # tie -> verifier-s11

ids = sorted(set(spec) & set.intersection(*[set(off[v]) for v in VOTERS]))
gold = {i: spec[i][1] for i in ids}
ens = {i: vote({v: off[v][i][0] for v in VOTERS}) for i in ids}

ea = sum(ens[i] == gold[i] for i in ids)
sa = sum(spec[i][0] == gold[i] for i in ids)
print(f"n={len(ids)}")
print(f"  ensemble (3 ballots, weighted)  {ea}/{len(ids)} = {ea/len(ids)*100:.2f}%")
print(f"  specialist (single program)     {sa}/{len(ids)} = {sa/len(ids)*100:.2f}%")

n01 = sum(1 for i in ids if ens[i] == gold[i] and spec[i][0] != gold[i])
n10 = sum(1 for i in ids if spec[i][0] == gold[i] and ens[i] != gold[i])
n = n01 + n10
p = min(1.0, sum(math.comb(n, k) for k in range(0, min(n01, n10) + 1)) / 2**n * 2)
print(f"\nMcNemar specialist vs ensemble: {n10}:{n01}  (discordant {n})  exact two-sided p={p:.3g}")

# where the specialist wins/loses by class
wins, losses = defaultdict(int), defaultdict(int)
for i in ids:
    if spec[i][0] == gold[i] and ens[i] != gold[i]: wins[gold[i]] += 1
    if ens[i] == gold[i] and spec[i][0] != gold[i]: losses[gold[i]] += 1
print("\nby true class   spec wins / spec loses")
for c in [f"C{k}" for k in range(1, 9)]:
    if wins[c] or losses[c]:
        print(f"  {c}   +{wins[c]:3d} / -{losses[c]:3d}   net {wins[c]-losses[c]:+d}")

# per-class accuracy side by side
print("\nper-class accuracy      ensemble   specialist")
for c in [f"C{k}" for k in range(1, 9)]:
    sub = [i for i in ids if gold[i] == c]
    e = sum(ens[i] == c for i in sub) / len(sub) * 100
    s = sum(spec[i][0] == c for i in sub) / len(sub) * 100
    print(f"  {c}  n={len(sub):3d}       {e:6.1f}%    {s:6.1f}%   {s-e:+6.1f}")
json.dump({"ens": {str(k): v for k, v in ens.items()}}, open(D/"ens_off864.json","w"))
