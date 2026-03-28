-- ═══════════════════════════════════════════════════════════════════════════════
-- WURYN PLATFORM — UNIFIED MULTI-TENANT DATABASE SCHEMA
-- ═══════════════════════════════════════════════════════════════════════════════
-- Platform:    Wuryn — AI-Powered Social Commerce
-- Database:    Supabase (PostgreSQL 15+)
-- Version:     1.0.0 (Phase 1)
-- Last updated: 2025
--
-- ARCHITECTURE OVERVIEW:
--   This schema supports three systems sharing one database:
--     1. CASIR WhatsApp Bot       — AI business assistant per store
--     2. Wuryn Storefront         — Customer-facing e-commerce web app
--     3. Wuryn Dashboard          — Business owner management interface
--
-- MULTI-TENANCY:
--   Every table that belongs to a store has a `store_id` foreign key.
--   All queries are scoped by store_id — data from one store is never
--   visible to another store unless explicitly designed to be (e.g. social feed).
--
-- EXECUTION ORDER:
--   Run this entire file in Supabase SQL Editor as a single transaction.
--   Safe to re-run only if you DROP all tables first (handled below).
-- ═══════════════════════════════════════════════════════════════════════════════


-- ─── SAFETY: Drop existing tables in reverse dependency order ─────────────────
-- Uncomment this block ONLY if you are wiping the schema to start fresh.
-- DO NOT run this in production without a backup.
/*
DROP TABLE IF EXISTS broadcast_recipients CASCADE;
DROP TABLE IF EXISTS broadcasts CASCADE;
DROP TABLE IF EXISTS follows CASCADE;
DROP TABLE IF EXISTS reviews CASCADE;
DROP TABLE IF EXISTS posts CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS cart_items CASCADE;
DROP TABLE IF EXISTS cart CASCADE;
DROP TABLE IF EXISTS conversations CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS product_images CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS categories CASCADE;
DROP TABLE IF EXISTS store_members CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS stores CASCADE;
*/


