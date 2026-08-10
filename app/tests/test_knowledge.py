"""Tests for the Knowledge Base module (FAQ articles)."""

from app.models.knowledge import KnowledgeArticle

API = "/api/v1/knowledge"


def _seed_article(db_session, **overrides) -> KnowledgeArticle:
    defaults = {
        "title": "How do I return an item?",
        "content": "You can return any item within 30 days of delivery.",
        "category": "returns",
        "tags": ["return", "refund"],
    }
    article = KnowledgeArticle(**{**defaults, **overrides})
    db_session.add(article)
    db_session.commit()
    return article


class TestSearch:
    def test_search_requires_query(self, client):
        assert client.get(f"{API}/search").status_code == 422
        assert client.get(f"{API}/search", params={"q": ""}).status_code == 422

    def test_search_by_title(self, client, db_session):
        _seed_article(db_session, title="How do I return an item?")
        _seed_article(
            db_session,
            title="Where is my order?",
            content="Track your order here.",
            tags=["tracking", "orders"],
        )

        response = client.get(f"{API}/search", params={"q": "return"})
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["title"] == "How do I return an item?"

    def test_search_by_content_and_tags(self, client, db_session):
        _seed_article(db_session, title="Refund policy", content="Refunds take 5-7 business days.")
        _seed_article(
            db_session, title="Shipping times", content="Standard shipping takes 3-5 days.",
            tags=["shipping", "delivery"],
        )

        by_content = client.get(f"{API}/search", params={"q": "business days"})
        assert by_content.json()[0]["title"] == "Refund policy"

        by_tag = client.get(f"{API}/search", params={"q": "delivery"})
        assert by_tag.json()[0]["title"] == "Shipping times"

    def test_search_ranks_title_above_content(self, client, db_session):
        content_match = _seed_article(
            db_session,
            title="Coverage details",
            content="Every gadget includes a two-year warranty.",
            category="support",
            tags=["coverage"],
        )
        title_match = _seed_article(
            db_session,
            title="Two-year warranty details",
            content="Information about coverage.",
            category="support",
            tags=["coverage"],
        )

        response = client.get(f"{API}/search", params={"q": "warranty"})
        body = response.json()
        assert [a["id"] for a in body][0] == str(title_match.id)
        assert str(content_match.id) in [a["id"] for a in body]

    def test_search_filter_by_category(self, client, db_session):
        _seed_article(db_session, title="Return an item", category="returns")
        _seed_article(db_session, title="Track an order", category="orders")

        response = client.get(f"{API}/search", params={"q": "order", "category": "orders"})
        body = response.json()
        assert len(body) == 1
        assert body[0]["category"] == "orders"

    def test_search_excludes_unpublished(self, client, db_session):
        _seed_article(db_session, title="Public draft", is_published=True)
        _seed_article(db_session, title="Hidden draft", is_published=False)

        body = client.get(f"{API}/search", params={"q": "draft"}).json()
        assert len(body) == 1
        assert body[0]["title"] == "Public draft"

    def test_search_case_insensitive(self, client, db_session):
        _seed_article(db_session, title="ACCOUNT SECURITY")
        response = client.get(f"{API}/search", params={"q": "account"})
        assert response.json()[0]["title"] == "ACCOUNT SECURITY"


class TestSlugs:
    def test_unique_slug_appends_suffix_on_collision(self, db_session):
        from app.repositories.knowledge import KnowledgeArticleRepository

        _seed_article(db_session, title="How do I return an item?")
        repository = KnowledgeArticleRepository(db_session)

        assert repository.unique_slug("How do I return an item?") == "how-do-i-return-an-item-2"
        assert repository.unique_slug("A brand new topic") == "a-brand-new-topic"


class TestGetArticle:
    def test_get_article_and_increments_views(self, client, db_session):
        article = _seed_article(db_session)
        assert article.view_count == 0

        first = client.get(f"{API}/articles/{article.id}")
        assert first.status_code == 200
        assert first.json()["view_count"] == 1

        second = client.get(f"{API}/articles/{article.id}")
        assert second.json()["view_count"] == 2

    def test_get_article_generates_slug_from_title(self, client, db_session):
        article = _seed_article(db_session, title="How Do I Reset My Password?")
        assert article.slug == "how-do-i-reset-my-password"
        response = client.get(f"{API}/articles/{article.id}")
        assert response.json()["slug"] == "how-do-i-reset-my-password"

    def test_get_article_404(self, client):
        import uuid

        assert client.get(f"{API}/articles/{uuid.uuid4()}").status_code == 404

    def test_get_unpublished_article_404(self, client, db_session):
        article = _seed_article(db_session, is_published=False)
        assert client.get(f"{API}/articles/{article.id}").status_code == 404

    def test_get_article_invalid_id_422(self, client):
        assert client.get(f"{API}/articles/not-a-uuid").status_code == 422
