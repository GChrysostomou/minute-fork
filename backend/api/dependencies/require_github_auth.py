import logging
from typing import Annotated

from fastapi import Depends, HTTPException

from backend.api.dependencies.get_current_user import UserDep
from common.redis_client import get_user_github_token

logger = logging.getLogger(__name__)


async def require_github_auth(user: UserDep) -> str:
    """
    Dependency for routes that call the GitHub API on a user's behalf.

    Looks up the caller's GitHub access token, cached in Redis under their Minute
    user ID by GET /auth/github/callback. Raises 401 if missing/expired, which
    should trigger the frontend to send the user through GET /auth/github again.
    """
    access_token = await get_user_github_token(user.id)
    if not access_token:
        logger.info("No cached GitHub token for user %s", user.id)
        raise HTTPException(
            status_code=401,
            detail="No GitHub session — authenticate via /auth/github",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return access_token


GithubAccessTokenDep = Annotated[str, Depends(require_github_auth)]
