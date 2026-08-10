"""String helpers."""

import re
import unicodedata


def slugify(text: str) -> str:
    """Convert arbitrary text into a URL-safe kebab-case slug."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "article"
