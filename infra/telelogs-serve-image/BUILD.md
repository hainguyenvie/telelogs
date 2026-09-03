# Build và push image

## Vì sao build trên cluster chứ không phải máy local

Đã đo, không phải phỏng đoán:

| | |
|---|---|
| `docker` trên dev pod `tensara-dev-0` | **không có** — cả `podman`, `buildah`, `nerdctl`, và không có `/var/run/docker.sock` lẫn containerd socket |
| `docker` trên jump host | **không có**, và chỉ còn 8,7 GB đĩa |
| đường tới node ngoài k8s | **không có** — jump host không resolve được `hgx046` |
| RBAC | `create pods` ✅ · `pods/exec` ❌ · `daemonsets` ❌ |
| link H200 → local | **629 KB/s** (100 MB trong 2 phút 39, đã tắt nén, cipher aes128-gcm) |
| RAM máy local | 14 GB tổng, trống 5 GB |

Kéo 16,4 GB trọng số về máy local mất **~7 tiếng**, rồi còn đẩy ~30 GB lên
Docker Hub từ đường truyền nhà. Trong khi trọng số **đã nằm sẵn trên đĩa của
node**, và cluster có đường đi internet riêng.

Không có `pods/exec` và không có `daemonsets` nên docker-in-docker kiểu privileged
không khả thi — nhưng cũng không cần: **kaniko** build và push thẳng từ một pod
thường, không daemon, không socket, không privileged.

## Điều kiện tiên quyết

**1. Cluster phải khoẻ.** Lúc viết tài liệu này nó đang hỏng:

```
kubectl get --raw /readyz
  [-]log failed: reason withheld
  readyz check failed
```

Triệu chứng: đọc pod vẫn được, **tạo** pod nào cũng `Timeout: context deadline
exceeded` — kể cả `pause:3.9`, `alpine`, `pytorch/pytorch`. Kiểm trước khi làm gì:

```bash
ssh H200_Tensara 'kubectl get --raw /readyz 2>&1 | grep -E "^\[-\]|readyz"'
```

Không in ra gì nghĩa là đã khoẻ.

**2. Repo phải được tạo PRIVATE trước.** Push vào một repository chưa tồn tại
sẽ tạo nó ở chế độ **public** — mà image này mang theo `train.json` và
`test.json` của TeleLogs. Vào hub.docker.com, tạo `hainh67/telelogs-serve`,
chọn Private, rồi mới build.

**3. Token Docker Hub.** Đừng dùng lại PAT đang nằm trong `~/.docker/config.json`
ở máy local. Tạo một token mới, scope **Read & Write** cho đúng repo đó, dùng
xong thu hồi:

```bash
# tạo Secret (chạy từ dev pod, thay <TOKEN>)
kubectl create secret docker-registry telelogs-dockerhub \
  --docker-server=https://index.docker.io/v1/ \
  --docker-username=hainh67 \
  --docker-password='<TOKEN>' \
  -n tensara
```

Secret là namespace-scoped, và namespace `tensara` có nhiều người dùng chung.
Vì thế: token scope hẹp, **xoá Secret ngay sau khi push**, và thu hồi token.

```bash
kubectl delete secret telelogs-dockerhub -n tensara
```

**4. Nguồn build.**

**Đã đẩy xong** lên `~/projects/telelogs/runs/image-build/src/` với checksum đã
đối chiếu. Nội dung:

```
Dockerfile  entrypoint.sh  program.json
pipeline_openai_shim.py  champion_gsma_full.py
official_telelogs_eval.py  verify_summary.py
code/      27 file — 14 module nhánh tool + 13 seed prompt đã hiệu chỉnh
harness/   full4_eval.py, bộ chấm chính thức
data/      ot-full/telelogs/*.parquet, official_test_864/test.json,
           raw_train_2400/train.json   (md5 đã khớp với repo)
```

Chỉ 7/14 module nằm trên đường chạy. Bảy cái còn lại không đổi câu trả lời nào —
chúng có mặt để program đang serve dựng lại được, không chỉ chạy được.

## Bẫy DNS — đã vấp và đã vá sẵn trong yaml

