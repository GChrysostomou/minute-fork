import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from backend.api.dependencies import UserDep
from common.redis_client import get_user_github_token, set_user_github_token
from common.settings import get_settings
from common.types import GithubAuthStatusResponse

settings = get_settings()
logger = logging.getLogger(__name__)

github_auth_router = APIRouter(tags=["GitHub Auth"])

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105
_STATE_COOKIE_NAME = "minute_gh_oauth_state"
_RETURN_TO_COOKIE_NAME = "minute_gh_oauth_return_to"
_STATE_COOKIE_TTL_SECONDS = 600


def _safe_return_to(path: str | None) -> str:
    """Only allow same-app relative paths, to rule out this becoming an open redirect."""
    if path and path.startswith("/") and not path.startswith("//") and "://" not in path:
        return path
    return "/"


def _require_oauth_app_configured() -> None:
    oauth_app_configured = (
        settings.GITHUB_OAUTH_CLIENT_ID and settings.GITHUB_OAUTH_CLIENT_SECRET and settings.GITHUB_OAUTH_REDIRECT_URI
    )
    if not oauth_app_configured:
        logger.error("GitHub OAuth is not configured (client ID/secret/redirect URI missing)")
        raise HTTPException(status_code=500, detail="GitHub OAuth is not configured")


@github_auth_router.get("/auth/github/status")
async def github_status(user: UserDep) -> GithubAuthStatusResponse:
    """Whether the caller has a live cached GitHub token — lets the frontend decide
    whether to send the user through GET /auth/github before starting a workflow
    that needs one, instead of finding out only after it fails.
    """
    token = await get_user_github_token(user.id)
    return GithubAuthStatusResponse(authenticated=bool(token))


@github_auth_router.get("/auth/github")
async def github_login(
    user: UserDep,  # noqa: ARG001 — enforces an authenticated Minute session
    return_to: str | None = None,
) -> RedirectResponse:
    """Redirect the user to GitHub to authorise this app."""
    _require_oauth_app_configured()

    state = secrets.token_urlsafe(32)
    params = {
        "client_id": settings.GITHUB_OAUTH_CLIENT_ID,
        "redirect_uri": settings.GITHUB_OAUTH_REDIRECT_URI,
        "scope": settings.GITHUB_OAUTH_SCOPES,
        "state": state,
    }

    response = RedirectResponse(url=f"{_GITHUB_AUTHORIZE_URL}?{urlencode(params)}", status_code=302)
    # SameSite=Lax (not Strict) because this cookie must survive the top-level
    # cross-site redirect GitHub sends the browser back on to /auth/github/callback.
    response.set_cookie(
        key=_STATE_COOKIE_NAME,
        value=state,
        max_age=_STATE_COOKIE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=_RETURN_TO_COOKIE_NAME,
        value=_safe_return_to(return_to),
        max_age=_STATE_COOKIE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@github_auth_router.get("/auth/github/callback")
async def github_callback(
    request: Request,
    user: UserDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Exchange the GitHub authorization code for an access token and cache it for this user.

    Runs under the caller's existing Minute session (ALB OIDC), which is what identifies
    *who* this GitHub token belongs to — GitHub's own OAuth 'state' round-trip only protects
    against CSRF on the authorization request itself, it carries no user identity.
    """
    _require_oauth_app_configured()

    if error:
        logger.warning("GitHub OAuth authorisation was denied or failed: %s", error)
        raise HTTPException(status_code=400, detail=f"GitHub authorisation failed: {error}")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing 'code' or 'state' parameter")

    expected_state = request.cookies.get(_STATE_COOKIE_NAME)
    if not expected_state or not secrets.compare_digest(expected_state, state):
        logger.warning("GitHub OAuth state mismatch — possible CSRF attempt")
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            _GITHUB_ACCESS_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.GITHUB_OAUTH_CLIENT_ID,
                "client_secret": settings.GITHUB_OAUTH_CLIENT_SECRET,
                "code": code,
                "redirect_uri": settings.GITHUB_OAUTH_REDIRECT_URI,
            },
        )

    try:
        token_response.raise_for_status()
        token_body = token_response.json()
    except (httpx.HTTPStatusError, ValueError):
        logger.exception("Failed to exchange GitHub OAuth code for an access token")
        raise HTTPException(status_code=502, detail="Failed to complete GitHub authorisation") from None

    access_token = token_body.get("access_token")
    if not access_token:
        logger.error("GitHub token exchange response did not contain an access_token: %s", token_body.get("error"))
        raise HTTPException(status_code=502, detail="GitHub did not return an access token")

    await set_user_github_token(user.id, access_token, settings.GITHUB_SESSION_TTL_SECONDS)
    logger.info("Cached GitHub OAuth token for user %s", user.id)

    return_to = _safe_return_to(request.cookies.get(_RETURN_TO_COOKIE_NAME))
    response = RedirectResponse(url=f"{settings.APP_URL}{return_to}", status_code=302)
    response.delete_cookie(key=_STATE_COOKIE_NAME, path="/")
    response.delete_cookie(key=_RETURN_TO_COOKIE_NAME, path="/")
    return response
