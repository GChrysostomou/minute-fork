from functools import lru_cache
from uuid import UUID

from redis.asyncio import Redis

from common.settings import get_settings

settings = get_settings()


@lru_cache
def get_redis() -> Redis:
    """Return a process-wide async Redis client, lazily connected on first use."""
    return Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        ssl=settings.REDIS_SSL,
        decode_responses=True,
    )


def _github_token_key(user_id: UUID) -> str:
    return f"user:{user_id}:github_token"


async def get_user_github_token(user_id: UUID) -> str | None:
    """Look up the caller's cached GitHub OAuth access token, if any."""
    return await get_redis().get(_github_token_key(user_id))


async def set_user_github_token(user_id: UUID, access_token: str, ttl_seconds: int) -> None:
    """Cache a user's GitHub OAuth access token for ttl_seconds."""
    await get_redis().set(_github_token_key(user_id), access_token, ex=ttl_seconds)