Lần build đầu chết ngay ở bước kéo base image:

```
error building image: unable to complete operation after 0 attempts, last error:
Get "https://index.docker.io/v2/": dial tcp: lookup index.docker.io on
10.96.0.10:53: read udp ...->10.96.0.10:53: i/o timeout
```

Không phải kaniko, không phải registry: **DNS của cluster (10.96.0.10) không trả
lời**. Kubelet kéo được image vì nó dùng resolver của node, còn pod thì không.
`tensara-dev-0` ra được internet chỉ vì nó mang sẵn `dnsConfig` bổ sung, và cùng
khối đó đã được thêm vào cả `build_pod.yaml` lẫn `build_pod_dry.yaml`:

```yaml
  dnsConfig:
    nameservers: ["8.8.8.8", "1.1.1.1"]
    options:
      - {name: timeout, value: "1"}
      - {name: attempts, value: "1"}
```

`timeout 1 / attempts 1` là phần quan trọng: nó làm resolver chết bị bỏ qua sau
một giây thay vì treo. Thiếu khối này thì cả `pip install` cũng chết y hệt.

Sau khi vá, build đi qua được bước pull và vào tới `Unpacking rootfs`.

## Build

```bash
ssh H200_Tensara 'cat > /tmp/build_pod.yaml' < infra/telelogs-serve-image/build_pod.yaml
ssh H200_Tensara 'kubectl apply -f /tmp/build_pod.yaml'
ssh H200_Tensara 'kubectl logs -f telelogs-imgbuild -n tensara'
```

Xong khi thấy `BUILD_PUSH_DONE`.

### Ba tham số kaniko không được bỏ, và lý do

- `--compressed-caching=false` — mặc định kaniko giữ nội dung layer đã nén trong
  RAM. Layer trọng số 16 GB sẽ làm pod OOM.
- `--snapshot-mode=redo` — bỏ qua hashing toàn bộ filesystem sau mỗi lệnh.
- `TMPDIR=/workspace/build/tmp` — kaniko dựng tarball layer qua thư mục tạm; mặc
  định nó nằm trong filesystem nhỏ của container chứ không phải đĩa 787 GB.

Context được ghép bằng **hardlink** cho phần trọng số (`ln`, không phải `cp`):
16 GB nằm cùng filesystem, copy chỉ tốn mười phút để tạo ra đúng những byte đã có.

## Sau khi push: kiểm trên máy khác

Việc phải làm, không phải tuỳ chọn — image chưa từng chạy trên Ampere:

```bash
docker pull hainh67/telelogs-serve:ckpt700
docker run --gpus all -p 8000:8000 hainh67/telelogs-serve:ckpt700
```

Đợi `=== READY ===`. Entrypoint tự chạy một ca chẩn đoán thật qua toàn bộ stack
trước khi báo READY, nên nếu program đã compile nạp hỏng thì nó lộ ra ngay ở đó
chứ không đợi đến lúc chấm sai cả 864 câu.

Rồi chấm thật bằng satellite:

```bash
export VLLM_BASE_URL=http://localhost:8000/v1
export VLLM_API_KEY=local
# model id: telelogs-ckpt700
```

Kỳ vọng ~93,9% ± 0,4pp trên track telelogs. **Không kỳ vọng trùng đúng 94,21%**:
A40 là Ampere SM 8.6, kernel khác H200 nên số học khác, và bản thân con số vốn đã
là một dải (xem [README.md](README.md)).

## Nếu kaniko không qua được

Thứ tự thử, đắt dần:

1. Nâng `limits.memory` lên 128Gi và thêm `--single-snapshot`.
2. Tách làm hai image: `:runtime` (base + venv + code, ~10 GB) build bằng kaniko,
   rồi trọng số gắn vào bằng volume ở máy đích. Mất tính "một artefact" mà bạn
   yêu cầu, nhưng push nhanh hơn ba lần.
3. Build ở máy local: cần kéo 16,4 GB qua link 629 KB/s (**~7 tiếng**) rồi đẩy
   ~30 GB lên Docker Hub từ đường truyền nhà. Chỉ dùng khi cluster hỏng lâu.
