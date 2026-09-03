# telelogs-rl2 — GRPO v2, train lại sau khi mất checkpoint v1

## Bối cảnh

Trọng số GRPO v1 đã mất (xem [infra/telelogs-repro9421/](../telelogs-repro9421/)).
Rerun trên base cho 797/864 = 92,25% so với 814/864 = 94,21% của bản GRPO —
McNemar 28:11, p = 0,0095, nên phần GRPO đóng góp là thật và phải dựng lại.

Vì đằng nào cũng phải train lại, track này sửa những khuyết điểm **đo được** của
v1 thay vì chép y nguyên.

## Khuyết điểm v1 và cách xử lý

### 1. Prompt dạy thang presence đã bị thay thế — SỬA

`make_grpo_dataset.py` của v1 dạy thang presence (`minimum_difference_mbps ≥
142.5 → C3`, `equal_residue_pairs không rỗng → C6`, …). Nhưng pipeline đang ship
quyết định bằng thang **magnitude**. Đo bằng
[ladder_reward_alignment.py](ladder_reward_alignment.py) trên đúng split GRPO train:

```
train: 1441 case (783 gated, 658 residual)
gate ladder trên gated: 783/783 = 100%

nửa residual — làm đúng theo thang thì được thưởng:
  presence  (v1) : 516/658 = 78.42%
  magnitude (v2) : 598/658 = 90.88%

cả split:  presence 90.15%  →  magnitude 95.84%
```

Trên **142/658 prompt residual (21,6%)**, v1 thưởng cho việc *không* làm theo
hướng dẫn của chính nó. Reward và instruction chỉ ngược hướng nhau. v2 thay đúng
đoạn residual của `SYSTEM_PROMPT` bằng thang magnitude, giữ nguyên mọi thứ khác.

### 2. Reward chỉ nhìn kết quả, không nhìn phép tính — SỬA

v1 thưởng 1.0 cho nhãn đúng và 0.05 cho dòng `Final answer:`. Một rollout viết
bất đẳng thức sai mà vẫn đoán trúng lớp thì được củng cố cả cái sai đó. Đây
không phải giả định: policy v1 được đo viết `100.98 > 160 → not triggered` cho
tiêu chí C8 và chỉ đạt 2/12 về kỷ luật gate ở dạng single-turn.

v2 thêm `reward_arithmetic`: −0,05 mỗi bất đẳng thức sai số học, chặn ở −0,15,
dùng đúng `INEQ_RE`/`_holds` mà tầng audit lúc inference đã tin cậy. Cố ý hẹp —
**chỉ kiểm tra số học**, không kiểm tra có theo thang hay không, vì `RESIDUAL_SPECS`
trong verifier vẫn là ngưỡng presence cũ. Số học thì độc lập với thang và không
đụng tới nhãn, nên term này không thể rò đáp án.

### 3. Chọn checkpoint mù — SỬA

v1 lấy thẳng `final`. v2 lưu `save_steps=25`, `save_total_limit=8`, rồi chọn
checkpoint theo dev-96 qua đúng pipeline deploy, xác nhận một lần trên holdout.

### 4. Rollout bị bóp nghẹt trên 1 card — SỬA MỘT PHẦN

v1 chạy `--vllm-mode colocate`, engine rollout chỉ được 25% VRAM của chính card
đang train. v2 tách rollout ra card riêng ở 0.85 utilisation. Nhưng phép đo sau
đó cho thấy đây **không** phải nút thắt (xem mục Hạ tầng): trainer mới là.

### 5. DDP không chạy được với reentrant gradient checkpointing — SỬA

Vừa bật DDP là chết ở step 0:

```
RuntimeError: Expected to mark a variable ready only once.
```

v1 chạy 1 card nên không bao giờ gặp. Đã thêm
`gradient_checkpointing_kwargs={"use_reentrant": False}`. Lưu ý TRL chỉ gọi
`enable_input_require_grads()` trên nhánh reentrant — bỏ nhánh đó là đúng, vì
non-reentrant checkpointing tự xử lý input không require grad.

