"""SQL LIKE pattern helpers."""


def escape_like(term: str) -> str:
    """Escape LIKE wildcards so user input is matched literally.

    Backslashes are escaped first so the ones we insert for ``%``/``_`` are
    not themselves treated as escapes.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
