"""Small-model servicing classifier (§8.1.2): structured output
{intent, product_code?, confidence}; below threshold => fall through."""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from orchestrator.intents import ALL_INTENTS, CLASSIFIER_CONFIDENCE_THRESHOLD
from orchestrator.planner import StructuredChat


class IntentPrediction(BaseModel):
    intent: str
    product_code: str | None = None
    confidence: float = 0.0


def classifier_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": ALL_INTENTS},
            "product_code": {"type": ["string", "null"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["intent", "confidence"],
    }


CLASSIFIER_PROMPT = (
    "Classify the customer's servicing request into exactly one intent from the "
    "allowed list. Set confidence to how certain you are (0-1). If the message "
    "is not clearly one of these intents, use low confidence.\n"
    "Allowed intents: " + ", ".join(ALL_INTENTS)
)


async def classify_servicing(
    client: StructuredChat, message: str, trace_id: str = ""
) -> IntentPrediction | None:
    """Returns a confident prediction or None (=> fall through to RAG QA)."""
    raw = await client.chat_structured(
        [
            {"role": "system", "content": CLASSIFIER_PROMPT},
            {"role": "user", "content": message},
        ],
        classifier_schema(),
        trace_id=trace_id,
    )
    try:
        prediction = IntentPrediction.model_validate(raw)
    except ValidationError:
        return None
    if prediction.intent not in ALL_INTENTS:
        return None
    if prediction.confidence < CLASSIFIER_CONFIDENCE_THRESHOLD:
        return None
    return prediction
