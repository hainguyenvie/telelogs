# repro-9421 — đo lại 94.21% official-864 trên trọng số base

## Vì sao có track này

Bản `h200_use.md` cũ (nay đã gộp vào [H200_SERVER_GUIDE.md](../../H200_SERVER_GUIDE.md)) §11 khẳng định kết quả 94,21% official-864 chạy
trên `Qwen/Qwen3-8B` **gốc**, và lấy đó làm lý do xoá 215 GB trọng số merged.
Log của chính run đó trên H200 nói ngược lại — cả ba job `magrule` đều ghi
`GRPO weights`. Lập luận trong `h200_use.md` sai về phương pháp: nó dựa vào
`DSPY_MODEL` để mặc định, nhưng `serve_grpo_model.sh` cố ý đặt
`--served-model-name Qwen/Qwen3-8B` để giả danh base, nên `DSPY_MODEL` không
phân biệt được gì. Thứ phân biệt là `DSPY_API_BASE`, và giá trị đó không được
ghi lại ở đâu.

Trọng số GRPO nay đã mất (`runs/rl-grpo` không còn; `runs/rl-skeleton` chỉ còn
72 KB script, `checkpoints/` và `models/` rỗng, không còn adapter nào). Nên câu
hỏi chỉ còn một cách trả lời: **đo base dưới đúng pipeline đã ship.**

## Giữ nguyên những gì

Mọi thứ trừ trọng số được giữ y hệt run ngày 03/08/2026:

| Thành phần | Giá trị |
|---|---|
| program.json | `bootstrap26_seeded11_b3` (md5 `798aa309…`, khớp bản trong Git) |
| Source pipeline | 14 module `.py` copy từ [infra/telelogs-dspy-tools/](../telelogs-dspy-tools/), md5 khớp H200 |
| Thang residual | magnitude, docstring `ResidualDecision` trong `specialist_program.py` |
| Scorer | boxed-int chính thức, copy nguyên văn trong `champion_gsma_full.py` |
| Dataset | `GSMA--ot-full/telelogs/test-00000-of-00001.parquet` |
| Tham số | `--method b3_react_specialist --workers 8 --max-tokens 1000`, temp 0.0, thinking off |

Biến duy nhất: `/workspace/models/Qwen3-8B` (base) thay cho endpoint GRPO.

## Hạ tầng

- Node: **hgx046** (bắt buộc — `/mnt/registry/tensara-home` là storage node-local).
- Card: **1× H200 nguyên con**, `GPU-6b6e1cb7-061c-6cf2-ade0-63ca11985911`, probe
  ngày 24/08/2026 thấy còn đúng 1 con trống.
- Pod: `telelogs-repro9421` (label `owner: h2n`), `restartPolicy: Never`.
- Pod tự serve vLLM ở `127.0.0.1:8000` rồi chạy client trong cùng một job — không
  cần Service, không cần client pod thứ hai.

`runs/v0-lora` đã bị xoá nên `/workspace/telelogs` không còn thư mục backing.
pod.yaml neo nó bằng một `emptyDir` (ghi được) rồi mount `venv` và `cache/hf`
lồng vào trong — mount lồng vào một parent read-only thì containerd không tạo nổi
mountpoint (bẫy đã ghi ở [H200_SERVER_GUIDE.md](../../H200_SERVER_GUIDE.md) §12).

## Bố cục file

```
local:  infra/telelogs-repro9421/
        ├── README.md                  ← file này
        ├── pod.yaml                   ← pod GPU + job-runner
        ├── probe_gpu.yaml             ← pause pod đếm GPU trống (xoá ngay sau khi probe)
        └── jobs/10_serve_and_eval.sh  ← serve base + chạy official-864 + kill vLLM

H200:   ~/projects/telelogs/runs/repro-9421/          (= /workspace/repro trong pod)
        ├── champion_gsma_full.py      ← runner, đặt ở gốc vì nó sys.path.insert(ROOT/"code")
        ├── code/                      ← 14 module pipeline
        ├── jobs/                      ← thả .sh vào đây để chạy
        ├── done/                      ← <job>.log, <job>.rcN
        └── results/champion_base_magrule/   ← results.jsonl, summary.json, report.md
```

Mount vào pod (chỉ `repro` là ghi được — run này không được phép sửa artifact nó
đang đo lại):

| Trong pod | Trên host | Quyền |
|---|---|---|
| `/workspace/repro` | `runs/repro-9421` | rw |
| `/workspace/models` | `shared/models` | ro |
| `/workspace/telelogs/venv` | `venvs/venv` (vLLM 0.11.0) | ro |
| `/workspace/telelogs/cache/hf` | `shared/hf-cache` | ro |
| `/workspace/telelogs-bench4` | `runs/bench4` (venv DSPy, program.json) | ro |

## Kết quả (24/08/2026)

**Không tái tạo được 94,21% trên trọng số base.** Base cho **797/864 = 92,25%**.

| | official-864 | mean LM calls |
|---|---:|---:|
| Run gốc 03/08 (log ghi "GRPO weights") | 814 = 94,21% | 11,61 |
| Rerun 24/08, base `Qwen/Qwen3-8B` | 797 = 92,25% | 12,17 |

Ghép cặp theo `sample_index`, McNemar chính xác: **28 : 11, p = 0,0095**. Sàn nhiễu
đo được của track này là hai run giống hệt ra 768 vs 767 (13 : 12, p = 1,0) — tức
~25 cặp lệch là bình thường, nhưng **độ lệch một chiều** ở đây thì không.

Toàn bộ chênh lệch nằm trong nửa residual: C2/C5/C7/C8 đều 108/108 ở cả hai run,
còn C4 −9, C3 −7, C1 −4, C6 +3. Đúng chỗ mà specialist call chịu trách nhiệm, tức
đúng chỗ GRPO được huấn luyện để chạy.

**Kết luận: `h200_use.md` §11 sai.** Run 94,21% chạy trên trọng số GRPO, đúng như log
của chính nó. Lý do dùng để xoá 215 GB merged là một suy luận sai từ `DSPY_MODEL`.
Trọng số GRPO nay đã mất, nên **94,21% hiện không tái tạo được**; con số tái tạo
được bằng checkpoint công khai là 92,25%.

Số liệu: [RESULT.txt](RESULT.txt), sinh bởi [mcnemar_base_vs_orig.py](mcnemar_base_vs_orig.py)
từ hai file `results.jsonl` trong [artifacts/telelogs-dspy-tools/results/](../../artifacts/telelogs-dspy-tools/results/).

## Chạy lại

```bash
scp infra/telelogs-repro9421/pod.yaml H200_Tensara:~/projects/telelogs/runs/repro-9421/
ssh H200_Tensara 'kubectl apply -f ~/projects/telelogs/runs/repro-9421/pod.yaml'
scp infra/telelogs-repro9421/jobs/10_serve_and_eval.sh \
  H200_Tensara:~/projects/telelogs/runs/repro-9421/jobs/
ssh H200_Tensara 'tail -f ~/projects/telelogs/runs/repro-9421/done/10_serve_and_eval.log'
```

Xong việc **xoá pod để trả card**: `kubectl delete pod telelogs-repro9421 -n tensara`.