### 6. Batch của optimizer bị đổi 3× mà không ai chọn — SỬA

Scale lên 3 rank trainer làm `8 x 3 / 8 = 3 prompt/micro x grad_accum 4 = 12
prompt/step`, tức 1/3 số update của v1 ở cùng learning rate — một thay đổi siêu
tham số do số card quyết định chứ không do ai cân nhắc. Đã đổi về 2 rank +
`grad_accum 2` = **4 prompt/step, trùng khít v1** (1048 step).

### 7. `reward_correct` và `reward_format` bất đồng chỗ đọc đáp án — SỬA

v1 lấy `C[1-8]` cuối cùng ở bất kỳ đâu, trong khi `reward_format` đòi dòng
`Final answer:`. Rollout kết thúc bằng `Final answer: C4 (not C6)` bị chấm thành
C6 — bị phạt vì trả lời đúng. `extract_answer()` ưu tiên dòng đã khai báo, chỉ
fallback về cách cũ khi không có dòng đó.

### 8. `reward_format` chưa từng kích hoạt một lần nào trong v1 — SỬA

Bật `log_completions=True` rồi đọc completion thật thì thấy policy viết:

```
Final answer: \boxed{C4}
```

Nó với tới `\boxed{}` vì đó là thứ scorer chính thức của TeleLogs đọc. Pattern
của v1 — `Final answer:\s*C[1-8]` — đòi lớp đứng ngay sau dấu hai chấm nên không
đời nào khớp. Đo được: `rewards/reward_format/mean = 0.0`, `std = 0.0`, suốt cả
batch. Term định hướng format đó là **đồ trang trí trong toàn bộ v1**.

Hệ quả nặng hơn: `reward_correct` luôn phải rơi về nhánh "lấy `C[1-8]` cuối cùng
ở bất kỳ đâu" — nhánh đọc sai `"... C4, not C6"` thành C6. Bản GRPO đạt 94,21%
được train với reward chấm sai một phần rollout của chính nó.

Sửa không phải bằng cách vá thêm regex mà bằng cách cho reward đọc đáp án **y hệt
`full4_eval.py`**: `\boxed{...}` cuối cùng, chữ số đầu tiên. Extraction lúc train
và scoring lúc test thành cùng một luật, nên không thể trôi khỏi nhau.
`reward_format` cũng đổi nghĩa: thưởng khi completion kết thúc bằng thứ scorer
đọc được, không thưởng cho một cách viết cụ thể.

Sau khi sửa: `reward_format/mean = 0.0500` — 100% completion parse được.

### 9. Lệch train↔deploy: `/no_think` — KHÔNG SỬA ĐƯỢC, ghi nhận

Prompt train kết thúc bằng `/no_think`; lúc inference DSPy truyền
`chat_template_kwargs` chứ không có token đó. Đã kiểm tra: **cả trl 0.21.0 lẫn
0.23.1 đều không có `chat_template_kwargs` trên `GRPOConfig`** (chỉ `SFTTrainer`
có), nên nâng TRL không mua được gì mà lại đổi stack. Bỏ `/no_think` thì Qwen3
bật thinking và 94% completion bị cắt ở 768 token — hỏng reward. Giữ nguyên.

### 10. Lệch train↔deploy: single-turn vs ReAct multi-turn — KHÔNG SỬA, ghi nhận

Train là single-turn plain text, deploy là ReAct nhiều lượt qua DSPy adapter.
Bằng chứng nó có thật: demo *làm hại* trên trọng số GRPO (75,7% → 59,5%,
p = 3,9e-05) trong khi hoà trên base. Sửa tận gốc là train trên chính trajectory
ReAct — một project khác. Nhưng lưu ý: toàn bộ chênh lệch 28:11 nằm ở nửa
residual, mà residual specialist **là** một call single-turn, nên sửa số 1 ở trên
đã kéo phân phối train về đúng chỗ quyết định.

