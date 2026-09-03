# TeleLogs API — hướng dẫn sử dụng

Endpoint chẩn đoán nguyên nhân gốc cho log drive-test 5G. Nói giao thức OpenAI
chat-completions, nên router gọi nó **y hệt gọi một model**.

Bên trong không phải một model. Mỗi request chạy qua pipeline DSPy ReAct với 6
tool đo đạc, khoảng 7–15 lượt gọi LM nội bộ, rồi một tầng audit kiểm lại số học
trước khi chốt đáp án. Người gọi không cần biết điều đó.

## Địa chỉ

```
POST  http://127.0.0.1:20503/v1/chat/completions
GET   http://127.0.0.1:20503/v1/models
GET   http://127.0.0.1:20503/health
```

Chỉ nghe trên loopback. Router chạy bằng Docker cùng network `telco` thì gọi
`http://telelogs:20503/v1` theo tên container.

## Gửi cái gì

```json
{
  "model": "Qwen3-8B-Telco",
  "messages": [
    {"role": "user", "content": "Analyze the 5G wireless network drive-test..."}
  ]
}
```

Một message `user` duy nhất, chứa nguyên văn câu hỏi — đề bài, 8 phương án C1–C8,
bảng drive-test, bảng tham số trạm. Đúng định dạng trong `data/telelogs.jsonl`.

**Ba thứ nhận rồi bỏ qua**, có chủ ý:

| trường | vì sao bỏ qua |
|---|---|
| `temperature`, `top_p`, `top_k`, `seed`, `max_tokens` | Đây là một chương trình có setting decode cố định, không phải model trần. Chiều theo tham số của người gọi sẽ làm endpoint hành xử khác nhau tuỳ ai gọi. |
| `chat_template_kwargs` | như trên |
| system prompt đứng trước | Chỉ dẫn đã compile sẵn trong `program.json`. Chèn thêm chỉ gây nhiễu. Shim lấy message `user` **cuối cùng**. |

Cái người gọi yêu cầu được ghi lại trong `x_pipeline.caller_sampling_ignored` để
log luôn thấy đã bỏ qua gì.

**`stream: true` chưa hỗ trợ** — luôn trả non-stream.

## Nhận lại cái gì

```json
{
  "id": "chatcmpl-telelogs-000042",
  "object": "chat.completion",
  "created": 1787660000,
  "model": "telelogs-ckpt700",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "<narrative>\n\n[Audited evidence]\n...\n\nFinal answer: \\boxed{2}"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
  "x_pipeline": {"pipeline_answer": "C2", "lm_calls": 7, "elapsed_seconds": 18.4, ...}
}
```

`content` gồm ba phần theo thứ tự:

1. **Narrative** — chẩn đoán bằng văn xuôi kỹ thuật, nêu nguyên nhân và lý do
   loại phương án cạnh tranh mạnh nhất.
2. **`[Audited evidence]`** — các bất đẳng thức đã được kiểm số học, dạng
   `C2: maximum_distance_km = 3.19 > 1 → triggered`. Kỹ sư kiểm lại được kết
   luận thay vì phải tin.
3. **`Final answer: \boxed{N}`** — N là 1..8. Đây là thứ bộ chấm đọc: `\boxed{}`
   **cuối cùng**, lấy số nguyên đầu tiên trong đó.

Lấy đáp án bằng cách parse `content`, hoặc đọc `x_pipeline.pipeline_answer`
(dạng `"C2"`) nếu tiện hơn.

## Lỗi trông như thế nào

**Không có HTTP 500.** Một case pipeline xử lý hỏng vẫn trả **200** với `content`
không chứa `\boxed{}` — bộ chấm chính thức tính là sai, đúng như nó phải vậy.
Trả 500 sẽ khiến harness retry 3 lần, biến một case hỏng thành ba.

Nhận biết: `'\\boxed{' not in content`, hoặc `x_pipeline.error` có giá trị.

