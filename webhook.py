"""
Wuryn Platform — WhatsApp Webhook Router
==========================================
Handles all incoming WhatsApp messages from Meta Cloud API.

ENDPOINTS:
    GET  /webhook  — Meta webhook verification (one-time setup per store)
    POST /webhook  — Incoming messages (all live traffic)

MULTI-TENANT MESSAGE PROCESSING PIPELINE:
    1.  Receive POST from Meta
    2.  Return HTTP 200 immediately (Meta timeout = 5 seconds)
    3.  Background task:
        a.  Parse raw payload → extract wa_phone_number_id
        b.  Look up store by wa_phone_number_id (multi-tenancy resolution)
        c.  If no store found → log and discard (misconfigured webhook)
        d.  Extract customer phone, name, message
        e.  Get or create customer record (automatic lead capture)
        f.  Get conversation state from database
        g.  Mark incoming message as read (blue ticks)
        h.  Route message → order flow OR intent classification
        i.  Generate response
        j.  Send response via WhatsApp API
        k.  Persist updated conversation state

WEBHOOK VERIFICATION:
    Meta sends GET /webhook with hub.mode=subscribe and hub.verify_token.
    In multi-tenant mode, the verify_token is checked against all active
    stores' wa_verify_token fields. The first match wins.
"""

import asyncio
import logging

from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse

from backend import database as db
from backend.modules.whatsapp import (
    send_message,
    mark_as_read,
    parse_incoming,
    format_catalog_text,
    format_order_status_message,
)
from backend.modules.ai import (
    classify_intent,
    generate_response,
    generate_contextual_response,
)
from backend.modules.orders import is_in_order_flow, handle_order_flow, start_order_flow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])


