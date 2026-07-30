# telelogs-rl — GRPO trên Qwen3-8B cho track tool-calling

Mục tiêu: dùng RL (GRPO, TRL) để dạy Qwen3-8B thực thi trung thành đúng luồng
quyết định đã chứng minh (gates → strong-C1 → residual C6>C4>C1 @142.5, trần
symbolic 96.88% dev-96). Nhắm thẳng vào khoảng cách ~24 điểm giữa trần và
thực thi LM hiện tại (72.92% dev).

## Thiết kế v1 (single-turn)

- **Prompt** (không bao giờ chứa nhãn): instruction phỏng theo seed11 + câu hỏi
  gốc + các observation JSON do tool tất định tính sẵn, theo đúng trạng thái
  thông tin của chương trình deploy hai giai đoạn:
  - case có gate kích hoạt (tính symbolic trên chính observation, không dùng
    nhãn): chỉ 4 observation giai đoạn 1;
  - case residual: đủ 6 observation.
- **Completion**: các dòng verification + `Final answer: Cx`.
- **Reward**: +1 nếu class cuối cùng khớp nhãn train, +0.05 nếu có dòng
  `Final answer:`. Nhãn CHỈ xuất hiện trong hàm reward — mô hình không bao giờ
  thấy nhãn, không có trace nào bị rationalize (rollout do chính policy sinh).
- **Data**: đúng hash-split train (~60% của raw train.json, ~1430 case).
  dev-96 chỉ để chọn; holdout-96 một lần; official-864 đóng băng.
- **LoRA** r=32 (giới hạn drift + tiết kiệm VRAM), KL beta 0.04, 8
  rollout/prompt qua vLLM server mode trên GPU thứ hai.
- **Eval**: merge LoRA → serve bằng vLLM trên pod này (service
  `telelogs-rl-vllm`, giả danh `Qwen/Qwen3-8B`) → chạy lại đúng chương trình
  b3 ReAct dev-96 từ client bench4 với `DSPY_API_BASE` trỏ sang. So sánh được
  1:1 với scoreboard hiện tại.

## Rủi ro đã biết (ghi nhận trước khi chạy)

1. **Lệch format train↔deploy**: train single-turn plain-text, deploy là ReAct
   multi-turn qua DSPy adapter. Kỳ vọng chuyển giao ở tầng kỹ năng (đọc số,
   verdict, stop-at-first) chứ không phải tầng format; đo bằng dev-96 thật.
2. **Phiên bản trl/vllm/torch**: venv riêng tự kéo torch (không dùng torch của
   image); cặp pin trl==0.21.0 + vllm==0.10.0 cần xác nhận lại lúc setup.
3. Case strong-C1 trong nhóm residual nhận đủ 6 observation dù luồng deploy
   có thể dừng ở 4 — chấp nhận, ghi chú để phân tích sau.

## Hạ tầng (v2 — ĐÃ launch 2026-07-30)

Khảo sát thực nghiệm 2026-07-30 (probe pod admission): hgx046 0 GPU trống
(kubelet "Available: 0" dù namespace tensara chỉ chiếm 3 — phần còn lại bị
namespace khác giữ), hgx45 0 trống, **hgx47 đúng 1 GPU trống** → pivot sang
thiết kế 1-GPU:

- Pod `telelogs-rl`: 1×H200 trên hgx47, image pytorch 2.8. Storage node-local
  `/mnt/tensara-home/projects/telelogs-rl` (registry storage của hgx046 KHÔNG
  share sang node khác — đã probe xác nhận cả hgx45 lẫn hgx47).
- Bootstrap: weights Qwen3-8B tải từ HF (hgx47 có internet); code+data kéo
  qua HTTP fileserver tạm trên bench4-client (job `zz_fileserver.sh`, tự tắt).
- Training: TRL vLLM **colocate** trên cùng GPU (`--vllm-mode colocate`,
  vLLM giữ 25% VRAM), chuỗi `run_all_v1.sh` = setup → dataset (assert số
  dòng) → train.
- Eval: `serve_grpo_model.sh` chiếm lại GPU đó sau khi train xong; service
  `telelogs-rl-vllm` định tuyến từ client bench4 qua mạng cụm.

## Checklist khi được lệnh launch

1. `kubectl apply -f config/pod.yaml && kubectl apply -f config/service.yaml`
2. Chép `setup_env.sh`, `make_grpo_dataset.py`, `grpo_train.py`,
   `run_grpo_v1.sh`, `serve_grpo_model.sh` vào
   `/mnt/registry/tensara-home/projects/telelogs-rl/` (qua tensara-dev-0).
3. Queue `jobs/setup_env.sh` → kiểm `done/setup_env.rc0` + version report.
4. Queue job build dataset → kiểm số dòng + tỉ lệ gated/residual khớp
   symbolic (874 gated / 567 residual trên train).
5. Queue `run_grpo_v1.sh` (vLLM rollout GPU1, trainer GPU0). Theo dõi
   `done/run_grpo_v1.log` (reward trend).
6. Sau train: queue `serve_grpo_model.sh` rồi queue eval dev-96 từ client
   bench4 (`jobs_staged/tools_dev96_grpo_v1.sh`).
