"""Probe M1 (BUILD-PLAN §4.6): Nova 2 Lite access via Strands structured output, and Nova web grounding."""
import json

from pydantic import BaseModel
from strands import Agent

from landfall import config, ledger
from landfall.cassette import Cassette
from landfall.nova import make_model

OUT = config.PRIVATE / "probes" / "m1"
cas = Cassette("record", OUT)
usages = []


class Answer(BaseModel):
    country: str
    confidence: str


agent = Agent(model=make_model(cas, "probe-m1", usages.append, max_tokens=200), callback_handler=None)
res = agent("Which country is Portland, Oregon in? Answer briefly.", structured_output_model=Answer)
print("structured:", res.structured_output)

# Web grounding is a Nova system tool; call Converse directly through the same recorded client.
model = make_model(cas, "probe-m1-grounding", usages.append, max_tokens=300)
resp = model.client.converse(
    modelId=config.NOVA_MODEL,
    messages=[{"role": "user", "content": [{"text": "Find the official website of Kettle & Crane Grocers in Toronto, if it exists."}]}],
    toolConfig={"tools": [{"systemTool": {"name": "nova_grounding"}}]},
    inferenceConfig={"maxTokens": 300})
blocks = resp["output"]["message"]["content"]
kinds = sorted({k for b in blocks for k in b})
cites = [c for b in blocks for c in (b.get("citationsContent", {}) or {}).get("citations", [])]
print("grounding stop:", resp["stopReason"], "| block kinds:", kinds, "| citations:", len(cites))
print("grounding usage:", resp.get("usage"))
print("sample citation keys:", sorted(cites[0].keys()) if cites else None)
print(json.dumps(usages))
print(ledger.report())
