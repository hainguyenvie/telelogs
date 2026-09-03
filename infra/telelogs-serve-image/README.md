# telelogs-serve-image — đóng gói dịch vụ chẩn đoán TeleLogs thành một image

## Cái này đóng gói cái gì

Không phải một model. Là **hệ thống**: trọng số GRPO v2 checkpoint-700 cộng
pipeline DSPy ReAct + 6 tool, đặt sau **một** URL `/v1/chat/completions` để
`gsma-labs/satellite` chĩa `VLLM_BASE_URL` vào là chấm được y hệt như đang chạy
trên H200.

Vì sao ghép được như vậy: provider `open-local` của satellite cấu hình bằng đúng
một biến `VLLM_BASE_URL`, Inspect gọi nó qua OpenAI chat-completions, và **không
có gì kiểm tra cái gì đứng sau URL**. Nên scaffold nằm trong image, và bài chấm
đo đúng hệ thống sẽ deploy chứ không phải một forward pass mà deployment không
bao giờ thực hiện.

## Hai cổng, và vì sao chỉ một cổng được publish

```
satellite ──▶ :8000  shim (pipeline_openai_shim.py)   ← publish cổng này
                       │  DSPy ReAct + 6 tool, ~12 lượt gọi LM mỗi câu
                       ▼
              127.0.0.1:8100  vLLM engine (ckpt-700 đã merge)   ← không publish
```

Engine cố tình **không** ra ngoài. Ai gọi thẳng vào nó sẽ nhận model trần và điểm
thấp hơn khoảng 30 điểm — chính sự nhầm lẫn đó là thứ bố cục này ngăn lại.

## Repo này PHẢI để private

Image mang theo `data/raw_train_2400/train.json` và `data/official_test_864/test.json`.
TeleLogs yêu cầu không phát tán công khai bộ benchmark; REPRODUCING.md cho phép
commit chúng trong repo này **chỉ vì repo là private**, và điều kiện đó đi theo
image. Tạo repo trên Docker Hub ở chế độ private **trước** lần push đầu — push
vào một repo chưa tồn tại sẽ tạo nó thành **public**.

## Chạy

```bash
docker login -u hainh67
docker run --gpus all -p 8000:8000 hainh67/telelogs-serve:ckpt700
```

Đợi dòng `=== READY ===`, rồi:

```bash
export VLLM_BASE_URL=http://localhost:8000/v1
export VLLM_API_KEY=local
curl $VLLM_BASE_URL/models
```

Model id là `telelogs-ckpt700`.

Entrypoint không báo READY cho đến khi nó đã **chạy thật một ca chẩn đoán qua
toàn bộ stack**. Một shim trả `/health` OK nhưng nạp hỏng program đã compile thì
vẫn trông khoẻ mạnh và chấm sai mọi câu; self-test đó chặn đúng trường hợp này.

## Yêu cầu phần cứng

Một GPU ≥ 24 GB. Trọng số bf16 chiếm 16,4 GB; phần còn lại là KV cache.

```
KV cache = 2 × 36 layer × 8 KV-head × 128 dim × 2B = 144 KiB mỗi token
A40 40 GB @ util 0.90 → 19.6 GB cho KV ≈ 36 sequence 4k token đồng thời
```

Pipeline chạy 8 worker nên dư xa. Cần NVIDIA Container Toolkit (`--gpus all`).

Chỉnh qua biến môi trường nếu card nhỏ hơn:

```bash
docker run --gpus all -p 8000:8000 \
  -e VLLM_GPU_MEM_UTIL=0.85 -e VLLM_MAX_NUM_SEQS=32 \
  hainh67/telelogs-serve:ckpt700
```

`VLLM_TP` mặc định 1. Đã đo trên H200: TP=1 với TP=2 cho McNemar 16:15, **p = 1,00**
— bậc tensor-parallel không đổi điểm, nên không cần chỉnh vì lý do chất lượng.

## Điểm nên kỳ vọng, và vì sao là một dải

Đo trên H200 (SM 9.0), cùng adapter, cùng program, `temperature=0`, official
boxed-int scorer trên ot-full 864 câu:

```
job 34   champion trực tiếp   TP=1   814/864 = 94.21%
job 53   champion trực tiếp   TP=1   812/864 = 93.98%
job 51A  champion trực tiếp   TP=2   811/864 = 93.87%
job 52   harness qua shim     TP=2   807/864 = 93.40%
mean 811.00 = 93.87%   sd 2.94 câu   range 0.81pp
```

Mọi cặp không phân biệt được (p 0,30–1,00); 24–37 câu (~3–4%) đổi đáp án mỗi lần
chạy nhưng gần như triệt tiêu nhau. Nguồn là batching liên tục của vLLM đổi thứ
tự cộng dồn dấu phẩy động; một token lật thì lật cả nhánh ReAct.

**Trên A40 con số sẽ rơi trong dải này nhưng không trùng đúng.** A40 là Ampere
(SM 8.6) chứ không phải Hopper, kernel khác nên số học khác. Và A40 chậm hơn
H200 khoảng 3 lần: chấm 864 câu mất ~2 tiếng thay vì 40 phút.

