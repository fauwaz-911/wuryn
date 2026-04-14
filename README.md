# Wuryn Platform
### AI-Powered Social Commerce — Built by Caseer

---

## Table of Contents

1. [What Wuryn Is](#1-what-wuryn-is)
2. [Platform Architecture](#2-platform-architecture)
3. [Project Structure](#3-project-structure)
4. [Database Schema Reference](#4-database-schema-reference)
5. [Multi-Tenancy Explained](#5-multi-tenancy-explained)
6. [Environment Variables Reference](#6-environment-variables-reference)
7. [Phase 1 Setup Guide](#7-phase-1-setup-guide)
8. [Supabase Setup](#8-supabase-setup)
9. [Local Development](#9-local-development)
10. [Render.com Deployment](#10-rendercom-deployment)
11. [WhatsApp Webhook Configuration](#11-whatsapp-webhook-configuration)
12. [Connecting a Client Store](#12-connecting-a-client-store)
13. [Managing Store Data (SQL Reference)](#13-managing-store-data-sql-reference)
14. [Message Processing Pipeline](#14-message-processing-pipeline)
15. [WhatsApp Order Flow](#15-whatsapp-order-flow)
16. [Testing Guide](#16-testing-guide)
17. [Monitoring & Logs](#17-monitoring--logs)
18. [Development Standards](#18-development-standards)
19. [Build Roadmap](#19-build-roadmap)
20. [Troubleshooting](#20-troubleshooting)

---

## 1. What Wuryn Is

Wuryn is a multi-tenant social commerce platform that gives SMEs three things in one:

**1. CASIR WhatsApp Business Assistant**
An AI bot that lives inside a business's WhatsApp and handles customer inquiries, order processing, lead capture, and follow-ups — automatically.

**2. Wuryn Storefront**
A customer-facing e-commerce website where customers can browse products, add to cart, checkout, and track orders.

**3. Wuryn Dashboard**
A business owner control panel to manage products, view orders (from both WhatsApp and web), track leads, run marketing broadcasts, and view analytics.

All three systems share one Supabase PostgreSQL database. Orders placed via WhatsApp and the web appear in the same dashboard. Leads captured on WhatsApp and the website are in the same customer list.

---

## 2. Platform Architecture

```
                        ┌─────────────────────────────────────┐
                        │        SUPABASE (PostgreSQL)          │
                        │                                       │
                        │  stores · users · products · orders   │
                        │  customers · conversations · posts     │
                        │  reviews · follows · broadcasts       │
                        └──────────┬─────────────┬─────────────┘
                                   │             │
                    ┌──────────────┘             └──────────────┐
                    │                                           │
                    ▼                                           ▼
     ┌──────────────────────────┐              ┌───────────────────────────┐
     │  FastAPI Backend          │              │  React Frontends           │
     │  (Render Web Service)     │              │  (Render Static Sites)     │
     │                           │              │                            │
     │  /webhook  ← Meta API     │              │  /storefront  ← Customers  │
     │  /auth     ← Dashboard    │              │  /dashboard   ← Owners     │
     │  /dashboard ← Owners      │◄────────────►│                            │
     │  /storefront ← Customers  │   REST API   │  React + Vite + Tailwind   │
     │  /social   ← Feed         │              │                            │
     │  /analytics ← Reports     │              └───────────────────────────┘
     └──────────────────────────┘
                    │
                    │ Outbound API calls
                    ▼
     ┌──────────────────────────┐
     │  Meta WhatsApp Cloud API  │
     │  Groq (Llama3 8B)         │
     │  Google Gemini Flash      │
     └──────────────────────────┘
```

**Multi-tenant flow (WhatsApp):**
```
Customer WhatsApp message
    → Meta Cloud API
    → POST /webhook (FastAPI)
    → parse payload → extract wa_phone_number_id
    → look up store by wa_phone_number_id
    → process message in store context
    → send response using store's wa_access_token
```

---

## 3. Project Structure

```
wuryn-platform/
│
├── backend/                          ← FastAPI API server
│   ├── main.py                       ← App factory, middleware, router registration
│   ├── config.py                     ← All env vars, startup validation
│   ├── database.py                   ← All Supabase queries (multi-tenant)
│   │
│   ├── routers/
│   │   ├── webhook.py                ← WhatsApp Cloud API (Phase 1 — live)
│   │   ├── auth.py                   ← Login, register, JWT (Phase 2)
│   │   ├── dashboard.py              ← Owner management API (Phase 2)
│   │   ├── storefront.py             ← Customer store API (Phase 3)
│   │   ├── social.py                 ← Posts, reviews, follows (Phase 4)
│   │   └── analytics.py             ← Revenue, metrics (Phase 6)
│   │
│   └── modules/
│       ├── whatsapp.py               ← Meta API communication + formatters
│       ├── ai.py                     ← Groq + Gemini dual-provider + intent classifier
│       └── orders.py                 ← WhatsApp order flow state machine
│
├── frontend/
│   ├── storefront/                   ← Customer-facing React app (Phase 3)
│   └── dashboard/                    ← Business owner React app (Phase 2)
│
├── supabase/
│   └── schema.sql                    ← Full database schema + seed data
│
├── docs/                             ← Additional documentation
│
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 4. Database Schema Reference

### Table Overview

| Table | Purpose | Multi-tenant key |
|---|---|---|
| `stores` | Tenant root — one record per business | IS the root |
| `users` | Business owners and staff | via `store_members` |
| `store_members` | Links users to stores with roles | `store_id` |
| `categories` | Product categories | `store_id` |
| `products` | Product catalog | `store_id` |
| `product_images` | Multiple images per product | via `product_id` |
| `customers` | Unified customer records | `store_id` |
| `conversations` | WhatsApp state machine | `store_id` |
| `cart` | Web shopping cart (pre-checkout) | `store_id` |
| `cart_items` | Items in cart | via `cart_id` |
| `orders` | All orders (WhatsApp + web) | `store_id` |
| `order_items` | Line items per order | via `order_id` |
| `payments` | Payment records | `store_id` |
| `posts` | Social feed posts | `store_id` |
| `reviews` | Product reviews | `store_id` |
| `follows` | Customer → store follows | `store_id` |
| `post_likes` | Post likes | via `post_id` |
| `broadcasts` | Marketing campaigns | `store_id` |
| `broadcast_recipients` | Per-recipient delivery tracking | via `broadcast_id` |

### Key Design Decisions

**Order snapshots:** `order_items.product_name` and `order_items.unit_price` are copied from the product at the time of ordering. If the product price changes later, historical orders show the original price. This is correct accounting behaviour.

**Conversation state (JSONB context):** Rather than creating a new table for every possible state variable, we use a `context JSONB` column. During an order flow, it holds: `{"selected_product_id": "uuid", "quantity": 2, "product_price": 25000.0}`. It is wiped when state resets to idle.

**Customer unification:** A customer's `phone` is unique within a store (UNIQUE on `store_id, phone`). The same WhatsApp customer and web customer can be merged if they provide the same phone number during web checkout.

**Order reference:** Auto-generated as `WRN-XXXX` (e.g. WRN-0001) — human-readable, searchable, and shown to customers in order confirmations.

---

## 5. Multi-Tenancy Explained

Wuryn serves multiple client businesses from a single deployment. Here's exactly how it works at each layer:

### Database Layer
Every table that belongs to a store has a `store_id UUID` foreign key referencing `stores.id`. Every query in `database.py` takes `store_id` as its first argument and includes `.eq("store_id", store_id)` in the filter. It is architecturally impossible for Store A's products to appear in Store B's queries.

### WhatsApp Layer
Each store has a `wa_phone_number_id` stored in the `stores` table. When Meta sends a webhook POST, the payload contains `entry.changes.value.metadata.phone_number_id`. The webhook router extracts this and calls `get_store_by_wa_phone_id()` to identify which store the message belongs to. All subsequent processing uses that store's context, AI persona, and access token.

### API Layer (Phase 2)
When a business owner logs into the dashboard, they receive a JWT token containing their `store_id`. All dashboard API routes extract `store_id` from the token — the client cannot request data for a different store by changing a URL parameter.

### AI Layer
The AI system prompt is generated per-store from `stores.name`, `stores.business_type`, and `stores.description`. The Groq/Gemini AI believes it is the dedicated assistant for that specific business.

---

## 6. Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `APP_ENV` | No | `development` | `development` or `production` |
| `CORS_ORIGINS` | No | `localhost:3000,5173` | Comma-separated allowed origins |
| `SUPABASE_URL` | ✅ | — | Supabase project URL |
| `SUPABASE_KEY` | ✅ | — | Supabase anon/public key |
| `SUPABASE_SERVICE_KEY` | No | — | Supabase service role key (admin ops) |
| `GROQ_API_KEY` | ✅ | — | Groq API key (primary AI) |
| `GEMINI_API_KEY` | No | — | Gemini API key (fallback AI) |
| `JWT_SECRET` | No | — | Secret for JWT signing (Phase 2 auth) |
| `JWT_EXPIRY_DAYS` | No | `7` | Session duration in days |

**Note on WhatsApp credentials:** In multi-tenant mode, each store's `wa_phone_number_id`, `wa_access_token`, and `wa_verify_token` are stored in the `stores` database table — not in environment variables. This is how one deployment routes messages for multiple businesses.

---

## 7. Phase 1 Setup Guide

### Prerequisites checklist

- [ ] Python 3.11+ installed on your machine
- [ ] Git installed
- [ ] GitHub account (for Render auto-deploy)
- [ ] Meta Developer account (developers.facebook.com)
- [ ] Meta PHONE_NUMBER_ID ready: `1067561433107095`
- [ ] Groq account + API key (console.groq.com)
- [ ] Google AI Studio account + API key (aistudio.google.com)
- [ ] Supabase account (supabase.com)
- [ ] Render.com account (render.com)
- [ ] UptimeRobot account (uptimerobot.com)

---

## 8. Supabase Setup

### Step 1: Create new project

1. supabase.com → **New Project**
2. Name: `wuryn-platform`
3. Database password: generate a strong one and save it
4. Region: **West EU (Ireland)** — closest to Nigeria with low latency
5. Wait ~2 minutes for provisioning

### Step 2: Get your credentials

Go to **Project Settings → API:**
- Copy **Project URL** → `SUPABASE_URL`
- Copy **anon/public** key → `SUPABASE_KEY`
- Copy **service_role** key → `SUPABASE_SERVICE_KEY` (keep this strictly secret)

### Step 3: Run the schema

1. Go to **SQL Editor** in your Supabase dashboard
2. Click **New query**
3. Paste the entire contents of `supabase/schema.sql`
4. Click **Run** (Ctrl+Enter)

You should see a result table at the bottom showing:
```
table_name   | row_count
─────────────────────────
stores       | 1
categories   | 6
products     | 12
users        | 0
orders       | 0
customers    | 0
```

### Step 4: Add your Meta access token to the store

After completing Meta Developer Setup, update the Wuryn Gadget Store with your access token:

```sql
UPDATE stores
SET wa_access_token = 'YOUR_META_ACCESS_TOKEN_HERE'
WHERE slug = 'wuryn-gadgets';
```

This is the token from Meta Developer Console → WhatsApp → API Setup. Replace it with a permanent System User token before going live (see Section 11).

---

## 9. Local Development

### Step 1: Clone and set up

```bash
git clone https://github.com/yourusername/wuryn-platform.git
cd wuryn-platform

python3 -m venv venv
source venv/bin/activate         # Linux/Mac
# venv\Scripts\activate          # Windows

pip install -r requirements.txt
```

### Step 2: Configure environment

```bash
cp .env.example .env
nano .env    # Fill in SUPABASE_URL, SUPABASE_KEY, GROQ_API_KEY, GEMINI_API_KEY
```

### Step 3: Install and configure Ngrok

Ngrok exposes your local server to the internet via HTTPS.
Meta's webhook requires a public HTTPS URL — Ngrok provides this for free in development.

```bash
# Install on Kali Linux
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc \
  | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null \
  && echo "deb https://ngrok-agent.s3.amazonaws.com buster main" \
  | sudo tee /etc/apt/sources.list.d/ngrok.list \
  && sudo apt update && sudo apt install ngrok

# Authenticate
ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN

# Start tunnel (keep this terminal open)
ngrok http 8000
```

Note the HTTPS URL: `https://abc123.ngrok.io` — use this as your Meta webhook URL.

### Step 4: Start the server

Open a second terminal:

```bash
source venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

Expected startup output:
```
============================================================
  Wuryn Platform v1.0.0 — Starting up
  Environment: development
============================================================
  app_name             = Wuryn Platform
  app_version          = 1.0.0
  environment          = development
  ...
============================================================
  ✅ Wuryn Platform is ready to receive messages
============================================================
```

Visit http://localhost:8000/health to confirm the server is running.
Visit http://localhost:8000/docs for the interactive API documentation (development only).

---

## 10. Render.com Deployment

### Step 1: Push to GitHub

```bash
cd wuryn-platform
git init
git add .
git commit -m "[init] Wuryn Platform Phase 1 — WhatsApp bot + multi-tenant foundation"
git branch -M main
git remote add origin https://github.com/yourusername/wuryn-platform.git
git push -u origin main
```

**Ensure `.env` is in `.gitignore` and was NOT committed.**
Check with: `git log --oneline` — the .env file must not appear in the commit.

### Step 2: Create Render Web Service

1. render.com → **New** → **Web Service**
2. Connect your GitHub repository
3. Configure:

| Setting | Value |
|---|---|
| Name | `wuryn-backend` |
| Region | Oregon (US) or Frankfurt (EU) |
| Branch | `main` |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` |
| Instance Type | Free |

4. **Create Web Service**

### Step 3: Set environment variables on Render

Service → **Environment** → **Add Environment Variable:**

Add each variable from your `.env` file:
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `GROQ_API_KEY`
- `GEMINI_API_KEY`
- `APP_ENV` = `production`
- `CORS_ORIGINS` = your frontend URLs (once built)

Click **Save Changes** — Render redeploys automatically.

### Step 4: Verify deployment

Visit `https://wuryn-backend.onrender.com/health`

Expected response:
```json
{
  "status": "ok",
  "platform": "Wuryn Platform",
  "version": "1.0.0",
  "environment": "production"
}
```

### Step 5: Set up UptimeRobot keep-alive

Render's free tier sleeps after 15 minutes of inactivity. Cold start = 30–60 seconds = Meta webhook timeout = message dropped.

1. uptimerobot.com → **Add New Monitor**
2. Type: HTTP(s)
3. Name: `Wuryn Backend Keep-Alive`
4. URL: `https://wuryn-backend.onrender.com/health`
5. Interval: Every 5 minutes
6. Create Monitor

UptimeRobot pings every 5 minutes for free, keeping the service warm.

### Auto-deploy on push

Every `git push origin main` triggers automatic redeployment on Render. Zero manual intervention needed.

---

## 11. WhatsApp Webhook Configuration

### Step 1: Register webhook in Meta Developer Console

1. Meta Developer Console → Your App → **WhatsApp** → **Configuration**
2. **Webhook** section → **Edit**
3. Enter:
   - **Callback URL:** `https://wuryn-backend.onrender.com/webhook`
   - **Verify token:** `wuryn_verify_2025`
     (This matches the `wa_verify_token` seeded in the `stores` table)
4. Click **Verify and Save**

Meta sends GET /webhook with your verify token. The backend checks it against all active stores and returns the challenge if it matches.

### Step 2: Subscribe to webhook fields

After verification, under **Webhook fields:**
- `messages` ✅ — check this box and click **Save**

### Step 3: Create a permanent access token

The temporary token from Meta's Quickstart expires in 24 hours.

1. Meta Developer Console → **Business Settings** → **System Users**
2. **Add** → System User → Role: **Standard**
3. **Add Assets** → add your WhatsApp app → permissions:
   - `whatsapp_business_messaging` ✅
   - `whatsapp_business_management` ✅
4. **Generate Token** → select your app → **Generate**
5. Copy the token (it won't expire)

Update in Supabase:
```sql
UPDATE stores
SET wa_access_token = 'YOUR_PERMANENT_SYSTEM_USER_TOKEN'
WHERE slug = 'wuryn-gadgets';
```

### Step 4: Test the connection

1. Go to Meta Developer Console → WhatsApp → Quickstart
2. Add your personal phone number as a test recipient
3. Send "hi" to the Wuryn test number from your phone
4. You should receive the welcome message within 2 seconds

---

## 12. Connecting a Client Store

Each client business needs:
1. Their own WhatsApp Business phone number (not already on any WhatsApp)
2. A Meta Business Account
3. Their number connected to the Meta Cloud API

### Steps to onboard a new client

**1. Add their phone number to Meta:**
Meta Developer Console → WhatsApp → Phone Numbers → Add Phone Number
Verify via SMS/call. Note the new `PHONE_NUMBER_ID`.

**2. Insert their store record:**
```sql
INSERT INTO stores (
    name, slug, description, business_type,
    phone, email, city,
    wa_phone_number_id, wa_access_token, wa_verify_token,
    plan
) VALUES (
    'Client Business Name',
    'client-slug',                       -- URL-safe, unique
    'One sentence about what they sell.',
    'fashion',                           -- or food, beauty, electronics, etc.
    '0801234567',
    'client@email.com',
    'Lagos',
    'THEIR_PHONE_NUMBER_ID',
    'THEIR_META_ACCESS_TOKEN',
    'wuryn_verify_clientslug',           -- unique verify token per store
    'free'
);
```

**3. Update the webhook verify token in Meta:**
The client's webhook uses the same Render URL but a different verify_token.
Enter `wuryn_verify_clientslug` in their Meta webhook config — the multi-tenant
verification handler will match it to their store.

**4. Add their products:**
```sql
INSERT INTO products (store_id, name, description, price, available)
SELECT
    (SELECT id FROM stores WHERE slug = 'client-slug'),
    'Product Name',
    'Product description',
    15000.00,
    true;
```

**5. Test:**
Send "hi" to their WhatsApp number — confirm you receive their store's welcome message (not Wuryn Gadget Store's message).

---

## 13. Managing Store Data (SQL Reference)

### Products

```sql
-- View all products for Wuryn Gadget Store
SELECT name, price, available, featured, stock
FROM products
WHERE store_id = (SELECT id FROM stores WHERE slug = 'wuryn-gadgets')
ORDER BY name;

-- Make a product unavailable (soft delete — hides from customers)
UPDATE products
SET available = false
WHERE store_id = (SELECT id FROM stores WHERE slug = 'wuryn-gadgets')
AND name = 'Product Name';

-- Update a product price
UPDATE products
SET price = 450000.00, compare_price = 480000.00
WHERE store_id = (SELECT id FROM stores WHERE slug = 'wuryn-gadgets')
AND name = 'Samsung Galaxy A55 5G';
```

### Orders

```sql
-- View all orders with customer and product details
SELECT
    o.reference,
    o.channel,
    o.status,
    c.full_name AS customer,
    c.phone,
    o.total_amount,
    o.delivery_address,
    o.created_at
FROM orders o
JOIN customers c ON o.customer_id = c.id
WHERE o.store_id = (SELECT id FROM stores WHERE slug = 'wuryn-gadgets')
ORDER BY o.created_at DESC;

-- Update order status
UPDATE orders
SET status = 'shipped'
WHERE reference = 'WRN-0001';

-- Pending orders requiring action
SELECT reference, total_amount, created_at
FROM orders
WHERE store_id = (SELECT id FROM stores WHERE slug = 'wuryn-gadgets')
AND status = 'pending'
ORDER BY created_at ASC;
```

### Customers / Leads

```sql
-- View all leads captured from WhatsApp
SELECT phone, full_name, interests, purchase_intent, created_at
FROM customers
WHERE store_id = (SELECT id FROM stores WHERE slug = 'wuryn-gadgets')
ORDER BY created_at DESC;

-- Customers who completed orders (ready for marketing)
SELECT DISTINCT c.phone, c.full_name
FROM customers c
JOIN orders o ON c.id = o.customer_id
WHERE c.store_id = (SELECT id FROM stores WHERE slug = 'wuryn-gadgets')
AND o.status IN ('delivered', 'confirmed');
```

---

## 14. Message Processing Pipeline

Every incoming WhatsApp message follows this exact sequence:

```
1. META → POST /webhook
   ↓
2. FastAPI returns HTTP 200 immediately
   ↓ (background task starts)
3. parse_incoming(payload)
   Extracts: wa_phone_number_id, phone, name, message, message_id, type
   ↓
4. get_store_by_wa_phone_id(wa_phone_number_id)
   Looks up which store this message belongs to.
   If no store found → log error + return (message dropped)
   ↓
5. mark_as_read(message_id)           ← async, non-blocking
   ↓
6. get_or_create_customer(store_id, phone, name)
   Automatic lead capture — new customers are saved immediately
   ↓
7. get_conversation(store_id, phone)
   Retrieves: state, context
   ↓
8. _route_message(store, phone, customer_id, message, state, context)
   ┌─ Priority 1: is_in_order_flow(state)?
   │    → handle_order_flow()  (continue mid-order)
   ├─ Priority 2: shortcut keyword?
   │    → instant response (no AI call)
   └─ Priority 3: classify_intent(message)
        → route to appropriate handler
   ↓
9. send_message(phone, response, wa_phone_number_id, wa_access_token)
   ↓
10. update_conversation(store_id, phone, new_state, new_context)
```

**Timing:** Steps 2 (HTTP 200) and 3–10 (processing) run in parallel. Meta never waits more than ~50ms for the 200 response.

---

## 15. WhatsApp Order Flow

```
Customer: "I want to buy the Samsung phone"
    ↓ intent = ORDER_REQUEST, product_name = "Samsung phone"
    ↓ start_order_flow() — finds "Samsung Galaxy A55 5G"
Bot: "I found Samsung Galaxy A55 5G at ₦430,000 each. How many?"
    ↓ state = selected

Customer: "2"
    ↓ _handle_quantity() — valid, shows summary
Bot: [Order Summary: 2 × ₦430,000 = ₦860,000]
     "Reply YES to confirm or CANCEL to cancel."
    ↓ state = confirming

Customer: "yes"
    ↓ _handle_confirmation() — YES detected
Bot: "Confirmed! Please send your full delivery address..."
    ↓ state = collecting_address

Customer: "12 Aminu Kano Crescent, Wuse 2, Abuja FCT"
    ↓ _handle_address() — address valid (≥20 chars), order created
Bot: "🎉 Order Confirmed! Reference: WRN-0001 ..."
    ↓ state = idle, context = {}
```

**Cancellation:** At any state, customer can type `cancel`, `stop`, `no`, etc. to return to idle with no order created.

**Error recovery:** If a database error occurs during order creation, the customer receives a polite error message and state resets to idle. The error is logged with full context for debugging.

---

## 16. Testing Guide

### Local testing checklist

1. Start server + Ngrok
2. Register Ngrok URL as webhook in Meta
3. Add your number as a test recipient
4. Send these messages in order and verify each response:

| Message sent | Expected response |
|---|---|
| `hi` | Welcome message with menu |
| `catalog` | Numbered product list |
| `1` | "Great choice! How many?" (selects first product) |
| `2` | Order summary for 2 units |
| `yes` | Address request |
| `12 Main Street, Victoria Island, Lagos` | Order confirmation with WRN-XXXX |
| `my orders` | Order status list |
| `cancel` (mid-order) | Cancellation message, state resets |
| `how much is the JBL headphone?` | AI product answer with price |
| `[IMAGE]` | "I can only read text messages" |

### Verify database after testing

```sql
-- Check customer was captured
SELECT * FROM customers WHERE phone = 'YOUR_TEST_PHONE_NUMBER';

-- Check order was created
SELECT * FROM orders ORDER BY created_at DESC LIMIT 1;

-- Check conversation reset to idle
SELECT state, context FROM conversations WHERE customer_phone = 'YOUR_TEST_PHONE_NUMBER';
```

### Production testing

1. Update webhook URL in Meta to Render URL
2. Run same checklist
3. Check Render logs: Service → **Logs** tab

---

## 17. Monitoring & Logs

### Log prefix guide

| Prefix | Source | Meaning |
|---|---|---|
| `[PIPELINE]` | webhook.py | Message processing events |
| `[ROUTER]` | webhook.py | Intent routing decisions |
| `[ORDER]` | orders.py | Order flow state transitions |
| `[WA]` | whatsapp.py | Meta API send/receive events |
| `[AI]` | ai.py | AI provider calls and fallbacks |
| `[DB:store]` | database.py | Store lookup operations |
| `[DB:products]` | database.py | Product query operations |
| `[DB:customers]` | database.py | Customer/lead operations |
| `[DB:conversations]` | database.py | State machine operations |
| `[DB:orders]` | database.py | Order creation and updates |
| `[WEBHOOK]` | webhook.py | Verification and raw events |

### Key log events to monitor

```
✅ [PIPELINE] ✅ Processed   — successful message handled
✅ [DB:customers] New lead captured  — automatic lead saved
✅ [DB:orders] Order created  — new order placed
❌ [PIPELINE] No active store found  — misconfigured webhook
❌ [AI] Both providers failed  — Groq + Gemini both down
❌ [WA] Send failed  — token expired or Meta API error
⚠️  [AI] Groq failed — switching to Gemini  — normal fallback
```

---

## 18. Development Standards

These standards apply to ALL code in this repository:

**Code quality:**
- Every function has a complete docstring: purpose, Args, Returns, Raises
- Every module has a header comment explaining its role
- All exceptions are caught, logged with context, handled gracefully
- No hardcoded values — everything configurable lives in config.py or the database
- All database operations isolated in database.py — no raw Supabase calls elsewhere
- `store_id` is ALWAYS the first parameter of any database function

**Git discipline:**
- One logical change per commit
- Message format: `[module] what changed and why`
- Examples:
  - `[database] add get_featured_products for storefront homepage`
  - `[webhook] fix state reset on order cancellation`
  - `[schema] add delivery_fee column to orders table`

**Documentation:**
- README updated with every phase addition
- Schema changes documented in `supabase/schema.sql` with inline comments
- `CHANGELOG.md` updated per phase (see Phase 2 docs)

---

## 19. Build Roadmap

### Phase 1 — Foundation (CURRENT) ✅
- [x] Multi-tenant Supabase schema (19 tables)
- [x] FastAPI backend with router architecture
- [x] WhatsApp webhook (multi-tenant, dual-AI, state machine)
- [x] Automatic lead capture
- [x] Full order flow (5 states)
- [x] Pilot store: Wuryn Gadget Store (12 products)

### Phase 2 — Business Dashboard
- [ ] JWT authentication (business owner login)
- [ ] Dashboard API routes (products, orders, customers, settings)
- [ ] React dashboard frontend:
  - Overview (stats, pending orders, recent leads)
  - Product manager (add/edit/delete with image upload)
  - Orders view (real-time, status updates)
  - Customers/CRM table
  - Store settings (name, description, WhatsApp config)

### Phase 3 — Customer Storefront
- [ ] Storefront API routes (catalog, cart, checkout, order tracking)
- [ ] React storefront frontend:
  - Homepage (featured products + social feed)
  - Product listing + search + filter
  - Product detail + reviews
  - Cart + checkout
  - Order tracking page

### Phase 4 — Social Commerce Layer
- [ ] Posts feed (store owners post product showcases)
- [ ] Review system (verified purchase reviews)
- [ ] Follow stores
- [ ] Post likes

### Phase 5 — Payments + Broadcasts
- [ ] Paystack payment integration (web checkout)
- [ ] Manual payment recording (WhatsApp orders)
- [ ] Marketing broadcast system (WhatsApp campaigns)
- [ ] Scheduled broadcasts via APScheduler

### Phase 6 — Analytics + Polish
- [ ] Revenue analytics dashboard
- [ ] Conversion rate tracking
- [ ] Abandoned order follow-ups (WhatsApp)
- [ ] Multi-language AI responses (Hausa, Pidgin)
- [ ] Webhook idempotency (deduplicate Meta retries)

---

## 20. Troubleshooting

### Bot not responding

Check in order:
1. Is Render service running? Visit `/health`
2. Is UptimeRobot showing green status?
3. Is the webhook verified in Meta? (Green tick in console)
4. Is `wa_access_token` set in the `stores` table?
5. Is the token still valid? (Temporary tokens expire in 24h)
6. Check Render logs for `[PIPELINE] No active store found` errors

### "Verification failed" during webhook setup

The verify token in Meta doesn't match any store's `wa_verify_token`.
- Check the exact value in your `stores` table: `SELECT wa_verify_token FROM stores WHERE slug = 'wuryn-gadgets';`
- It should be `wuryn_verify_2025`
- Copy-paste this exactly into Meta's webhook verify token field

### Messages arrive but no response sent

Bot received the message but failed to send a response.
Look for `[WA] ❌ Send failed` in Render logs.
Most common causes:
- `wa_access_token` is null or expired in the `stores` table
- Temporary 24-hour token expired — replace with permanent System User token
- Customer number not in test recipients (development mode limitation)

### AI responses are slow

Groq failed and Gemini fallback is slower. Normal — check:
- Groq API key is valid in Render env vars
- Groq rate limit not exceeded (check console.groq.com)
- Render logs for `[AI] Groq failed` warnings

### Order not saved to database

A database error occurred during `create_order()`.
- Check Render logs for `[DB:orders] create_order failed`
- Verify the `customers` record exists (customer must be created before order)
- Verify foreign key constraints: product must exist and be available

---

*Wuryn Platform v1.0.0 — Phase 1*
*Built by Caseer | Abuja, Nigeria*
