"""
Wuryn Platform — AI Module
============================
Dual-provider AI layer: Groq (primary) → Gemini (fallback).

MULTI-TENANT CHANGES FROM v1:
    - generate_response() accepts store context (name, type, description)
      so each store gets its own AI persona — no code changes per client.
    - classify_intent() is unchanged — purely functional, no store context needed.

RELIABILITY ARCHITECTURE:
    Every AI call follows: Groq → Gemini → Static fallback
    The static fallback ensures customers always receive a response,
    even if both AI providers are simultaneously unavailable.

PROVIDER SELECTION RATIONALE:
    Groq   (primary)  : Sub-second latency, generous free tier,
                        excellent instruction-following for JSON outputs.
    Gemini (fallback) : Google infrastructure reliability, free tier,
                        different failure modes from Groq.
"""

import json
import logging
from groq import Groq
from backend.config import GROQ_API_KEY, GROQ_MODEL, GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

# ─── Groq Client ──────────────────────────────────────────────────────────────
# Initialised once at module import — thread-safe, reused across all requests
groq_client = Groq(api_key=GROQ_API_KEY)


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_store_system_prompt(store: dict) -> str:
    """
    Build an AI system prompt tailored to a specific store's identity.

    Called per-request with the store dict from the database.
    Each store gets a unique AI persona based on its name, type, and description —
    without any code changes. This is how one deployment serves many clients.

    Args:
        store: Store dict from the database. Expected keys:
               name, business_type, description, currency.

    Returns:
        System prompt string that defines the AI's behaviour for this store.
    """
    name        = store.get("name", "Our Store")
    biz_type    = store.get("business_type", "retail")
    description = store.get("description", "We sell quality products.")
    currency    = store.get("currency", "NGN")
    currency_sym = "₦" if currency == "NGN" else "$" if currency == "USD" else currency

    return f"""You are a smart, friendly WhatsApp business assistant representing *{name}*.

Business type: {biz_type}
About this business: {description}
Currency: {currency_sym} ({currency})

Your rules — follow these strictly:
- Answer questions about products, pricing, availability, and delivery
- Be warm, concise, and professional — you represent the brand
- Keep responses short — WhatsApp messages should be under 200 words
- Use simple, clear language — most customers are non-technical
- Use 1–3 emojis per message maximum — don't overuse them
- NEVER invent product details, prices, or stock status you haven't been given
- If you don't know something, say: "Let me check on that for you 🙏"
- Do not reveal you are an AI unless sincerely and directly asked
- Always quote prices in {currency_sym}
- If the customer seems ready to buy, guide them toward placing an order
- You represent *{name}* only — do not discuss competitors or unrelated topics

You are not a generic chatbot. You are the dedicated assistant for *{name}*."""


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL PROVIDER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _call_groq(
    user_message: str,
    system_prompt: str,
    history: list = None,
) -> str:
    """
    Call the Groq API with the Llama3 8B model.

    Args:
        user_message:  Current customer message.
        system_prompt: System prompt (store-specific or task-specific).
        history:       Optional list of prior {role, content} message dicts.
                       Maximum last 6 messages are used to stay within context limits.

    Returns:
        Response text from Groq.

    Raises:
        Exception: On API error, rate limit, or timeout. Caller falls back to Gemini.
    """
    messages = []
    if history:
        messages.extend(history[-6:])   # Last 6 messages for context window safety
    messages.append({"role": "user", "content": user_message})

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        system=system_prompt,
        max_tokens=350,     # Concise responses for WhatsApp UX
        temperature=0.7,    # Balanced: natural but not hallucinatory
    )

    return response.choices[0].message.content.strip()


def _call_gemini(user_message: str, system_prompt: str) -> str:
    """
    Call Google Gemini Flash as fallback provider.

    Gemini's Python SDK doesn't support a separate system_prompt field,
    so we prepend it directly to the user message. This is standard practice
    and produces equivalent results for instruction-following tasks.

    Args:
        user_message:  Current customer message.
        system_prompt: System prompt (prepended to message).

    Returns:
        Response text from Gemini.

    Raises:
        Exception: On API error. Caller uses static fallback.
    """
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    model    = genai.GenerativeModel(GEMINI_MODEL)
    full_msg = f"{system_prompt}\n\nCustomer message: {user_message}"

    response = model.generate_content(
        full_msg,
        generation_config=genai.GenerationConfig(
            max_output_tokens=350,
            temperature=0.7,
        ),
    )

    return response.text.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC INTERFACE
# ═══════════════════════════════════════════════════════════════════════════════

def generate_response(
    user_message: str,
    store: dict,
    history: list = None,
    system_override: str = None,
) -> str:
    """
    Generate an AI response using the dual-provider architecture.

    Tries Groq → Gemini → static fallback. Always returns a string.
    Callers never receive None — the static fallback guarantees a response.

    Args:
        user_message:    Customer's message text.
        store:           Store dict from database (used to build system prompt).
        history:         Prior conversation messages for multi-turn context.
        system_override: Optional custom system prompt (e.g. with product catalog
                         injected for product inquiry responses).
                         If None, the store's default system prompt is used.

    Returns:
        AI-generated response string. Never None.
    """
    system_prompt = system_override or build_store_system_prompt(store)
    groq_error    = None

    # ── Attempt 1: Groq ───────────────────────────────────────────────────────
    try:
        response = _call_groq(user_message, system_prompt, history)
        logger.debug(f"[AI] Response from Groq for store={store.get('id', 'unknown')}")
        return response
    except Exception as e:
        groq_error = e
        logger.warning(f"[AI] Groq failed — trying Gemini. Error: {e}")

    # ── Attempt 2: Gemini ─────────────────────────────────────────────────────
    try:
        response = _call_gemini(user_message, system_prompt)
        logger.debug(f"[AI] Response from Gemini (fallback) for store={store.get('id', 'unknown')}")
        return response
    except Exception as gemini_error:
        logger.error(
            f"[AI] Both providers failed for store={store.get('id', 'unknown')}. "
            f"Groq: {groq_error} | Gemini: {gemini_error}"
        )

    # ── Attempt 3: Static fallback ────────────────────────────────────────────
    # Customer always gets a response. No silent failures.
    store_name = store.get("name", "us")
    return (
        f"Sorry, I'm having a little trouble right now. 😅 "
        f"Please try again in a moment, or contact *{store_name}* directly!"
    )


