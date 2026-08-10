"""Tests for the Orders module (place, list, detail, tracking, cancel, return)."""

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.core.config import get_settings
from app.models.order import Order, OrderStatus
from app.models.product import Product

AUTH = "/api/v1/auth"
API = "/api/v1/orders"
PASSWORD = "StrongPass123!"


def _round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _expected_totals(subtotal: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Recompute the checkout math using the configured settings."""
    settings = get_settings()
    shipping = settings.ORDER_SHIPPING_COST.quantize(Decimal("0.01"))
    tax = _round2(subtotal * settings.ORDER_TAX_RATE)
    total = _round2(subtotal + shipping + tax)
    return subtotal, shipping, tax, total


def _signup(client, email="customer@example.com") -> dict:
    reg = client.post(
        f"{AUTH}/register",
        json={"email": email, "password": PASSWORD, "full_name": "Jane Doe"},
    )
    assert reg.status_code == 201, reg.text
    otp = reg.json()["dev_otp"]
    verify = client.post(f"{AUTH}/verify-otp", json={"email": email, "otp": otp})
    assert verify.status_code == 200, verify.text
    return verify.json()


def _headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _seed_product(db_session, **overrides) -> Product:
    defaults = {
        "sku": "SKU-0001",
        "name": "Wireless Mouse",
        "description": "A comfortable wireless mouse.",
        "category": "Electronics",
        "brand": "Acme",
        "price": Decimal("29.99"),
    }
    product = Product(**{**defaults, **overrides})
    db_session.add(product)
    db_session.commit()
    return product


def _create_address(client, tokens, **overrides) -> dict:
    payload = {
        "label": "Home",
        "recipient_name": "Jane Doe",
        "street": "123 Main St",
        "city": "Springfield",
        "state": "IL",
        "postal_code": "62701",
        "country": "US",
    }
    response = client.post(
        "/api/v1/customers/addresses",
        json={**payload, **overrides},
        headers=_headers(tokens),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _items(products: list[Product], quantities: list[int] | None = None) -> list[dict]:
    if quantities is None:
        quantities = [1] * len(products)
    return [
        {"product_id": str(product.id), "quantity": quantity}
        for product, quantity in zip(products, quantities, strict=True)
    ]


def _post_order(client, tokens, *, items: list[dict], **overrides):
    payload = {"items": items, **overrides}
    return client.post(API, json=payload, headers=_headers(tokens))


def _fresh(db_session, model, obj_id):
    """Fetch an object, bypassing a possibly-stale identity map."""
    obj = db_session.get(model, obj_id)
    db_session.refresh(obj)
    return obj


def _ready_user(client, db_session, email="customer@example.com") -> tuple[dict, Product]:
    """Signup + address + product, ready to place an order."""
    tokens = _signup(client, email=email)
    _create_address(client, tokens)
    product = _seed_product(db_session, stock_quantity=10)
    return tokens, product


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #
class TestCreateOrder:
    def test_requires_auth(self, client):
        assert client.post(API, json={"items": []}).status_code == 401

    def test_create_order_success(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        response = _post_order(
            client, tokens, items=_items([product]), notes="Leave at the door"
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["order_number"].startswith("ORD-")
        assert body["status"] == "pending"
        assert body["currency"] == "USD"

        subtotal, shipping, tax, total = _expected_totals(Decimal("29.99"))
        assert body["items_subtotal"] == str(subtotal)
        assert body["shipping_cost"] == str(shipping)
        assert body["tax_amount"] == str(tax)
        assert body["total_amount"] == str(total)

        assert body["notes"] == "Leave at the door"
        assert body["shipping_address"]["recipient_name"] == "Jane Doe"
        assert body["shipping_address"]["country"] == "US"

        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["sku"] == product.sku
        assert item["name"] == product.name
        assert item["unit_price"] == "29.99"
        assert item["quantity"] == 1
        assert item["line_total"] == "29.99"

    def test_create_order_decrements_stock(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        product.stock_quantity = 5
        db_session.commit()

        _post_order(client, tokens, items=_items([product], [2]))
        assert _fresh(db_session, Product, product.id).stock_quantity == 3

    def test_create_order_uses_default_address(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        body = _post_order(client, tokens, items=_items([product])).json()
        assert body["shipping_address"]["recipient_name"] == "Jane Doe"

    def test_create_order_uses_specified_address(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        work = _create_address(
            client, tokens, label="Work", recipient_name="Jane at Work"
        )
        body = _post_order(
            client, tokens, items=_items([product]), address_id=work["id"]
        ).json()
        assert body["shipping_address"]["recipient_name"] == "Jane at Work"

    def test_create_order_foreign_address_404(self, client, db_session):
        tokens_a, product = _ready_user(client, db_session, email="alice@example.com")
        tokens_b = _signup(client, email="bob@example.com")
        address = _create_address(client, tokens_a)

        response = _post_order(
            client, tokens_b, items=_items([product]), address_id=address["id"]
        )
        assert response.status_code == 404

    def test_create_order_without_address_400(self, client, db_session):
        tokens = _signup(client)
        product = _seed_product(db_session, stock_quantity=10)
        response = _post_order(client, tokens, items=_items([product]))
        assert response.status_code == 400

    def test_create_order_unknown_product_404(self, client, db_session):
        tokens, _ = _ready_user(client, db_session)
        response = _post_order(
            client, tokens, items=[{"product_id": str(uuid.uuid4()), "quantity": 1}]
        )
        assert response.status_code == 404

    def test_create_order_inactive_product_404(self, client, db_session):
        tokens = _signup(client)
        _create_address(client, tokens)
        hidden = _seed_product(db_session, stock_quantity=10, is_active=False)
        response = _post_order(client, tokens, items=_items([hidden]))
        assert response.status_code == 404

    def test_create_order_insufficient_stock_409(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        product.stock_quantity = 1
        db_session.commit()

        response = _post_order(client, tokens, items=_items([product], [2]))
        assert response.status_code == 409
        # Failed order must not consume stock.
        assert _fresh(db_session, Product, product.id).stock_quantity == 1

    def test_create_order_merges_duplicate_product_ids(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        product.price = Decimal("10.00")
        db_session.commit()

        items = [
            {"product_id": str(product.id), "quantity": 1},
            {"product_id": str(product.id), "quantity": 2},
        ]
        body = _post_order(client, tokens, items=items).json()
        assert len(body["items"]) == 1
        assert body["items"][0]["quantity"] == 3
        assert body["items"][0]["line_total"] == "30.00"
        assert body["items_subtotal"] == "30.00"

    def test_create_order_multiple_products(self, client, db_session):
        tokens = _signup(client)
        _create_address(client, tokens)
        mouse = _seed_product(
            db_session, sku="SKU-M", price=Decimal("29.99"), stock_quantity=10
        )
        keyboard = _seed_product(
            db_session, sku="SKU-K", name="Keyboard", price=Decimal("59.99"), stock_quantity=10
        )

        body = _post_order(client, tokens, items=_items([mouse, keyboard])).json()
        subtotal = Decimal("89.98")
        _, _, _, total = _expected_totals(subtotal)
        assert body["items_subtotal"] == "89.98"
        assert body["total_amount"] == str(total)
        assert len(body["items"]) == 2

    def test_create_order_mixed_currencies_400(self, client, db_session):
        tokens, mouse = _ready_user(client, db_session)
        earbuds = _seed_product(
            db_session, sku="SKU-E", name="Earbuds", currency="EUR", stock_quantity=10
        )
        response = _post_order(client, tokens, items=_items([mouse, earbuds]))
        assert response.status_code == 400

    def test_create_order_empty_items_422(self, client, db_session):
        tokens, _ = _ready_user(client, db_session)
        assert _post_order(client, tokens, items=[]).status_code == 422

    def test_create_order_quantity_zero_422(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        response = _post_order(
            client, tokens, items=[{"product_id": str(product.id), "quantity": 0}]
        )
        assert response.status_code == 422

    def test_create_order_quantity_above_max_422(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        response = _post_order(
            client, tokens, items=[{"product_id": str(product.id), "quantity": 100}]
        )
        assert response.status_code == 422

    def test_create_order_too_many_items_422(self, client, db_session):
        tokens = _signup(client)
        _create_address(client, tokens)
        products = [
            _seed_product(
                db_session, sku=f"SKU-{i}", name=f"Product {i}", stock_quantity=10
            )
            for i in range(51)
        ]
        response = _post_order(client, tokens, items=_items(products))
        assert response.status_code == 422

    def test_create_order_notes_too_long_422(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        response = _post_order(
            client, tokens, items=_items([product]), notes="x" * 501
        )
        assert response.status_code == 422

    def test_create_order_stock_not_reserved_when_currency_invalid(self, client, db_session):
        """A rejected order must not have reserved stock."""
        tokens, mouse = _ready_user(client, db_session)
        earbuds = _seed_product(
            db_session, sku="SKU-E", name="Earbuds", currency="EUR", stock_quantity=10
        )
        response = _post_order(client, tokens, items=_items([mouse, earbuds]))
        assert response.status_code == 400
        assert _fresh(db_session, Product, mouse.id).stock_quantity == 10


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #
class TestListOrders:
    def test_requires_auth(self, client):
        assert client.get(API).status_code == 401

    def test_list_empty(self, client):
        tokens = _signup(client)
        body = client.get(API, headers=_headers(tokens)).json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_paginated(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        for _ in range(3):
            _post_order(client, tokens, items=_items([product]))

        page1 = client.get(f"{API}?limit=2", headers=_headers(tokens)).json()
        assert page1["total"] == 3
        assert len(page1["items"]) == 2

        page2 = client.get(f"{API}?limit=2&offset=2", headers=_headers(tokens)).json()
        assert page2["total"] == 3
        assert len(page2["items"]) == 1

    def test_list_only_own_orders(self, client, db_session):
        tokens_a, product = _ready_user(client, db_session, email="alice@example.com")
        _post_order(client, tokens_a, items=_items([product]))

        tokens_b = _signup(client, email="bob@example.com")
        body = client.get(API, headers=_headers(tokens_b)).json()
        assert body["total"] == 0

    def test_list_newest_first(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        created = [
            _post_order(client, tokens, items=_items([product])).json() for _ in range(3)
        ]
        # Stagger created_at (SQLite now() has 1s precision) so ordering is
        # deterministic regardless of execution speed.
        for index, order_json in enumerate(created):
            record = _fresh(db_session, Order, uuid.UUID(order_json["id"]))
            record.created_at = datetime(2026, 8, index + 1, 12, 0, tzinfo=UTC)
        db_session.commit()

        body = client.get(API, headers=_headers(tokens)).json()
        ids = [item["id"] for item in body["items"]]
        assert ids == [created[2]["id"], created[1]["id"], created[0]["id"]]


# --------------------------------------------------------------------------- #
# Detail
# --------------------------------------------------------------------------- #
class TestGetOrder:
    def test_requires_auth(self, client):
        assert client.get(f"{API}/{uuid.uuid4()}").status_code == 401

    def test_get_order_detail(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        order = _post_order(client, tokens, items=_items([product])).json()

        response = client.get(f"{API}/{order['id']}", headers=_headers(tokens))
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == order["id"]
        assert body["order_number"] == order["order_number"]
        assert len(body["items"]) == 1

    def test_get_foreign_order_404(self, client, db_session):
        tokens_a, product = _ready_user(client, db_session, email="alice@example.com")
        order = _post_order(client, tokens_a, items=_items([product])).json()

        tokens_b = _signup(client, email="bob@example.com")
        response = client.get(f"{API}/{order['id']}", headers=_headers(tokens_b))
        assert response.status_code == 404

    def test_get_missing_order_404(self, client):
        tokens = _signup(client)
        response = client.get(f"{API}/{uuid.uuid4()}", headers=_headers(tokens))
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Tracking
# --------------------------------------------------------------------------- #
class TestTracking:
    def test_requires_auth(self, client):
        assert client.get(f"{API}/{uuid.uuid4()}/tracking").status_code == 401

    def test_initial_tracking_event(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        order = _post_order(client, tokens, items=_items([product])).json()

        tracking = client.get(
            f"{API}/{order['id']}/tracking", headers=_headers(tokens)
        ).json()
        assert tracking["order_number"] == order["order_number"]
        assert tracking["status"] == "pending"
        assert len(tracking["events"]) == 1
        event = tracking["events"][0]
        assert event["status"] == "pending"
        assert event["description"] == "Order placed"

    def test_tracking_reflects_cancel(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        order = _post_order(client, tokens, items=_items([product])).json()
        client.post(f"{API}/{order['id']}/cancel", headers=_headers(tokens))

        tracking = client.get(
            f"{API}/{order['id']}/tracking", headers=_headers(tokens)
        ).json()
        assert tracking["status"] == "cancelled"
        assert len(tracking["events"]) == 2
        assert tracking["events"][-1]["description"] == "Order cancelled"

    def test_tracking_foreign_order_404(self, client, db_session):
        tokens_a, product = _ready_user(client, db_session, email="alice@example.com")
        order = _post_order(client, tokens_a, items=_items([product])).json()

        tokens_b = _signup(client, email="bob@example.com")
        response = client.get(f"{API}/{order['id']}/tracking", headers=_headers(tokens_b))
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Cancel
# --------------------------------------------------------------------------- #
class TestCancelOrder:
    def test_requires_auth(self, client):
        assert client.post(f"{API}/{uuid.uuid4()}/cancel").status_code == 401

    def test_cancel_pending_order_restores_stock(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        product.stock_quantity = 5
        db_session.commit()
        order = _post_order(client, tokens, items=_items([product], [2])).json()
        assert _fresh(db_session, Product, product.id).stock_quantity == 3

        response = client.post(f"{API}/{order['id']}/cancel", headers=_headers(tokens))
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "cancelled"
        assert _fresh(db_session, Product, product.id).stock_quantity == 5

    def test_cancel_shipped_order_409(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        order = _post_order(client, tokens, items=_items([product])).json()
        record = _fresh(db_session, Order, uuid.UUID(order["id"]))
        record.status = OrderStatus.SHIPPED
        db_session.commit()

        response = client.post(f"{API}/{order['id']}/cancel", headers=_headers(tokens))
        assert response.status_code == 409

    def test_cancel_twice_409(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        order = _post_order(client, tokens, items=_items([product])).json()
        first = client.post(f"{API}/{order['id']}/cancel", headers=_headers(tokens))
        assert first.status_code == 200

        second = client.post(f"{API}/{order['id']}/cancel", headers=_headers(tokens))
        assert second.status_code == 409

    def test_cancel_restores_stock_exactly_once(self, client, db_session):
        """A failed second cancel must not double-restore stock."""
        tokens, product = _ready_user(client, db_session)
        product.stock_quantity = 3
        db_session.commit()
        order = _post_order(client, tokens, items=_items([product], [2])).json()
        assert _fresh(db_session, Product, product.id).stock_quantity == 1

        first = client.post(f"{API}/{order['id']}/cancel", headers=_headers(tokens))
        assert first.status_code == 200
        second = client.post(f"{API}/{order['id']}/cancel", headers=_headers(tokens))
        assert second.status_code == 409

        assert _fresh(db_session, Product, product.id).stock_quantity == 3

    def test_cancel_foreign_order_404(self, client, db_session):
        tokens_a, product = _ready_user(client, db_session, email="alice@example.com")
        order = _post_order(client, tokens_a, items=_items([product])).json()

        tokens_b = _signup(client, email="bob@example.com")
        response = client.post(f"{API}/{order['id']}/cancel", headers=_headers(tokens_b))
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Return
# --------------------------------------------------------------------------- #
class TestReturnOrder:
    def test_requires_auth(self, client):
        assert client.post(f"{API}/{uuid.uuid4()}/return").status_code == 401

    def test_return_delivered_order(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        order = _post_order(client, tokens, items=_items([product])).json()
        record = _fresh(db_session, Order, uuid.UUID(order["id"]))
        record.status = OrderStatus.DELIVERED
        db_session.commit()

        response = client.post(f"{API}/{order['id']}/return", headers=_headers(tokens))
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "return_requested"

        tracking = client.get(
            f"{API}/{order['id']}/tracking", headers=_headers(tokens)
        ).json()
        assert tracking["events"][-1]["description"] == "Return requested"

    def test_return_pending_order_409(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        order = _post_order(client, tokens, items=_items([product])).json()
        response = client.post(f"{API}/{order['id']}/return", headers=_headers(tokens))
        assert response.status_code == 409

    def test_return_twice_409(self, client, db_session):
        tokens, product = _ready_user(client, db_session)
        order = _post_order(client, tokens, items=_items([product])).json()
        record = _fresh(db_session, Order, uuid.UUID(order["id"]))
        record.status = OrderStatus.DELIVERED
        db_session.commit()

        first = client.post(f"{API}/{order['id']}/return", headers=_headers(tokens))
        assert first.status_code == 200
        second = client.post(f"{API}/{order['id']}/return", headers=_headers(tokens))
        assert second.status_code == 409

    def test_return_foreign_order_404(self, client, db_session):
        tokens_a, product = _ready_user(client, db_session, email="alice@example.com")
        order = _post_order(client, tokens_a, items=_items([product])).json()

        tokens_b = _signup(client, email="bob@example.com")
        response = client.post(f"{API}/{order['id']}/return", headers=_headers(tokens_b))
        assert response.status_code == 404
