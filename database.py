"""
Wuryn Platform — Database Module
==================================
Central data access layer for the entire Wuryn platform.
All Supabase interactions go through this module — no raw database
calls are made anywhere else in the codebase.

ARCHITECTURE:
    Every public function in this module is scoped to a store_id.
    This is the core of multi-tenancy: a query for products always
    filters by store_id, ensuring stores never see each other's data.

FUNCTION NAMING CONVENTION:
    get_*       — fetch one or many records
    create_*    — insert a new record
    update_*    — modify an existing record
    delete_*    — remove a record
    upsert_*    — insert or update based on unique constraint

ERROR HANDLING:
    All functions catch exceptions, log them with full context, and either:
    - Return a safe default (None, [], {}) for read operations
    - Re-raise for write operations (caller must handle and inform the user)

ADDING A NEW FUNCTION:
    1. Follow the store_id-first argument convention
    2. Write a complete docstring (purpose, args, returns, raises)
    3. Wrap in try/except with a descriptive log message
    4. Return a typed value — never return raw Supabase response objects
"""

import logging
from supabase import create_client, Client
from backend.config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)


# ─── Supabase Client ──────────────────────────────────────────────────────────
# Initialised once at module import — reused across all requests.
# The Supabase Python client is thread-safe for concurrent use.
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ═══════════════════════════════════════════════════════════════════════════════
# STORE FUNCTIONS
# Used by the webhook router to resolve which store owns an incoming message.
# ═══════════════════════════════════════════════════════════════════════════════

def get_store_by_wa_phone_id(wa_phone_number_id: str) -> dict | None:
    """
    Find a store record by its WhatsApp phone number ID.

    This is the entry point for ALL incoming WhatsApp messages.
    The Meta webhook payload contains the phone_number_id — we use it
    to identify which store the message belongs to, then scope all
    subsequent queries to that store's ID.

    Args:
        wa_phone_number_id: The Meta PHONE_NUMBER_ID string from the webhook payload.
                            Example: "1067561433107095"

    Returns:
        Full store dict if found and active, None otherwise.
        None is also returned if the store exists but active=false (suspended).
    """
    try:
        result = (
            supabase.table("stores")
            .select("*")
            .eq("wa_phone_number_id", wa_phone_number_id)
            .eq("active", True)
            .single()
            .execute()
        )
        return result.data
    except Exception as e:
        logger.error(
            f"[DB:store] get_store_by_wa_phone_id failed "
            f"(phone_id={wa_phone_number_id}): {e}"
        )
        return None


def get_store_by_slug(slug: str) -> dict | None:
    """
    Find a store by its URL slug.

    Used by the storefront API to load store configuration when a
    customer visits a store's page (e.g. /store/wuryn-gadgets).

    Args:
        slug: URL-safe store identifier. Example: "wuryn-gadgets"

    Returns:
        Store dict if found and active, None otherwise.
    """
    try:
        result = (
            supabase.table("stores")
            .select("*")
            .eq("slug", slug)
            .eq("active", True)
            .single()
            .execute()
        )
        return result.data
    except Exception as e:
        logger.error(f"[DB:store] get_store_by_slug failed (slug={slug}): {e}")
        return None


def get_store_by_id(store_id: str) -> dict | None:
    """
    Fetch a store by its primary key UUID.

    Used internally after the store has already been identified,
    e.g. when refreshing store config during a long-running session.

    Args:
        store_id: Store UUID string.

    Returns:
        Store dict or None.
    """
    try:
        result = (
            supabase.table("stores")
            .select("*")
            .eq("id", store_id)
            .single()
            .execute()
        )
        return result.data
    except Exception as e:
        logger.error(f"[DB:store] get_store_by_id failed (id={store_id}): {e}")
        return None


