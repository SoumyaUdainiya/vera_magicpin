"""
Magicpin Vera AI Challenge — deterministic HTTP bot.
Run: uvicorn bot:app --host 0.0.0.0 --port $PORT
"""
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
START = time.time()

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
contexts: dict[tuple[str, str], dict] = {}     # (scope, context_id) -> {"version": int, "payload": dict}
conversations: dict[str, dict] = {}            # conversation_id -> {"turns": int, "sent_bodies": [str], "merchant_id":..., "trigger_id":...}
sent_suppression: set[str] = set()             # suppression_keys already sent this run


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_ctx(scope: str, cid: Optional[str]) -> Optional[dict]:
    if not cid:
        return None
    entry = contexts.get((scope, cid))
    return entry["payload"] if entry else None


# ---------------------------------------------------------------------------
# GET /v1/healthz
# ---------------------------------------------------------------------------
@app.get("/v1/healthz")
async def healthz():
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _cid) in contexts.keys():
        counts[scope] = counts.get(scope, 0) + 1
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START),
        "contexts_loaded": counts,
    }


# ---------------------------------------------------------------------------
# GET /v1/metadata
# ---------------------------------------------------------------------------
@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Kartik Solo",
        "team_members": ["Kartik"],
        "model": "rule-based-deterministic",
        "approach": "Deterministic rule-based composer over 4-context fusion (category+merchant+trigger+customer); no LLM dependency for reliability and speed.",
        "contact_email": "kartik@example.com",
        "version": "1.0.0",
        "submitted_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# POST /v1/context
# ---------------------------------------------------------------------------
class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any] = {}
    delivered_at: Optional[str] = None


VALID_SCOPES = {"category", "merchant", "customer", "trigger"}


@app.post("/v1/context")
async def push_context(body: CtxBody):
    try:
        if body.scope not in VALID_SCOPES:
            return {"accepted": False, "reason": "invalid_scope", "details": f"unknown scope '{body.scope}'"}
        key = (body.scope, body.context_id)
        cur = contexts.get(key)
        if cur and cur["version"] > body.version:
            return {"accepted": False, "reason": "stale_version", "current_version": cur["version"]}
        if cur and cur["version"] == body.version:
            # idempotent no-op re-post
            return {
                "accepted": True,
                "ack_id": f"ack_{body.context_id}_v{body.version}",
                "stored_at": now_iso(),
            }
        contexts[key] = {"version": body.version, "payload": body.payload or {}}
        return {
            "accepted": True,
            "ack_id": f"ack_{body.context_id}_v{body.version}",
            "stored_at": now_iso(),
        }
    except Exception as e:
        return {"accepted": False, "reason": "invalid_scope", "details": str(e)}


# ---------------------------------------------------------------------------
# Composer helpers — deterministic, fact-grounded
# ---------------------------------------------------------------------------

