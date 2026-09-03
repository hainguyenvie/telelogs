# TeleLogs diagnosis service: GRPO ckpt-700 + the DSPy tool pipeline, behind one
# OpenAI /v1/chat/completions URL that gsma-labs/satellite can be pointed at.
#
# satellite's `open-local` provider is configured by nothing but VLLM_BASE_URL and
# Inspect talks to it over OpenAI chat-completions; nothing inspects what is
# behind the URL. So the whole scaffold goes in the image and the harness scores
# the system as deployed, not a bare forward pass the deployment never makes.
#
# Base is vllm/vllm-openai:v0.10.0, the exact vLLM the scores were measured on.
# Pinning it is not tidiness: vLLM's continuous batching is what gives the score
# its measured +-0.4pp rerun band, and a different minor version moves that band
# by an unknown amount.
#
# THIS IMAGE MUST STAY PRIVATE. It carries data/raw_train_2400/train.json and
# data/official_test_864/test.json, which TeleLogs asks users not to publicly
# redistribute. REPRODUCING.md permits them in this repo only because the repo is
# private; the same condition is inherited here.
FROM vllm/vllm-openai:v0.10.0

# The pipeline gets its OWN venv, mirroring the H200 layout where DSPy and vLLM
# never shared an interpreter. dspy pulls litellm, which pins openai and pydantic;
# resolving those against vLLM's own pins in one environment is how a serving
# image silently gets a different client library than the one it was measured on.
RUN python3 -m venv /opt/pipeline/venv \
 && /opt/pipeline/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/pipeline/venv/bin/pip install --no-cache-dir \
      "dspy==3.2.1" \
      "litellm==1.93.0" \
      "openai==2.48.0" \
      "pydantic==2.13.4" \
      "pyarrow>=17,<22"

# The complete tool track, not just the seven modules the shim imports. Only the
# seven are on the runtime path -- adding the rest changes no answer -- but the
# image is the handover artefact, and the optimisers, the audit CLI and the
# calibrated seed prompts are what make the served program reconstructible rather
# than merely runnable.
COPY code/ /opt/pipeline/code/
COPY harness/ /opt/pipeline/harness/
COPY pipeline_openai_shim.py /opt/pipeline/
COPY champion_gsma_full.py /opt/pipeline/
COPY official_telelogs_eval.py /opt/pipeline/
COPY verify_summary.py /opt/pipeline/
COPY program.json /opt/pipeline/program.json
COPY data/ /opt/data/
COPY entrypoint.sh /opt/entrypoint.sh

# The merged bf16 weights: base Qwen3-8B with GRPO v2 checkpoint-700 folded in.
# Merged once on the H200 from adapter md5 3a744a9363528220448107747b6719c0.
COPY model/ /opt/model/

# Fail the BUILD, not the first request, if any piece of the contract is missing.
RUN chmod +x /opt/entrypoint.sh \
 && /opt/pipeline/venv/bin/python -c "import dspy, litellm, pyarrow; print('dspy', dspy.__version__)" \
 && test -f /opt/model/config.json \
 && test -f /opt/pipeline/program.json \
 && test -f /opt/pipeline/harness/full4_eval.py \
 && test -f /opt/data/ot-full/telelogs/test-00000-of-00001.parquet \
 && test -f /opt/pipeline/verify_summary.py \
 && /opt/pipeline/venv/bin/python -c "\
import sys; sys.path.insert(0, '/opt/pipeline/code'); \
import neutral_tools, verifier; print('tool layer imports clean')"

# Satellite's default is http://localhost:8000/v1, so the SHIM owns 8000. The
# vLLM engine stays on 127.0.0.1:8100 and is never published: a caller that
# reached it directly would get the bare model and a score tens of points lower,
# which is exactly the confusion this layout prevents.
EXPOSE 8000

ENV PIPELINE_SERVED_NAME=telelogs-ckpt700 \
    PIPELINE_PORT=8000 \
    PIPELINE_HOST=0.0.0.0 \
    PIPELINE_METHOD=b3_react_specialist \
    PIPELINE_MAX_TOKENS=1000 \
    VLLM_PORT=8100 \
    VLLM_TP=1 \
    VLLM_MAX_MODEL_LEN=40960 \
    VLLM_GPU_MEM_UTIL=0.90 \
    VLLM_MAX_NUM_SEQS=64 \
    VERIFY_WORKERS=8 \
    HF_HUB_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=8

ENTRYPOINT ["/opt/entrypoint.sh"]
CMD ["serve"]
