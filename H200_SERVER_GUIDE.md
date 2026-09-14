# H200_Tensara — hướng dẫn dùng máy chủ cho repo telelogs

> **File DUY NHẤT về máy chủ.** Viết 14/09/2026, probe trực tiếp trên server.
> Gộp và thay thế hai bản cũ: `docs/H200_SERVER_GUIDE.md` (guide của project **vt-track1**, lạc sang đây)
> và `h200_use.md` (bản thời k8s — phần vận hành đã sai; những gì còn giá trị nằm ở mục 9–12 dưới đây).
> IP/jump-host không nằm trong file này — chúng ở `~/.ssh/config` (alias `H200_Tensara`, ProxyJump `H200_NTCN_root`).

**Mục lục** — [0 Đổi gì](#0-điều-đã-thay-đổi-quan-trọng-nhất) · [1 Truy cập](#1-truy-cập-và-công-cụ) ·
[2 GPU](#2-gpu--luật-dùng-card) · [3 CPU/RAM/đĩa](#3-ba-giới-hạn-dễ-chết-người) ·
[4 Thư mục](#4-bố-cục-thư-mục-và-bảng-quy-đổi-đường-dẫn) · [5 Venv](#5-dựng-lại-venv-bắt-buộc) ·
[6 Chạy job](#6-chạy-một-job) · [7 vLLM](#7-phục-vụ-vllm-và-tunnel-về-máy-local) · [8 Bẫy](#8-bẫy-đã-dẫm-phải) ·
[9 Dọn dẹp](#9-dọn-dẹp-đi-qua-thùng-rác-đừng-rm--rf-thẳng) · [10 Kỷ luật dữ liệu](#10-kỷ-luật-dữ-liệu-thí-nghiệm) ·
[11 Backup](#11-backup-và-sơ-tán) · [12 Di tích k8s](#12-di-tích-k8s--chỉ-để-đọc-script-cũ)

## 0. Điều đã thay đổi (quan trọng nhất)

Trước đây `tensara-dev-0` **không có GPU**: muốn train phải `kubectl apply` một pod GPU riêng rồi điều khiển nó
qua job-runner trên hostPath, vì `kubectl exec` bị chặn. **Bây giờ không cần nữa** — ssh thẳng vào dev pod là đã
thấy cả 8 × H200 và chạy được CUDA tại chỗ.

Đo ngày 14/09/2026 từ chính shell ssh:

```
nvidia-smi -L                → 8 × NVIDIA H200
cuInit / cuCtxCreate GPU7    → rc 0, free 139.3 / 139.8 GiB
kubectl get pods -n tensara  → vẫn chạy, nhưng không còn pod telelogs nào
```

Hệ quả kéo theo, đều đã kiểm chứng:

| Thứ | Trạng thái hôm nay |
|---|---|
| `/workspace/...` | **không tồn tại** → mọi script `infra/**` có `ROOT=/workspace/...` không chạy được nguyên trạng (237 file có chuỗi này) |
| `venvs/venv`, `venv-grpo`, `venv-train` | **chết** — `bin/python` trỏ `/opt/conda/bin/python`, shebang trỏ `/workspace/...`; phải dựng lại |
| `venvs/venv-dl` | interpreter còn chạy (`/usr/bin/python3.12`), có `datasets`/`hf_transfer`, **không có torch** |
| job-runner (`jobs/` → `done/*.rcN`) | không còn pod nào chạy vòng lặp → submit job kiểu cũ sẽ nằm im mãi |
| `port_forward.sh` | hỏng: `telelogs-bench4-vllm.tensara.svc.cluster.local` **không resolve** |
| `/mnt/registry/tensara-home` | không còn; dữ liệu nằm thẳng ở `/home/tensara/projects/` |
| `pods/exec`, `nodes`, `pvc` | vẫn Forbidden như cũ — nhưng đã hết quan trọng |

## 1. Truy cập và công cụ

```bash
ssh H200_Tensara                      # → pod tensara-dev-0, user tensara, /home/tensara
scp <local> H200_Tensara:~/…
```

Server chạy **UTC** (VN = UTC+7). Mọi `date`/timestamp trong log là UTC.

Đẩy code: repo ở local là nguồn sự thật, server chỉ là nơi chạy. **Không có `rsync`** → tar qua ssh pipe:

```bash
tar czf - --exclude .git --exclude __pycache__ -C /home/h2n/viettel/telelogs infra data \
  | ssh H200_Tensara 'tar xzf - -C ~/projects/telelogs/runs/<track>/code/'

# kéo kết quả về
ssh H200_Tensara 'tar czf - -C ~/projects/telelogs/runs/<track> results' | tar xzf - -C ./
```

Có sẵn: `git`, `tmux`, `wget`, `curl`, `gzip`, `kubectl`, `uv` (`~/.local/bin/uv`), `python3` 3.12.3.
**Không có**: `rsync`, `screen`, `conda`, `docker`/`podman`/`buildah`, `/opt/conda`.
Mạng ra ngoài thông (huggingface.co trả 200) nhưng hay đứt giữa chừng khi kéo nhiều GB — luôn bọc retry.

## 2. GPU — luật dùng card

8 × H200 141 GB, **dùng chung với các project khác trong cùng một home** (`vlm2vec`, `ares-gs`, `vt-track1`,
`telcollm-*`, …). Tất cả chạy dưới cùng user `tensara`, cùng PID namespace — nên nhìn thấy nhau.

- **Chỉ vào card đang 0 MiB.** Lúc probe: GPU 0–4 bị `vlm2vec` giữ 74–84 GB, GPU 5–7 trống.
- Xem ai giữ card — đường dẫn process cho biết project chủ:

```bash
nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader
nvidia-smi --query-compute-apps=gpu_uuid,pid,used_memory,process_name --format=csv,noheader
```

- **Không kill process của project khác.** Thấy `/home/tensara/projects/<khác>/...` thì để yên.
- Không còn MIG slice: hoặc cả một H200 141 GB, hoặc không. Chọn card bằng `CUDA_VISIBLE_DEVICES=<i>`.
- Card có thể bị chiếm **giữa chừng** khi project khác khởi động lại → job dài phải có cổng chờ VRAM:

```bash
G=7
vgate() { until [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i $G)" -le 1000 ]; do sleep 120; done; }
```

## 3. Ba giới hạn dễ chết người

**CPU/RAM: `nproc` nói dối.** Pod thấy 192 core / 2 TB RAM của node, nhưng cgroup của ta là:

```
/sys/fs/cgroup/cpu.max     → 3200000 100000   = 32 CPU
/sys/fs/cgroup/memory.max  → 549755813888     = 512 GiB
```

Torch/vLLM đọc `nproc` rồi mở 192 thread OMP và tự bóp cổ mình → luôn set `OMP_NUM_THREADS` (≤ 16) và
`--dataloader_num_workers` vừa phải. Nạp nhiều model lớn song song thì OOM-killer giết theo cgroup 512 GiB,
**không có traceback**.

**Ổ đĩa gần đầy.** `/home/tensara` 3,5 TB, **93% dùng, còn ~248 GB** — dùng chung cho mọi project.

```bash
df -h /home/tensara                          # kiểm TRƯỚC mỗi lô mới
du -sh ~/projects/telelogs/runs/* | sort -rh
```

Phần của telelogs là 30 GB: `runs/` 25 GB (rl-grpo2 22 G, eval-v2 2 G, bench4 858 M) và `venvs/` 4,8 GB, cộng
phần dùng chung `~/projects/_shared` 110 GB (models 94 G, corpora 17 G) chia với teleqna. Cách dọn: mục 9.

## 4. Bố cục thư mục và bảng quy đổi đường dẫn

```text
~/projects/telelogs/                30G
├── runs/
│   ├── rl-grpo2/     22G   GRPO v2: checkpoints/v2/ 8,3G, results/ckpt_archive/checkpoint-700 ⭐, venv/ 8,3G (chết)
│   ├── eval-v2/     2,0G   pipeline chấm official-864 + shim OpenAI
│   ├── bench4/      858M   phục vụ vLLM + dashboard
│   └── canon/, repro-9421/, rl-skeleton/, image-build/
├── venvs/            4,8G  venv, venv-dl, venv-grpo, venv-train  ← 3/4 đã chết, xem mục 5
└── shared -> ../_shared     symlink

~/projects/_shared/                110G — DÙNG CHUNG với project teleqna, không bên nào sở hữu
├── models/     94G   OTel-2.0-31B-IT 60G, Qwen3.5-9B 19G, Qwen3-8B 16G, Qwen3-Embedding-8B
├── corpora/    17G   tele-data 12G, tspec-llm 4,6G, telecom-kg-rel19
└── hf-cache/  128M
```

**Track teleqna đã tách ra** ngày 14/09/2026: `runs/teleqna-{8b,sft,spare1,spare4}` nay nằm ở
`~/projects/teleqna/runs/` (repo `~/viettel/teleqna`). Đừng đi tìm chúng ở đây, và đừng xoá gì trong
`_shared` mà không hỏi bên đó — `shared/` của cả hai project chỉ là symlink tới cùng một chỗ.

Các thư mục project khác trong `~/projects/` **không đụng tới**.

**Bảng quy đổi cũ → mới** (cần khi chạy lại bất kỳ script nào trong `infra/`):

| Trong script cũ | Trên server hôm nay |
|---|---|
| `/workspace/telelogs-rl` | `~/projects/telelogs/runs/rl-grpo2` |
| `/workspace/telelogs-bench4` | `~/projects/telelogs/runs/bench4` |
| `/workspace/eval` | `~/projects/telelogs/runs/eval-v2` |
| `/workspace/models/<M>` | `~/projects/_shared/models/<M>` |
| `/workspace/hf-cache` | `~/projects/_shared/hf-cache` |
| `/workspace/teleqna-sft`, `/workspace/teleqna-spare4` | đã sang project teleqna: `~/projects/teleqna/runs/...` |
| `/workspace/telelogs-base`, `-v12/13/14-sft` | đã xoá trong đợt dọn — chỉ còn adapter/kết quả trong track tương ứng |

Cách sửa: mỗi script chỉ có **một** dòng `ROOT=` ở đầu; đổi dòng đó là đủ, phần còn lại dùng `$ROOT`.

## 5. Dựng lại venv (bắt buộc)

`venvs/venv`, `venv-grpo`, `venv-train` được tạo **bên trong pod GPU cũ** nên `bin/python` là symlink tới
`/opt/conda/bin/python` và shebang mọi console-script là `/workspace/telelogs/venv/bin/python`. Cả hai đường dẫn
đều không tồn tại trên dev pod → gọi vào là `No such file or directory`. Không vá được, dựng mới:

```bash
export UV_CACHE_DIR=~/projects/telelogs/shared/uv-cache     # xoá sau khi xong, nó phình vài GB
V=~/projects/telelogs/venvs/venv-h200
~/.local/bin/uv venv --python 3.12 $V
~/.local/bin/uv pip install --python $V/bin/python torch --torch-backend=cu128
~/.local/bin/uv pip install --python $V/bin/python vllm transformers peft trl datasets accelerate
$V/bin/python -c 'import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.device_count())'
```

Gọi thẳng `$V/bin/python`, **không `activate`**. Wheel CUDA nặng (cudnn 627 MB, cublas 567 MB) nên bước này mất
~10 phút và ~5 GB đĩa — kiểm `df -h` trước. Không pipe output của `pip`/`uv` qua `tail`: build lỗi sẽ bị nuốt.

> Trạng thái kiểm chứng: `uv venv` + tải wheel đã chạy thật và trơn tru; tôi **dừng giữa chừng và xoá sạch** để
> không chiếm thêm đĩa, nên dòng `torch.cuda.is_available()` chưa chạy tới. Đường GPU thì đã chứng minh bằng
> `cuCtxCreate` trên GPU 7 (rc 0) và bằng việc project khác đang train 5 card từ đúng shell này.

## 6. Chạy một job

Không còn job-runner. Chạy thẳng, nền, ghi log ra file:

```bash
# 1) viết script ở LOCAL rồi scp lên — đừng heredoc lồng qua ssh, quote bị mangle
scp myjob.sh H200_Tensara:~/projects/telelogs/runs/<track>/jobs/

# 2) phóng nền — CHÚ Ý `</dev/null`, thiếu nó phiên ssh treo không thoát
ssh H200_Tensara 'cd ~/projects/telelogs/runs/<track> && \
  nohup bash jobs/myjob.sh > logs/myjob.log 2>&1 </dev/null & disown'

# 3) theo dõi
ssh H200_Tensara 'tail -f ~/projects/telelogs/runs/<track>/logs/myjob.log'
```

Hoặc `tmux new -d -s <ten> '<lệnh>'` nếu muốn quay lại xem terminal.

Khung tối thiểu của một script:

```bash
#!/bin/bash
set -euo pipefail
ROOT=$HOME/projects/telelogs/runs/<track>
V=$HOME/projects/telelogs/venvs/venv-h200
export HF_HOME=$HOME/projects/_shared/hf-cache
export OMP_NUM_THREADS=8            # cgroup chỉ cho 32 CPU
G=7; vgate                          # chờ card trống (mục 2)
CUDA_VISIBLE_DEVICES=$G $V/bin/python -u $ROOT/code/train.py … >> $ROOT/logs/x.log 2>&1
```

Ba quy ước giữ nguyên giá trị từ các vòng trước: **cổng rẻ trước bước đắt** (probe rồi mới train 5 h),
**guard tái chạy** (`[ -f out/meta.json ] || …` để phóng lại không làm lại từ đầu), và **`python3 -u`**
để log chảy theo thời gian thực.

## 7. Phục vụ vLLM và tunnel về máy local

`port_forward.sh` trong `infra/telelogs-bench4/` trỏ vào DNS của Service k8s — **đã chết**. Nay đơn giản hơn:
vLLM chạy như một tiến trình thường trên dev pod, tunnel thẳng bằng ssh.

```bash
# trên server
CUDA_VISIBLE_DEVICES=7 ~/projects/telelogs/venvs/venv-h200/bin/vllm serve \
  ~/projects/_shared/models/Qwen3-8B --port 8000 --served-model-name Qwen/Qwen3-8B \
  > ~/projects/telelogs/runs/bench4/logs/serve.log 2>&1 </dev/null &

# ở local
ssh -N -L 8000:127.0.0.1:8000 H200_Tensara
curl http://127.0.0.1:8000/v1/models
export VLLM_BASE_URL=http://127.0.0.1:8000/v1 VLLM_API_KEY=local
```

`kubectl port-forward` vẫn bị chặn, nhưng không cần tới nữa. Dừng server: `kill <PID>` (xem mục 8 về `pkill`).
Không phơi cổng 8000 ra ngoài.

## 8. Bẫy đã dẫm phải

| Triệu chứng | Nguyên nhân | Cách xử |
|---|---|---|
| ssh trả **exit 255**, lệnh chết giữa chừng | `pkill -f` / `pgrep -f` khớp chính dòng lệnh ssh đang chạy | kill **theo PID**; bracket trick `[m]yjob` chỉ cứu nếu chuỗi gốc **không** xuất hiện ở chỗ khác trong cùng dòng lệnh (dính lại đúng kiểu này 14/09: một `rm` phía sau có chứa tên file) |
| Vòng `until ! pgrep -f "x"` không bao giờ thoát | dòng lệnh của chính vòng lặp chứa `x` | cùng cách xử như trên |
| ssh không thoát khi phóng job nền | quên `</dev/null`, ssh giữ channel chờ stdin | `nohup … > log 2>&1 </dev/null & disown` |
| `No such file or directory` khi gọi `venvs/venv/bin/python` | venv của pod GPU cũ | dựng lại bằng `uv` (mục 5) |
| Script `infra/**` chết ngay dòng đầu | `ROOT=/workspace/...` | đổi theo bảng quy đổi (mục 4) |
| Train chết `CUDA out of memory` giữa chừng | project khác vừa chiếm card | `vgate` trong script, chỉ vào card 0 MiB |
| Train chậm bất thường / treo khi nạp | 192 thread OMP trên quota 32 CPU | `OMP_NUM_THREADS=8` |
| Hai job cùng chết, không traceback | OOM-killer theo cgroup 512 GiB | giảm song song, không nạp hai model lớn cùng lúc |
| `No space left on device` | đĩa 94% đầy, dùng chung | dọn merged/dump theo mục 9 |
| Job nền không có log để xem tiến độ | `cmd \| tail -80` buffer toàn bộ | ghi thẳng ra file rồi `tail -f` file đó |
| `pip install … \| tail -3` báo thành công nhưng thiếu module | pipe nuốt lỗi build from source | không pipe output của pip; ưu tiên wheel dựng sẵn |
| `snapshot_download` gãy ở repo nhiều file | reset ngay khâu liệt kê metadata (GSMA/3GPP: 84.220 file) | `GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1`; hoặc bọc retry + `HF_HUB_ENABLE_HF_TRANSFER=1` (nhanh hơn curl ~20×, có resume) |
| `$VAR` rỗng trong script sinh bằng sed/heredoc | biến bị nội suy sớm trong heredoc nháy kép | viết bằng heredoc `<<'EOF'` |

## 9. Dọn dẹp: đi qua thùng rác, đừng `rm -rf` thẳng

Trên cùng một filesystem, `mv` là tức thời và **không giải phóng byte nào** cho tới khi thật sự xoá. Đó là điểm
mạnh: dồn hết vào một chỗ, kiểm tra hệ thống vẫn chạy, rồi mới xoá bằng một lệnh duy nhất.

```bash
T=~/projects/_trash_$(date +%Y%m%d); mkdir -p $T
mv <thứ-cần-bỏ> $T/
# … kiểm tra lại …
rm -rf $T
```

Đã dọn 14/09/2026 theo đúng mẫu này: 46 GB hai bản merge (`rl-grpo2/models/qwen3-8b-grpo-v2` 31 G fp32 và
`eval-v2/models/...-ckpt700` 16 G bf16). Bản fp32 vốn đã hỏng — hai rank DDP ghi đè lên nhau, chính
`50_merge_ckpt700.sh` ghi rõ nó **không** dùng thay được. Bản bf16 dựng lại bằng script đó từ
`results/ckpt_archive/checkpoint-700`. **Trước khi xoá, script dựng lại đã được đưa vào git** — xoá dữ liệu
dẫn xuất mà công thức tái lập chỉ nằm trên đĩa thì không phải dọn, là mất.

Với checkpoint: **adapter là bản gốc, merged là dữ liệu dẫn xuất.** Mỗi lần train sinh một adapter (0,3–4 GB)
và một bản merged (16 GB) = base + adapter. Giữ adapter, bỏ merged, dựng lại khi cần — và merge ở `bfloat16`,
một bản lỡ lưu `float32` chiếm 31 GB thay vì 16 GB. Trước khi bỏ bản merged nào, **kiểm tra adapter tương ứng
còn không**. Mẫu hai pha đã chạy thật: [`infra/cleanup_h200_20260905.sh`](infra/cleanup_h200_20260905.sh).

⚠ Bản `h200_use.md` cũ từng lập luận rằng toàn bộ 215 GB merged là bỏ được vì kết quả 94,21% official-864 chạy
trên `Qwen/Qwen3-8B` gốc. **Lập luận đó sai** — run 94,21% chạy trên trọng số GRPO; xem
[`infra/telelogs-repro9421/README.md`](infra/telelogs-repro9421/README.md). Đừng suy ra "không cần trọng số
fine-tune" từ một giá trị mặc định trong script; kiểm bằng log của chính run đó.

## 10. Kỷ luật dữ liệu thí nghiệm

Mỗi track mới dùng một workspace riêng `runs/<track>/`. **Không để tên file là cơ chế duy nhất** phân biệt dữ
liệu được phép train với dữ liệu test-derived — một glob `*.jsonl` sẽ hút nhầm chúng vào training job.

```text
runs/<track>/
├── code/                 # source đã có bản trong Git/local; không sửa chỉ trên server
├── data/
│   ├── eval/             # test/dev/heldout immutable; không dùng làm train input
│   ├── corpus/           # tài liệu nguồn hoặc retrieval artifacts
│   ├── train/{eligible,candidates}/   # eligible = hợp lệ cho candidate submit được
│   └── restricted/       # answer key, explanation, haystack, leakage control
├── models/{candidates,controls,closed}/
├── results/{submitted-grade,diagnostics,controls}/
├── jobs/  logs/
└── MANIFEST.tsv          # path, bytes, sha256, role, source, status, created_at
```

1. **Không train bằng glob rộng.** `TRAIN_DATA` phải là path tuyệt đối trong `data/train/eligible/`; script phải
   từ chối path nằm dưới `eval/` hoặc `restricted/`.
2. **Mỗi artifact mới cần provenance**: nguồn, script/version sinh ra, split, có test-derived hay không, trạng
   thái `candidate` / `control` / `closed` / `submitted`.
3. **Đo base cùng model-load với mọi adapter.** Kết quả adapter thiếu arm base cùng stack chỉ là diagnostic,
   không được gọi là gain/loss.
4. **Đóng experiment có bằng chứng**: giữ source, summary, per-row result và adapter cuối; checkpoint trung gian
   chỉ bỏ sau khi manifest + backup đã kiểm và chủ sở hữu đồng ý.
5. **Phân quyền là tín hiệu cần xử lý.** File root-owned hoặc không đọc được không được coi là backup thành công
   — ghi vào manifest là `inaccessible` và hỏi chủ sở hữu trước khi sửa permission.

## 11. Backup và sơ tán

**Server không được là bản duy nhất.** Đĩa đang 94% và mọi project dùng chung — một đợt dọn của người khác,
hay một lần pod bị dựng lại, là đủ mất. Tối thiểu phải có ở local: code, manifest, result/log, dữ liệu train tự
sinh khó tái tạo, và adapter candidate. Corpus/model công khai chỉ cần manifest + revision + checksum.

Không `scp` cả cây lớn một cách mù quáng. Lập manifest trước, stream archive sau, so `sha256` hai đầu:

```bash
ssh H200_Tensara 'cd ~/projects/telelogs/runs/<track> && \
  find code data/train results models/candidates -type f -printf "%s %p\n" | sort' > <track>-manifest.txt

ssh H200_Tensara 'tar czf - -C ~/projects/telelogs/runs/<track> code data/train results models/candidates' \
  > <track>-backup.tgz
sha256sum <track>-backup.tgz
```

**Không xoá hoặc di chuyển nguồn trong cùng lượt với backup.** So manifest/checksum trước đã. Khi đối chiếu hai
cây, so `md5` của danh sách `kích thước + đường dẫn` đã sắp xếp chứ không chỉ so tổng dung lượng:

```bash
find . -type f -printf "%s %P\n" | sort | md5sum
```

## 12. Di tích k8s — chỉ để đọc script cũ

Không còn lý do tạo pod GPU: GPU đã ở ngay trong dev pod. Các file `pod.yaml`, `*_pod.yaml`, `jobs/`, `done/`
rải trong `runs/**` là di tích của vòng trước — **giữ để đọc, đừng apply lại**. Phần này chỉ để giải mã chúng.

Quyền còn lại của serviceaccount `tensara-runner` (namespace `tensara`): create/delete/get pods, `pods/log`,
Service. Bị chặn: `pods/exec` (webhook chặn dù `kubectl auth can-i` trả lời **yes** — đừng tin can-i),
`pods/portforward`, `nodes`, `resourcequotas`, `persistentvolumeclaims`, `namespaces`.
`kubectl cp` chạy trên nền `exec` nên cũng vô dụng.

Kiến trúc cũ: pod GPU mount `hostPath /mnt/registry/tensara-home/projects/<proj>` (đường dẫn `/mnt/tensara-home`
là **bẫy** — một cây khác, rỗng) vào `/workspace/<proj>`, ghim `nodeName: hgx046` vì storage là node-local.
Dev pod và pod GPU chia sẻ đúng thư mục đó; vì không exec được, mọi điều khiển đi qua một **job-runner**: pod
chạy vòng lặp nhặt `jobs/*.sh`, đổi tên thành `done/<n>.running`, chạy, ghi `done/<n>.log`, kết thúc thành
`done/<n>.rc<mã>`; kill bằng `touch done/<n>.kill`. Đọc log runner bằng `kubectl logs <pod> -n tensara`.

Các bẫy chỉ liên quan tới thời đó, để hiểu vì sao script cũ viết như vậy:

- **Mount lồng trong mount read-only**: nếu mount `A` read-only tại `/workspace/x` rồi mount `B` tại
  `/workspace/x/sub` thì `sub` phải tồn tại sẵn trên host, không thì pod chết `RunContainerError … read-only
  file system`. Cách xử là dời thư mục dùng chung (venv, HF cache) lên gốc project rồi mount trở lại đúng vị trí
  cũ, **để lại thư mục rỗng làm neo** — vì thế trong các cây cũ có những thư mục rỗng cố ý.
- Pod chạy **root**, dev pod chạy uid 1000 → thư mục do pod tạo phải `chmod 777` dev pod mới ghi được.
- Image pull lần đầu ~7 phút; `ContainerCreating` lâu là bình thường.
- Sửa args trong `pod.yaml` phải `delete` rồi `apply` lại (Pod không update in-place).
- Đếm GPU: chỉ probe mới đáng tin — thả pod xin 1 GPU **không ghim `nodeName`** rồi đọc `.spec.nodeName`.
  Pod thường thua admission sẽ nằm chết ở `UnexpectedAdmissionError` vĩnh viễn.
- Chuyển dữ liệu giữa hai node khi `kubectl cp` không dùng được: pod HTTP (`python3 -m http.server`) mount
  read-only thư mục nguồn + Service ClusterIP, rồi `wget -r` từ login host (đã chạy: 36 GB / 46 s). `wget` để
  lại `index.html` mỗi thư mục — xoá trước khi đối chiếu manifest.