def update_store(store_id: str, data: dict) -> dict | None:
    """
    Update a store's configuration fields.

    Called from the dashboard settings endpoint when a business owner
    updates their store name, description, contact details, etc.

    Args:
        store_id: Store UUID.
        data:     Dict of fields to update. Only include fields being changed.
                  Example: {"name": "New Name", "description": "Updated desc"}

    Returns:
        Updated store dict on success, None on failure.
    """
    try:
        data["updated_at"] = "now()"
        result = (
            supabase.table("stores")
            .update(data)
            .eq("id", store_id)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"[DB:store] update_store failed (id={store_id}): {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# PRODUCT FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_products(store_id: str, available_only: bool = True) -> list:
    """
    Fetch all products for a store, ordered alphabetically by name.

    Used by both the WhatsApp bot (to display catalog) and the
    storefront API (to render product listing page).

    Args:
        store_id:       Store UUID — all results are scoped to this store.
        available_only: If True (default), return only available=True products.
                        Set False for dashboard views where all products are shown.

    Returns:
        List of product dicts. Empty list if none found or on error.
        Each dict includes: id, name, description, price, compare_price,
                            available, featured, stock, wa_display_text,
                            category_id, created_at.
    """
    try:
        query = (
            supabase.table("products")
            .select("*, categories(name, slug)")
            .eq("store_id", store_id)
            .order("name")
        )
        if available_only:
            query = query.eq("available", True)

        result = query.execute()
        return result.data or []

    except Exception as e:
        logger.error(
            f"[DB:products] get_products failed "
            f"(store={store_id}, available_only={available_only}): {e}"
        )
        return []


def get_featured_products(store_id: str, limit: int = 6) -> list:
    """
    Fetch featured products for a store.

    Used on the storefront homepage hero/featured section.
    Returns only available products with featured=True.

    Args:
        store_id: Store UUID.
        limit:    Maximum number of featured products to return. Default 6.

    Returns:
        List of product dicts (up to `limit`). Empty list on error.
    """
    try:
        result = (
            supabase.table("products")
            .select("*, product_images(url, is_primary)")
            .eq("store_id", store_id)
            .eq("available", True)
            .eq("featured", True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"[DB:products] get_featured_products failed (store={store_id}): {e}")
        return []


def get_product_by_id(store_id: str, product_id: str) -> dict | None:
    """
    Fetch a single product by UUID, scoped to a store.

    The store_id check ensures a product from Store A cannot be
    accessed via Store B's API — cross-store data leakage prevention.

    Args:
        store_id:   Store UUID — must match product's store.
        product_id: Product UUID.

    Returns:
        Product dict with joined images, or None if not found.
    """
    try:
        result = (
            supabase.table("products")
            .select("*, product_images(url, is_primary, alt_text, sort_order), categories(name, slug)")
            .eq("store_id", store_id)
            .eq("id", product_id)
            .single()
            .execute()
        )
        return result.data
    except Exception as e:
        logger.error(
            f"[DB:products] get_product_by_id failed "
            f"(store={store_id}, product={product_id}): {e}"
        )
        return None


def find_product_by_name(store_id: str, name: str) -> dict | None:
    """
    Search for an available product by approximate name match (case-insensitive).

    Used by the WhatsApp bot when a customer mentions a product by name
    and the intent classifier extracts it. "samsung a55" matches "Samsung Galaxy A55 5G".

    Uses PostgreSQL's ILIKE operator for case-insensitive partial matching.
    Returns the first match — for ambiguous matches (multiple results),
    the bot should show the full catalog.

    Args:
        store_id: Store UUID.
        name:     Product name search string (can be partial, case-insensitive).

    Returns:
        First matching available product dict, or None if no match found.
    """
    try:
        result = (
            supabase.table("products")
            .select("*")
            .eq("store_id", store_id)
            .eq("available", True)
            .ilike("name", f"%{name}%")
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(
            f"[DB:products] find_product_by_name failed "
            f"(store={store_id}, name='{name}'): {e}"
        )
        return None


def create_product(store_id: str, data: dict) -> dict:
    """
    Insert a new product into the catalog.

    Called from the dashboard API when a business owner adds a product.
    The store_id is always injected server-side — never trusted from client input.

    Args:
        store_id: Store UUID (injected from authenticated session — not from request body).
        data:     Product fields dict. Must include: name, price.
                  Optional: description, category_id, stock, available, featured,
                             wa_display_text, compare_price.

    Returns:
        Newly created product dict including generated id and timestamps.

    Raises:
        Exception: If the insert fails (e.g. missing required field, constraint violation).
                   Caller must handle and return appropriate HTTP error.
    """
    try:
        data["store_id"] = store_id
        result = supabase.table("products").insert(data).execute()
        logger.info(f"[DB:products] Product created: '{data.get('name')}' in store {store_id}")
        return result.data[0]
    except Exception as e:
        logger.error(
            f"[DB:products] create_product failed "
            f"(store={store_id}, name='{data.get('name')}'): {e}"
        )
        raise


def update_product(store_id: str, product_id: str, data: dict) -> dict | None:
    """
    Update a product's fields.

    The store_id + product_id combination ensures a business owner
    can only update products that belong to their store.

    Args:
        store_id:   Store UUID.
        product_id: Product UUID to update.
        data:       Dict of fields to update (partial update — only changed fields).

    Returns:
        Updated product dict, or None if product not found or update failed.
    """
    try:
        data["updated_at"] = "now()"
        result = (
            supabase.table("products")
            .update(data)
            .eq("store_id", store_id)
            .eq("id", product_id)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(
            f"[DB:products] update_product failed "
            f"(store={store_id}, product={product_id}): {e}"
        )
        return None


def delete_product(store_id: str, product_id: str) -> bool:
    """
    Delete a product from the catalog.

    Hard delete — this is permanent. For soft-delete behaviour,
    use update_product with available=False instead.
    Note: Products referenced in existing order_items cannot be
    deleted due to the foreign key constraint — use available=False.

    Args:
        store_id:   Store UUID.
        product_id: Product UUID to delete.

    Returns:
        True if deleted, False if product not found or error.
    """
    try:
        supabase.table("products").delete().eq("store_id", store_id).eq("id", product_id).execute()
        logger.info(f"[DB:products] Product deleted: {product_id} from store {store_id}")
        return True
    except Exception as e:
        logger.error(
            f"[DB:products] delete_product failed "
            f"(store={store_id}, product={product_id}): {e}"
        )
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_or_create_customer(
    store_id: str,
    phone: str,
    name: str = "",
    source: str = "whatsapp"
) -> dict:
    """
    Retrieve a customer by phone, or create a new record if first contact.

    This is the primary lead capture mechanism — every WhatsApp number
    that contacts a store is automatically saved here without any manual action.
    The business owner's customer database grows automatically.

    Args:
        store_id: Store UUID — customer is scoped to this store.
        phone:    Customer phone in international format (e.g. "2348012345678").
        name:     WhatsApp display name (may be empty string).
        source:   Acquisition channel. Default "whatsapp".
                  Other values: "web", "social", "manual".

    Returns:
        Customer dict from database (existing or newly created).

    Raises:
        Exception: If database insert fails. Should not happen in normal operation.
    """
    try:
        # Attempt to fetch existing customer
        result = (
            supabase.table("customers")
            .select("*")
            .eq("store_id", store_id)
            .eq("phone", phone)
            .execute()
        )

        if result.data:
            customer = result.data[0]
            # Update name if we now have one and previously didn't
            if name and not customer.get("full_name"):
                supabase.table("customers").update(
                    {"full_name": name, "updated_at": "now()", "last_seen_at": "now()"}
                ).eq("id", customer["id"]).execute()
                customer["full_name"] = name
            else:
                # Update last seen timestamp on every contact
                supabase.table("customers").update(
                    {"last_seen_at": "now()"}
                ).eq("id", customer["id"]).execute()
            return customer

        # New customer — insert record (automatic lead capture)
        new_customer = {
            "store_id":  store_id,
            "phone":     phone,
            "full_name": name,
            "source":    source,
        }
        insert_result = supabase.table("customers").insert(new_customer).execute()
        logger.info(
            f"[DB:customers] New lead captured: {phone} ({name or 'no name'}) "
            f"via {source} for store {store_id}"
        )
        return insert_result.data[0]

    except Exception as e:
        logger.error(
            f"[DB:customers] get_or_create_customer failed "
            f"(store={store_id}, phone={phone}): {e}"
        )
        raise


def get_customers(store_id: str, limit: int = 100, offset: int = 0) -> list:
    """
    Fetch a paginated list of customers for a store.

    Used by the dashboard CRM view. Returns customers ordered by
    most recent contact first.

    Args:
        store_id: Store UUID.
        limit:    Maximum records to return. Default 100.
        offset:   Number of records to skip (for pagination). Default 0.

    Returns:
        List of customer dicts ordered by last_seen_at DESC.
    """
    try:
        result = (
            supabase.table("customers")
            .select("*")
            .eq("store_id", store_id)
            .order("last_seen_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"[DB:customers] get_customers failed (store={store_id}): {e}")
        return []


def update_customer_profile(
    store_id: str,
    phone: str,
    interests: str = None,
    purchase_intent: str = None
) -> None:
    """
    Enrich a customer's lead profile with interest and intent data.

    Called automatically by the WhatsApp bot during conversations
    as the customer reveals what they're looking for.
    Builds a richer lead database over time for marketing broadcasts.

    Args:
        store_id:        Store UUID.
        phone:           Customer phone number.
        interests:       Product name or category they're interested in.
        purchase_intent: Intent level. Values: browsing | enquiring |
                         completed_order | cancelled | churned.
    """
    try:
        update_data: dict = {"updated_at": "now()"}
        if interests:
            update_data["interests"] = interests
        if purchase_intent:
            update_data["purchase_intent"] = purchase_intent

        supabase.table("customers").update(update_data).eq(
            "store_id", store_id
        ).eq("phone", phone).execute()

    except Exception as e:
        logger.warning(
            f"[DB:customers] update_customer_profile failed "
            f"(store={store_id}, phone={phone}): {e}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CONVERSATION STATE FUNCTIONS (WhatsApp state machine)
# ═══════════════════════════════════════════════════════════════════════════════

def get_conversation(store_id: str, customer_phone: str) -> dict:
    """
    Retrieve the current WhatsApp conversation state for a customer.

    The state machine tracks where a customer is in the order flow:
        idle → browsing → selected → confirming → collecting_address → idle

    Context is a JSONB field holding temporary data between messages
    (e.g. selected product ID, quantity) without requiring additional DB queries.

    Args:
        store_id:       Store UUID.
        customer_phone: Customer's phone number.

    Returns:
        Conversation dict with keys: store_id, customer_phone, state, context.
        Returns a safe default idle state if no record exists (new customer).
    """
    try:
        result = (
            supabase.table("conversations")
            .select("*")
            .eq("store_id", store_id)
            .eq("customer_phone", customer_phone)
            .execute()
        )
        if result.data:
            return result.data[0]

        # No record — return safe default for first-time customers
        return {
            "store_id":       store_id,
            "customer_phone": customer_phone,
            "state":          "idle",
            "context":        {},
        }

    except Exception as e:
        logger.error(
            f"[DB:conversations] get_conversation failed "
            f"(store={store_id}, phone={customer_phone}): {e}"
        )
        # Return safe default on error to keep the bot running
        return {"store_id": store_id, "customer_phone": customer_phone, "state": "idle", "context": {}}


def update_conversation(
    store_id: str,
    customer_phone: str,
    state: str,
    context: dict = None
) -> None:
    """
    Persist the updated conversation state after processing a message.

    Uses Supabase upsert on (store_id, customer_phone) — inserts if no
    record exists, updates if it does. Called at the end of every message
    processing cycle regardless of what state transition occurred.

    Args:
        store_id:       Store UUID.
        customer_phone: Customer's phone number.
        state:          New state string.
        context:        Updated context dict. Pass {} to clear context.
    """
    try:
        supabase.table("conversations").upsert(
            {
                "store_id":        store_id,
                "customer_phone":  customer_phone,
                "state":           state,
                "context":         context or {},
                "last_message_at": "now()",
            },
            on_conflict="store_id,customer_phone"
        ).execute()
    except Exception as e:
        logger.error(
            f"[DB:conversations] update_conversation failed "
            f"(store={store_id}, phone={customer_phone}, state={state}): {e}"
        )


def reset_conversation(store_id: str, customer_phone: str) -> None:
    """
    Reset a customer's conversation to idle with empty context.

    Called after order completion, cancellation, or unrecoverable error.
    Shorthand for update_conversation(..., state="idle", context={}).

    Args:
        store_id:       Store UUID.
        customer_phone: Customer's phone number.
    """
    update_conversation(store_id, customer_phone, "idle", {})


# ═══════════════════════════════════════════════════════════════════════════════
# ORDER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_order_reference(store_id: str) -> str:
    """
    Generate a human-readable order reference number.

    Format: WRN-XXXX where XXXX is zero-padded count of store's orders.
    Example: WRN-0001, WRN-0042, WRN-1337

    Args:
        store_id: Store UUID.

    Returns:
        Reference string. Falls back to WRN-XXXX with random suffix if count fails.
    """
    try:
        result = (
            supabase.table("orders")
            .select("id", count="exact")
            .eq("store_id", store_id)
            .execute()
        )
        count = (result.count or 0) + 1
        return f"WRN-{count:04d}"
    except Exception:
        import random
        return f"WRN-{random.randint(1000, 9999)}"


def create_order(
    store_id: str,
    customer_id: str,
    product: dict,
    quantity: int,
    delivery_address: str,
    channel: str = "whatsapp",
) -> dict:
    """
    Create a new order with a single product line item.

    Handles both the orders table insert and the order_items insert
    as a logical unit. The order reference is auto-generated.
    Product name and price are snapshotted to prevent price drift.

    Args:
        store_id:         Store UUID.
        customer_id:      Customer UUID (from customers table).
        product:          Full product dict from database.
        quantity:         Number of units ordered.
        delivery_address: Full delivery address as provided by customer.
        channel:          Order source. "whatsapp" | "web". Default "whatsapp".

    Returns:
        Newly created order dict including id and reference.

    Raises:
        Exception: If either insert fails. Caller must handle.
    """
    try:
        unit_price = float(product.get("price", 0))
        subtotal   = unit_price * quantity
        reference  = _generate_order_reference(store_id)

        # Insert order record
        order_result = supabase.table("orders").insert({
            "store_id":         store_id,
            "customer_id":      customer_id,
            "channel":          channel,
            "reference":        reference,
            "status":           "pending",
            "subtotal":         subtotal,
            "delivery_fee":     0,               # Phase 5: calculate delivery fee
            "total_amount":     subtotal,
            "delivery_address": delivery_address,
        }).execute()

        order = order_result.data[0]

        # Insert order line item (snapshot product name + price)
        supabase.table("order_items").insert({
            "order_id":     order["id"],
            "product_id":   product.get("id"),
            "product_name": product.get("name", "Unknown Product"),  # Snapshot
            "unit_price":   unit_price,                              # Snapshot
            "quantity":     quantity,
            "subtotal":     subtotal,
        }).execute()

        logger.info(
            f"[DB:orders] Order created: {reference} | "
            f"Store: {store_id} | Customer: {customer_id} | "
            f"Product: {product.get('name')} x{quantity} | "
            f"Total: ₦{subtotal:,.0f} | Channel: {channel}"
        )

        return order

    except Exception as e:
        logger.error(
            f"[DB:orders] create_order failed "
            f"(store={store_id}, customer={customer_id}): {e}"
        )
        raise


def get_orders(
    store_id: str,
    status: str = None,
    limit: int = 50,
    offset: int = 0
) -> list:
    """
    Fetch orders for a store, with optional status filter.

    Used by the dashboard orders page. Returns newest orders first.
    Joins customers table to include customer name and phone.

    Args:
        store_id: Store UUID.
        status:   Optional filter. Values: pending | confirmed | processing |
                  shipped | delivered | cancelled. None = all statuses.
        limit:    Maximum records. Default 50.
        offset:   Skip count for pagination. Default 0.

    Returns:
        List of order dicts with joined customer and order_items data.
    """
    try:
        query = (
            supabase.table("orders")
            .select("*, customers(full_name, phone, email), order_items(*, products(name))")
            .eq("store_id", store_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if status:
            query = query.eq("status", status)

        result = query.execute()
        return result.data or []

    except Exception as e:
        logger.error(
            f"[DB:orders] get_orders failed "
            f"(store={store_id}, status={status}): {e}"
        )
        return []


def get_customer_orders(store_id: str, customer_id: str, limit: int = 5) -> list:
    """
    Fetch a customer's most recent orders.

    Used by the WhatsApp bot to respond to "where is my order?" messages.
    Also used on the storefront order history page.

    Args:
        store_id:    Store UUID.
        customer_id: Customer UUID.
        limit:       Maximum orders to return. Default 5.

    Returns:
        List of order dicts (newest first) with order_items included.
    """
    try:
        result = (
            supabase.table("orders")
            .select("*, order_items(product_name, unit_price, quantity, subtotal)")
            .eq("store_id", store_id)
            .eq("customer_id", customer_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(
            f"[DB:orders] get_customer_orders failed "
            f"(store={store_id}, customer={customer_id}): {e}"
        )
        return []


def update_order_status(store_id: str, order_id: str, status: str, notes: str = None) -> bool:
    """
    Update an order's status.

    Called from the dashboard when a business owner processes an order.
    Valid status transitions: pending → confirmed → processing → shipped → delivered
    Any status can transition to: cancelled

    Args:
        store_id:  Store UUID (prevents cross-store order manipulation).
        order_id:  Order UUID.
        status:    New status string.
        notes:     Optional staff notes to attach to the update.

    Returns:
        True if updated, False if order not found or update failed.
    """
    try:
        update_data: dict = {"status": status, "updated_at": "now()"}
        if notes:
            update_data["staff_notes"] = notes

        result = (
            supabase.table("orders")
            .update(update_data)
            .eq("store_id", store_id)
            .eq("id", order_id)
            .execute()
        )
        logger.info(
            f"[DB:orders] Order {order_id} status → {status} "
            f"(store={store_id})"
        )
        return bool(result.data)
    except Exception as e:
        logger.error(
            f"[DB:orders] update_order_status failed "
            f"(store={store_id}, order={order_id}, status={status}): {e}"
        )
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYTICS FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_store_analytics(store_id: str) -> dict:
    """
    Compute key business metrics for the dashboard overview.

    Queries multiple tables and returns a consolidated summary dict.
    In Phase 6, this will be replaced with a proper analytics layer
    using Supabase Views or pre-computed materialised tables.

    Args:
        store_id: Store UUID.

    Returns:
        Dict with keys:
            total_orders     (int)   — all-time order count
            pending_orders   (int)   — orders awaiting action
            total_customers  (int)   — total unique customers
            total_revenue    (float) — sum of all delivered order totals
            whatsapp_orders  (int)   — orders placed via WhatsApp
            web_orders       (int)   — orders placed via web
    """
    try:
        # Total orders
        orders_result = (
            supabase.table("orders")
            .select("id, status, channel, total_amount", count="exact")
            .eq("store_id", store_id)
            .execute()
        )
        orders = orders_result.data or []
        total_orders    = orders_result.count or 0
        pending_orders  = sum(1 for o in orders if o["status"] == "pending")
        whatsapp_orders = sum(1 for o in orders if o["channel"] == "whatsapp")
        web_orders      = sum(1 for o in orders if o["channel"] == "web")
        total_revenue   = sum(
            float(o["total_amount"])
            for o in orders
            if o["status"] == "delivered"
        )

        # Total customers
        customers_result = (
            supabase.table("customers")
            .select("id", count="exact")
            .eq("store_id", store_id)
            .execute()
        )
        total_customers = customers_result.count or 0

        return {
            "total_orders":     total_orders,
            "pending_orders":   pending_orders,
            "total_customers":  total_customers,
            "total_revenue":    total_revenue,
            "whatsapp_orders":  whatsapp_orders,
            "web_orders":       web_orders,
        }

    except Exception as e:
        logger.error(f"[DB:analytics] get_store_analytics failed (store={store_id}): {e}")
        return {
            "total_orders": 0, "pending_orders": 0, "total_customers": 0,
            "total_revenue": 0, "whatsapp_orders": 0, "web_orders": 0,
        }
