# Chạy TeleLogs pipeline trên Windows PowerShell

## 1. Yêu cầu

- Windows PowerShell.
- Python 3.12. Repo đã được kiểm tra với Python 3.12.1.
- Một endpoint tương thích OpenAI:
  - đúng cấu hình tái lập: `Qwen/Qwen3-8B` chạy bằng vLLM;
  - smoke test thay thế: model NetMind được tài khoản của bạn cấp quyền.

Không dùng Python 3.13 trên máy này: bản cài hiện tại thiếu `venvlauncher.exe`.

## 2. Tạo môi trường và cài package

Từ thư mục gốc repo:

```powershell
Set-Location D:\another-telelogs\telelogs
& 'C:\Users\Minh\AppData\Local\Programs\Python\Python312\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dspy.txt
```

Kiểm tra:

```powershell
.\.venv\Scripts\python.exe -c "import dspy; print(dspy.__version__)"
.\.venv\Scripts\python.exe -m pip check
```

## 3. Tạo deterministic facts trên CPU

```powershell
.\.venv\Scripts\python.exe infra\telelogs-dspy\prepare_repro_data.py `
  --train-json data\raw_train_2400\train.json `
  --train-output artifacts\telelogs-dspy\data\telelogs_train_splits.jsonl `
  --official-json data\official_test_864\test.json `
  --official-output artifacts\telelogs-dspy\data\telelogs_official_facts.jsonl
```

Kết quả mong đợi: train 1.441, dev 480, holdout 479 và official 864.

Kiểm tra ngưỡng C3:

```powershell
.\.venv\Scripts\python.exe infra\telelogs-dspy\calibrate_residual_threshold.py `
  artifacts\telelogs-dspy\data\telelogs_train_splits.jsonl
```

Ngưỡng mong đợi là `142.5 Mbps`.

Kiểm tra các nhánh deterministic mà không gọi API:

```powershell
.\.venv\Scripts\python.exe infra\telelogs-dspy\smoke_local.py `
  artifacts\telelogs-dspy\data\telelogs_train_splits.jsonl
```

## 4. Chạy smoke test với NetMind

Tạo `.env` từ `.env.example`, điền API key, rồi nạp biến môi trường mà không
in key ra terminal:

```powershell
$envLines = Get-Content .env
foreach ($envLine in $envLines) {
  if ($envLine -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
    $envValue = $matches[2].Trim().Trim('"').Trim("'")
    [Environment]::SetEnvironmentVariable($matches[1], $envValue, 'Process')
  }
}
```

Kiểm tra an toàn bằng fact sheet tổng hợp, không gửi dữ liệu TeleLogs:

```powershell
$env:DSPY_API_KEY = $env:NETMIND_API_KEY
.\.venv\Scripts\python.exe infra\telelogs-dspy\smoke_synthetic.py
```

Chỉ chạy đánh giá dưới đây khi bạn chấp nhận gửi các fact sheet được suy ra từ
dữ liệu TeleLogs đến dịch vụ model bên ngoài:

```powershell

$env:DSPY_ROOT = (Resolve-Path artifacts\telelogs-dspy).Path
$env:DSPY_DATA = (Resolve-Path artifacts\telelogs-dspy\data\telelogs_train_splits.jsonl).Path

Set-Location infra\telelogs-dspy
..\..\.venv\Scripts\python.exe run_stage.py calibrated_prompt `
  --run-name netmind_smoke `
  --eval-per-label 1 `
  --workers 2 `
  --max-tokens 600 `
  --c3-advantage-threshold-mbps 142.5
Set-Location ..\..
```

Output nằm tại `artifacts\telelogs-dspy\results\netmind_smoke`.

Model NetMind khác `Qwen/Qwen3-8B` chỉ dùng để kiểm tra pipeline chạy thông.
Không so accuracy của nó với kết quả chuẩn trong README.

## 5. Chạy đánh giá đầy đủ

Khi endpoint đúng `Qwen/Qwen3-8B` đã sẵn sàng:

```powershell
$env:DSPY_MODEL = 'openai/Qwen/Qwen3-8B'
$env:DSPY_API_BASE = 'http://127.0.0.1:8000/v1'
$env:DSPY_API_KEY = 'local'
$env:DSPY_ROOT = (Resolve-Path artifacts\telelogs-dspy).Path
$env:DSPY_DATA = (Resolve-Path artifacts\telelogs-dspy\data\telelogs_train_splits.jsonl).Path

Set-Location infra\telelogs-dspy
..\..\.venv\Scripts\python.exe run_stage.py calibrated_prompt `
  --run-name calibrated_prompt_boolean_dev224 `
  --eval-per-label 28 `
  --eval-offset-per-label 4 `
  --workers 16 `
  --max-tokens 1200 `
  --c3-advantage-threshold-mbps 142.5
Set-Location ..\..
```

Để đánh giá cả 2.400 mẫu phát triển bằng model đang cấu hình:

```powershell
Set-Location infra\telelogs-dspy
..\..\.venv\Scripts\python.exe run_stage.py calibrated_prompt `
  --run-name netmind_qwen35_9b_train2400 `
  --eval-split all `
  --eval-per-label 0 `
  --workers 16 `
  --max-tokens 600 `
  --c3-advantage-threshold-mbps 142.5
Set-Location ..\..
```

Mỗi run tạo:

- `program.json`: prompt/program DSPy;
- `summary.json`: accuracy, confusion, latency và decision path;
- `predictions.jsonl`: dự đoán, reasoning, lỗi và thời gian từng mẫu.

## 6. Lỗi thường gặp

- `Connection refused`: endpoint chưa chạy hoặc `DSPY_API_BASE` sai.
- `401/403`: `DSPY_API_KEY` sai hoặc model chưa được cấp quyền.
- Model không tồn tại: chạy `list_netmind_models.py` để lấy đúng model ID.
- Kết quả khác mốc README: kiểm tra checkpoint, DSPy, temperature, thinking,
  split và ngưỡng C3; kernel/vLLM khác vẫn có thể tạo sai khác hiếm.