def generate_contextual_response(
    user_message: str,
    store: dict,
    products: list,
) -> str:
    """
    Generate an AI response with the live product catalog injected as context.

    Used for PRODUCT_INQUIRY and SUPPORT intents where accurate product data
    is required. Injecting the catalog prevents the AI from hallucinating
    prices, availability, or product details.

    Args:
        user_message: Customer's question.
        store:        Store dict from database.
        products:     List of product dicts (from get_products()).

    Returns:
        AI response string grounded in actual product data.
    """
    store_system = build_store_system_prompt(store)
    currency_sym = "₦" if store.get("currency") == "NGN" else "$"

    if products:
        catalog_lines = "\n".join([
            f"- {p['name']}: {currency_sym}{float(p['price']):,.0f}"
            + (f" | {p['description'][:80]}" if p.get("description") else "")
            for p in products
        ])
        system_with_catalog = (
            f"{store_system}\n\n"
            f"CURRENT AVAILABLE PRODUCTS:\n{catalog_lines}\n\n"
            f"Use the product data above to answer the customer's question accurately. "
            f"If they seem interested in buying, suggest they type 'catalog' to place an order."
        )
    else:
        system_with_catalog = store_system

    return generate_response(user_message, store, system_override=system_with_catalog)


def classify_intent(message: str) -> dict:
    """
    Classify the intent behind a customer's message into structured output.

    Returns a dict (not free text) so the router can make deterministic
    decisions without further parsing. Uses Groq for speed and accuracy
    on structured JSON tasks.

    Intent taxonomy:
        GREETING        — Hello, good morning, salaam, hi
        CATALOG_REQUEST — Show products, what do you sell, price list
        PRODUCT_INQUIRY — Questions about specific products (size, colour, specs)
        ORDER_REQUEST   — I want to buy, place an order, I'll take...
        ORDER_STATUS    — Where is my order, delivery update, tracking
        SUPPORT         — Complaint, refund, damaged item, wrong order
        FOLLOW_UP       — Yes, no, ok, sure (reply to a bot question)
        UNKNOWN         — Cannot classify with confidence

    Args:
        message: Raw customer message text.

    Returns:
        Dict with keys:
            intent       (str)      — one of the intent types above
            product_name (str|None) — extracted product name if mentioned
            quantity     (int|None) — extracted quantity if mentioned
    """
    classification_prompt = """You are a strict intent classifier for a WhatsApp e-commerce chatbot.

Classify the customer message into exactly ONE intent from this list:
GREETING, CATALOG_REQUEST, PRODUCT_INQUIRY, ORDER_REQUEST, ORDER_STATUS, SUPPORT, FOLLOW_UP, UNKNOWN

Return ONLY a raw JSON object. No preamble, no markdown, no explanation. Exactly this format:
{"intent": "INTENT_NAME", "product_name": null, "quantity": null}

Extraction rules:
- product_name: extract ONLY if customer clearly names a specific product. Otherwise null.
- quantity: extract ONLY if customer states a specific number. Otherwise null.
- Prefer PRODUCT_INQUIRY over ORDER_REQUEST when intent is ambiguous.

Examples:
"Hi" → {"intent": "GREETING", "product_name": null, "quantity": null}
"What products do you sell?" → {"intent": "CATALOG_REQUEST", "product_name": null, "quantity": null}
"How much is the JBL headphone?" → {"intent": "PRODUCT_INQUIRY", "product_name": "JBL headphone", "quantity": null}
"I want to buy 2 Samsung phones" → {"intent": "ORDER_REQUEST", "product_name": "Samsung phones", "quantity": 2}
"Where is my order?" → {"intent": "ORDER_STATUS", "product_name": null, "quantity": null}
"Yes please" → {"intent": "FOLLOW_UP", "product_name": null, "quantity": null}
"My item arrived broken" → {"intent": "SUPPORT", "product_name": null, "quantity": null}"""

    raw = ""
    try:
        raw   = _call_groq(message, system_prompt=classification_prompt)
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)

        if "intent" not in result:
            raise ValueError("Missing 'intent' in classification result")

        return {
            "intent":       result.get("intent", "UNKNOWN"),
            "product_name": result.get("product_name"),
            "quantity":     result.get("quantity"),
        }

    except json.JSONDecodeError as e:
        logger.warning(f"[AI] Intent classification returned invalid JSON: {e}. Raw: '{raw[:100]}'")
    except Exception as e:
        logger.warning(f"[AI] Intent classification failed: {e}")

    # Safe default — treat unknown intent conversationally
    return {"intent": "UNKNOWN", "product_name": None, "quantity": None}
