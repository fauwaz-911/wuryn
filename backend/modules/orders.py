"""
Wuryn Platform — Orders Module
================================
WhatsApp order flow state machine. Multi-tenant aware.

MULTI-TENANT CHANGES FROM v1:
    - All database calls now pass store_id as first argument
    - All WhatsApp sends now pass wa_phone_number_id + wa_access_token
    - store dict passed through for AI persona + formatting

STATE MACHINE:
──────────────────────────────────────────────────────────────────────
  idle
    │ (ORDER_REQUEST intent or CATALOG_REQUEST)
    ▼
  browsing  ◄─── (no product match — show catalog again)
    │ (customer selects product by number or name)
    ▼
  selected  ◄─── (invalid quantity — ask again)
    │ (valid quantity received)
    ▼
  confirming  ◄── (ambiguous reply — ask yes/no again)
    │ (customer says YES)
    ▼
  collecting_address  ◄─── (address too short — ask again)
    │ (valid address received)
    ▼
  idle  (order written to DB, confirmation sent, state reset)
──────────────────────────────────────────────────────────────────────

At ANY state: typing 'cancel', 'stop', or 'quit' returns to idle.
"""

import logging
from backend import database as db
from backend.modules.whatsapp import (
    format_catalog_text,
    format_order_summary,
    format_order_confirmed,
    format_order_status_message,
)

logger = logging.getLogger(__name__)

# ─── State constants ──────────────────────────────────────────────────────────
ACTIVE_ORDER_STATES = {"browsing", "selected", "confirming", "collecting_address"}

YES_WORDS = {
    "yes", "y", "ok", "okay", "confirm", "sure", "yeah",
    "yep", "proceed", "go ahead", "do it", "continue",
}

CANCEL_WORDS = {
    "no", "n", "nope", "cancel", "stop", "quit", "exit",
    "back", "nevermind", "never mind", "forget it",
}


def is_in_order_flow(state: str) -> bool:
    """
    Check if a customer is currently mid-order.

    Used by the webhook router to bypass intent classification
    and continue the order flow directly.

    Args:
        state: Current conversation state string.

    Returns:
        True if the customer is in an active order state.
    """
    return state in ACTIVE_ORDER_STATES


# ═══════════════════════════════════════════════════════════════════════════════
# ORDER FLOW INITIATOR
# Called from the webhook router when intent = ORDER_REQUEST or CATALOG_REQUEST
# ═══════════════════════════════════════════════════════════════════════════════