Cũng lưu ý 94,21% mang hai tầng chọn-đỉnh chồng nhau: cao nhất trong bốn lần
chạy, và checkpoint 700 được chọn bằng cách so ba checkpoint trên chính tập
test. Con số nên báo cáo là **~93,9% ± 0,4pp**.

## Tự chấm lại ngay trong container

Đây mới là thứ bảo chứng accuracy — không phải lời hứa của tôi mà là số bạn tự đo:

```bash
docker run --gpus all hainh67/telelogs-serve:ckpt700 verify
```

Nó dựng đúng stack đó, chạy **harness chính thức** (`full4_eval.py`, track
telelogs) qua shim trên đủ 864 câu, in điểm, rồi thoát. Đúng đường đã cho
807/864 trên H200, nên hai con số so được với nhau mà không phải tranh cãi về
phương pháp. Kết quả in kèm dải đã đo và nói thẳng con số vừa ra có nằm trong đó
không.

~2 tiếng trên A40, ~40 phút trên H200. Chỉnh song song bằng `VERIFY_WORKERS`.

## Đo thật trên máy đích — 817/864 = 94,56%

Chạy 2026-08-26 trên A100-SXM4-40GB (SM 8.0), trong chính container này, bằng
harness chính thức gọi qua shim. `errors: 0`, hết 5.676 s (94 phút), 9,13 câu/phút
với 8 worker.

```
H200 job 34   champion trực tiếp  TP=1        814/864
H200 job 53   champion trực tiếp  TP=1        812/864
H200 job 51A  champion trực tiếp  TP=2        811/864
H200 job 52   harness qua shim    TP=2        807/864
A100 container, harness qua shim              817/864   <- cao nhất
mean 812.20/864 = 93.98%   sd 3.70 câu   range 807-817
```

**Đừng đọc 817 là "A100 tốt hơn H200".** Năm con số này là năm lần rút từ cùng
một phân phối; kiến trúc GPU khác làm số học khác nên ra một lần rút khác, lần
này rơi phía cao. Chạy lại trên chính A100 đó sẽ ra một số khác trong khoảng ấy.

Thứ nó **thực sự** chứng minh: stack trong container khớp với thứ đã đo trên
H200. Không có gì mất trong lúc đóng gói. Nếu `program.json` nạp hỏng thì điểm
đã rơi xuống vùng 70-80%, không phải 94,56%.

Đã kiểm riêng bản compile có nạp đúng không, vì DSPy cảnh báo lệch python
(compile ở 3.11, venv image là 3.12):

```
inner.inner.react.react            demos=2  instructions=5674 chars
inner.inner.react.extract.predict  demos=2  instructions=3414 chars
residual                           demos=0  instructions=1778 chars
```

Khớp `program.json` gốc (đúng hai danh sách demo, mỗi cái 2 phần tử; `residual`
chạy bằng instruction, không có demo). Cảnh báo python vô hại ở đây — không cần
dựng lại venv bằng 3.11.

## Trong image có gì

```
/opt/model/                      trọng số bf16 đã merge (16,4 GB, 4 shard)
/opt/pipeline/code/              TOÀN BỘ nhánh tool: 14 module + 13 seed prompt
/opt/pipeline/program.json       program DSPy đã compile (bootstrap26_seeded11_b3)
/opt/pipeline/harness/           full4_eval.py — bộ chấm chính thức
/opt/pipeline/champion_gsma_full.py, official_telelogs_eval.py, verify_summary.py
/opt/pipeline/venv/              dspy 3.2.1 + litellm 1.93.0, TÁCH khỏi venv vLLM
/opt/data/ot-full/telelogs/      parquet ot-full 864 câu
/opt/data/official_test_864/     test.json
/opt/data/raw_train_2400/        train.json
/opt/entrypoint.sh
```

Chỉ 7 trong 14 module nằm trên đường chạy — `neutral_tools`, `tool_program`,
`specialist_program`, `forced_program`, `verifier`, `narrate`, `compare_program`.
Bảy cái còn lại (optimiser, audit CLI, seed prompt đã hiệu chỉnh) **không đổi một
câu trả lời nào**, chúng có mặt vì image là vật bàn giao: chúng làm program đang
serve có thể dựng lại được chứ không chỉ chạy được.

Pipeline lúc chạy không đọc file dữ liệu nào: nhận câu hỏi qua tham số, parse
bằng `neutral_tools.parse_case`, tính toàn bộ đo đạc từ chính câu hỏi đó. Ba thư
mục `data/` chỉ phục vụ `verify` và việc dựng lại.

## Vì sao pin `vllm/vllm-openai:v0.10.0`

Không phải để gọn. vLLM quyết định dải dao động ±0,4pp của điểm, và một minor
version khác sẽ dịch dải đó đi một lượng không ai biết. DSPy nằm ở venv riêng vì
`dspy` kéo `litellm`, mà `litellm` pin `openai` và `pydantic`; hoà giải chúng với
pin riêng của vLLM trong một interpreter là cách một image serving âm thầm chạy
client library khác với bản đã được đo.

## Build

Xem [BUILD.md](BUILD.md).
