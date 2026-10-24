"""Nova 2 Lite on Bedrock through Strands, with every Converse attempt passing the cassette and the ledger."""
import json
from collections.abc import Callable
from urllib.parse import unquote, urlsplit

import boto3
import botocore.session
from botocore.awsrequest import AWSResponse
from botocore.config import Config
from botocore.httpsession import URLLib3Session
from strands.models import BedrockModel

from . import config, ledger
from .cassette import Cassette, NoRecording, key_of

KEEP_HEADERS = {"content-type", "x-amzn-requestid"}


class _Raw:
    def __init__(self, data: bytes):
        self._data = data

    def stream(self, **_):
        yield self._data


def _exposure(raw: bytes | None, body: dict) -> float:
    """Worst-case cost of one Converse attempt, reserved before dispatch: input at ~1 token per 3 bytes of request,
    the full maxTokens of output, and up to 3 grounding searches when that tool is attached."""
    out = (body.get("inferenceConfig") or {}).get("maxTokens", 4000)
    grounding = 3 if "nova_grounding" in json.dumps(body.get("toolConfig") or {}) else 0
    return ledger.nova_cost(len(raw or b"") // 3, out, grounding)


def _session(cassette: Cassette, stage: str = "") -> boto3.Session:
    if cassette.mode == "replay" and stage not in cassette.live_stages:  # replay never signs a real request; a fresh clone needs no AWS credentials
        bs = botocore.session.get_session({"profile": (None, None, None, None)})  # ignore AWS_PROFILE
        return boto3.Session(aws_access_key_id="replay", aws_secret_access_key="replay", region_name=config.AWS_REGION,
                             botocore_session=bs)
    return boto3.Session(profile_name=config.AWS_PROFILE, region_name=config.AWS_REGION)


def make_model(cassette: Cassette, stage: str, on_usage: Callable[[dict], None] | None = None,
               max_tokens: int = 4000, temperature: float = 0.2) -> BedrockModel:
    model = BedrockModel(
        boto_session=_session(cassette, stage),
        boto_client_config=Config(retries={"max_attempts": 3, "mode": "standard"}, read_timeout=120, connect_timeout=10),
        model_id=config.NOVA_MODEL, streaming=False, max_tokens=max_tokens, temperature=temperature)
    http = URLLib3Session(timeout=120)

    def before_send(request, **_):
        body = json.loads(request.body) if request.body else {}
        op = unquote(urlsplit(request.url).path)  # /model/<id>/converse
        req = {"path": op, "body": body}
        key = key_of("bedrock", "Converse", req)
        if cassette.mode == "replay":
            ex = cassette.lookup(key)
            if ex is not None:
                cassette.reused(key, ex)
                r = ex["response"]
                _report(r, replayed=True)
                return AWSResponse(request.url, r["status"], r["headers"], _Raw(r["body"].encode()))
            if stage not in cassette.live_stages:
                cassette.misses.append("bedrock:Converse")
                raise NoRecording(f"no recorded Nova exchange for this request ({key[:12]}); new live evidence needed")
        rid = ledger.reserve("bedrock", "Converse", exposure_usd=_exposure(request.body, body), model=config.NOVA_MODEL, stage=stage)
        try:
            resp = http.send(request)
            r = {"status": resp.status_code, "body": resp.content.decode(),
                 "headers": {k.lower(): v for k, v in resp.headers.items() if k.lower() in KEEP_HEADERS}}
        except BaseException as e:
            # The request may have reached Bedrock and been billed: keep the full reserved exposure, cost unknown.
            ledger.finalize(rid, "bedrock", "Converse", est_usd=_exposure(request.body, body), outcome="transport_error",
                            cost_known=False, error=type(e).__name__, model=config.NOVA_MODEL, stage=stage)
            raise
        usage = _report({**r, "request_tools": body.get("toolConfig")}, replayed=False)
        ledger.finalize(rid, "bedrock", "Converse", est_usd=usage["est_usd"], outcome=str(r["status"]),
                        model=config.NOVA_MODEL, stage=stage, status=r["status"], input_tokens=usage["input_tokens"],
                        output_tokens=usage["output_tokens"], grounding_calls=usage["grounding_calls"])
        if cassette.mode != "live" and cassette.record_dir is not None:
            cassette.write(key, "bedrock", "Converse", req, r, stage)
        return resp

    def _report(r: dict, replayed: bool) -> dict:
        u, grounding = {}, 0
        if r["status"] == 200:
            parsed = json.loads(r["body"])
            u = parsed.get("usage", {})
            # Grounding is billed per search request; count each nova_grounding invocation (at least one if used).
            blocks = parsed.get("output", {}).get("message", {}).get("content", [])
            grounding = sum(1 for b in blocks if b.get("toolUse", {}).get("name") == "nova_grounding")
            if not grounding and "nova_grounding" in json.dumps(r.get("request_tools", "")):
                grounding = 1
        usage = {"stage": stage, "replayed": replayed, "status": r["status"], "grounding_calls": grounding,
                 "input_tokens": u.get("inputTokens", 0), "output_tokens": u.get("outputTokens", 0)}
        usage["est_usd"] = ledger.nova_cost(usage["input_tokens"], usage["output_tokens"], grounding)
        if on_usage:
            on_usage(usage)
        return usage

    model.client.meta.events.register("before-send.bedrock-runtime.Converse", before_send)
    return model