def safe_get(d: Optional[dict], *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def merchant_name(merchant: Optional[dict]) -> str:
    return safe_get(merchant, "identity", "name", default="there")


def pick_active_offer(merchant: Optional[dict]) -> Optional[dict]:
    offers = safe_get(merchant, "offers", default=[]) or []
    for o in offers:
        if o.get("status") == "active":
            return o
    return None


def compose_from_trigger(trigger: dict, merchant: Optional[dict], category: Optional[dict],
                          customer: Optional[dict]) -> tuple[str, str]:
    """Returns (body, rationale) built from concrete facts. Never invents data."""
    kind = trigger.get("kind", "generic")
    name = merchant_name(merchant)
    parts = []
    facts_used = []

    if kind == "research_digest":
        top_id = safe_get(trigger, "payload", "top_item_id")
        digest_items = safe_get(category, "digest", default=[]) or []
        item = next((d for d in digest_items if d.get("id") == top_id), None)
        if item:
            title = item.get("title", "a new industry finding")
            source = item.get("source", "")
            parts.append(f"{name}, {title.rstrip('.')}" + (f" ({source})." if source else "."))
            facts_used.append("digest_title")
        else:
            parts.append(f"{name}, there's a new research update relevant to your category.")
        parts.append("Want the full abstract, or a ready-to-send patient note based on it?")

    elif kind == "regulation_change":
        top_id = safe_get(trigger, "payload", "top_item_id")
        deadline = safe_get(trigger, "payload", "deadline_iso")
        digest_items = safe_get(category, "digest", default=[]) or []
        item = next((d for d in digest_items if d.get("id") == top_id), None)
        title = item.get("title") if item else "a compliance update"
        parts.append(f"{name}, heads up — {title}" + (f", deadline {deadline}." if deadline else "."))
        parts.append("Want a 1-line checklist to stay compliant?")

    elif kind == "recall_due":
        svc = safe_get(trigger, "payload", "service_due", default="a follow-up")
        due = safe_get(trigger, "payload", "due_date")
        cust_name = safe_get(customer, "identity", "name", default="your patient")
        parts.append(f"{name}, {cust_name} is due for {svc.replace('_', ' ')}" + (f" around {due}." if due else "."))
        active_offer = pick_active_offer(merchant)
        if active_offer:
            parts.append(f"Want me to nudge them with your current offer — {active_offer.get('title')}?")
        else:
            parts.append("Want me to send them a recall reminder?")

    elif kind in ("perf_dip", "perf_spike"):
        perf = safe_get(merchant, "performance", default={}) or {}
        views = perf.get("views")
        calls = perf.get("calls")
        delta = safe_get(perf, "delta_7d", "calls_pct")
        if delta is not None and delta < 0:
            parts.append(f"{name}, calls dipped {abs(delta)*100:.0f}% this week (from {views} views, {calls} calls this month).")
            active_offer = pick_active_offer(merchant)
            if active_offer:
                parts.append(f"Your '{active_offer.get('title')}' offer is live — want a push to boost visibility?")
            else:
                parts.append("Want to set up a fresh offer to recover it?")
        elif delta is not None and delta > 0:
            parts.append(f"{name}, nice — calls are up {delta*100:.0f}% this week ({views} views, {calls} calls).")
            parts.append("Want to capitalize with a limited-time push while momentum's up?")
        else:
            parts.append(f"{name}, here's your latest performance snapshot: {views} views, {calls} calls this month.")
            parts.append("Want tips to improve it?")

    else:
        signals = safe_get(merchant, "signals", default=[]) or []
        if signals:
            parts.append(f"{name}, noticed: {signals[0].replace('_', ' ').replace(':', ' ')}.")
        else:
            parts.append(f"{name}, quick check-in on your listing.")
        parts.append("Want a quick fix or suggestion?")

    body = " ".join(parts).strip()
    rationale = f"Composed from trigger kind='{kind}' + merchant facts" + (f"; used {facts_used}" if facts_used else "") + "."
    return body, rationale


# ---------------------------------------------------------------------------
# POST /v1/tick
# ---------------------------------------------------------------------------
class TickBody(BaseModel):
    now: Optional[str] = None
    available_triggers: list[str] = []


@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []
    try:
        seen_merchant_conv = set()  # one action per merchant per tick
        for trg_id in body.available_triggers[:20]:
            trigger = get_ctx("trigger", trg_id)
            if not trigger:
                continue

            supp_key = trigger.get("suppression_key")
            if supp_key and supp_key in sent_suppression:
                continue

            merchant_id = trigger.get("merchant_id")
            customer_id = trigger.get("customer_id")
            if not merchant_id or merchant_id in seen_merchant_conv:
                continue

            merchant = get_ctx("merchant", merchant_id)
            if not merchant:
                continue
            category_slug = merchant.get("category_slug")
            category = get_ctx("category", category_slug)
            customer = get_ctx("customer", customer_id) if customer_id else None

            msg_body, rationale = compose_from_trigger(trigger, merchant, category, customer)
            if not msg_body:
                continue

            conv_id = f"conv_{merchant_id}_{trg_id}_{uuid.uuid4().hex[:6]}"
            action = {
                "conversation_id": conv_id,
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "send_as": "vera",
                "trigger_id": trg_id,
                "template_name": f"vera_{trigger.get('kind', 'generic')}_v1",
                "template_params": [merchant_name(merchant)],
                "body": msg_body,
                "cta": "open_ended",
                "suppression_key": supp_key or "",
                "rationale": rationale,
            }
            actions.append(action)
            conversations[conv_id] = {
                "turns": 1,
                "sent_bodies": [msg_body],
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "trigger_id": trg_id,
            }
            seen_merchant_conv.add(merchant_id)
            if supp_key:
                sent_suppression.add(supp_key)

            if len(actions) >= 20:
                break
    except Exception:
        return {"actions": []}

    return {"actions": actions}


# ---------------------------------------------------------------------------
# POST /v1/reply
# ---------------------------------------------------------------------------
class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str = ""
    received_at: Optional[str] = None
    turn_number: int = 1


NEGATIVE_WORDS = ["not interested", "no thanks", "stop", "unsubscribe", "leave me alone", "don't message", "no need"]
POSITIVE_WORDS = ["yes", "sure", "ok", "okay", "sounds good", "let's do it", "lets do it", "send it", "go ahead", "send me"]
WAIT_WORDS = ["later", "busy", "not now", "call me", "remind me", "in a bit", "after", "tomorrow"]
OFFTOPIC_HINTS = ["gst", "loan", "insurance", "unrelated"]


def classify_message(msg: str) -> str:
    m = msg.lower().strip()
    if not m:
        return "empty"
    if any(w in m for w in NEGATIVE_WORDS):
        return "negative"
    if any(w in m for w in WAIT_WORDS):
        return "wait"
    if any(w in m for w in POSITIVE_WORDS):
        return "positive"
    return "neutral"


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    try:
        conv = conversations.setdefault(
            body.conversation_id,
            {"turns": 0, "sent_bodies": [], "merchant_id": body.merchant_id, "customer_id": body.customer_id, "trigger_id": None},
        )
        conv["turns"] = max(conv.get("turns", 0), body.turn_number)

        msg = (body.message or "").strip()

        # Auto-reply-hell detection: same message repeated 3+ times in this conversation
        history = conv.setdefault("incoming", [])
        history.append(msg.lower())
        if len(history) >= 3 and len(set(history[-3:])) == 1:
            return {"action": "end", "rationale": "Detected repeated canned/auto-reply text; exiting gracefully to avoid spamming."}

        # Hard turn cap (judge caps at 5 turns anyway)
        if conv["turns"] >= 6:
            return {"action": "end", "rationale": "Reached max conversation turns; ending politely."}

        cls = classify_message(msg)
        merchant = get_ctx("merchant", body.merchant_id) if body.merchant_id else None
        name = merchant_name(merchant)

        if cls == "negative":
            return {"action": "end", "rationale": "Merchant declined; ending the conversation gracefully."}

        if cls == "wait":
            return {"action": "wait", "wait_seconds": 1800, "rationale": "Merchant asked for time; backing off 30 minutes."}

        if cls == "positive":
            active_offer = pick_active_offer(merchant)
            if active_offer:
                next_body = f"Great — sending it now. Also, your '{active_offer.get('title')}' offer is live if you want to bundle it in."
            else:
                next_body = "Great — sending it now, and I'll follow up once it's live."
            if next_body in conv["sent_bodies"]:
                next_body += " Let me know if you'd like anything adjusted."
            conv["sent_bodies"].append(next_body)
            return {"action": "send", "body": next_body, "cta": "confirm",
                    "rationale": "Merchant accepted; moving from qualification straight to action, honoring the accept."}

        # off-topic / hostile handling: stay on-mission politely
        if any(h in msg.lower() for h in OFFTOPIC_HINTS) or "abuse" in msg.lower():
            next_body = f"Totally hear you, {name} — that's outside what I can help with here, but happy to continue on your listing/offers whenever you're ready."
            conv["sent_bodies"].append(next_body)
            return {"action": "send", "body": next_body, "cta": "open_ended",
                    "rationale": "Off-topic/hostile input; politely redirecting back to mission without engaging the tangent."}

        # neutral/question - ask a grounded clarifying follow-up using merchant facts
        active_offer = pick_active_offer(merchant)
        if active_offer:
            next_body = f"No problem — quick one: should I go ahead with '{active_offer.get('title')}' for this, or something else?"
        else:
            next_body = "No problem — could you tell me a bit more so I set this up right for you?"
        if next_body in conv["sent_bodies"]:
            next_body = "Just checking in — want me to go ahead, or would you prefer I hold off?"
        conv["sent_bodies"].append(next_body)
        return {"action": "send", "body": next_body, "cta": "open_ended",
                "rationale": "Neutral/unclear reply; asking a grounded clarifying question tied to merchant's actual offer."}

    except Exception:
        return {"action": "end", "rationale": "Internal safety fallback; ending conversation cleanly to avoid malformed output."}


# ---------------------------------------------------------------------------
# Optional teardown
# ---------------------------------------------------------------------------
@app.post("/v1/teardown")
async def teardown():
    contexts.clear()
    conversations.clear()
    sent_suppression.clear()
    return {"ok": True}
