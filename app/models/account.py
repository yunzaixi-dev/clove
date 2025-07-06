from typing import Optional
from enum import Enum
from datetime import datetime

from app.core.exceptions import ClaudeRateLimitedError, OrganizationDisabledError


class AccountStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    RATE_LIMITED = "rate_limited"


class AuthType(str, Enum):
    COOKIE_ONLY = "cookie_only"
    OAUTH_ONLY = "oauth_only"
    BOTH = "both"


class Account:
    """Represents a Claude.ai account with cookie and/or OAuth authentication."""

    def __init__(
        self,
        organization_uuid: str,
        cookie_value: Optional[str] = None,
        auth_type: AuthType = AuthType.COOKIE_ONLY,
    ):
        self.organization_uuid = organization_uuid
        self.cookie_value = cookie_value
        self.status = AccountStatus.VALID
        self.auth_type = auth_type
        self.last_used = datetime.now()
        self.resets_at: Optional[datetime] = None

        # OAuth fields
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at: Optional[float] = None

    def __enter__(self) -> "Account":
        """Enter the context manager."""
        self.last_used = datetime.now()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the context manager and handle CookieRateLimitedError."""
        if exc_type is ClaudeRateLimitedError and isinstance(
            exc_val, ClaudeRateLimitedError
        ):
            self.status = AccountStatus.RATE_LIMITED
            self.resets_at = exc_val.resets_at

        if exc_type is OrganizationDisabledError and isinstance(
            exc_val, OrganizationDisabledError
        ):
            self.status = AccountStatus.INVALID

        return False

    def to_dict(self) -> dict:
        """Convert Account to dictionary for JSON serialization."""
        return {
            "organization_uuid": self.organization_uuid,
            "cookie_value": self.cookie_value,
            "status": self.status.value,
            "auth_type": self.auth_type.value,
            "last_used": self.last_used.isoformat(),
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        """Create Account from dictionary."""
        account = cls(
            organization_uuid=data["organization_uuid"],
            cookie_value=data.get("cookie_value"),
            auth_type=AuthType(data["auth_type"]),
        )
        account.status = AccountStatus(data["status"])
        account.last_used = datetime.fromisoformat(data["last_used"])
        account.resets_at = (
            datetime.fromisoformat(data["resets_at"]) if data["resets_at"] else None
        )
        account.access_token = data.get("access_token")
        account.refresh_token = data.get("refresh_token")
        account.expires_at = data.get("expires_at")
        return account