## Cố ý KHÔNG đổi

`num_generations=8`, `lr=1e-5`, `beta=0.04`, `temperature=0.9`, `epochs=2`,
LoRA r32/α64, `max_completion_length=768`, `residual-repeat=2`, và toàn bộ pin
thư viện. Không có bằng chứng nào cho việc đổi chúng, và đổi cùng lúc sáu thứ thì
kết quả không diễn giải được.

## Hạ tầng — 3 card, không phải 4

Pod `telelogs-rl2`, node **hgx046**, label `owner: h2n`, `restartPolicy: Never`,
**3× H200 nguyên con** (`pod_3gpu.yaml`): GPU 0–1 trainer, GPU 2 rollout.
k8s không cho chọn index vật lý — device plugin tự cấp và phơi ra thành 0–2, và
bộ card đổi sau mỗi lần dựng lại pod, nên UUID phải đọc từ
`kubectl logs telelogs-rl2 -n tensara` chứ đừng chép cứng vào đây.

Vì sao 3 chứ không 4, dù được cấp 4:

1. `--data_parallel_size 2` cho rollout **không chạy được**. vLLM xếp cả hai
   replica lên cùng một device; replica 0 chiếm 0.85 (127 GB), replica 1 vào
   thấy còn 15.9/139.8 GiB rồi chết. Rollout thực tế luôn là 1 card.
2. Đo lúc bản 4 card đang chạy: hai card trainer **100% ở 5/6 lần lấy mẫu**,
   card rollout **0% ở 6/6**. Workload này **trainer-bound**, nên thêm replica
   rollout có sống cũng không mua được gì.

Card thứ tư là lãng phí thuần tuý nên đã trả lại pool. Cấu hình chạy không đổi
một chữ: 2 trainer + 1 rollout, 1048 step, 4 prompt/optimizer step.

### Ước lượng tốc độ ban đầu đã sai — ghi lại để lần sau đừng lặp

Tôi đã đoán 4 card nhanh hơn 3–4× vì cho rằng GRPO nghẽn ở khâu sinh rollout.
Đo thật thì nghẽn ở trainer. Bài học: đừng hứa hệ số tăng tốc trước khi lấy mẫu
`utilization.gpu` của cả hai phía.

## Bố cục file

```
local:  infra/telelogs-rl2/
        ├── README.md
        ├── pod.yaml                     ← bản 4 card, giữ làm ghi chép
        ├── pod_3gpu.yaml                ← ĐANG DÙNG
        ├── ladder_reward_alignment.py   ← phép đo dựng nên quyết định số 1
        ├── make_grpo_dataset_v2.py      ← fork của v1, chỉ đổi thang residual
        ├── grpo_train_v2.py             ← fork của v1, thêm reward_arithmetic + 5,7
        └── jobs/
            ├── 20_setup_env.sh
            ├── 21_dataset.sh
            ├── 22_train_4gpu.sh         ← 1+3, chết vì reentrant DDP (mục 5)
            ├── 24_train_4gpu_v1batch.sh ← 2+2, chạy được, phí 1 card
            └── 25_train_3gpu.sh         ← ĐANG DÙNG: 2 trainer + 1 rollout

H200:   ~/projects/telelogs/runs/rl-grpo2/   (= /workspace/telelogs-rl trong pod)
        ├── data/{train.json,grpo_train_v2.jsonl}
        ├── checkpoints/v2/   models/qwen3-8b-grpo-v2/
        └── jobs/ done/ results/
```

Giữ đúng in-pod path `/workspace/telelogs-rl` để mọi default path trong script v1
resolve được mà không phải sửa.

**Bài học lưu trữ từ v1:** adapter là bản gốc, merged là dẫn xuất. Giữ
`checkpoints/`, merge lại khi cần bằng `merge_checkpoint.sh` ở **bfloat16**. Và
đừng bao giờ suy ra một run chạy trọng số nào từ `DSPY_MODEL` — `serve_grpo_model.sh`
cố ý giả danh base bằng `--served-model-name`.

