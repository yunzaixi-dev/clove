import os
import json
from typing import List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl

from app.dependencies.auth import AdminAuthDep
from app.core.config import Settings, settings


class SettingsRead(BaseModel):
    """Model for returning settings."""

    api_keys: List[str]
    admin_api_keys: List[str]

    proxy_url: HttpUrl | None

    claude_ai_url: HttpUrl
    claude_api_baseurl: HttpUrl

    custom_prompt: str | None
    use_real_roles: bool
    human_name: str
    assistant_name: str
    padtxt_length: int
    allow_external_images: bool

    preserve_chats: bool

    oauth_client_id: str
    oauth_authorize_url: str
    oauth_token_url: str
    oauth_redirect_uri: str


class SettingsUpdate(BaseModel):
    """Model for updating settings."""

    api_keys: List[str] | None = None
    admin_api_keys: List[str] | None = None

    proxy_url: HttpUrl | None = None

    claude_ai_url: HttpUrl | None = None
    claude_api_baseurl: HttpUrl | None = None

    custom_prompt: str | None = None
    use_real_roles: bool | None = None
    human_name: str | None = None
    assistant_name: str | None = None
    padtxt_length: int | None = None
    allow_external_images: bool | None = None

    preserve_chats: bool | None = None

    oauth_client_id: str | None = None
    oauth_authorize_url: str | None = None
    oauth_token_url: str | None = None
    oauth_redirect_uri: str | None = None


router = APIRouter(prefix="/admin/settings", tags=["Settings Management"])


@router.get("", response_model=SettingsRead)
async def get_settings(_: AdminAuthDep) -> Settings:
    """Get current settings."""
    # Get settings that can be modified
    return settings


@router.put("", response_model=SettingsUpdate)
async def update_settings(_: AdminAuthDep, updates: SettingsUpdate) -> Settings:
    """Update settings and save to config.json."""
    # Get current config from file if exists
    config_path = os.path.join(settings.data_folder, "config.json")
    os.makedirs(settings.data_folder, exist_ok=True)

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            config_data = {}
    else:
        config_data = {}

    update_dict = updates.model_dump(exclude_unset=True)
    config_data.update(update_dict)

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
    except IOError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {str(e)}")

    for key, value in update_dict.items():
        if hasattr(settings, key):
            setattr(settings, key, value)

    return settings
