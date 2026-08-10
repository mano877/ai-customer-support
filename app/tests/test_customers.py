"""Tests for the Customers module (profile + address book)."""

AUTH = "/api/v1/auth"
API = "/api/v1/customers"

PASSWORD = "StrongPass123!"

ADDRESS = {
    "label": "Home",
    "recipient_name": "Jane Doe",
    "street": "123 Main St",
    "city": "Springfield",
    "state": "IL",
    "postal_code": "62701",
    "country": "us",  # deliberately lowercase; must be normalized to "US"
}


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


def _create_address(client, tokens, **overrides) -> dict:
    payload = {**ADDRESS, **overrides}
    response = client.post(f"{API}/addresses", json=payload, headers=_headers(tokens))
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #
class TestProfile:
    def test_get_me_requires_auth(self, client):
        assert client.get(f"{API}/me").status_code == 401

    def test_get_me_returns_profile(self, client):
        tokens = _signup(client)
        response = client.get(f"{API}/me", headers=_headers(tokens))
        assert response.status_code == 200
        body = response.json()
        assert body["email"] == "customer@example.com"
        assert body["role"] == "customer"
        assert body["is_verified"] is True
        assert body["marketing_opt_in"] is False
        assert body["preferred_language"] == "en"

    def test_patch_me_updates_profile(self, client):
        tokens = _signup(client)
        response = client.patch(
            f"{API}/me",
            json={
                "full_name": "Jane Q. Doe",
                "phone": "+1-555-0100",
                "date_of_birth": "1990-05-14",
                "gender": "female",
                "marketing_opt_in": True,
                "preferred_language": "es",
                "avatar_url": "https://example.com/avatar.png",
            },
            headers=_headers(tokens),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["full_name"] == "Jane Q. Doe"
        assert body["phone"] == "+1-555-0100"
        assert body["date_of_birth"] == "1990-05-14"
        assert body["gender"] == "female"
        assert body["marketing_opt_in"] is True
        assert body["preferred_language"] == "es"
        assert body["avatar_url"] == "https://example.com/avatar.png"

    def test_patch_me_partial_update(self, client):
        tokens = _signup(client)
        response = client.patch(
            f"{API}/me", json={"marketing_opt_in": True}, headers=_headers(tokens)
        )
        assert response.status_code == 200
        assert response.json()["marketing_opt_in"] is True
        # Unrelated fields untouched.
        assert response.json()["preferred_language"] == "en"

    def test_patch_me_empty_body_rejected(self, client):
        tokens = _signup(client)
        response = client.patch(f"{API}/me", json={}, headers=_headers(tokens))
        assert response.status_code == 422

    def test_patch_me_invalid_date_rejected(self, client):
        tokens = _signup(client)
        response = client.patch(
            f"{API}/me", json={"date_of_birth": "not-a-date"}, headers=_headers(tokens)
        )
        assert response.status_code == 422

    def test_patch_me_null_clears_nullable_field(self, client):
        tokens = _signup(client)
        client.patch(f"{API}/me", json={"phone": "+1-555-0100"}, headers=_headers(tokens))
        response = client.patch(f"{API}/me", json={"phone": None}, headers=_headers(tokens))
        assert response.status_code == 200
        assert response.json()["phone"] is None

    def test_patch_me_null_ignored_for_non_nullable_field(self, client):
        tokens = _signup(client)
        client.patch(
            f"{API}/me", json={"marketing_opt_in": True}, headers=_headers(tokens)
        )
        response = client.patch(
            f"{API}/me", json={"marketing_opt_in": None}, headers=_headers(tokens)
        )
        assert response.status_code == 200
        assert response.json()["marketing_opt_in"] is True


# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #
class TestAddresses:
    def test_addresses_require_auth(self, client):
        assert client.get(f"{API}/addresses").status_code == 401

    def test_create_first_address_is_default(self, client):
        tokens = _signup(client)
        address = _create_address(client, tokens)
        assert address["is_default"] is True
        # Lowercase country normalized to uppercase ISO code.
        assert address["country"] == "US"
        assert address["recipient_name"] == "Jane Doe"

    def test_create_second_address_not_default(self, client):
        tokens = _signup(client)
        _create_address(client, tokens)  # first → default
        second = _create_address(client, tokens, label="Work", street="9 Industrial Rd")
        assert second["is_default"] is False

    def test_create_explicit_default_flips_existing(self, client):
        tokens = _signup(client)
        first = _create_address(client, tokens)
        second = _create_address(client, tokens, label="Work", is_default=True)
        assert second["is_default"] is True

        refreshed = client.get(f"{API}/addresses", headers=_headers(tokens)).json()
        by_id = {a["id"]: a for a in refreshed}
        assert by_id[first["id"]]["is_default"] is False
        assert by_id[second["id"]]["is_default"] is True

    def test_list_addresses_default_first(self, client):
        tokens = _signup(client)
        first = _create_address(client, tokens)  # default
        _create_address(client, tokens, label="Work")

        response = client.get(f"{API}/addresses", headers=_headers(tokens))
        assert response.status_code == 200
        addresses = response.json()
        assert len(addresses) == 2
        assert addresses[0]["id"] == first["id"]  # default first

    def test_update_address(self, client):
        tokens = _signup(client)
        address = _create_address(client, tokens)
        response = client.patch(
            f"{API}/addresses/{address['id']}",
            json={"label": "Office", "city": "Chicago"},
            headers=_headers(tokens),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["label"] == "Office"
        assert body["city"] == "Chicago"

    def test_update_address_make_default(self, client):
        tokens = _signup(client)
        first = _create_address(client, tokens)
        second = _create_address(client, tokens, label="Work")

        response = client.patch(
            f"{API}/addresses/{second['id']}", json={"is_default": True}, headers=_headers(tokens)
        )
        assert response.status_code == 200
        assert response.json()["is_default"] is True

        refreshed = client.get(f"{API}/addresses", headers=_headers(tokens)).json()
        by_id = {a["id"]: a for a in refreshed}
        assert by_id[first["id"]]["is_default"] is False

    def test_update_address_unsetting_default_promotes_newest(self, client):
        tokens = _signup(client)
        first = _create_address(client, tokens)  # default
        second = _create_address(client, tokens, label="Work")

        response = client.patch(
            f"{API}/addresses/{first['id']}",
            json={"is_default": False},
            headers=_headers(tokens),
        )
        assert response.status_code == 200
        assert response.json()["is_default"] is False

        refreshed = client.get(f"{API}/addresses", headers=_headers(tokens)).json()
        by_id = {a["id"]: a for a in refreshed}
        assert by_id[second["id"]]["is_default"] is True

    def test_update_address_empty_body_rejected(self, client):
        tokens = _signup(client)
        address = _create_address(client, tokens)
        response = client.patch(
            f"{API}/addresses/{address['id']}", json={}, headers=_headers(tokens)
        )
        assert response.status_code == 422

    def test_update_foreign_address_404(self, client):
        tokens_a = _signup(client, email="alice@example.com")
        tokens_b = _signup(client, email="bob@example.com")
        address = _create_address(client, tokens_a)

        response = client.patch(
            f"{API}/addresses/{address['id']}",
            json={"label": "Hijacked"},
            headers=_headers(tokens_b),
        )
        assert response.status_code == 404

    def test_delete_address(self, client):
        tokens = _signup(client)
        address = _create_address(client, tokens)
        response = client.delete(f"{API}/addresses/{address['id']}", headers=_headers(tokens))
        assert response.status_code == 204
        assert client.get(f"{API}/addresses", headers=_headers(tokens)).json() == []

    def test_delete_default_promotes_newest(self, client):
        tokens = _signup(client)
        first = _create_address(client, tokens)  # default
        second = _create_address(client, tokens, label="Work")

        response = client.delete(f"{API}/addresses/{first['id']}", headers=_headers(tokens))
        assert response.status_code == 204

        remaining = client.get(f"{API}/addresses", headers=_headers(tokens)).json()
        assert len(remaining) == 1
        assert remaining[0]["id"] == second["id"]
        assert remaining[0]["is_default"] is True

    def test_delete_foreign_address_404(self, client):
        tokens_a = _signup(client, email="alice@example.com")
        tokens_b = _signup(client, email="bob@example.com")
        address = _create_address(client, tokens_a)

        response = client.delete(f"{API}/addresses/{address['id']}", headers=_headers(tokens_b))
        assert response.status_code == 404

    def test_invalid_country_rejected(self, client):
        tokens = _signup(client)
        response = client.post(
            f"{API}/addresses", json={**ADDRESS, "country": "USA"}, headers=_headers(tokens)
        )
        assert response.status_code == 422