## Chọn checkpoint trên holdout-479 — ĐÃ DỰNG, CHƯA CHẠY

> **Trạng thái:** hạ tầng và giao thức dựng xong, chạy được 2 phút thì dừng
> theo yêu cầu. **Không có số đo nào từ holdout-479.** Mục này mô tả thứ sẽ
> chạy nếu quay lại, không mô tả thứ đã chạy.

### Giao thức, ghi trước khi có kết quả

Ba checkpoint đã chấm trên official-864 (300/700/1048 → 809/814/808) **không**
khác nhau có ý nghĩa: 300 vs 700 cho p = 0,50, 700 vs 1048 cho p = 0,39. Lấy
814 nghĩa là lấy cực đại của ba lần rút nhiễu, rút ngay trên chính tập đang báo
cáo. Con số đó không còn là một ước lượng.

Nên toàn bộ 16 checkpoint lưu trong `results/ckpt_archive/`, cộng thêm base làm
mốc, được chấm trên **holdout-479** — 479 câu mà `split_for()` xếp vào holdout,
dựng bằng [make_holdout479.py](make_holdout479.py). Tập này chưa từng bị nhìn
thấy ở cả hai tầng: GRPO chỉ train trên split `train` (1441 câu), và chương
trình DSPy `bootstrap26_seeded11_b3` đã kiểm — **0/479 câu holdout xuất hiện
trong 4 demo đã compile**.

**Luật chọn, cố định trước khi đọc bất kỳ kết quả nào:**

> thắng = số câu đúng cao nhất trên holdout-479; hoà thì lấy step **nhỏ nhất**.

Checkpoint mà luật này gọi tên là checkpoint được báo cáo trên official-864.
Không có lượt nhìn thứ hai, không đổi luật sau khi thấy bảng.

Cùng script, cùng program đã compile, cùng bộ chấm boxed-int chính thức như mọi
con số ot-full trong repo — chỉ khác `--parquet` và `--suite`. Nếu hai tập được
chấm bằng hai đoạn code khác nhau thì phép so sánh đang đo đoạn code.

`--suite` là cờ mới thêm vào `champion_gsma_full.py`, mặc định giữ nguyên chuỗi
cũ nên mọi summary ot-full đã có vẫn khớp từng byte; nó chỉ tồn tại để summary
của holdout đừng tự nhận mình là ot-full.

### Điều holdout-479 làm được và không làm được

Nó **không** phân biệt nổi checkpoint 700 với 1048: chênh lệch giữa các
checkpoint cỡ 0,7pp, trên 479 câu là khoảng 3 câu. Việc nó làm được là khác:
cho một quy tắc chọn **độc lập với tập test**. Phép chọn không cần đúng, nó cần
không dùng thông tin từ official-864 — và như vậy con số ot-full báo cáo sau đó
mới là ước lượng không chệch của checkpoint ấy.

Holdout-479 **không** cân bằng lớp (C1..C8 = 54/61/70/57/72/44/69/52), trong khi
ot-full là 108 mỗi lớp. Đây là đánh đổi có chủ ý: mất một chút tương ứng giữa
hai hợp thành, đổi lại 479 câu thay vì 352 câu của lát cắt cân bằng.

### Hạ tầng

Pod `telelogs-holdsel`, node hgx046, **6× H200**, hàng đợi dùng chung 17 việc,
6 lane tự lấy việc kế tiếp khi rảnh. Merge bị chặn ở 2 việc cùng lúc bằng
semaphore hai khe `flock`: mỗi bản merge tốn ~16 GB RAM và ~16 GB đĩa, sáu bản
một lúc là 98 GB cả hai mà không nhanh hơn, vì wall-clock nằm ở 20 phút chấm
điểm chứ không ở 3 phút merge.

### Chạy lại thế nào