async def start_order_flow(
    store: dict,
    phone: str,
    product_name: str = None,
) -> tuple[str, str, dict]:
    """
    Initiate the order flow from idle state.

    If the AI extracted a product name from the customer's message,
    we try to pre-select it — skipping the browsing step for smoother UX.
    Otherwise, the full catalog is displayed and state → browsing.

    Args:
        store:        Full store dict from database.
        phone:        Customer's phone number.
        product_name: Optional product name extracted by intent classifier.

    Returns:
        Tuple of (response_text, new_state, new_context).
        Caller sends response and saves new state.
    """
    store_id   = store["id"]
    store_name = store.get("name", "Our Store")
    products   = db.get_products(store_id)

    if not products:
        return (
            "Sorry, we don't have any products available right now. 😔\n"
            "Please check back soon!",
            "idle",
            {},
        )

    # ── Try to pre-select a product if name was extracted ─────────────────────
    if product_name:
        product = db.find_product_by_name(store_id, product_name)
        if product:
            price   = float(product.get("price", 0))
            context = {
                "selected_product_id":   str(product["id"]),
                "selected_product_name": product["name"],
                "product_price":         price,
            }
            logger.info(
                f"[ORDER] Pre-selected '{product['name']}' for {phone} "
                f"(store={store_id})"
            )
            return (
                f"I found *{product['name']}* at ₦{price:,.0f} each. 🛍️\n\n"
                f"How many would you like to order?",
                "selected",
                context,
            )

    # ── No match — show full catalog, enter browsing ──────────────────────────
    catalog_text = format_catalog_text(products, store_name)
    return (catalog_text, "browsing", {})


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORDER FLOW DISPATCHER
# Routes mid-order messages to the correct state handler
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_order_flow(
    store: dict,
    phone: str,
    customer_id: str,
    message: str,
    state: str,
    context: dict,
) -> tuple[str, str, dict]:
    """
    Route an in-progress order message to the correct state handler.

    Called when is_in_order_flow(state) is True. Continues the order
    from whatever state the customer left off at.

    Args:
        store:       Full store dict from database.
        phone:       Customer's phone number.
        customer_id: Customer's UUID from the customers table.
        message:     Customer's current message text.
        state:       Current conversation state.
        context:     Conversation context dict (holds in-progress order data).

    Returns:
        Tuple of (response_text, new_state, new_context).
    """
    message_lower = message.lower().strip()

    # ── Global cancel — works at any order state ──────────────────────────────
    if message_lower in CANCEL_WORDS:
        logger.info(f"[ORDER] Customer {phone} cancelled at state='{state}' (store={store['id']})")
        return (
            "Order cancelled. 😊 No worries!\n\n"
            "Type *catalog* to browse our products anytime.",
            "idle",
            {},
        )

    # ── Route to state-specific handler ──────────────────────────────────────
    if state == "browsing":
        return await _handle_browsing(store, phone, message, context)

    elif state == "selected":
        return await _handle_quantity(store, phone, message, context)

    elif state == "confirming":
        return await _handle_confirmation(store, phone, message, context)

    elif state == "collecting_address":
        return await _handle_address(store, phone, customer_id, message, context)

    # Unknown state — should never reach here — reset safely
    logger.warning(
        f"[ORDER] Unknown order state '{state}' for {phone} (store={store['id']}). Resetting."
    )
    return (
        "Something went wrong. Let's start fresh!\n"
        "Type *catalog* to see our products. 🛍️",
        "idle",
        {},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STATE HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

async def _handle_browsing(
    store: dict,
    phone: str,
    message: str,
    context: dict,
) -> tuple[str, str, dict]:
    """
    State: browsing
    Customer has seen the catalog. Waiting for product selection.
    Accepts: product number (1, 2...) or product name (partial match).
    """
    store_id = store["id"]
    products = db.get_products(store_id)

    if not products:
        return ("All products are currently out of stock. 😔", "idle", {})

    selected = None

    # ── Selection by number ───────────────────────────────────────────────────
    stripped = message.strip()
    if stripped.isdigit():
        idx = int(stripped) - 1
        if 0 <= idx < len(products):
            selected = products[idx]
        else:
            return (
                f"Please choose a number between *1* and *{len(products)}*. 🔢",
                "browsing",
                context,
            )

    # ── Selection by name ─────────────────────────────────────────────────────
    if not selected:
        selected = db.find_product_by_name(store_id, message)

    # ── No match ──────────────────────────────────────────────────────────────
    if not selected:
        numbered = "\n".join(
            [f"{i+1}. {p['name']}" for i, p in enumerate(products)]
        )
        return (
            f"I couldn't find that product. 🤔\n\n"
            f"Please reply with the *number* next to the item:\n\n{numbered}",
            "browsing",
            context,
        )

    # ── Product found → move to quantity collection ───────────────────────────
    price   = float(selected.get("price", 0))
    context = {
        "selected_product_id":   str(selected["id"]),
        "selected_product_name": selected["name"],
        "product_price":         price,
    }

    logger.info(
        f"[ORDER] {phone} selected: '{selected['name']}' at ₦{price:,.0f} "
        f"(store={store_id})"
    )

    db.update_customer_profile(
        store_id, phone,
        interests=selected["name"],
        purchase_intent="browsing"
    )

    return (
        f"Great choice! 🎉\n\n"
        f"*{selected['name']}* — ₦{price:,.0f} each\n\n"
        f"How many would you like? (Reply with a number)",
        "selected",
        context,
    )


async def _handle_quantity(
    store: dict,
    phone: str,
    message: str,
    context: dict,
) -> tuple[str, str, dict]:
    """
    State: selected
    Customer has chosen a product. Waiting for quantity.
    Accepts: digits or simple word numbers.
    """
    quantity = _extract_quantity(message)

    if not quantity:
        return (
            "Please reply with the *number of items* you'd like.\n"
            "For example: *1*, *2*, *3* 🔢",
            "selected",
            context,
        )

    if quantity > 99:
        return (
            "For bulk orders (100+), please contact us directly for special pricing. 📞\n"
            "Please enter a quantity between 1 and 99.",
            "selected",
            context,
        )

    product = db.get_product_by_id(store["id"], context.get("selected_product_id"))
    if not product:
        logger.error(
            f"[ORDER] Product not found during quantity step "
            f"(phone={phone}, store={store['id']}, "
            f"product_id={context.get('selected_product_id')})"
        )
        return (
            "Sorry, I lost track of the selected product. 😬\n"
            "Please type *catalog* to start again.",
            "idle",
            {},
        )

    new_context  = {**context, "quantity": quantity}
    summary_text = format_order_summary(product, quantity)

    logger.info(
        f"[ORDER] {phone} wants qty={quantity} of '{product['name']}' "
        f"(store={store['id']})"
    )

    return (summary_text, "confirming", new_context)


async def _handle_confirmation(
    store: dict,
    phone: str,
    message: str,
    context: dict,
) -> tuple[str, str, dict]:
    """
    State: confirming
    Order summary shown. Waiting for YES or NO from customer.
    """
    msg_lower = message.lower().strip()

    if msg_lower in YES_WORDS:
        logger.info(f"[ORDER] {phone} confirmed order. Collecting address. (store={store['id']})")
        return (
            "✅ Confirmed! Almost there.\n\n"
            "Please send your *full delivery address*:\n"
            "• House / flat number\n"
            "• Street name\n"
            "• Area / estate\n"
            "• City\n\n"
            "Type it all in one message. 📦",
            "collecting_address",
            context,
        )

    elif msg_lower in CANCEL_WORDS:
        return (
            "Order cancelled. 😊 Type *catalog* to browse again!",
            "idle",
            {},
        )

    else:
        # Ambiguous — nudge without being annoying
        return (
            "Please reply *YES* to confirm your order ✅ or *CANCEL* to cancel ❌",
            "confirming",
            context,
        )


async def _handle_address(
    store: dict,
    phone: str,
    customer_id: str,
    message: str,
    context: dict,
) -> tuple[str, str, dict]:
    """
    State: collecting_address
    Waiting for delivery address. Validate then place the order.
    """
    address = message.strip()

    # Basic validation: must be at least 20 characters to be meaningful
    # e.g. "5 Aminu Kano Crescent, Wuse 2, Abuja" = 38 chars
    if len(address) < 20:
        return (
            "Please provide your *full delivery address* — including street, "
            "area, and city. 📍\n\n"
            "Example: _12 Adeola Odeku St, Victoria Island, Lagos_",
            "collecting_address",
            context,
        )

    # ── All data collected — place the order ──────────────────────────────────
    store_id   = store["id"]
    product_id = context.get("selected_product_id")
    quantity   = context.get("quantity", 1)

    product = db.get_product_by_id(store_id, product_id)
    if not product:
        logger.error(
            f"[ORDER] Product not found during address step "
            f"(phone={phone}, store={store_id}, product_id={product_id})"
        )
        return (
            "😟 Sorry, there was a problem placing your order.\n"
            "Please try again or contact us directly.",
            "idle",
            {},
        )

    try:
        order = db.create_order(
            store_id=store_id,
            customer_id=customer_id,
            product=product,
            quantity=quantity,
            delivery_address=address,
            channel="whatsapp",
        )

        # Enrich lead profile with completed purchase intent
        db.update_customer_profile(
            store_id, phone,
            interests=context.get("selected_product_name", ""),
            purchase_intent="completed_order",
        )

        confirmation = format_order_confirmed(order, product)

        logger.info(
            f"[ORDER] ✅ Order placed: {order.get('reference')} | "
            f"Phone: {phone} | Product: {product['name']} x{quantity} | "
            f"Store: {store_id}"
        )

        return (confirmation, "idle", {})

    except Exception as e:
        logger.error(
            f"[ORDER] create_order failed for {phone} (store={store_id}): {e}"
        )
        return (
            "😟 Sorry, there was a problem placing your order.\n"
            "Please try again or contact us directly for help.",
            "idle",
            {},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_quantity(message: str) -> int | None:
    """
    Extract a positive integer quantity from a customer message.

    Handles digit strings ("2", "10") and simple English word numbers.
    Rejects zero, negatives, and anything above 999.

    Args:
        message: Raw customer message text.

    Returns:
        Positive integer quantity if found, None otherwise.
    """
    word_numbers = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    }
    msg_lower = message.lower().strip()

    # Try extracting digits from any token in the message
    for token in message.split():
        clean = token.replace(",", "").replace(".", "").replace("x", "")
        if clean.isdigit():
            qty = int(clean)
            if 1 <= qty <= 999:
                return qty

    # Try word numbers
    for word, num in word_numbers.items():
        if word in msg_lower:
            return num

    return None
