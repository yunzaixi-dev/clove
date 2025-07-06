from typing import Optional, Annotated
from loguru import logger
from fastapi import Depends, Header

from app.core.config import settings
from app.core.exceptions import InvalidAPIKeyError


async def get_api_key(
    x_api_key: Annotated[Optional[str], Header()] = None,
    authorization: Annotated[Optional[str], Header()] = None,
) -> str:
    # Check X-API-Key header
    api_key = x_api_key

    # Check Authorization header
    if not api_key and authorization:
        if authorization.startswith("Bearer "):
            api_key = authorization[7:]

    if not api_key:
        raise InvalidAPIKeyError()

    return api_key


APIKeyDep = Annotated[str, Depends(get_api_key)]


async def verify_api_key(
    api_key: APIKeyDep,
) -> str:
    # Verify against configured keys
    valid_keys = settings.api_keys + settings.admin_api_keys

    if not valid_keys:
        logger.error("No API keys configured, Please configure at least one API key.")
        raise InvalidAPIKeyError()

    if api_key not in valid_keys:
        raise InvalidAPIKeyError()

    return api_key


AuthDep = Annotated[str, Depends(verify_api_key)]


async def verify_admin_api_key(
    api_key: APIKeyDep,
) -> str:
    # Verify against configured admin keys
    valid_keys = settings.admin_api_keys

    if not valid_keys:
        logger.error(
            "No admin API keys configured, Please configure at least one admin API key."
        )
        raise InvalidAPIKeyError()

    if api_key not in valid_keys:
        raise InvalidAPIKeyError()

    return api_key


AdminAuthDep = Annotated[str, Depends(verify_admin_api_key)]
