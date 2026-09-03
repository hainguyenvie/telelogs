#!/usr/bin/env python3
"""Serve the whole TeleLogs pipeline behind one OpenAI /v1/chat/completions URL.

This is what makes the submitted artefact match the deployed one. `gsma-labs/
satellite`, GSMA's own submission TUI, has an `open-local` provider configured by
nothing but a base URL, and Inspect talks to it over OpenAI chat-completions —
anything that speaks that protocol is a "model" to the harness, and nothing
inspects what is behind the URL. So the scaffold goes behind the URL, and the
official task scores the system that would actually be deployed rather than a
bare forward pass the deployment never makes.

Request in: the raw TeleLogs question as the last user message, exactly as
full4_eval.py and satellite's telelogs task send it.
Response out: an OpenAI chat completion whose content is the engineer narrative,
then the audited evidence, then `Final answer: \\boxed{N}` — byte-for-byte the
completion champion_gsma_full.py builds, because it is the same three lines of
assembly. The official parser reads the last \\boxed{} and takes its first
integer; nothing else in the body is scored.

Sampling parameters that arrive in the request (temperature, top_p, max_tokens,
enable_thinking) are ACCEPTED AND IGNORED. They configure a bare model; this
endpoint is a program whose internal decoding is fixed at temperature 0 and its
own token budget. Silently honouring some of them and not others would make the
endpoint's behaviour depend on which harness called it, which is exactly the
kind of drift that makes a number unreproducible. What the caller asked for is
echoed back in `x_pipeline` so a log always shows what was overridden.

Deliberately NOT included: any retry, cache or vote across requests. One
question in, one program run out, no state carried between calls, so a rerun of
the same set gives the same answers in any order and at any concurrency.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CODE = os.environ["PIPELINE_CODE_DIR"]
sys.path.insert(0, CODE)

import dspy  # noqa: E402
from neutral_tools import parse_case  # noqa: E402
from tool_program import PROGRAMS, normalize_answer  # noqa: E402
from narrate import NarrationLayer  # noqa: E402

SERVED_NAME = os.environ.get("PIPELINE_SERVED_NAME", "telelogs-pipeline")
INNER_BASE = os.environ["PIPELINE_INNER_BASE"]      # inner vLLM, e.g. http://127.0.0.1:8300/v1
INNER_MODEL = os.environ["PIPELINE_INNER_MODEL"]    # its --served-model-name
METHOD = os.environ.get("PIPELINE_METHOD", "b3_react_specialist")
PROGRAM_JSON = os.environ["PIPELINE_PROGRAM"]
MAX_TOKENS = int(os.environ.get("PIPELINE_MAX_TOKENS", "1000"))
PORT = int(os.environ.get("PIPELINE_PORT", "8400"))
# 127.0.0.1 is the right default for a job that runs the harness beside the
# shim inside one pod; a container that must be reachable from outside sets
# PIPELINE_HOST=0.0.0.0. Defaulting to 0.0.0.0 would silently expose every
# in-pod run to the pod network, so the exposure is opt-in.
HOST = os.environ.get("PIPELINE_HOST", "127.0.0.1")
# A router asks for a model by name and may check that the answer came back
# under the same name. Echoing the caller's value is the accommodating
# default; set PIPELINE_ECHO_MODEL=0 to always answer as SERVED_NAME.
ECHO_MODEL = os.environ.get("PIPELINE_ECHO_MODEL", "1") != "0"
# x_pipeline is a non-standard field. It is worth a lot when debugging and
# nothing to a router that forwards responses verbatim, so it can be turned off.
EXPOSE_META = os.environ.get("PIPELINE_EXPOSE_META", "1") != "0"

dspy.configure(lm=dspy.LM(
    f"openai/{INNER_MODEL}", api_base=INNER_BASE, api_key="local",
    temperature=0.0, max_tokens=MAX_TOKENS,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    cache=False,
))

# Per-prediction usage, not a global counter: the shim serves requests
# concurrently, so summing dspy's shared history would attribute one caller's
# tokens to another. If this DSPy build lacks the feature the shim still works
# and simply reports zeros, which is the honest answer to "how many tokens".
try:
    dspy.settings.configure(track_usage=True)
    USAGE_TRACKED = True
except Exception:
    USAGE_TRACKED = False

PROGRAM = PROGRAMS[METHOD]()
PROGRAM.load(PROGRAM_JSON)
NARRATOR = NarrationLayer()


def usage_of(pred) -> dict:
    """Sum the token counts DSPy recorded for one prediction, per inner LM call."""
    if not USAGE_TRACKED:
        return {}
    try:
        per_model = pred.get_lm_usage() or {}
    except Exception:
        return {}
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for entry in per_model.values():
        for key in totals:
            value = entry.get(key)
            if isinstance(value, int):
                totals[key] += value
    return totals

_counter = threading.Lock()
_served = {"n": 0, "errors": 0}


def last_user_message(body: dict) -> str:
    for message in reversed(body.get("messages") or []):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            # OpenAI allows a content-part list; concatenate the text parts.
            if isinstance(content, list):
                return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def run_pipeline(question: str) -> tuple[str, dict]:
    """Return (completion_text, meta). Assembly copied from champion_gsma_full.py."""
    case = parse_case(question)
    pred = PROGRAM(raw_question=question, case=case)
    answer = normalize_answer(getattr(pred, "answer", ""))
    narrative = NARRATOR(raw_question=question, pred=pred)
    evidence = str(getattr(pred, "reasoning", ""))
    parts = []
    if narrative:
        parts.append(narrative)
    if evidence:
        parts.append("[Audited evidence]\n" + evidence)
    if answer:
        # answer is "C4"; the official parser takes the first integer inside the box.
        parts.append(f"Final answer: \\boxed{{{answer[1]}}}")
    meta = {
        "pipeline_answer": answer,
        "lm_calls": int(getattr(pred, "lm_calls", 0)) + 1,
        "narrative_ungrounded": list(getattr(pred, "narrative_ungrounded", [])),
        "usage": usage_of(pred),
    }
    return "\n\n".join(parts), meta


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter than the default per-request line
        return

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/v1/health"):
            return self._send(200, {"status": "ok", **_served})
        if self.path.rstrip("/") == "/v1/models":
            return self._send(200, {"object": "list", "data": [
                {"id": SERVED_NAME, "object": "model", "owned_by": "local"}]})
        return self._send(404, {"error": {"message": f"no route {self.path}"}})

    def do_POST(self):
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            return self._send(404, {"error": {"message": f"no route {self.path}"}})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:
            return self._send(400, {"error": {"message": f"bad request body: {exc}"}})

        question = last_user_message(body)
        if not question.strip():
            return self._send(400, {"error": {"message": "no user message with content"}})

        # A router in front of several specialists asks for a model by name; the
        # answer should come back under the name that was asked for.
        requested = body.get("model")
        answer_as = requested if (ECHO_MODEL and isinstance(requested, str) and requested) else SERVED_NAME

        started = time.monotonic()
        try:
            completion, meta = run_pipeline(question)
            finish = "stop"
        except Exception as exc:
            # A failed case must score wrong, not break the harness: return a
            # valid completion carrying no \boxed{}, which the official parser
            # scores as incorrect. Raising a 500 would instead trip full4_eval's
            # retry loop and turn one bad case into three.
            traceback.print_exc()
            with _counter:
                _served["errors"] += 1
            completion = f"[pipeline error] {type(exc).__name__}: {exc}"
            meta = {"pipeline_answer": "", "lm_calls": 0, "usage": {},
                    "error": f"{type(exc).__name__}: {exc}"}
            finish = "stop"

        with _counter:
            _served["n"] += 1
            index = _served["n"]

        usage = meta.pop("usage", None) or {}
        payload = {
            "id": f"chatcmpl-telelogs-{index:06d}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": answer_as,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": completion},
                "finish_reason": finish,
            }],
            # Summed over the pipeline's inner calls, so a caller that meters
            # tokens sees what the request actually cost rather than the single
            # call it would have cost from a bare model. Known undercount: the
            # narration layer runs after the prediction and returns a plain
            # string, so its one call carries no usage to attribute. `lm_calls`
            # in x_pipeline is the honest total; this field is the token subtotal
            # DSPy could attribute.
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }
        if EXPOSE_META:
            payload["x_pipeline"] = {
                **meta,
                "method": METHOD,
                "served_name": SERVED_NAME,
                "inner_model": INNER_MODEL,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "caller_sampling_ignored": {
                    k: body.get(k) for k in
                    ("temperature", "top_p", "top_k", "max_tokens", "seed", "chat_template_kwargs")
                    if k in body
                },
            }
        self._send(200, payload)


def main() -> None:
    print(f"pipeline shim: {SERVED_NAME} on {HOST}:{PORT}", flush=True)
    print(f"  method={METHOD} program={PROGRAM_JSON}", flush=True)
    print(f"  inner={INNER_MODEL} at {INNER_BASE} (temperature 0, max_tokens {MAX_TOKENS})", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
