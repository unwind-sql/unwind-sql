"""Web-layer errors."""

from unwind.errors import UnwindError


class WebServerError(UnwindError):
    """Raised when the web server cannot start (e.g. port unavailable)."""
