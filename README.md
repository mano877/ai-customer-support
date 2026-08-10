<div align="center">

# 🤖 AI Customer Care — Backend API

A production-ready AI Customer Care Bot backend built with **FastAPI**, following
**Clean Architecture**, **SOLID** principles and the **Repository pattern**. Pairs with the
[CareConsole frontend](https://github.com/mano877/ai-customer-care-frontend) — a role-aware
customer self-service portal and support agent/admin console.

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?logo=fastapi)](https://fastapi.tiangolo.com)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00)](https://www.sqlalchemy.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![Pytest](https://img.shields.io/badge/Pytest-361%20passing-0A9EDC?logo=pytest&logoColor=white)](https://pytest.org)

</div>

---

## Tech Stack

- Python 3.12+ · FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic v2
- JWT auth (PyJWT) with rotating refresh tokens + Passlib (bcrypt) hashing
- PostgreSQL (production) · SQLite (tests) · Docker / Docker Compose
- Pytest · Ruff · Structured request logging

## Architecture

```
app/
├── api/           # Routers (thin, orchestration only)
├── core/          # Config, security, exceptions, logging
├── db/            # Engine, session, declarative base
├── models/        # SQLAlchemy 2.0 ORM models
├── schemas/       # Pydantic v2 request/response models
├── repositories/  # Data access (repository pattern)
├── services/      # Business logic
├── dependencies/  # Auth & role dependencies (DI)
├── middleware/    # Request logging
├── ai/            # AI service layer (later module)
├── utils/         # Misc helpers
└── tests/         # Pytest suite
```

Layering rules:

- Routers → Services → Repositories → ORM. Never the other way around.
- Business logic lives in services, data access in repositories.
- `Base.metadata.create_all` is never used — **Alembic migrations only**.

## Modules

| # | Module | Status |
|---|--------|--------|
| 1 | Authentication | ✅ Complete |
| 2 | Customers | ✅ Complete |
| 3 | Products | ✅ Complete |
| 4 | Orders | ✅ Complete |
| 5 | Support Tickets | ✅ Complete |
| 6 | Chat | ✅ Complete |
| 7 | Knowledge Base | ✅ Complete |
| 8 | Notifications | ✅ Complete |
| 9 | Feedback | ✅ Complete |
| 10 | AI Service | ✅ Complete |

## Quickstart (local, without Docker)

```bash
uv sync                      # install dependencies
uv run alembic upgrade head  # run migrations
uv run uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs · Health: http://localhost:8000/health

### Environment

Copy `.env.example` to `.env` and adjust values (especially `JWT_SECRET_KEY`).
`EXPOSE_OTP_IN_RESPONSE` is a **dev-only** convenience that returns the
registration OTP in the API response — disable it in production.

## Quickstart (Docker)

```bash
docker compose up --build
```

`api` runs `alembic upgrade head` automatically before starting. Add
`--profile full` to also start Redis (reserved for later modules).

## Authentication flow

1. `POST /api/v1/auth/register` → creates a `customer` account, returns an OTP (dev only)
2. `POST /api/v1/auth/verify-otp` → activates the account, returns `access_token` + `refresh_token`
3. `POST /api/v1/auth/login` → email/password → token pair
4. Use `Authorization: Bearer <access_token>` on protected routes
5. `POST /api/v1/auth/refresh` → rotates the refresh token (old one is revoked)
6. `POST /api/v1/auth/logout` → revokes the refresh token

### Endpoints (v1)

```
POST   /api/v1/auth/register
POST   /api/v1/auth/verify-otp
POST   /api/v1/auth/login
POST   /api/v1/auth/refresh
POST   /api/v1/auth/logout
GET    /api/v1/auth/me

GET    /api/v1/customers/me
PATCH  /api/v1/customers/me
GET    /api/v1/customers/addresses
POST   /api/v1/customers/addresses
PATCH  /api/v1/customers/addresses/{id}
DELETE /api/v1/customers/addresses/{id}

GET    /api/v1/products
GET    /api/v1/products/{id}
GET    /api/v1/products/search?q=...
GET    /api/v1/products/recommendations

GET    /api/v1/knowledge/search?q=...
GET    /api/v1/knowledge/articles/{id}

POST   /api/v1/orders                 (auth, customer-owned)
GET    /api/v1/orders
GET    /api/v1/orders/{id}
GET    /api/v1/orders/{id}/tracking
POST   /api/v1/orders/{id}/cancel
POST   /api/v1/orders/{id}/return

POST   /api/v1/chat/conversations                            (auth)
GET    /api/v1/chat/conversations                            (auth)
GET    /api/v1/chat/conversations/{id}                       (auth)
POST   /api/v1/chat/conversations/{id}/messages              (auth)
POST   /api/v1/chat/conversations/{id}/escalate              (auth)
POST   /api/v1/chat/conversations/{id}/resolve               (auth)
POST   /api/v1/chat/conversations/{id}/feedback              (auth)
GET    /api/v1/chat/agent/conversations?status=escalated     (agent+)
GET    /api/v1/chat/agent/conversations/{id}                 (agent+)
POST   /api/v1/chat/agent/conversations/{id}/claim           (agent+)
POST   /api/v1/chat/agent/conversations/{id}/messages        (agent+)
POST   /api/v1/chat/agent/conversations/{id}/resolve         (agent+)

POST   /api/v1/tickets                    (auth, customer-owned)
GET    /api/v1/tickets?status=&priority=&category=&limit=&offset=
GET    /api/v1/tickets/{id}
PATCH  /api/v1/tickets/{id}
POST   /api/v1/tickets/{id}/comments

GET    /api/v1/notifications?is_read=&type=&limit=&offset=   (auth)
PATCH  /api/v1/notifications/{id}/read                        (auth)

POST   /api/v1/feedback                                     (auth, own conversation)
GET    /api/v1/feedback/summary                              (agent+)

# Chat bot replies come from the AI Service when CHAT_REPLY_BACKEND=groq.
POST   /api/v1/chat/conversations/{id}/messages             (auth, AI reply)
```

### Notifications module

Every notification belongs to exactly one user and every read/action resolves
through the authenticated user's id, so foreign notifications are
indistinguishable from missing ones (404). The list endpoint supports
pagination plus `is_read` and `type` filters; `PATCH /notifications/{id}/read`
is idempotent (`read_at` is set once, never reset).

Types: `order_shipped`, `order_delivered`, `refund_completed`,
`ticket_created`, `ticket_updated`, `ticket_assigned`, `payment_successful`,
`ai_handoff`, `system`.

Notifications are created through `NotificationService.create_notification`
(the integration hook for the Orders, Tickets, Payments, and AI Service
modules). Delivery is delegated to a swappable `NotificationChannel`
protocol — today `NOTIFICATION_CHANNEL=noop` records in the database only;
email, SMS, WhatsApp, and push providers plug in later by implementing the
protocol and setting the config value, with no API changes.

### Feedback module

Customers rate their own chat conversations (1–5 stars, optional comment).
The conversation must belong to the caller — foreign conversations are
indistinguishable from missing ones (404). A feedback record is a
(customer, conversation) pair: resubmitting updates the existing record, so
analytics never double-count a conversation. There is no endpoint to read
individual feedback records; the aggregate summary is staff-only
(`support_agent` and `admin`).

`GET /feedback/summary` returns `total_feedback`, `average_rating`,
`rating_distribution` (always zero-filled for every rating 1–5),
`positive_percentage` (ratings ≥ 4) and `negative_percentage` (ratings ≤ 2;
neutral 3s are excluded from both). Unlike the chat module's own feedback
endpoint (which requires a resolved conversation), this module accepts
feedback at any point in the conversation lifecycle. The aggregation lives in
`FeedbackService.get_summary`, the single integration point for the future AI
quality / analytics dashboard — which can also drill into a conversation's
rating via `FeedbackRepository.list_for_conversation`.

### Support tickets module

Customers create and view their own tickets only — foreign tickets are
indistinguishable from missing ones (404). Support agents see the full ticket
queue but can only *write* to tickets that are unassigned or assigned to them:
updating or commenting on an unassigned ticket claims it for that agent
(atomically, so two agents cannot both claim). Admins can read and manage every
ticket, including reassigning the assigned agent. Role-scoped PATCH:
customers edit `subject`/`description`/`category`; agents may also change
`status`, `priority`, and `resolution_notes`; only admins may set
`assigned_agent_id` (the target must be an existing `support_agent`).

Tickets start with status `open` / priority `medium`. The optional
`conversation_id` on creation links a ticket to the chat conversation that
caused it (the Chat → Ticket handoff); the conversation must belong to the
creator, and tickets keep the link even if the conversation is later deleted
(`ON DELETE SET NULL`).

Comments stay open on resolved/closed tickets (a customer can ask follow-up
questions); reopening a ticket is a `status` update made by the support team.

### AI Service module (Groq)

**GROQ is the only LLM provider.** The chat module's `ChatReplyProvider` is
implemented by `app/ai/ai_service.py` (via `CHAT_REPLY_BACKEND=groq`); the
chat module talks to the AI Service, never to Groq directly. Configure
`GROQ_API_KEY` and `GROQ_MODEL` in the environment — nothing is hardcoded, and
without a key the AI degrades to a graceful “assistant unavailable” fallback.

Each customer message flows through three stages:

1. **Classify** — Groq returns structured JSON: intent (14 intents),
   sentiment (positive/neutral/negative/frustrated/angry), confidence,
   `requires_human`, and an optional tool request.
2. **Tools** — the requested tool runs through the existing business services
   (KnowledgeService, ProductService, OrderService, CustomerService,
   TicketService). The AI never touches the database directly, and every
   customer-scoped lookup resolves through the authenticated session user —
   order/ticket ownership is enforced by the services, so the model can never
   read another customer's data (foreign ids simply come back “not found”).
3. **Respond** — Groq writes the final customer-facing message grounded in the
   tool result; the reply carries intent/sentiment/confidence/`requires_human`/
   `ticket_id`/`tool_used` metadata.

Cancellations, returns, refunds, and explicit human requests always trigger a
**handoff**: a support ticket is opened (linked to the conversation) and the
conversation is escalated to the agent queue. Only the last
`AI_MAX_CONTEXT_MESSAGES` messages are sent to Groq per turn (bounded context
window), and follow-up messages see the prior history. Prompts live in
`app/ai/prompts.py` and instruct the model to never invent policies, prices, or
warranties, and to treat instructions inside customer messages as content.

All Groq interaction (JSON mode, timeouts, rate limits, auth/network errors,
invalid responses) is isolated in `app/ai/groq_service.py` and converted to a
safe fallback reply — customers never see internal errors or stack traces.

### Chat module

Conversations are customer-owned: every customer read/action resolves the
conversation through the authenticated user's id, so foreign conversations are
indistinguishable from missing ones (404). The lifecycle is `active` →
(`escalated` when a human handoff is requested) → `resolved`. State transitions
use guarded atomic UPDATEs so concurrent escalations/claims/resolutions succeed
only once.

The bot's reply is generated by a swappable `ChatReplyProvider`: today
`CHAT_REPLY_BACKEND=stub` returns a deterministic placeholder, and
`CHAT_REPLY_BACKEND=groq` uses the AI Service (the only LLM integration). When
an AI reply signals a handoff (`requires_human`), the conversation is
escalated automatically and a support ticket is opened; the send-message
response reports `requires_human` / `ticket_id`. Support agents (`support_agent`
and `admin`) see the escalation queue, claim conversations, reply, and resolve
them; customers rate a resolved conversation once (1–5 stars + optional comment).

### Roles

`customer` · `support_agent` · `admin`. Role checks use the
`require_roles(...)` dependency (admins bypass all checks).

### Public vs protected routes

Everything is protected except `/health`, the product catalog (`/products*`)
and the knowledge base (`/knowledge*`), which are intentionally public so the
AI service and the customer chat can look them up without a user session.
Prices and order totals are returned as decimal strings (e.g. `"29.99"`) to
avoid float precision loss.

Orders are customer-owned: every read/action resolves the order through the
authenticated user's id, so foreign orders are indistinguishable from missing
ones (404). Placing an order reserves stock atomically (no overselling) and
snapshots the shipping address and line items; cancelling a
pending/paid/processing order restores the reserved stock. Checkout math uses
`ORDER_SHIPPING_COST` and `ORDER_TAX_RATE` from the environment.

### Knowledge base search backends

Article search is delegated to a pluggable `KnowledgeSearchProvider`. Today
`KNOWLEDGE_SEARCH_BACKEND=database` runs a relevance-ranked SQL search
(title > tags > content). Adding a vector backend (Pinecone / ChromaDB) later
means implementing the same protocol and wiring it in
`app/services/knowledge.py` — no API changes needed.

Similarly, chat bot replies go through the `ChatReplyProvider` protocol in
`app/services/chat.py` — swapping in a real LLM later only requires a new
provider implementation and a `CHAT_REPLY_BACKEND` value.

## Testing

```bash
uv run pytest            # runs the full suite against a disposable SQLite DB
uv run pytest -v         # verbose
uv run ruff check app    # lint
```

The test suite applies the real Alembic migration chain to SQLite, so the
migrations themselves are verified. The suite refuses to run against a
non-SQLite database.

## Migrations

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
uv run alembic downgrade -1
```

## Security notes

- Passwords hashed with bcrypt; refresh tokens stored as SHA-256 digests.
- Refresh tokens are single-use (rotated on every refresh) and revocable.
- JWT secret must be rotated before production; generate with
  `python -c "import secrets; print(secrets.token_urlsafe(64))"`.
