"""
Wuryn Platform — WhatsApp Module
==================================
All direct communication with the Meta WhatsApp Cloud API.

MULTI-TENANT CHANGES FROM v1:
    - send_message() now accepts per-store token instead of global env var
    - parse_incoming() now also extracts the wa_phone_number_id so the
      webhook router can identify which store the message belongs to
    - All formatter functions are store-aware (accept store name/currency)

RESPONSIBILITIES:
    - send_message()    : POST a text message to a customer
    - mark_as_read()    : Send read receipt (blue ticks)
    - parse_incoming()  : Extract clean message data from Meta's nested payload
    - format_catalog_text()     : Render product list for WhatsApp
    - format_order_summary()    : Render order review before confirmation
    - format_order_confirmed()  : Render final order confirmation
"""

import httpx
import logging
from backend.config import WHATSAPP_API_BASE

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE SENDING
# ═══════════════════════════════════════════════════════════════════════════════

async def send_message(
    phone: str,
    text: str,
    wa_phone_number_id: str,
    wa_access_token: str,
) -> bool:
    """
    Send a plain text WhatsApp message to a customer.

    Constructs the Meta Cloud API URL from the store's phone_number_id
    and authenticates with the store's access token.
    Both values come from the `stores` database record — not from env vars —
    enabling multi-tenant message sending from a single deployment.

    WhatsApp supports limited markdown in text messages:
        *bold*   _italic_   ~strikethrough~   ```monospace```

    Args:
        phone:              Recipient's phone in international format, no '+'.
                            Example: "2348012345678"
        text:               Message body. Max 4096 characters.
        wa_phone_number_id: Store's Meta PHONE_NUMBER_ID from database.
        wa_access_token:    Store's Meta access token from database.

    Returns:
        True if Meta API accepted the message (HTTP 200), False otherwise.
    """
    api_url = f"{WHATSAPP_API_BASE}/{wa_phone_number_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type":    "individual",
        "to":                phone,
        "type":              "text",
        "text": {
            "preview_url": False,
            "body":        text,
        },
    }

    headers = {
        "Authorization": f"Bearer {wa_access_token}",
        "Content-Type":  "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(api_url, json=payload, headers=headers)

        if response.status_code == 200:
            logger.info(f"[WA] ✅ Sent to {phone} via phone_id={wa_phone_number_id}")
            return True
        else:
            logger.error(
                f"[WA] ❌ Send failed to {phone} | "
                f"HTTP {response.status_code} | Body: {response.text[:200]}"
            )
            return False

    except httpx.TimeoutException:
        logger.error(f"[WA] Timeout sending to {phone} — Meta API slow to respond")
        return False
    except httpx.ConnectError:
        logger.error(f"[WA] Connection error — Meta API unreachable")
        return False
    except Exception as e:
        logger.error(f"[WA] Unexpected error sending to {phone}: {e}")
        return False


async def mark_as_read(
    message_id: str,
    wa_phone_number_id: str,
    wa_access_token: str,
) -> bool:
    """
    Mark an incoming customer message as read (shows blue double tick).

    Called immediately after receiving a message for better UX.
    Non-critical — failure does not affect message processing.

    Args:
        message_id:         The 'id' field from the incoming Meta webhook message.
        wa_phone_number_id: Store's Meta PHONE_NUMBER_ID.
        wa_access_token:    Store's Meta access token.

    Returns:
        True if successful, False otherwise (non-critical).
    """
    api_url = f"{WHATSAPP_API_BASE}/{wa_phone_number_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "status":            "read",
        "message_id":        message_id,
    }

    headers = {
        "Authorization": f"Bearer {wa_access_token}",
        "Content-Type":  "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(api_url, json=payload, headers=headers)
        return response.status_code == 200
    except Exception as e:
        logger.warning(f"[WA] Could not mark message {message_id} as read: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# WEBHOOK PAYLOAD PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_incoming(payload: dict) -> dict | None:
    """
    Parse Meta's raw webhook POST payload into a clean, flat dict.

    Meta wraps all message data in a deeply nested structure:
        payload.entry[0].changes[0].value → messages, contacts, metadata

    This function flattens that structure and extracts everything needed
    for message processing and store identification.

    Args:
        payload: Raw JSON body from Meta's POST request to /webhook.

    Returns:
        Clean dict with keys:
            wa_phone_number_id  (str) — identifies which store this belongs to
            phone               (str) — sender's phone in international format
            name                (str) — sender's WhatsApp display name
            message             (str) — text content of the message
            message_id          (str) — unique message ID for read receipts
            type                (str) — message type: text|image|audio|interactive

        Returns None if:
            - Payload is a delivery/read status update (not a message)
            - No messages found in payload
            - Parse error occurred
    """
    try:
        entry    = payload.get("entry", [{}])[0]
        changes  = entry.get("changes", [{}])[0]
        value    = changes.get("value", {})
        metadata = value.get("metadata", {})

        # ── Status updates ─────────────────────────────────────────────────────
        # Meta sends read receipts and delivery updates through the same webhook.
        # These have 'statuses' but no 'messages'. Ignore them.
        if "statuses" in value and "messages" not in value:
            logger.debug("[WA] Skipping status update webhook event")
            return None

        messages = value.get("messages", [])
        contacts = value.get("contacts", [])

        if not messages:
            return None

        msg     = messages[0]
        contact = contacts[0] if contacts else {}
        msg_type = msg.get("type", "unknown")

        # ── Extract the phone_number_id from metadata ─────────────────────────
        # This is the KEY for multi-tenancy — identifies which WhatsApp number
        # received the message, which maps to a store in our database.
        wa_phone_number_id = metadata.get("phone_number_id", "")

        # ── Extract text based on message type ────────────────────────────────
        if msg_type == "text":
            text_content = msg.get("text", {}).get("body", "").strip()

        elif msg_type == "interactive":
            # Button or list reply from customer
            interactive = msg.get("interactive", {})
            if interactive.get("type") == "button_reply":
                text_content = interactive["button_reply"].get("title", "")
            elif interactive.get("type") == "list_reply":
                text_content = interactive["list_reply"].get("title", "")
            else:
                text_content = ""

        elif msg_type == "image":
            text_content = "[IMAGE]"     # Phase 2: extract caption or handle image

        elif msg_type == "audio":
            text_content = "[VOICE NOTE]"  # Phase 2: transcribe audio

        else:
            text_content = f"[{msg_type.upper()}]"

        return {
            "wa_phone_number_id": wa_phone_number_id,
            "phone":              msg.get("from", ""),
            "name":               contact.get("profile", {}).get("name", ""),
            "message":            text_content,
            "message_id":         msg.get("id", ""),
            "type":               msg_type,
        }

    except (IndexError, KeyError, TypeError) as e:
        logger.warning(f"[WA] Failed to parse webhook payload: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE FORMATTERS
# WhatsApp does not support HTML. These functions produce clean plain text
# using only WhatsApp-supported formatting (*bold*, _italic_).
# ═══════════════════════════════════════════════════════════════════════════════

def format_catalog_text(products: list, store_name: str = "Our Store") -> str:
    """
    Format the product catalog as a numbered WhatsApp message.

    Customers can reply with a number (1, 2, 3) or a product name to select.
    Uses the product's wa_display_text if available — a compact description
    optimised for WhatsApp — otherwise falls back to the full description.

    Args:
        products:   List of product dicts from the database.
        store_name: Display name of the store (shown in the header).

    Returns:
        Formatted catalog string ready to send as a WhatsApp message.
    """
    if not products:
        return (
            "😔 No products are available right now.\n"
            "Please check back soon or message us directly!"
        )

    lines = [f"🛍️ *{store_name} — Product Catalog*\n", "─" * 22]

    for i, product in enumerate(products, start=1):
        name        = product.get("name", "Unknown Product")
        price       = float(product.get("price", 0))
        compare     = product.get("compare_price")
        display_txt = (
            product.get("wa_display_text", "").strip()
            or product.get("description", "").strip()
        )

        # Price line — show "was ₦X" if compare_price is set
        if compare and float(compare) > price:
            price_line = f"₦{price:,.0f} ~~₦{float(compare):,.0f}~~"
        else:
            price_line = f"₦{price:,.0f}"

        lines.append(f"\n*{i}. {name}*")
        lines.append(f"   💰 {price_line}")
        if display_txt:
            # Truncate to 80 chars for WhatsApp readability
            short = display_txt[:80] + ("..." if len(display_txt) > 80 else "")
            lines.append(f"   📝 {short}")

    lines.append(f"\n{'─' * 22}")
    lines.append("Reply with the *number* or *name* of the item to order. 🛒")
    lines.append("Type *cancel* at any time to stop.")

    return "\n".join(lines)


def format_order_summary(
    product: dict,
    quantity: int,
    currency: str = "₦"
) -> str:
    """
    Format an order summary for customer review before confirmation.

    Displayed after quantity is collected, before asking for delivery address.
    Shows clear breakdown of product, quantity, unit price, and total.

    Args:
        product:  Product dict from database.
        quantity: Number of units requested.
        currency: Currency symbol. Default "₦" (Naira).

    Returns:
        Formatted order summary string.
    """
    name      = product.get("name", "Unknown Product")
    price     = float(product.get("price", 0))
    total     = price * quantity

    return (
        f"📋 *Order Summary*\n"
        f"{'─' * 22}\n"
        f"Product:  *{name}*\n"
        f"Quantity: *{quantity}*\n"
        f"Price:    {currency}{price:,.0f} each\n"
        f"{'─' * 22}\n"
        f"*Total: {currency}{total:,.0f}*\n\n"
        f"Reply *YES* to confirm ✅ or *CANCEL* to cancel ❌"
    )


def format_order_confirmed(
    order: dict,
    product: dict,
    currency: str = "₦"
) -> str:
    """
    Format the final order confirmation message after delivery address is collected.
    Last message in the order flow — includes the order reference number.

    Args:
        order:    Order dict from database (includes reference, quantity, address).
        product:  Product dict (for name and price calculation).
        currency: Currency symbol. Default "₦".

    Returns:
        Order confirmation string.
    """
    reference = order.get("reference", "WRN-XXXX")
    qty       = order.get("order_items", [{}])[0].get("quantity", 1) if order.get("order_items") else 1
    address   = order.get("delivery_address", "")
    prod_name = product.get("name", "Your item") if product else "Your item"
    total     = float(order.get("total_amount", 0))

    return (
        f"🎉 *Order Confirmed!*\n"
        f"{'─' * 22}\n"
        f"Reference: *{reference}*\n"
        f"Product:   {prod_name}\n"
        f"Quantity:  {qty}\n"
        f"Total:     {currency}{total:,.0f}\n"
        f"Address:   {address}\n"
        f"{'─' * 22}\n"
        f"We'll contact you shortly to arrange delivery. 📦\n"
        f"Thank you for shopping with us! 🙏"
    )


def format_order_status_message(orders: list, store_name: str = "Our Store") -> str:
    """
    Format a customer's order history for the order status inquiry response.

    Shows up to 3 most recent orders with reference, product, and status.
    Called when customer asks "where is my order" or similar.

    Args:
        orders:     List of order dicts (newest first, max 3 shown).
        store_name: Store display name.

    Returns:
        Formatted order status string.
    """
    if not orders:
        return (
            "You don't have any orders with us yet. 📦\n"
            "Type *catalog* to place your first order! 🛍️"
        )

    lines = [f"📦 *Your Recent Orders — {store_name}*\n"]

    for order in orders[:3]:
        ref    = order.get("reference", "N/A")
        status = order.get("status", "pending").replace("_", " ").title()
        total  = float(order.get("total_amount", 0))

        # Get first line item name (most common case: single-item orders)
        items = order.get("order_items", [])
        if items:
            item_name = items[0].get("product_name", "Unknown Product")
            qty       = items[0].get("quantity", 1)
            item_line = f"{item_name} (x{qty})"
        else:
            item_line = "Unknown Product"

        # Status emoji map
        status_emoji = {
            "Pending":    "🟡", "Confirmed":  "🔵",
            "Processing": "🟠", "Shipped":    "🚚",
            "Delivered":  "✅", "Cancelled":  "❌",
        }.get(status, "⚪")

        lines.append(f"{status_emoji} *{ref}* — {item_line}")
        lines.append(f"   Status: _{status}_ | Total: ₦{total:,.0f}\n")

    lines.append("Contact us directly for delivery updates on a specific order.")
    return "\n".join(lines)
