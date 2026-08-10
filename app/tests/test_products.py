"""Tests for the Products module (public catalog)."""

from decimal import Decimal

from app.models.product import Product

API = "/api/v1/products"


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


class TestListProducts:
    def test_list_empty(self, client):
        response = client.get(API)
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_products_paginated(self, client, db_session):
        _seed_product(db_session, sku="SKU-A", name="Alpha")
        _seed_product(db_session, sku="SKU-B", name="Beta")
        _seed_product(db_session, sku="SKU-C", name="Gamma")

        response = client.get(f"{API}?limit=2&offset=0")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert len(body["items"]) == 2

        page2 = client.get(f"{API}?limit=2&offset=2").json()
        assert len(page2["items"]) == 1

    def test_list_filter_by_category(self, client, db_session):
        _seed_product(db_session, sku="SKU-A", category="Electronics")
        _seed_product(db_session, sku="SKU-B", category="Books")

        response = client.get(f"{API}?category=books")
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["category"] == "Books"

    def test_list_excludes_inactive(self, client, db_session):
        _seed_product(db_session, sku="SKU-A", name="Visible")
        _seed_product(db_session, sku="SKU-B", name="Hidden", is_active=False)

        response = client.get(API)
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "Visible"

    def test_list_invalid_limit_rejected(self, client):
        assert client.get(f"{API}?limit=0").status_code == 422


class TestGetProduct:
    def test_get_product(self, client, db_session):
        product = _seed_product(
            db_session, sku="SKU-42", price=Decimal("129.99"), is_featured=True
        )
        response = client.get(f"{API}/{product.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["sku"] == "SKU-42"
        # Decimal amounts are serialized as strings (no float precision loss).
        assert body["price"] == "129.99"
        assert body["currency"] == "USD"
        assert body["rating"] == "0.00"
        assert body["is_featured"] is True

    def test_get_product_404(self, client):
        import uuid

        response = client.get(f"{API}/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_get_inactive_product_404(self, client, db_session):
        product = _seed_product(db_session, is_active=False)
        response = client.get(f"{API}/{product.id}")
        assert response.status_code == 404

    def test_get_invalid_id_422(self, client):
        assert client.get(f"{API}/not-a-uuid").status_code == 422


class TestSearch:
    def test_search_by_name(self, client, db_session):
        _seed_product(db_session, sku="SKU-A", name="Wireless Mouse")
        _seed_product(
            db_session, sku="SKU-B", name="Wired Keyboard", description="A quiet keyboard."
        )

        response = client.get(f"{API}/search", params={"q": "mouse"})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "Wireless Mouse"

    def test_search_case_insensitive(self, client, db_session):
        _seed_product(db_session, sku="SKU-A", name="GAMING Headset")
        response = client.get(f"{API}/search", params={"q": "gaming"})
        assert response.json()["total"] == 1

    def test_search_by_description_and_brand(self, client, db_session):
        _seed_product(db_session, sku="SKU-A", brand="Acme", description="bluetooth speaker")
        _seed_product(db_session, sku="SKU-B", brand="Globex", description="plain speaker")

        by_description = client.get(f"{API}/search", params={"q": "bluetooth"})
        assert by_description.json()["items"][0]["sku"] == "SKU-A"

        by_brand = client.get(f"{API}/search", params={"q": "globex"})
        assert by_brand.json()["total"] == 1

    def test_search_escapes_wildcards(self, client, db_session):
        _seed_product(db_session, sku="SKU-A", name="100% Cotton Shirt")
        response = client.get(f"{API}/search", params={"q": "100%"})
        assert response.json()["total"] == 1

    def test_search_empty_query_rejected(self, client):
        assert client.get(f"{API}/search", params={"q": ""}).status_code == 422
        assert client.get(f"{API}/search").status_code == 422

    def test_search_no_results(self, client):
        response = client.get(f"{API}/search", params={"q": "nonexistent"})
        body = response.json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_search_pagination_keeps_total(self, client, db_session):
        for i in range(3):
            _seed_product(db_session, sku=f"SKU-{i}", name=f"Bluetooth Speaker {i}")

        page1 = client.get(f"{API}/search", params={"q": "bluetooth", "limit": 2}).json()
        assert page1["total"] == 3
        assert len(page1["items"]) == 2

        page2 = client.get(
            f"{API}/search", params={"q": "bluetooth", "limit": 2, "offset": 2}
        ).json()
        assert page2["total"] == 3
        assert len(page2["items"]) == 1

    def test_search_combined_with_category(self, client, db_session):
        _seed_product(db_session, sku="SKU-A", name="Bluetooth Speaker", category="Audio")
        _seed_product(db_session, sku="SKU-B", name="Bluetooth Earbuds", category="Audio")
        _seed_product(db_session, sku="SKU-C", name="Bluetooth Speaker Mini", category="Toys")

        response = client.get(
            f"{API}/search", params={"q": "bluetooth", "category": "audio"}
        )
        body = response.json()
        assert body["total"] == 2
        assert {item["name"] for item in body["items"]} == {
            "Bluetooth Speaker",
            "Bluetooth Earbuds",
        }


class TestRecommendations:
    def test_recommendations_featured_first_then_rated(self, client, db_session):
        plain = _seed_product(db_session, sku="SKU-A", name="Plain", rating=Decimal("4.9"))
        featured = _seed_product(
            db_session, sku="SKU-B", name="Featured", rating=Decimal("3.0"), is_featured=True
        )

        response = client.get(f"{API}/recommendations")
        assert response.status_code == 200
        body = response.json()
        assert [p["id"] for p in body] == [str(featured.id), str(plain.id)]

    def test_recommendations_exclude_inactive(self, client, db_session):
        _seed_product(db_session, sku="SKU-A", name="Visible", is_featured=True)
        _seed_product(db_session, sku="SKU-B", name="Hidden", is_featured=True, is_active=False)

        body = client.get(f"{API}/recommendations").json()
        assert len(body) == 1
        assert body[0]["name"] == "Visible"

    def test_recommendations_limit(self, client, db_session):
        for i in range(3):
            _seed_product(db_session, sku=f"SKU-{i}", name=f"Product {i}")
        body = client.get(f"{API}/recommendations?limit=2").json()
        assert len(body) == 2
