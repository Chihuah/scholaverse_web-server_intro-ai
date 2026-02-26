"""Auth middleware - resolves Cloudflare user on every request."""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, RedirectResponse

from app.config import settings
from app.database import async_session
from app.services.auth import get_user_by_email

# Paths that don't require a registered user
PUBLIC_PATHS = frozenset({"/register", "/static", "/api/internal"})


def _is_public(path: str) -> bool:
    return any(path.startswith(p) for p in PUBLIC_PATHS)


class AuthMiddleware(BaseHTTPMiddleware):
    """Resolve CF-authenticated email -> local user on every request.

    Sets ``request.state.user`` (Student | None) and
    ``request.state.user_email`` (str | None).

    The session factory can be overridden via ``app.state.session_factory``
    for testing purposes.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        email = request.headers.get(settings.CF_AUTH_HEADER)
        request.state.user_email = email
        request.state.user = None

        if email:
            # Use overridable session factory (for tests) or default
            session_factory = getattr(
                request.app.state, "session_factory", None
            ) or async_session
            async with session_factory() as db:
                user = await get_user_by_email(db, email)
                request.state.user = user

            # Redirect unregistered users to registration page
            # (skip redirect in guest mode so visitors can browse)
            if (
                user is None
                and not _is_public(request.url.path)
                and not settings.GUEST_MODE
            ):
                return RedirectResponse(url="/register", status_code=302)

        return await call_next(request)
