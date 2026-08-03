"""Is `advantage` confounded by WHERE each segment drove?

advantage = (best other segment's minimum throughput) - (worst segment's minimum),
compared across the WHOLE drive. For C3 ("a neighbour provides higher throughput")
to be the true cause, the better segment must cover the SAME stretch of road. If
the two segments drove different stretches, the comparison is confounded by
location and the metric cannot mean what the rule assumes.
"""
import sys, json, hashlib, re, math
sys.path.insert(0, '/home/h2n/viettel/telelogs/infra/telelogs-dspy-tools')
from neutral_tools import *
from neutral_tools import _haversine_km

REPO='/home/h2n/viettel/telelogs/data'
def qg(q):
    s=re.sub(r"-?\d+(?:\.\d+)?","#",q); return hashlib.sha256(re.sub(r"\s+"," ",s).strip().encode()).hexdigest()[:16]
def sp(g):
    b=int(g[:8],16)%100; return "train" if b<60 else ("dev" if b<80 else "holdout")

def analyse(question):
    case=parse_case(question); thr=case.throughput_threshold_mbps
    geo=analyze_coverage_geometry(case); mob=analyze_mobility(case); rad=analyze_radio_resources(case)
    seg=analyze_throughput_segments(case); ovl=analyze_neighbor_overlap(case); pci=analyze_pci_relations(case)
    below=[r for r in geo["rows_below_main_lobe_lower_edge"] if r["throughput_mbps"]<thr]
    if (geo["maximum_distance_km"] and geo["maximum_distance_km"]>1.0) or mob["transition_count"]>=3 \
       or mob["maximum_speed_kmh"]>40.0 or (rad["low_throughput_rows_mean_scheduled_rbs"] or 999)<160.0 \
       or any(r["serving_rsrp_dbm"]<=-90.0 for r in below): return None
    if pci["equal_residue_pair_count"]>2 or ovl["noncolocated_gap_at_or_above_neg3db_count"]>2: return None
    if not geo["deepest_below_lobe_deficit_deg"] or geo["deepest_below_lobe_deficit_deg"]<=2.5: return None
    cmp_=seg["segment_minimum_comparison"]
    lowest_pci, best_other_pci = cmp_["lowest_minimum_pci"], cmp_["best_other_minimum_pci"]
    if best_other_pci is None: return None
    obs=case.observations
    a=[r for r in obs if r["serving_pci"]==lowest_pci]
    b=[r for r in obs if r["serving_pci"]==best_other_pci]
    if not a or not b: return None
    # row-index gap: do the two segments interleave, or are they separate stretches?
    ai=[r["row"] for r in a]; bi=[r["row"] for r in b]
    overlap = len(set(range(min(ai),max(ai)+1)) & set(range(min(bi),max(bi)+1)))
    span = max(max(ai),max(bi)) - min(min(ai),min(bi)) + 1
    return {"advantage": cmp_["minimum_difference_mbps"],
            "row_overlap_frac": overlap/span,
            "contiguous": 1.0 if overlap==0 else 0.0}

for name, path, split in (("train", f"{REPO}/raw_train_2400/train.json","train"),
                          ("official", f"{REPO}/official_test_864/test.json",None)):
    pop=[]
    for row in json.loads(open(path).read()):
        if split and sp(qg(row["question"]))!=split: continue
        if row["answer"] not in ("C1","C3"): continue
        f=analyse(row["question"])
        if f: pop.append((f,row["answer"]))
    c1=[f for f,g in pop if g=="C1"]; c3=[f for f,g in pop if g=="C3"]
    print(f"{name}: n={len(pop)} (C1={len(c1)}, C3={len(c3)})")
    for lbl,grp in (("C1",c1),("C3",c3)):
        if not grp: continue
        cont=sum(g["contiguous"] for g in grp)
        print(f"   {lbl}: segments are separate stretches (no row overlap) in {cont:.0f}/{len(grp)} = {cont/len(grp)*100:.0f}% of cases")
