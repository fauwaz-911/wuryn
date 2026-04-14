# Wuryn Platform — Changelog

All significant changes to the platform are documented here.
Format: `[YYYY-MM-DD] Phase X — Description`

---

## [2025] Phase 1 — Foundation

### Added
- Multi-tenant Supabase PostgreSQL schema (19 tables)
  - `stores` as tenant root with WhatsApp credentials per store
  - `products` with categories, images, stock tracking
  - `customers` unified across WhatsApp and web channels
  - `conversations` for WhatsApp state machine (JSONB context)
  - `orders` + `order_items` with price snapshots
  - `payments` scaffold for Phase 5 integration
  - Social tables: `posts`, `reviews`, `follows`, `post_likes`
  - Marketing: `broadcasts`, `broadcast_recipients`
  - Performance indexes on all common query patterns

- FastAPI backend with router-based architecture
  - `backend/main.py` — app factory, CORS, lifecycle management
  - `backend/config.py` — env validation with startup fail-fast
  - `backend/database.py` — full multi-tenant data access layer

- WhatsApp webhook router (`backend/routers/webhook.py`)
  - Multi-tenant store resolution via `wa_phone_number_id`
  - HTTP 200 immediate response + background task processing
  - Multi-tenant webhook verification (checks all stores' tokens)

- WhatsApp module (`backend/modules/whatsapp.py`)
  - Per-store `send_message()` with individual tokens
  - `parse_incoming()` extracts `wa_phone_number_id` for multi-tenancy
  - `mark_as_read()` for blue tick UX
  - Message formatters: catalog, order summary, order confirmation, order status

- AI module (`backend/modules/ai.py`)
  - Groq (primary) → Gemini (fallback) → static fallback architecture
  - Per-store AI persona via `build_store_system_prompt(store)`
  - `classify_intent()` returning structured JSON dict
  - `generate_contextual_response()` with live catalog injection

- Orders module (`backend/modules/orders.py`)
  - Full 5-state order flow: idle → browsing → selected → confirming → collecting_address
  - Multi-tenant: all DB calls scoped to store_id
  - Global cancel at any state
  - Quantity extraction (digits + word numbers)
  - Lead profile enrichment at key order milestones

- Pilot store: Wuryn Gadget Store
  - PHONE_NUMBER_ID: 1067561433107095
  - 6 categories, 12 products across mixed tech/lifestyle
  - Seeded via schema.sql DO block

### Infrastructure
- Render.com deployment configuration
- UptimeRobot keep-alive setup documented
- Ngrok local development workflow

---

## Upcoming — Phase 2 (Business Dashboard)

- JWT authentication for business owners
- Dashboard API: products CRUD, order management, customer CRM
- React dashboard frontend
- Store settings management
- Image upload via Supabase Storage