```
kubectl apply -f holdsel_pod.yaml            # 6x H200, hgx046
cp jobs/40_holdout479_select.sh  <eval-v2>/jobs/
```

Job có tính resume: checkpoint nào đã có `summary.json` thì bỏ qua, nên dừng
giữa chừng rồi chạy tiếp không mất việc đã làm. Cần sẵn trên server:
`<eval-v2>/data/holdout479.parquet` (đã upload) và bản
`champion_gsma_full.py` có cờ `--suite` (đã upload).

**Một thay đổi còn nằm trong artefact ship:** `--suite` là cờ mới trong
[champion_gsma_full.py](../telelogs-dspy-tools/champion_gsma_full.py). Mặc định
của nó trùng từng byte chuỗi cũ — đã đối chiếu với `summary.json` của các run
ot-full đã có — nên không truyền cờ thì hành vi không đổi một chút nào. Nếu
muốn bản nộp đúng nguyên trạng lúc đạt 94,21% thì revert 5 dòng đó, đổi lại là
job 40 sẽ hỏng.

## Serve lại ckpt-700 và chấm qua harness chính thức

### Vật thể bàn giao

Adapter `results/ckpt_archive/checkpoint-700` (349 MB, md5
`3a744a9363528220448107747b6719c0`) merge vào `Qwen3-8B` ở bf16, một tiến trình,
trên CPU — cùng cách đã merge để chấm 300/700/1048, và chính điều đó làm bản
merge này là cùng một vật thể với các con số kia. Ra 16,38 GB / 4 shard, có
assert dtype và kích thước trong [50_merge_ckpt700.sh](jobs/50_merge_ckpt700.sh)
vì một bản merge lỡ ra fp32 vẫn serve bình thường và âm thầm ngốn gấp đôi bộ nhớ.

**Không dùng `runs/rl-grpo2/models/qwen3-8b-grpo-v2`.** Nó 31 GB (TRL giữ master
weight fp32 dưới mixed precision) và hai rank DDP cùng ghi vào một thư mục.

### Pipeline nằm sau một URL OpenAI

`gsma-labs/satellite` có provider `open-local` cấu hình bằng đúng một biến
`VLLM_BASE_URL`, Inspect gọi nó qua OpenAI chat-completions, và **không có gì
kiểm tra cái gì đứng sau URL**. Nên cấu hình nộp — GRPO + tools — đặt sau URL đó
bằng [pipeline_openai_shim.py](pipeline_openai_shim.py), và bài chấm chính thức
đo đúng hệ thống sẽ deploy thay vì một forward pass mà deployment không bao giờ
thực hiện.

Hai quyết định trong shim đáng ghi:

- **Tham số sampling của người gọi được nhận rồi bỏ qua.** `temperature`,
  `top_p`, `max_tokens`, `enable_thinking` cấu hình một model trần; endpoint này
  là một chương trình có setting decode cố định của nó. Chiều theo một nửa số
  tham số sẽ làm endpoint hành xử khác nhau tuỳ harness nào gọi. Cái người gọi
  yêu cầu được echo lại trong `x_pipeline`.
- **Case lỗi trả 200 kèm completion không có `\boxed{}`**, để parser chính thức
  chấm sai. Trả 500 thì full4_eval retry 3 lần, biến một case hỏng thành ba.

### Pipeline KHÔNG tất định giữa các lần chạy

Cùng adapter, cùng program, cùng scorer, `temperature=0`:

```
job 34   TP=1   814/864 = 94.21%
job 51A  TP=2   811/864 = 93.87%

ghép cặp theo sample_index:
  both 797   old-only 17   new-only 14   neither 36
  McNemar 17:14,  p = 0.72
  pipeline đổi đáp án ở 33/864 câu
  per-class:  C1 -2   C3 +3   C4 -3   C6 -1
```

