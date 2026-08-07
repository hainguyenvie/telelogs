#!/usr/bin/env python3
"""Score an adapter (or the bare base) on the real dev-1000 with the harness
contract: same prompt builder, same enable_thinking=False, greedy, same
parse_answers port as run_baseline.py. Selection metric only — held-out 9,000
stays frozen until one final checkpoint is chosen.

Runs inside the training pod with transformers (the bench4 vLLM serves the
frozen base and cannot load adapters at runtime)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

STRICT = re.compile(r"(?i)^ANSWER\s*:\s*([A-Za-z\d ,]+)\s*(?:$|\n|\.)", re.MULTILINE)
LOOSE = re.compile(r"(?i)ANSWER\s*:\s*([A-Za-z\d ,]+)(?:[^\w]|\n|$|\.)")
BARE = re.compile(r"^\s*([A-Ea-e])(?:[).:,\s]|$)")

TEMPLATE = (
    "Answer the following multiple choice question. The entire content of your "
    "response should be of the following format: 'ANSWER: $LETTER' (without "
    "quotes) where LETTER is one of {letters}.\n\n{question}\n\n{choices}"
)


def parse(completion: str, n: int) -> str:
    m = STRICT.findall(completion or "") or LOOSE.findall(completion or "") \
        or BARE.findall(completion or "")
    if not m:
        return ""
    got = m[-1].strip().rstrip(".").upper()
    return got if got in {chr(65 + i) for i in range(n)} else ""


def build_prompt(row: dict) -> str:
    ch = "\n".join(f"{chr(65+i)}) {c}" for i, c in enumerate(row["choices"]))
    letters = ",".join(chr(65 + i) for i in range(len(row["choices"])))
    return TEMPLATE.format(letters=letters, question=row["question"], choices=ch)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--permute", action="store_true",
                     help="rotate choices with run_baseline.py's exact "
                          "permutation_for — comparable with b0_nothink_perm")
    ap.add_argument("--thinking", action="store_true",
                     help="enable_thinking=True + 6k-token budget, matching "
                          "the b0_think baseline arm")
    args = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(args.base, local_files_only=True)
    tok.padding_side = "left"
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        args.base, dtype=torch.bfloat16, attn_implementation="sdpa",
        device_map="cuda", local_files_only=True)
    if args.adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, args.adapter)
    model.eval()

    rows = [json.loads(l) for l in args.data.open(encoding="utf-8")]
    if args.permute:
        for row in rows:
            n = len(row["choices"])
            shift = hashlib.sha256(row["sample_id"].encode()).digest()[0] % n
            order = [(i + shift) % n for i in range(n)]
            row["choices"] = [row["choices"][j] for j in order]
            row["answer"] = order.index(int(row["answer"]))
    results = []
    correct = 0
    by_subject: dict[str, list[int]] = {}
    for start in range(0, len(rows), args.batch):
        chunk = rows[start:start + args.batch]
        prompts = [
            tok.apply_chat_template(
                [{"role": "user", "content": build_prompt(r)}],
                tokenize=False, add_generation_prompt=True,
                enable_thinking=args.thinking)
            for r in chunk]
        enc = tok(prompts, return_tensors="pt", padding=True,
                  add_special_tokens=False).to("cuda")
        with torch.no_grad():
            gen = model.generate(
                **enc, max_new_tokens=6000 if args.thinking else 8,
                do_sample=False, pad_token_id=tok.pad_token_id)
        for row, seq in zip(chunk, gen[:, enc["input_ids"].shape[1]:]):
            completion = tok.decode(seq, skip_special_tokens=True)
            got = parse(completion, len(row["choices"]))
            ok = got == chr(65 + int(row["answer"]))
            correct += ok
            by_subject.setdefault(row.get("subject", "?"), []).append(int(ok))
            results.append({"sample_id": row["sample_id"], "parsed": got,
                            "correct": ok, "completion": completion})
        done = start + len(chunk)
        print(f"{done}/{len(rows)} acc={correct/done:.4f}", flush=True)

    summary = {
        "adapter": args.adapter, "total": len(rows), "correct": correct,
        "accuracy": round(correct / len(rows), 4),
        "by_subject": {s: {"n": len(v), "acc": round(sum(v) / len(v), 4)}
                        for s, v in sorted(by_subject.items())},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": summary, "results": results},
                                    ensure_ascii=False, indent=1))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