-- ═══════════════════════════════════════════════════════════════════════════════
-- SECTION 1: TENANT ROOT
-- The `stores` table is the root of the entire multi-tenant hierarchy.
-- Every other table traces back to a store via store_id.
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE stores (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- Identity
    name                TEXT NOT NULL,
    slug                TEXT UNIQUE NOT NULL,       -- URL-safe identifier: "wuryn-gadgets"
                                                    -- Used in storefront URLs: wuryn.com/store/wuryn-gadgets
    description         TEXT DEFAULT '',
    business_type       TEXT DEFAULT 'retail',      -- retail | fashion | food | electronics | beauty | lifestyle

    -- Contact
    phone               TEXT DEFAULT '',            -- Business contact number (not WhatsApp API number)
    email               TEXT DEFAULT '',
    address             TEXT DEFAULT '',
    city                TEXT DEFAULT '',
    country             TEXT DEFAULT 'Nigeria',

    -- Branding
    logo_url            TEXT DEFAULT '',            -- Stored in Supabase Storage
    banner_url          TEXT DEFAULT '',            -- Storefront banner image
    primary_color       TEXT DEFAULT '#10b981',     -- Hex — used in storefront theming
    currency            TEXT DEFAULT 'NGN',         -- NGN | USD | AED

    -- WhatsApp Integration
    -- Each store has its own Meta phone number connected to the WhatsApp Cloud API.
    -- The webhook router uses wa_phone_number_id to identify which store a message belongs to.
    wa_phone_number_id  TEXT UNIQUE,               -- Meta PHONE_NUMBER_ID (e.g. 1067561433107095)
    wa_access_token     TEXT,                       -- Per-store Meta access token (encrypted at rest in production)
    wa_verify_token     TEXT,                       -- Per-store webhook verify token

    -- Platform Settings
    active              BOOLEAN DEFAULT true,       -- False = store suspended
    plan                TEXT DEFAULT 'free',        -- free | starter | growth | pro
    plan_expires_at     TIMESTAMPTZ,                -- NULL = no expiry (free plan)

    -- Metadata
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE stores IS
    'Tenant root table. Every store on Wuryn has one record here. '
    'All other business data references store_id.';

COMMENT ON COLUMN stores.slug IS
    'URL-safe store identifier. Used in storefront routing. Must be unique platform-wide.';

COMMENT ON COLUMN stores.wa_phone_number_id IS
    'Meta WhatsApp Cloud API phone number ID. Used by the webhook router to '
    'identify which store an incoming WhatsApp message belongs to.';


-- ═══════════════════════════════════════════════════════════════════════════════
-- SECTION 2: USERS & AUTHENTICATION
-- Business owners and staff who manage stores.
-- Supabase Auth handles passwords — this table stores profile data only.
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE users (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
                -- In production: link this to Supabase Auth uid
                -- auth.uid() can be used in RLS policies

    email       TEXT UNIQUE NOT NULL,
    full_name   TEXT DEFAULT '',
    phone       TEXT DEFAULT '',
    avatar_url  TEXT DEFAULT '',
    role        TEXT DEFAULT 'owner',               -- owner | admin (platform-level roles)
    active      BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE users IS
    'Business owners and platform admins. Passwords are managed by Supabase Auth. '
    'This table stores profile and role data only.';


CREATE TABLE store_members (
    -- Junction table: links users to stores they can manage.
    -- One user can manage multiple stores (agency use case).
    -- One store can have multiple users (owner + staff).
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    store_id    UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'owner',      -- owner | manager | staff
                                                    -- owner: full access
                                                    -- manager: manage products/orders, no billing
                                                    -- staff: view orders only
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(store_id, user_id)                       -- One membership record per user per store
);

COMMENT ON TABLE store_members IS
    'Links users to stores with a role. One user can manage multiple stores. '
    'One store can have multiple users with different roles.';


-- ═══════════════════════════════════════════════════════════════════════════════
-- SECTION 3: PRODUCT CATALOG
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE categories (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    store_id    UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,                      -- Display name: "Mobile Phones"
    slug        TEXT NOT NULL,                      -- URL slug: "mobile-phones"
    description TEXT DEFAULT '',
    sort_order  INTEGER DEFAULT 0,                  -- Controls display order in storefront
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(store_id, slug)                          -- Slug must be unique within a store
);

COMMENT ON TABLE categories IS
    'Product categories scoped to a store. Each store defines its own categories.';


CREATE TABLE products (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    store_id        UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    category_id     UUID REFERENCES categories(id) ON DELETE SET NULL,

    -- Core fields
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    price           NUMERIC(12,2) NOT NULL CHECK (price >= 0),
    compare_price   NUMERIC(12,2),                  -- Original price for "was ₦X" display
    sku             TEXT DEFAULT '',                -- Stock-keeping unit (optional)

    -- Inventory
    stock           INTEGER DEFAULT -1,             -- -1 = unlimited; 0 = out of stock
    track_stock     BOOLEAN DEFAULT false,          -- If true, decrement stock on each order

    -- Visibility
    available       BOOLEAN DEFAULT true,           -- False = hidden from storefront + WhatsApp
    featured        BOOLEAN DEFAULT false,          -- Pinned to storefront hero/featured section

    -- WhatsApp display
    wa_display_text TEXT DEFAULT '',               -- Custom short description for WhatsApp catalog
                                                    -- Falls back to description if empty

    -- Metadata
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE products IS
    'Product catalog. Shared by WhatsApp bot and web storefront. '
    'The bot reads available=true products; storefront uses same data.';

COMMENT ON COLUMN products.compare_price IS
    'Original/crossed-out price shown alongside current price for discount display.';

COMMENT ON COLUMN products.wa_display_text IS
    'Short description optimised for WhatsApp. If empty, full description is used.';


CREATE TABLE product_images (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    product_id  UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    url         TEXT NOT NULL,                      -- Supabase Storage URL
    alt_text    TEXT DEFAULT '',                    -- Accessibility + SEO
    is_primary  BOOLEAN DEFAULT false,              -- True = main display image
    sort_order  INTEGER DEFAULT 0,                  -- Controls gallery order
    created_at  TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE product_images IS
    'Multiple images per product. One image should have is_primary=true. '
    'Stored in Supabase Storage bucket.';


-- ═══════════════════════════════════════════════════════════════════════════════
-- SECTION 4: CUSTOMERS & CONVERSATIONS
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE customers (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    store_id        UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,

    -- Identity (at least one of phone or email must be present)
    phone           TEXT DEFAULT '',               -- International format: 2348012345678
    email           TEXT DEFAULT '',
    full_name       TEXT DEFAULT '',

    -- Acquisition
    source          TEXT DEFAULT 'whatsapp',       -- whatsapp | web | social | manual
    referrer        TEXT DEFAULT '',               -- UTM source or referral slug

    -- Lead enrichment (auto-populated by AI during WhatsApp conversations)
    interests       TEXT DEFAULT '',               -- Products/categories they've shown interest in
    purchase_intent TEXT DEFAULT '',               -- browsing | enquiring | completed_order | churned

    -- Engagement stats (updated via triggers or application logic)
    total_orders    INTEGER DEFAULT 0,
    total_spent     NUMERIC(12,2) DEFAULT 0,
    last_seen_at    TIMESTAMPTZ DEFAULT now(),

    -- Metadata
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    -- Constraints: phone or email must be unique within a store
    -- A customer from WhatsApp and web can be merged if both have same phone
    UNIQUE(store_id, phone),
    UNIQUE(store_id, email)
);

COMMENT ON TABLE customers IS
    'Unified customer record across WhatsApp and web channels. '
    'A customer is scoped to a store — the same person shopping at two stores has two records. '
    'WhatsApp and web records can be merged by matching phone number.';


CREATE TABLE conversations (
    -- Tracks each customer''s position in the WhatsApp order flow state machine.
    -- One record per customer per store, upserted on every message.
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    store_id        UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    customer_phone  TEXT NOT NULL,

    -- State machine
    state           TEXT NOT NULL DEFAULT 'idle',
    -- Valid states: idle | browsing | selected | confirming | collecting_address
    -- See modules/orders.py for full state transition documentation

    -- Context: temporary data held between messages during an active order flow
    -- Example: {"selected_product_id": "uuid", "quantity": 2, "product_price": 25000}
    context         JSONB NOT NULL DEFAULT '{}',

    last_message_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE(store_id, customer_phone)
);

COMMENT ON TABLE conversations IS
    'WhatsApp conversation state machine. One record per customer per store. '
    'Upserted on every incoming message. Context holds in-progress order data.';


-- ═══════════════════════════════════════════════════════════════════════════════
-- SECTION 5: CART & ORDERS
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE cart (
    -- Web storefront cart (before checkout). Not used by WhatsApp flow.
    -- WhatsApp orders go directly to the orders table via the state machine.
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    store_id    UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    customer_id UUID REFERENCES customers(id) ON DELETE CASCADE,
    session_id  TEXT DEFAULT '',                    -- Guest cart identifier (browser session)
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE cart_items (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    cart_id     UUID NOT NULL REFERENCES cart(id) ON DELETE CASCADE,
    product_id  UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity    INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    added_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE(cart_id, product_id)                     -- One line item per product per cart
);

COMMENT ON TABLE cart IS
    'Web storefront shopping cart. Temporary — cleared after checkout. '
    'WhatsApp orders bypass this table and write directly to orders.';


CREATE TABLE orders (
    -- Unified orders table for ALL channels (WhatsApp + web).
    -- The channel column identifies the source.
    -- Both channels produce identical order records — reports and dashboards
    -- see all orders regardless of how they were placed.
    id               UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    store_id         UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    customer_id      UUID REFERENCES customers(id) ON DELETE SET NULL,

    -- Channel identification
    channel          TEXT NOT NULL DEFAULT 'whatsapp',  -- whatsapp | web
    reference        TEXT UNIQUE,                        -- Human-readable: "WRN-0001"
                                                         -- Generated by application on insert

    -- Status lifecycle
    status           TEXT NOT NULL DEFAULT 'pending',
    -- pending → confirmed → processing → shipped → delivered
    --                                              ↘ cancelled (from any state)

    -- Financials
    subtotal         NUMERIC(12,2) NOT NULL DEFAULT 0,  -- Sum of order_items.subtotal
    delivery_fee     NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_amount     NUMERIC(12,2) NOT NULL DEFAULT 0,  -- subtotal + delivery_fee

    -- Delivery
    delivery_address TEXT DEFAULT '',
    delivery_notes   TEXT DEFAULT '',                    -- e.g. "Call before delivery"

    -- Internal notes (business owner only — not shown to customer)
    staff_notes      TEXT DEFAULT '',

    -- Metadata
    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE orders IS
    'Unified orders table. Both WhatsApp bot and web storefront write here. '
    'channel column identifies source. reference is the human-readable order ID shown to customers.';


CREATE TABLE order_items (
    -- Line items for each order. One row per product per order.
    -- Product name and price are snapshotted at time of order —
    -- so changing a product''s price later doesn''t affect historical orders.
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    order_id        UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id      UUID REFERENCES products(id) ON DELETE SET NULL,

    -- Snapshots (frozen at order time — do not reference live product data)
    product_name    TEXT NOT NULL,                  -- Snapshot of products.name
    unit_price      NUMERIC(12,2) NOT NULL,         -- Snapshot of products.price

    quantity        INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    subtotal        NUMERIC(12,2) NOT NULL          -- unit_price × quantity
);

COMMENT ON TABLE order_items IS
    'Line items per order. Product name and price are snapshotted at order time — '
    'historical orders are unaffected by future product price changes.';


-- ═══════════════════════════════════════════════════════════════════════════════
-- SECTION 6: PAYMENTS
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE payments (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    order_id    UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    store_id    UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,

    provider    TEXT NOT NULL DEFAULT 'manual',     -- manual | paystack | flutterwave | transfer
    reference   TEXT UNIQUE,                        -- Provider transaction reference
    amount      NUMERIC(12,2) NOT NULL,
    currency    TEXT DEFAULT 'NGN',

    status      TEXT NOT NULL DEFAULT 'pending',    -- pending | success | failed | refunded
    paid_at     TIMESTAMPTZ,                        -- Set when status → success

    -- Provider response (raw JSON for debugging and reconciliation)
    provider_response JSONB DEFAULT '{}',

    created_at  TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE payments IS
    'Payment records linked to orders. Phase 1 supports manual/bank transfer. '
    'Paystack/Flutterwave integration in Phase 5.';


-- ═══════════════════════════════════════════════════════════════════════════════
-- SECTION 7: SOCIAL COMMERCE
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE posts (
    -- Store owners create posts to showcase products and promotions.
    -- Displayed in the social feed on the Wuryn storefront.
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    store_id    UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    product_id  UUID REFERENCES products(id) ON DELETE SET NULL,

    caption     TEXT DEFAULT '',
    media_url   TEXT DEFAULT '',                    -- Supabase Storage URL
    media_type  TEXT DEFAULT 'image',               -- image | video
    post_type   TEXT DEFAULT 'product',             -- product | promotion | announcement | lifestyle

    -- Engagement counters (denormalised for query performance)
    likes_count   INTEGER DEFAULT 0,
    shares_count  INTEGER DEFAULT 0,
    views_count   INTEGER DEFAULT 0,

    published     BOOLEAN DEFAULT true,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE posts IS
    'Social feed posts created by store owners. Can be linked to a product. '
    'Displayed in Wuryn storefront social feed.';


CREATE TABLE reviews (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    store_id    UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    product_id  UUID REFERENCES products(id) ON DELETE CASCADE,
    order_id    UUID REFERENCES orders(id) ON DELETE SET NULL,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,

    rating      SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title       TEXT DEFAULT '',
    comment     TEXT DEFAULT '',

    -- verified = customer actually ordered this product
    -- Set automatically when review is linked to a completed order
    verified    BOOLEAN DEFAULT false,
    published   BOOLEAN DEFAULT true,              -- Business owner can hide reviews

    created_at  TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE reviews IS
    'Product reviews from customers. verified=true means the customer has a '
    'completed order for this product — shown as "Verified Purchase".';


CREATE TABLE follows (
    -- Customers can follow stores to receive updates in their feed.
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    store_id    UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(store_id, customer_id)
);

COMMENT ON TABLE follows IS
    'Customers follow stores to receive social feed updates. '
    'Unique constraint prevents duplicate follows.';


CREATE TABLE post_likes (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    post_id     UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(post_id, customer_id)
);

COMMENT ON TABLE post_likes IS
    'Tracks which customers liked which posts. '
    'posts.likes_count is updated by application logic on insert/delete here.';


-- ═══════════════════════════════════════════════════════════════════════════════
-- SECTION 8: MARKETING & BROADCASTS
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE broadcasts (
    id           UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    store_id     UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,

    title        TEXT NOT NULL,                     -- Internal name (not shown to customers)
    message      TEXT NOT NULL,                     -- Message body sent to customers
    channel      TEXT NOT NULL DEFAULT 'whatsapp',  -- whatsapp | email | both

    -- Scheduling
    status       TEXT NOT NULL DEFAULT 'draft',     -- draft | scheduled | sending | sent | failed
    scheduled_at TIMESTAMPTZ,                       -- NULL = send immediately on publish
    sent_at      TIMESTAMPTZ,

    -- Stats (updated as sending progresses)
    total_recipients  INTEGER DEFAULT 0,
    delivered_count   INTEGER DEFAULT 0,
    failed_count      INTEGER DEFAULT 0,

    created_at   TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE broadcasts IS
    'Marketing broadcast campaigns. Supports WhatsApp and email. '
    'Phase 1: manual trigger. Phase 5: scheduled sending via APScheduler.';


CREATE TABLE broadcast_recipients (
    id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    broadcast_id  UUID NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
    customer_id   UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,

    delivered     BOOLEAN DEFAULT false,
    delivered_at  TIMESTAMPTZ,
    failed        BOOLEAN DEFAULT false,
    error_message TEXT DEFAULT '',

    UNIQUE(broadcast_id, customer_id)
);

COMMENT ON TABLE broadcast_recipients IS
    'Individual delivery records per customer per broadcast. '
    'Enables delivery tracking and retry logic.';


-- ═══════════════════════════════════════════════════════════════════════════════
-- SECTION 9: PERFORMANCE INDEXES
-- Created on columns used in WHERE clauses and JOINs across the application.
-- ═══════════════════════════════════════════════════════════════════════════════

-- Stores
CREATE INDEX idx_stores_slug         ON stores(slug);
CREATE INDEX idx_stores_wa_phone     ON stores(wa_phone_number_id);
CREATE INDEX idx_stores_active       ON stores(active);

-- Products
CREATE INDEX idx_products_store      ON products(store_id);
CREATE INDEX idx_products_available  ON products(store_id, available);
CREATE INDEX idx_products_featured   ON products(store_id, featured);
CREATE INDEX idx_products_category   ON products(category_id);

-- Customers
CREATE INDEX idx_customers_store     ON customers(store_id);
CREATE INDEX idx_customers_phone     ON customers(store_id, phone);
CREATE INDEX idx_customers_email     ON customers(store_id, email);
CREATE INDEX idx_customers_source    ON customers(store_id, source);

-- Conversations
CREATE INDEX idx_conversations_store ON conversations(store_id, customer_phone);
CREATE INDEX idx_conversations_state ON conversations(store_id, state);

-- Orders
CREATE INDEX idx_orders_store        ON orders(store_id);
CREATE INDEX idx_orders_status       ON orders(store_id, status);
CREATE INDEX idx_orders_customer     ON orders(customer_id);
CREATE INDEX idx_orders_channel      ON orders(store_id, channel);
CREATE INDEX idx_orders_created      ON orders(store_id, created_at DESC);

-- Order items
CREATE INDEX idx_order_items_order   ON order_items(order_id);
CREATE INDEX idx_order_items_product ON order_items(product_id);

-- Social
CREATE INDEX idx_posts_store         ON posts(store_id, created_at DESC);
CREATE INDEX idx_posts_product       ON posts(product_id);
CREATE INDEX idx_reviews_product     ON reviews(product_id);
CREATE INDEX idx_reviews_store       ON reviews(store_id);
CREATE INDEX idx_follows_store       ON follows(store_id);
CREATE INDEX idx_follows_customer    ON follows(customer_id);

-- Broadcasts
CREATE INDEX idx_broadcasts_store    ON broadcasts(store_id);
CREATE INDEX idx_broadcasts_status   ON broadcasts(store_id, status);


-- ═══════════════════════════════════════════════════════════════════════════════
-- SECTION 10: SEED DATA
-- Development and pilot data for the Wuryn Gadget Store.
-- ═══════════════════════════════════════════════════════════════════════════════

-- ─── Pilot Store: Wuryn Gadget Store ─────────────────────────────────────────
INSERT INTO stores (
    name, slug, description, business_type,
    phone, email, city, country,
    primary_color, currency,
    wa_phone_number_id, wa_verify_token,
    plan
) VALUES (
    'Wuryn Gadget Store',
    'wuryn-gadgets',
    'Your go-to store for the latest gadgets, accessories, and lifestyle tech in Nigeria.',
    'lifestyle',
    '',                                         -- Add business phone after setup
    '',                                         -- Add business email after setup
    'Abuja',
    'Nigeria',
    '#6366f1',                                  -- Indigo — Wuryn brand color
    'NGN',
    '1067561433107095',                         -- Meta PHONE_NUMBER_ID
    'wuryn_verify_2025',                        -- Webhook verify token
    'free'
);


-- ─── Product Categories ───────────────────────────────────────────────────────
-- Get the store ID first (used in subsequent inserts)
DO $$
DECLARE
    store_uuid UUID;
BEGIN
    SELECT id INTO store_uuid FROM stores WHERE slug = 'wuryn-gadgets';

    -- Categories
    INSERT INTO categories (store_id, name, slug, sort_order) VALUES
        (store_uuid, 'Mobile Phones',     'mobile-phones',     1),
        (store_uuid, 'Accessories',       'accessories',        2),
        (store_uuid, 'Audio & Sound',     'audio-sound',        3),
        (store_uuid, 'Smart Devices',     'smart-devices',      4),
        (store_uuid, 'Fashion Tech',      'fashion-tech',       5),
        (store_uuid, 'Lifestyle',         'lifestyle',          6);


    -- ─── Sample Products ──────────────────────────────────────────────────────
    -- 12 realistic products across all categories with Nigerian Naira pricing

    -- Mobile Phones
    INSERT INTO products (store_id, category_id, name, description, price, compare_price, stock, available, featured, wa_display_text) VALUES
        (
            store_uuid,
            (SELECT id FROM categories WHERE store_id = store_uuid AND slug = 'mobile-phones'),
            'Samsung Galaxy A55 5G',
            'Samsung Galaxy A55 5G — 8GB RAM, 256GB storage, 50MP triple camera, 5000mAh battery. Available in Awesome Navy and Awesome Iceblue.',
            430000.00, 480000.00, 15, true, true,
            'Samsung Galaxy A55 5G | 8GB/256GB | 50MP camera | ₦430,000'
        ),
        (
            store_uuid,
            (SELECT id FROM categories WHERE store_id = store_uuid AND slug = 'mobile-phones'),
            'Tecno Camon 30 Pro',
            'Tecno Camon 30 Pro — 12GB RAM, 256GB storage, 50MP AI portrait camera. Comes with 45W fast charger.',
            185000.00, 210000.00, 20, true, false,
            'Tecno Camon 30 Pro | 12GB/256GB | 50MP AI cam | ₦185,000'
        ),
        (
            store_uuid,
            (SELECT id FROM categories WHERE store_id = store_uuid AND slug = 'mobile-phones'),
            'iPhone 15 (128GB)',
            'Apple iPhone 15 — 128GB, Dynamic Island, 48MP main camera, A16 Bionic chip. UK used, Grade A condition.',
            750000.00, NULL, 5, true, true,
            'iPhone 15 128GB | Grade A | Dynamic Island | ₦750,000'
        );

    -- Accessories
    INSERT INTO products (store_id, category_id, name, description, price, compare_price, stock, available, featured, wa_display_text) VALUES
        (
            store_uuid,
            (SELECT id FROM categories WHERE store_id = store_uuid AND slug = 'accessories'),
            'Anker 65W USB-C Charger',
            'Anker Nano Pro 65W GaN charger — charges laptop, phone, and tablet simultaneously. Foldable plug, travel-ready.',
            18500.00, 22000.00, 50, true, false,
            'Anker 65W GaN Charger | 3-port | Laptop + Phone | ₦18,500'
        ),
        (
            store_uuid,
            (SELECT id FROM categories WHERE store_id = store_uuid AND slug = 'accessories'),
            'Tempered Glass Screen Protector (Universal)',
            '9H hardness tempered glass screen protector. Available for Samsung, iPhone, and Tecno models. Specify your model when ordering.',
            2500.00, NULL, 200, true, false,
            'Tempered Glass | 9H hardness | All models | ₦2,500'
        ),
        (
            store_uuid,
            (SELECT id FROM categories WHERE store_id = store_uuid AND slug = 'accessories'),
            'MagSafe-Compatible Phone Case',
            'Premium silicone phone case with MagSafe magnet ring. Compatible with iPhone 13/14/15 series. Available in 6 colours.',
            8500.00, 12000.00, 80, true, false,
            'MagSafe Silicone Case | iPhone 13/14/15 | 6 colours | ₦8,500'
        );

    -- Audio
    INSERT INTO products (store_id, category_id, name, description, price, compare_price, stock, available, featured, wa_display_text) VALUES
        (
            store_uuid,
            (SELECT id FROM categories WHERE store_id = store_uuid AND slug = 'audio-sound'),
            'JBL Tune 520BT Wireless Headphones',
            'JBL Tune 520BT — 57 hours playtime, foldable design, JBL Pure Bass sound, fast charging (5min = 1hr playtime).',
            35000.00, 42000.00, 25, true, true,
            'JBL Tune 520BT | 57hr battery | Pure Bass | ₦35,000'
        ),
        (
            store_uuid,
            (SELECT id FROM categories WHERE store_id = store_uuid AND slug = 'audio-sound'),
            'TWS Earbuds Pro (ANC)',
            'True wireless earbuds with Active Noise Cancellation, 30hr total playtime, IPX5 water resistance. Compatible with all Bluetooth devices.',
            15000.00, 20000.00, 40, true, false,
            'TWS ANC Earbuds | 30hr battery | IPX5 | ₦15,000'
        );

    -- Smart Devices
    INSERT INTO products (store_id, category_id, name, description, price, compare_price, stock, available, featured, wa_display_text) VALUES
        (
            store_uuid,
            (SELECT id FROM categories WHERE store_id = store_uuid AND slug = 'smart-devices'),
            'Smart Watch Pro X7',
            'Full-touch smartwatch — heart rate, SpO2, sleep tracking, 7-day battery, 100+ sport modes. Works with Android and iOS.',
            22000.00, 28000.00, 35, true, true,
            'Smart Watch Pro X7 | Health tracking | 7-day battery | ₦22,000'
        ),
        (
            store_uuid,
            (SELECT id FROM categories WHERE store_id = store_uuid AND slug = 'smart-devices'),
            'Mini Portable Bluetooth Speaker',
            'Compact waterproof Bluetooth speaker — 12hr playtime, IPX7, 360° stereo sound. Perfect for outdoor use.',
            12000.00, 15000.00, 60, true, false,
            'Mini BT Speaker | IPX7 waterproof | 12hr | ₦12,000'
        );

    -- Fashion Tech
    INSERT INTO products (store_id, category_id, name, description, price, compare_price, stock, available, featured, wa_display_text) VALUES
        (
            store_uuid,
            (SELECT id FROM categories WHERE store_id = store_uuid AND slug = 'fashion-tech'),
            'Tech Crossbody Bag with USB Port',
            'Anti-theft crossbody bag with built-in USB charging port, hidden pockets, and laptop compartment (up to 10"). Water-resistant.',
            18000.00, 24000.00, 30, true, false,
            'Tech Crossbody Bag | USB port | Anti-theft | ₦18,000'
        ),
        (
            store_uuid,
            (SELECT id FROM categories WHERE store_id = store_uuid AND slug = 'fashion-tech'),
            'LED Strip Lights (5m Smart RGB)',
            '5-metre smart LED strip lights — app-controlled, music sync, 16 million colours, works with Alexa & Google Home.',
            9500.00, 14000.00, 100, true, false,
            'Smart LED Strip 5m | App controlled | Music sync | ₦9,500'
        );

END $$;


-- ─── Verification Query ───────────────────────────────────────────────────────
-- Run this after executing the schema to confirm everything was created correctly.
-- Expected: 1 store, 6 categories, 12 products

SELECT
    'stores'     AS table_name, COUNT(*) AS row_count FROM stores
UNION ALL SELECT 'categories',  COUNT(*) FROM categories
UNION ALL SELECT 'products',    COUNT(*) FROM products
UNION ALL SELECT 'users',       COUNT(*) FROM users
UNION ALL SELECT 'orders',      COUNT(*) FROM orders
UNION ALL SELECT 'customers',   COUNT(*) FROM customers
ORDER BY table_name;
