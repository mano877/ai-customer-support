"""Tests for the Authentication module."""

import threading
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal, get_db
from app.dependencies.auth import require_roles
from app.main import register_exception_handlers
from app.models.user import User, UserRole
from app.schemas.auth import LoginRequest
from app.services.auth import AuthService

API = "/api/v1/auth"
EMAIL = "customer@example.com"
PASSWORD = "StrongPass123!"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _register(client, email=EMAIL, password=PASSWORD, **overrides) -> dict:
    payload = {"email": email, "password": password, "full_name": "Jane Doe", **overrides}
    response = client.post(f"{API}/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _verify(client, email=EMAIL, otp=None) -> dict:
    otp = otp or _register(client, email=email)["dev_otp"]
    response = client.post(f"{API}/verify-otp", json={"email": email, "otp": otp})
    assert response.status_code == 200, response.text
    return response.json()


def _auth_headers(tokens) -> dict:
    """Build auth headers from a token dict or a TokenResponse object."""
    access = tokens["access_token"] if isinstance(tokens, dict) else tokens.access_token
    return {"Authorization": f"Bearer {access}"}


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
class TestRegister:
    def test_register_success(self, client):
        body = _register(client)
        assert body["requires_otp_verification"] is True
        assert body["dev_otp"] and len(body["dev_otp"]) == 6
        assert body["user"]["email"] == EMAIL
        assert body["user"]["role"] == "customer"
        assert body["user"]["is_verified"] is False
        assert body["user"]["is_active"] is True

    def test_register_normalizes_email(self, client):
        body = _register(client, email="  Customer@Example.COM  ")
        assert body["user"]["email"] == "customer@example.com"

    def test_register_duplicate_email_conflict(self, client):
        _register(client)
        response = client.post(
            f"{API}/register",
            json={"email": EMAIL, "password": PASSWORD},
        )
        assert response.status_code == 409

    def test_register_weak_password_rejected(self, client):
        response = client.post(
            f"{API}/register", json={"email": EMAIL, "password": "short"}
        )
        assert response.status_code == 422

    def test_register_invalid_email_rejected(self, client):
        response = client.post(
            f"{API}/register", json={"email": "not-an-email", "password": PASSWORD}
        )
        assert response.status_code == 422

    def test_register_never_stores_plain_password(self, client, db_session):
        _register(client)
        user = db_session.scalars(select(User).where(User.email == EMAIL)).one()
        assert user.hashed_password != PASSWORD
        assert user.hashed_password.startswith("$2")


# --------------------------------------------------------------------------- #
# OTP verification
# --------------------------------------------------------------------------- #
class TestVerifyOtp:
    def test_verify_otp_activates_and_returns_tokens(self, client):
        tokens = _verify(client)
        assert tokens["access_token"]
        assert tokens["refresh_token"]
        assert tokens["token_type"] == "bearer"

        me = client.get(f"{API}/me", headers=_auth_headers(tokens))
        assert me.status_code == 200
        assert me.json()["is_verified"] is True

    def test_verify_otp_wrong_code(self, client):
        _register(client)
        response = client.post(f"{API}/verify-otp", json={"email": EMAIL, "otp": "000000"})
        assert response.status_code == 400

    def test_verify_otp_twice_rejected(self, client):
        otp = _register(client, email="second@example.com")["dev_otp"]
        response = client.post(
            f"{API}/verify-otp", json={"email": "second@example.com", "otp": otp}
        )
        assert response.status_code == 200
        # Re-verifying the same account now fails.
        response = client.post(
            f"{API}/verify-otp", json={"email": "second@example.com", "otp": otp}
        )
        assert response.status_code == 400

    def test_verify_otp_malformed_code_rejected(self, client):
        _register(client)
        response = client.post(f"{API}/verify-otp", json={"email": EMAIL, "otp": "12ab"})
        assert response.status_code == 422

    def test_verify_otp_brute_force_lockout(self, client):
        """After MAX_OTP_ATTEMPTS failures the OTP is invalidated."""
        from app.core.config import get_settings

        registered = _register(client, email="locked@example.com")
        locked_otp = registered["dev_otp"]
        max_attempts = get_settings().MAX_OTP_ATTEMPTS

        for _ in range(max_attempts):
            response = client.post(
                f"{API}/verify-otp", json={"email": "locked@example.com", "otp": "111111"}
            )
            assert response.status_code == 400

        # The OTP is exhausted — even the correct code is now rejected.
        final = client.post(
            f"{API}/verify-otp", json={"email": "locked@example.com", "otp": locked_otp}
        )
        assert final.status_code == 400


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
class TestLogin:
    def _verified_tokens(self, client):
        return _verify(client)

    def test_login_success(self, client):
        _verify(client)
        response = client.post(
            f"{API}/login", json={"email": EMAIL, "password": PASSWORD}
        )
        assert response.status_code == 200
        assert response.json()["access_token"]

    def test_login_wrong_password(self, client):
        _verify(client)
        response = client.post(
            f"{API}/login", json={"email": EMAIL, "password": "WrongPass123!"}
        )
        assert response.status_code == 401

    def test_login_unknown_email(self, client):
        response = client.post(
            f"{API}/login", json={"email": "ghost@example.com", "password": PASSWORD}
        )
        assert response.status_code == 401

    def test_login_unverified_account_rejected(self, client):
        _register(client)  # never verifies
        response = client.post(
            f"{API}/login", json={"email": EMAIL, "password": PASSWORD}
        )
        assert response.status_code == 403


# --------------------------------------------------------------------------- #
# /auth/me
# --------------------------------------------------------------------------- #
class TestMe:
    def test_me_requires_auth(self, client):
        assert client.get(f"{API}/me").status_code == 401

    def test_me_with_invalid_token(self, client):
        response = client.get(f"{API}/me", headers=_auth_headers({"access_token": "garbage"}))
        assert response.status_code == 401

    def test_me_returns_profile(self, client):
        tokens = _verify(client)
        me = client.get(f"{API}/me", headers=_auth_headers(tokens))
        assert me.status_code == 200
        assert me.json()["email"] == EMAIL
        assert me.json()["role"] == "customer"


# --------------------------------------------------------------------------- #
# Refresh & logout
# --------------------------------------------------------------------------- #
class TestRefresh:
    def test_refresh_rotates_token(self, client):
        tokens = _verify(client)
        response = client.post(f"{API}/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert response.status_code == 200
        rotated = response.json()
        assert rotated["access_token"] != tokens["access_token"]

        # Old refresh token is now revoked.
        old = client.post(f"{API}/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert old.status_code == 401

        # New refresh token still works.
        again = client.post(f"{API}/refresh", json={"refresh_token": rotated["refresh_token"]})
        assert again.status_code == 200

    def test_refresh_with_access_token_rejected(self, client):
        tokens = _verify(client)
        response = client.post(f"{API}/refresh", json={"refresh_token": tokens["access_token"]})
        assert response.status_code == 401

    def test_refresh_garbage_token_rejected(self, client):
        response = client.post(f"{API}/refresh", json={"refresh_token": "garbage"})
        assert response.status_code == 401

    def test_refresh_token_single_use_under_race(self, client):
        """Concurrent double-use of the same refresh token must succeed once.

        Requires WAL journal mode on the test DB (see conftest) so the two
        writers serialize instead of deadlocking on shared read locks.
        """
        tokens = _verify(client)
        refresh_token = tokens["refresh_token"]

        outcomes: list[str] = []
        outcomes_lock = threading.Lock()
        start_barrier = threading.Barrier(2)

        def attempt() -> None:
            session = SessionLocal()
            try:
                service = AuthService(session)
                start_barrier.wait()
                try:
                    service.refresh(refresh_token)
                    result = "ok"
                except Exception:
                    result = "rejected"
            finally:
                session.close()
            with outcomes_lock:
                outcomes.append(result)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert sorted(outcomes) == ["ok", "rejected"]


class TestLogout:
    def test_logout_revokes_refresh_token(self, client):
        tokens = _verify(client)
        response = client.post(
            f"{API}/logout",
            json={"refresh_token": tokens["refresh_token"]},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 204

        # Revoked refresh token can no longer be used.
        refreshed = client.post(f"{API}/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert refreshed.status_code == 401

    def test_logout_requires_auth(self, client):
        tokens = _verify(client)
        response = client.post(
            f"{API}/logout", json={"refresh_token": tokens["refresh_token"]}
        )
        assert response.status_code == 401

    def test_logout_is_idempotent(self, client):
        tokens = _verify(client)
        headers = _auth_headers(tokens)
        payload = {"refresh_token": tokens["refresh_token"]}
        assert client.post(f"{API}/logout", json=payload, headers=headers).status_code == 204
        assert client.post(f"{API}/logout", json=payload, headers=headers).status_code == 204


# --------------------------------------------------------------------------- #
# Role-based authorization
# --------------------------------------------------------------------------- #
class TestRoles:
    def _mini_admin_app(self):
        application = FastAPI()
        register_exception_handlers(application)

        def _override_get_db():
            session = SessionLocal()
            try:
                yield session
            finally:
                session.close()

        application.dependency_overrides[get_db] = _override_get_db

        @application.get("/admin-only")
        def admin_only(user: Annotated[User, Depends(require_roles(UserRole.ADMIN))]):
            return {"role": user.role.value}

        return application

    def test_customer_forbidden_from_admin_route(self, client):
        tokens = _verify(client)
        admin_app = self._mini_admin_app()
        with TestClient(admin_app) as admin_client:
            response = admin_client.get("/admin-only", headers=_auth_headers(tokens))
            assert response.status_code == 403

    def test_admin_allowed_on_admin_route(self, client, db_session):
        admin = User(
            email="admin@example.com",
            hashed_password=hash_password("AdminPass123!"),
            full_name="Root Admin",
            role=UserRole.ADMIN,
            is_verified=True,
            is_active=True,
        )
        db_session.add(admin)
        db_session.commit()

        tokens = AuthService(db_session).login(
            LoginRequest(email="admin@example.com", password="AdminPass123!")
        )
        admin_app = self._mini_admin_app()
        with TestClient(admin_app) as admin_client:
            response = admin_client.get("/admin-only", headers=_auth_headers(tokens))
            assert response.status_code == 200
            assert response.json()["role"] == "admin"