17:14 ở p = 0,72 là nhiễu chạy lại, không phải khác biệt hệ thống — 33 câu đổi
đáp án nhưng gần như triệt tiêu nhau. Nguyên nhân là batching liên tục của vLLM
và bậc tensor-parallel đổi thứ tự cộng dồn dấu phẩy động; đủ để lật một token, và
một token lật trong ReAct thì lật cả nhánh suy luận.

**Hệ quả cho bài nộp: con số báo cáo mang dải dao động ±3 câu (~0,35pp) khi chạy
lại.** 94,21% không phải hằng số.

Một lỗi cứng ở `sample_index 67`: model rơi vào vòng lặp lặp lại, vượt
`max_tokens=1000`, JSONAdapter của DSPy không parse được field `answer` → chấm
sai. Lần chạy cũ câu này không lỗi. Đây là failure mode có thật của greedy
decoding, không phải hạ tầng, và không sửa bằng cách nới `max_tokens` mà không
đo lại toàn bộ.

### Bậc tensor-parallel không phải nguyên nhân — chạy lại mới là

[53_tp1_control.sh](jobs/53_tp1_control.sh) giữ TP=1, khớp đúng job 34, để tách
hai thứ đã đổi cùng lúc:

```
TP=1 vs TP=1  (job 34 vs job 53)   814 vs 812   13:11   p = 0.84   24/864 đổi đáp án
TP=1 vs TP=2  (job 53 vs job 51A)  812 vs 811   16:15   p = 1.00   34/864 đổi đáp án
```

**p = 1,00 giữa TP=1 và TP=2 — bậc tensor-parallel không tốn gì.** Biên dao động
đã có sẵn ngay cả khi giữ nguyên mọi thứ: hai lần chạy TP=1 vẫn lệch 2 câu và
churn 24 câu. Nguồn là batching liên tục của vLLM chứ không phải cách chia
tensor, nên endpoint nộp bài **không bị ràng buộc vào bậc TP nào**.

### Bốn lần chấm cùng một vật thể

```
job 34   champion trực tiếp   TP=1   814/864 = 94.21%
job 53   champion trực tiếp   TP=1   812/864 = 93.98%
job 51A  champion trực tiếp   TP=2   811/864 = 93.87%
job 52   harness qua shim     TP=2   807/864 = 93.40%

mean 811.00/864 = 93.87%   sd 2.94 câu   range 807-814 (0.81pp)
```

Mọi cặp đều không phân biệt được (p từ 0,30 đến 1,00). Mỗi lần chạy có 24-37 câu
(~3-4%) đổi đáp án, chỉ là chúng gần như triệt tiêu nhau.

**Con số nên báo cáo là ~93,9% với dải ±0,4pp, không phải 94,21%.** 94,21 là bản
cao nhất trong bốn lần rút — và bản thân checkpoint đó cũng đang được chọn bằng
cách so ba checkpoint trên chính tập test (mục holdout ở trên, phần chưa chạy).
Hai tầng chọn-đỉnh chồng lên nhau.

Ghép cặp bằng [pair_runs.py](pair_runs.py). Một bẫy trong chính công cụ này:
`champion_gsma_full.py` ghi `pipeline_answer` là `"C4"` còn `full4_eval.py` ghi
`parsed_answer` là `"4"`, nên so chuỗi thô làm cả 864 dòng trông như bất đồng.
Đó là lỗi của phép so, không phải phát hiện về các run.

### Cửa CDI, để CHỌN card chứ không phải để có card

Device plugin còn slot, nhưng pod xin qua nó bị cấp card tuỳ ý rồi đánh số lại
0..N-1 và không biết mình cầm card host nào. `runtimeClassName: nvidia` +
`NVIDIA_VISIBLE_DEVICES=all` phơi cả 8 card theo **chỉ số host thật**, nên
`CUDA_VISIBLE_DEVICES` ghim được đúng card được phép dùng.
[serve700_pod.yaml](serve700_pod.yaml). Cô lập là mềm: container thấy cả 8 card,
nên job nào quên `CUDA_VISIBLE_DEVICES` sẽ chạy lên card của người khác.