Tần suất đo được: **1 trên 864**. Nguyên nhân là greedy decoding rơi vào vòng
lặp lặp lại tới khi chạm trần token nên dòng đáp án không bao giờ được viết ra.
Có ở mọi lần chạy, kể cả trên H200.

400 chỉ xảy ra khi request không có message `user` nào có nội dung.

## Hiệu năng

Đo trên A100-SXM4-40GB, card đơn:

| | |
|---|---|
| một request lẻ | 18–35 giây |
| 8 request song song | 9,1 câu/phút |
| 864 câu | 94 phút |
| lượt gọi LM mỗi câu | 7–15 |
| KV cache | 145.024 token, ~36 sequence đồng thời |

Chậm là do bản chất: mỗi câu là hơn chục lượt gọi model tuần tự, không phải một
lượt. Đừng đặt timeout dưới 120 giây.

## Độ chính xác

**817/864 = 94,56%** trên official-864, đo bằng harness chính thức
(`full4_eval.py`, track telelogs) gọi qua chính endpoint này, `errors 0`.

Nhưng con số đúng là một **dải**, không phải một điểm:

```
H200  814 / 812 / 811 / 807        A100 (chính container này)  817
mean 812.2/864 = 94.00%   sd 3.7 câu
```

Năm lần chạy cùng một vật thể. Mọi cặp không phân biệt được về mặt thống kê, và
mỗi lần chạy có 24–37 câu đổi đáp án nhưng gần như triệt tiêu nhau. Nguyên nhân
là batching liên tục của vLLM đổi thứ tự cộng dồn dấu phẩy động — một token lật
thì lật cả nhánh suy luận. **Báo cáo ~94% ± 0,5pp**, đừng báo một con số cụ thể.

Tự đo lại bất cứ lúc nào:

```bash
docker exec -d \
  -e BENCH4_SCRIPTS=/opt/pipeline/harness -e OFFICIAL_RUN_DIR=/tmp/verify864 \
  -e OFFICIAL_MODEL=telelogs-ckpt700 -e OT_FULL_ROOT=/opt/data/ot-full \
  -e VLLM_CHAT_URL=http://127.0.0.1:20503/v1/chat/completions -e EVAL_WORKERS=8 \
  telelogs sh -c '/opt/pipeline/venv/bin/python /opt/pipeline/official_telelogs_eval.py > /tmp/verify864.log 2>&1'

docker exec telelogs tail -f /tmp/verify864.log
docker exec telelogs /opt/pipeline/venv/bin/python /opt/pipeline/verify_summary.py /tmp/verify864
```

Nó dùng lại engine đang chạy nên không chiếm thêm GPU.

## Vận hành

```bash
docker compose up -d           # bật
docker compose logs -f         # chờ dòng === READY ===
docker compose restart         # khởi động lại (nạp lại trọng số, ~2 phút)
docker compose down            # tắt, trả GPU
```

Container **chưa sẵn sàng khi vừa start**. Nó nạp 15,3 GB trọng số rồi chạy thử
một ca chẩn đoán trước khi in `=== READY ===`. Trước đó gọi vào sẽ bị từ chối kết
nối. Healthcheck có `start_period: 600s` để Docker không tuyên bố unhealthy oan.

Đổi card, đổi port, chạy nhiều GPU: xem [docker-compose.yml](docker-compose.yml),
mọi tham số đều là biến môi trường.

## Giới hạn đã biết

- `stream: true` chưa hỗ trợ.
- `usage` luôn trả 0 — bản shim trong image này chưa đếm token nội bộ.
- Field `model` trả về là `telelogs-ckpt700`, không echo lại tên người gọi hỏi.
- 1 trên 864 câu trả về không có `\boxed{}` và bị chấm sai.
- Chỉ nhận câu hỏi TeleLogs. Câu thuộc benchmark khác sẽ làm `parse_case` báo
  `engineering table separator is missing` và trả về completion không đóng hộp.

Ba cái đầu đã có bản vá trong repo, chưa đưa vào image.
