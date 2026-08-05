# Qwen3-8B vLLM service for the four-benchmark study

This deployment serves the untouched `Qwen/Qwen3-8B` checkpoint on one H200.
It is intended for prompt and few-shot experiments on TeleLogs, TeleMath,
TeleTables, and 3GPP before any fine-tuning.

## Connect from the local workstation

```bash
./infra/telelogs-bench4/port_forward.sh
curl http://127.0.0.1:8000/v1/models
```

Use these Inspect/Satellite settings while the tunnel is open:

```bash
export VLLM_BASE_URL=http://127.0.0.1:8000/v1
export VLLM_API_KEY=local
```

The model identifier is `vllm/Qwen/Qwen3-8B` in Inspect and
`Qwen/Qwen3-8B` in a raw OpenAI-compatible request.

## Operate the server

```bash
ssh H200_Tensara 'kubectl get pod telelogs-bench4 -n tensara'
ssh H200_Tensara 'tail -f ~/projects/telelogs/runs/bench4/done/serve_qwen3_8b.log'
ssh H200_Tensara 'touch ~/projects/telelogs/runs/bench4/done/serve_qwen3_8b.kill'
```

To start it again after an intentional stop, upload the serving job:

```bash
scp infra/telelogs-bench4/serve_qwen3_8b.sh \
  H200_Tensara:~/projects/telelogs/runs/bench4/jobs/
```

The Service is cluster-internal. Do not expose port 8000 publicly.
The helper uses SSH TCP forwarding directly to the Service because this
cluster blocks the Kubernetes `pods/portforward` subresource.
