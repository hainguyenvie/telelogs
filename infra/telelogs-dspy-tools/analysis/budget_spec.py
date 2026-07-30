import json, sys, collections
from pathlib import Path
SP = Path("/tmp/claude-1000/-home-h2n-viettel-telelogs/e44d1b9f-dbf8-4e14-b0ed-5a04c5ca977d/scratchpad")
sys.path.insert(0, "/home/h2n/viettel/telelogs/infra/telelogs-dspy-tools")
import symbolic_reference as S

def load(p):
    return {json.loads(l)["source_index"]: json.loads(l) for l in open(SP / p)}

spec  = load("off864_tool_off864_s11_specialist.jsonl")
alts  = {n: load(f) for n, f in [
    ("v21",  "off864_tool_off864_s11boot26_verified21.jsonl"),
    ("s4v2", "off864_tool_off864_s4boot46_verified2.jsonl"),
    ("think","off864_tool_off864_s11boot26_think1.jsonl"),
    ("forced","off864_tool_off864_s11boot26_forced.jsonl")]}
ens = {int(k): v for k, v in json.load(open(SP/"ens_off864.json"))["ens"].items()}

data = json.loads((SP/"test_official864.json").read_text())
items = data if isinstance(data, list) else data.get("questions") or data.get("data")

sym = {}
for i in sorted(spec):
    _f = S.features(items[i]["question"]); sym[i] = S.gate_label(_f) or S.residual_pred(_f, ("C6","C4","C1"), 142.5)
if False:
    print("no S.decide — available:", [x for x in dir(S) if not x.startswith("_")]); sys.exit(1)

sa = sum(sym[i] == spec[i]["target"] for i in spec)
print(f"symbolic on official-864: {sa}/{len(spec)} = {sa/len(spec)*100:.2f}%")

cats = collections.Counter(); detail = collections.defaultdict(collections.Counter)
for i, r in spec.items():
    if r["correct"]: continue
    g = r["target"]
    if sym[i] == g:
        c = "A"                                   # policy right, model wrong
    elif any(a[i]["answer"] == g for a in alts.values()) or ens[i] == g:
        c = "B"                                   # policy wrong, some run we have got it
    else:
        c = "C"                                   # nothing we have got it
    cats[c] += 1; detail[c][g] += 1
tot = sum(cats.values())
print(f"\nspecialist errors: {tot}")
for c, lab in [("A","policy right here, the model did not execute it"),
               ("B","policy wrong, but another run we have holds the gold"),
               ("C","policy wrong and no run we have holds the gold")]:
    print(f"  {c} {cats[c]:3d} ({cats[c]/tot*100:.1f}%)  {lab}")
    print(f"      by gold: {dict(sorted(detail[c].items()))}")

# residual-zone summary vs symbolic on the same 432 cases
GATES = {"C2","C5","C7","C8"}
resid = [i for i in spec if spec[i]["target"] not in GATES]
print(f"\nresidual-class cases n={len(resid)}")
print(f"  specialist {sum(spec[i]['correct'] for i in resid)}/{len(resid)}")
print(f"  symbolic   {sum(sym[i]==spec[i]['target'] for i in resid)}/{len(resid)}")
for n, a in alts.items():
    print(f"  {n:8s}   {sum(a[i]['answer']==spec[i]['target'] for i in resid)}/{len(resid)}")
print(f"  ensemble   {sum(ens[i]==spec[i]['target'] for i in resid)}/{len(resid)}")

# oracle over everything we have
orc = sum(1 for i in spec if spec[i]["correct"] or sym[i]==spec[i]["target"]
          or any(a[i]["answer"]==spec[i]["target"] for a in alts.values()) or ens[i]==spec[i]["target"])
print(f"\noracle over all runs+symbolic: {orc}/{len(spec)} = {orc/len(spec)*100:.2f}%")
json.dump({"cats": dict(cats), "detail": {k: dict(v) for k,v in detail.items()}},
          open(SP/"budget_spec.json","w"))