# ═══════════════════════════════════════════════════════════════════════════════
# WEBHOOK VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("")
async def verify_webhook(request: Request):
    """
    Meta Webhook Verification Endpoint.

    Meta sends this GET request once when you register your webhook URL
    in the Meta Developer Console. It verifies that the URL is controlled
    by you before starting to send live message traffic.

    Multi-tenant verification: checks the received token against all active
    stores' wa_verify_token fields. Any matching store confirms verification.

    Meta sends three query parameters:
        hub.mode          — always "subscribe"
        hub.verify_token  — the token entered in Meta Developer Console
        hub.challenge     — random string to echo back as response body

    Returns:
        PlainTextResponse with the challenge string if token matches.
        HTTP 403 if token does not match any store.
    """
    params    = request.query_params
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode != "subscribe":
        raise HTTPException(status_code=400, detail="Invalid hub.mode")

    # ── Multi-tenant token check ──────────────────────────────────────────────
    # Check if the received token matches any active store's verify token.
    # This allows each store to have a unique verify token for security.
    try:
        result = (
            db.supabase.table("stores")
            .select("id, name, wa_verify_token")
            .eq("wa_verify_token", token)
            .eq("active", True)
            .execute()
        )

        if result.data:
            store_name = result.data[0].get("name", "Unknown Store")
            logger.info(f"[WEBHOOK] ✅ Verification successful for store: {store_name}")
            return PlainTextResponse(content=challenge, status_code=200)

    except Exception as e:
        logger.error(f"[WEBHOOK] Error during verification token lookup: {e}")

    logger.warning(
        f"[WEBHOOK] ❌ Verification failed — token not found in any active store. "
        f"Received: '{token}'"
    )
    raise HTTPException(
        status_code=403,
        detail="Webhook verification failed — token does not match any active store."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INCOMING MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("")
async def receive_message(request: Request):
    """
    Incoming WhatsApp Message Handler.

    Meta POSTs every incoming message here — including customer messages,
    delivery status updates, and read receipts.

    CRITICAL: Meta requires an HTTP 200 response within 5 seconds.
    If we don't respond in time, Meta retries the delivery, causing
    duplicate responses. Strategy: return 200 immediately, process async.

    The background task (_process_message) handles all actual logic.
    If it fails, the customer receives a fallback message — they are
    never left waiting silently.
    """
    try:
        payload = await request.json()
    except Exception:
        # Malformed body — return 200 to prevent Meta retry storms
        logger.warning("[WEBHOOK] Could not parse request body — returning 200 to stop retries")
        return Response(status_code=200)

    # Fire and forget — return 200 immediately
    asyncio.create_task(_process_message(payload))
    return Response(status_code=200)


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE PROCESSING PIPELINE (Background Task)
# ═══════════════════════════════════════════════════════════════════════════════

async def _process_message(payload: dict):
    """
    Core message processing logic. Runs as a background asyncio task.

    Fully isolated from the HTTP response cycle — errors here do not
    affect the 200 response already sent to Meta.

    Multi-tenant flow:
        payload → parse → identify store → process in store context → respond
    """
    store = None
    phone = None

    try:
        # ── Step 1: Parse Meta webhook payload ───────────────────────────────
        parsed = parse_incoming(payload)

        if not parsed:
            # Status update, read receipt, or non-message event
            logger.debug("[PIPELINE] Non-message webhook event — skipping")
            return

        wa_phone_number_id = parsed["wa_phone_number_id"]
        phone              = parsed["phone"]
        name               = parsed.get("name", "")
        message            = parsed["message"]
        message_id         = parsed.get("message_id", "")
        message_type       = parsed.get("type", "text")

        logger.info(
            f"[PIPELINE] Incoming | from={phone} | wa_id={wa_phone_number_id} | "
            f"type={message_type} | msg='{message[:50]}{'...' if len(message) > 50 else ''}'"
        )

        # ── Step 2: Identify store (multi-tenancy resolution) ─────────────────
        # The wa_phone_number_id in the payload tells us WHICH store this
        # message was sent to. We look up the store record to get its
        # access token, business context, and configuration.
        store = db.get_store_by_wa_phone_id(wa_phone_number_id)

        if not store:
            logger.error(
                f"[PIPELINE] ❌ No active store found for wa_phone_number_id={wa_phone_number_id}. "
                f"Message from {phone} cannot be processed."
            )
            return

        store_id       = store["id"]
        wa_token       = store.get("wa_access_token") or ""
        wa_pid         = store.get("wa_phone_number_id") or wa_phone_number_id

        logger.debug(f"[PIPELINE] Store identified: {store.get('name')} (id={store_id})")

        # ── Step 3: Mark as read (blue ticks) — non-blocking ──────────────────
        if message_id and wa_token:
            asyncio.create_task(mark_as_read(message_id, wa_pid, wa_token))

        # ── Step 4: Handle non-text message types ─────────────────────────────
        if message_type not in {"text", "interactive"}:
            await send_message(
                phone,
                "Hi! I can only read text messages at the moment. 😊\n"
                "Please type your question or send *catalog* to see our products.",
                wa_pid, wa_token,
            )
            return

        if not message.strip():
            return   # Empty message body — ignore silently

        # ── Step 5: Get or create customer (automatic lead capture) ───────────
        customer = db.get_or_create_customer(
            store_id=store_id,
            phone=phone,
            name=name,
            source="whatsapp",
        )
        customer_id = customer["id"]

        # ── Step 6: Get conversation state ────────────────────────────────────
        conversation = db.get_conversation(store_id, phone)
        state        = conversation.get("state", "idle")
        context      = conversation.get("context", {})

        # ── Step 7: Route and generate response ───────────────────────────────
        response_text, new_state, new_context = await _route_message(
            store=store,
            phone=phone,
            customer_id=customer_id,
            message=message,
            state=state,
            context=context,
        )

        # ── Step 8: Send response ─────────────────────────────────────────────
        if response_text and wa_token:
            await send_message(phone, response_text, wa_pid, wa_token)
        elif not wa_token:
            logger.error(
                f"[PIPELINE] Cannot send response — store {store_id} "
                f"has no wa_access_token configured."
            )

        # ── Step 9: Persist updated state ─────────────────────────────────────
        db.update_conversation(store_id, phone, new_state, new_context)

        logger.info(
            f"[PIPELINE] ✅ Processed | store={store.get('name')} | "
            f"phone={phone} | state={state}→{new_state}"
        )

    except Exception as e:
        logger.error(
            f"[PIPELINE] ❌ Unhandled error processing message: {e}",
            exc_info=True
        )
        # Always attempt a fallback message so customer isn't left waiting
        if phone and store:
            try:
                wa_token = store.get("wa_access_token", "")
                wa_pid   = store.get("wa_phone_number_id", "")
                if wa_token and wa_pid:
                    await send_message(
                        phone,
                        "Sorry, something went wrong on our end. 🙏 "
                        "Please try again in a moment!",
                        wa_pid,
                        wa_token,
                    )
            except Exception:
                pass   # Don't let error handling crash the error handler


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

async def _route_message(
    store: dict,
    phone: str,
    customer_id: str,
    message: str,
    state: str,
    context: dict,
) -> tuple[str, str, dict]:
    """
    Determine which handler processes the message and return the response.

    ROUTING PRIORITY (checked in order):
        1. Active order flow      — customer is mid-order, continue it
        2. Shortcut keywords      — instant responses, no AI needed
        3. AI intent classification — all other messages

    Args:
        store:       Store dict (context for AI, formatting, DB queries).
        phone:       Customer phone number.
        customer_id: Customer UUID.
        message:     Customer message text.
        state:       Current conversation state.
        context:     Conversation context dict.

    Returns:
        Tuple of (response_text, new_state, new_context).
    """
    store_id      = store["id"]
    store_name    = store.get("name", "Our Store")
    message_lower = message.lower().strip()

    # ── Priority 1: Active order flow ─────────────────────────────────────────
    # Customer is mid-order. Don't classify intent — continue the flow.
    if is_in_order_flow(state):
        logger.debug(f"[ROUTER] {phone} is in order flow, state='{state}'")
        return await handle_order_flow(
            store=store,
            phone=phone,
            customer_id=customer_id,
            message=message,
            state=state,
            context=context,
        )

    # ── Priority 2: Shortcut keywords (no AI call needed) ────────────────────
    # These common patterns are handled instantly without AI classification.

    CATALOG_KEYWORDS = {
        "catalog", "catalogue", "products", "menu", "shop", "items",
        "stock", "price list", "prices", "what do you sell", "show me products",
    }
    GREETING_KEYWORDS = {
        "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
        "salam", "salaam", "salamu alaikum", "assalamu alaikum", "peace be upon you",
        "greetings", "howdy", "yo", "sup",
    }
    HELP_KEYWORDS = {
        "help", "options", "what can you do", "commands", "menu options",
    }

    if message_lower in CATALOG_KEYWORDS:
        products     = db.get_products(store_id)
        catalog_text = format_catalog_text(products, store_name)
        return (catalog_text, "browsing", {})

    if message_lower in GREETING_KEYWORDS:
        return (_build_welcome_message(store), "idle", {})

    if message_lower in HELP_KEYWORDS:
        return (_build_help_message(store), "idle", {})

    # ── Priority 3: AI intent classification ──────────────────────────────────
    intent_data  = classify_intent(message)
    intent       = intent_data.get("intent", "UNKNOWN")
    product_name = intent_data.get("product_name")

    logger.info(
        f"[ROUTER] Intent: {intent} | product_name='{product_name}' | "
        f"phone={phone} | store={store_name}"
    )

    # ── Route by intent ───────────────────────────────────────────────────────

    if intent == "GREETING":
        return (_build_welcome_message(store), "idle", {})

    elif intent == "CATALOG_REQUEST":
        products     = db.get_products(store_id)
        catalog_text = format_catalog_text(products, store_name)
        return (catalog_text, "browsing", {})

    elif intent == "ORDER_REQUEST":
        return await start_order_flow(store, phone, product_name)

    elif intent == "ORDER_STATUS":
        orders = db.get_customer_orders(store_id, customer_id)
        return (format_order_status_message(orders, store_name), "idle", {})

    elif intent in {"PRODUCT_INQUIRY", "SUPPORT"}:
        # AI responds with live product catalog as context
        products  = db.get_products(store_id)
        ai_response = generate_contextual_response(message, store, products)
        return (ai_response, "idle", {})

    elif intent == "FOLLOW_UP":
        # Standalone yes/no not in an order flow — handle conversationally
        ai_response = generate_response(message, store)
        return (ai_response, "idle", {})

    else:
        # UNKNOWN — let AI handle conversationally
        ai_response = generate_response(message, store)
        return (ai_response, "idle", {})


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSE BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _build_welcome_message(store: dict) -> str:
    """Build the standard welcome/greeting message for a store."""
    name = store.get("name", "Our Store")
    return (
        f"Hello! 👋 Welcome to *{name}*!\n\n"
        f"Here's what I can help you with:\n\n"
        f"🛍️ Type *catalog* — browse our products\n"
        f"🛒 Name a product — start ordering\n"
        f"❓ Ask anything — about products, prices, delivery\n"
        f"📦 Ask about — your order status\n\n"
        f"How can I help you today? 😊"
    )


def _build_help_message(store: dict) -> str:
    """Build the help/commands message for a store."""
    name = store.get("name", "Our Store")
    return (
        f"*{name} — How I can help:*\n\n"
        f"• *catalog* → see all available products\n"
        f"• Name a product → start an order\n"
        f"• Ask a question → AI-powered answers\n"
        f"• *my orders* → check order status\n"
        f"• *cancel* → stop current order at any time\n\n"
        f"Just message me naturally — I understand plain language! 🤖"
    )
