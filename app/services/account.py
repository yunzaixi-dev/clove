import asyncio
from datetime import datetime, UTC
from typing import Optional, Dict, Set

from collections import defaultdict
import threading
import json
from pathlib import Path
from loguru import logger
import uuid

from app.core.config import settings
from app.core.exceptions import NoAccountsAvailableError
from app.models.account import Account, AccountStatus, AuthType
from app.services.oauth import oauth_authenticator


class AccountManager:
    """
    Singleton manager for Claude.ai accounts with load balancing and rate limit recovery.
    Supports both cookie and OAuth authentication.
    """

    _instance: Optional["AccountManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        """Implement singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the AccountManager."""
        self._accounts: Dict[str, Account] = {}  # organization_uuid -> Account
        self._cookie_to_uuid: Dict[str, str] = {}  # cookie_value -> organization_uuid
        self._session_accounts: Dict[str, str] = {}  # session_id -> organization_uuid
        self._account_sessions: Dict[str, Set[str]] = defaultdict(
            set
        )  # organization_uuid -> set of session_ids
        self._account_task: Optional[asyncio.Task] = None
        self._max_sessions_per_account = settings.max_sessions_per_cookie
        self._account_task_interval = settings.account_task_interval

        logger.info("AccountManager initialized")

    async def add_account(
        self,
        cookie_value: Optional[str] = None,
        organization_uuid: Optional[str] = None,
    ) -> Account:
        """Add a new account to the manager.

        Args:
            cookie_value: The cookie value (optional)
            organization_uuid: The organization UUID (optional, will be fetched or generated if not provided)
        """
        if cookie_value and cookie_value in self._cookie_to_uuid:
            return self._accounts[self._cookie_to_uuid[cookie_value]]

        if cookie_value and not organization_uuid:
            fetched_uuid = await oauth_authenticator.get_organization_uuid(cookie_value)
            if fetched_uuid:
                organization_uuid = fetched_uuid

        if organization_uuid and organization_uuid in self._accounts:
            existing_account = self._accounts[organization_uuid]

            if cookie_value and existing_account.cookie_value != cookie_value:
                if existing_account.cookie_value:
                    del self._cookie_to_uuid[existing_account.cookie_value]
                existing_account.cookie_value = cookie_value
                self._cookie_to_uuid[cookie_value] = organization_uuid
            return existing_account

        if not organization_uuid:
            organization_uuid = str(uuid.uuid4())
            logger.info(f"Generated new organization UUID: {organization_uuid}")

        # Create new account
        auth_type = AuthType.COOKIE_ONLY if cookie_value else AuthType.OAUTH_ONLY
        account = Account(organization_uuid, cookie_value, auth_type)
        self._accounts[organization_uuid] = account

        if cookie_value:
            self._cookie_to_uuid[cookie_value] = organization_uuid

        logger.info(
            f"Added new account: {organization_uuid[:8]}... (cookie: {cookie_value[:20] + '...' if cookie_value else 'None'})"
        )

        if auth_type == AuthType.COOKIE_ONLY:
            asyncio.create_task(self._attempt_oauth_authentication(account))

        return account

    async def remove_account(self, organization_uuid: str) -> None:
        """Remove an account from the manager."""
        if organization_uuid in self._accounts:
            account = self._accounts[organization_uuid]
            sessions_to_remove = list(
                self._account_sessions.get(organization_uuid, set())
            )

            for session_id in sessions_to_remove:
                if session_id in self._session_accounts:
                    del self._session_accounts[session_id]

            if account.cookie_value and account.cookie_value in self._cookie_to_uuid:
                del self._cookie_to_uuid[account.cookie_value]

            del self._accounts[organization_uuid]

            if organization_uuid in self._account_sessions:
                del self._account_sessions[organization_uuid]

            logger.info(f"Removed account: {organization_uuid[:8]}...")

    async def get_account_for_session(
        self,
        session_id: str,
    ) -> Account:
        """
        Get an available account for the session with load balancing.

        Args:
            session_id: Unique identifier for the session
            auth_type: Filter by authentication type(s). None means any type.

        Returns:
            Account instance if available
        """
        # Convert single auth_type to list for uniform handling
        if session_id in self._session_accounts:
            organization_uuid = self._session_accounts[session_id]
            if organization_uuid in self._accounts:
                account = self._accounts[organization_uuid]
                if account.status == AccountStatus.VALID:
                    return account
                else:
                    del self._session_accounts[session_id]
                    self._account_sessions[organization_uuid].discard(session_id)

        best_account = None
        min_sessions = float("inf")
        earliest_last_used = None

        for organization_uuid, account in self._accounts.items():
            if account.status != AccountStatus.VALID:
                continue

            # Filter by auth type if specified
            if account.auth_type not in [AuthType.BOTH, AuthType.COOKIE_ONLY]:
                continue

            session_count = len(self._account_sessions[organization_uuid])
            if session_count >= self._max_sessions_per_account:
                continue

            # Select account with least sessions
            # If multiple accounts have the same least sessions, select the one with earliest last_used
            if session_count < min_sessions or (
                session_count == min_sessions
                and (
                    earliest_last_used is not None
                    and account.last_used < earliest_last_used
                )
            ):
                min_sessions = session_count
                earliest_last_used = account.last_used
                best_account = account

        if best_account:
            self._session_accounts[session_id] = best_account.organization_uuid
            self._account_sessions[best_account.organization_uuid].add(session_id)

            logger.debug(
                f"Assigned account to session {session_id}, "
                f"account now has {len(self._account_sessions[best_account.organization_uuid])} sessions"
            )

            return best_account

        raise NoAccountsAvailableError()

    async def get_account_for_oauth(self) -> Account:
        """
        Get an available account for OAuth authentication.

        Returns:
            Account instance if available, None otherwise
        """
        earliest_account = None
        earliest_last_used = None

        for account in self._accounts.values():
            if account.status != AccountStatus.VALID:
                continue

            if account.auth_type not in [AuthType.OAUTH_ONLY, AuthType.BOTH]:
                continue

            if earliest_last_used is None or account.last_used < earliest_last_used:
                earliest_last_used = account.last_used
                earliest_account = account

        if earliest_account:
            logger.debug(
                f"Selected OAuth account: {earliest_account.organization_uuid[:8]}... "
                f"(last used: {earliest_account.last_used.isoformat()})"
            )
            return earliest_account

        raise NoAccountsAvailableError()

    async def release_session(self, session_id: str) -> None:
        """Release a session's account assignment."""
        if session_id in self._session_accounts:
            organization_uuid = self._session_accounts[session_id]
            del self._session_accounts[session_id]

            if organization_uuid in self._account_sessions:
                self._account_sessions[organization_uuid].discard(session_id)

            logger.debug(f"Released account for session {session_id}")

    async def start_task(self) -> None:
        """Start the background task for AccountManager."""
        if self._account_task is None or self._account_task.done():
            self._account_task = asyncio.create_task(self._task_loop())

    async def stop_task(self) -> None:
        """Stop the background task for AccountManager."""
        if self._account_task and not self._account_task.done():
            self._account_task.cancel()
            try:
                await self._account_task
            except asyncio.CancelledError:
                pass

    async def _task_loop(self) -> None:
        """Background loop for AccountManager."""
        while True:
            try:
                await self._check_and_recover_accounts()
                await asyncio.sleep(self._account_task_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in recovery: {e}")

            try:
                await self._check_and_refresh_accounts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in refresh: {e}")

            await asyncio.sleep(self._account_task_interval)

    async def _check_and_recover_accounts(self) -> None:
        """Check and recover rate-limited accounts."""
        current_time = datetime.now(UTC)

        for account in self._accounts.values():
            # Check rate-limited accounts
            if (
                account.status == AccountStatus.RATE_LIMITED
                and account.resets_at
                and current_time >= account.resets_at
            ):
                account.status = AccountStatus.VALID
                account.resets_at = None
                logger.info(
                    f"Recovered rate-limited account: {account.organization_uuid[:8]}..."
                )

    async def _check_and_refresh_accounts(self) -> None:
        """Check and refresh expired/expiring tokens."""
        current_timestamp = datetime.now(UTC).timestamp()

        for account in self._accounts.values():
            if (
                account.auth_type in [AuthType.OAUTH_ONLY, AuthType.BOTH]
                and account.refresh_token
                and account.expires_at
            ):
                if account.expires_at - current_timestamp < 300:
                    asyncio.create_task(self._refresh_account_token(account))

    async def _refresh_account_token(self, account: Account) -> None:
        """Refresh OAuth token for an account."""
        logger.info(
            f"Refreshing OAuth token for account: {account.organization_uuid[:8]}..."
        )

        success = await oauth_authenticator.refresh_account_token(account)
        if success:
            logger.info(
                f"Successfully refreshed OAuth token for account: {account.organization_uuid[:8]}..."
            )
            await self.save_accounts()
        else:
            logger.warning(
                f"Failed to refresh OAuth token for account: {account.organization_uuid[:8]}..."
            )
            # Downgrade to cookie-only if refresh fails
            account.auth_type = AuthType.COOKIE_ONLY
            account.access_token = None
            account.refresh_token = None
            account.expires_at = None

    async def _attempt_oauth_authentication(self, account: Account) -> None:
        """Attempt OAuth authentication for an account."""

        logger.info(
            f"Attempting OAuth authentication for account: {account.organization_uuid[:8]}..."
        )

        success = await oauth_authenticator.authenticate_account(account)
        if not success:
            logger.warning(
                f"OAuth authentication failed for account: {account.organization_uuid[:8]}..., keeping as CookieOnly"
            )
        else:
            logger.info(
                f"OAuth authentication successful for account: {account.organization_uuid[:8]}..."
            )

    async def get_status(self) -> Dict:
        """Get the current status of all accounts."""
        status = {
            "total_accounts": len(self._accounts),
            "valid_accounts": sum(
                1 for a in self._accounts.values() if a.status == AccountStatus.VALID
            ),
            "rate_limited_accounts": sum(
                1
                for a in self._accounts.values()
                if a.status == AccountStatus.RATE_LIMITED
            ),
            "invalid_accounts": sum(
                1 for a in self._accounts.values() if a.status == AccountStatus.INVALID
            ),
            "active_sessions": len(self._session_accounts),
            "accounts": [],
        }

        for organization_uuid, account in self._accounts.items():
            account_info = {
                "organization_uuid": organization_uuid[:8] + "...",
                "cookie": account.cookie_value[:20] + "..."
                if account.cookie_value
                else "None",
                "status": account.status.value,
                "auth_type": account.auth_type.value,
                "sessions": len(self._account_sessions[organization_uuid]),
                "last_used": account.last_used.isoformat(),
                "resets_at": account.resets_at.isoformat()
                if account.resets_at
                else None,
                "has_oauth": account.access_token is not None,
            }
            status["accounts"].append(account_info)

        return status

    async def save_accounts(self) -> None:
        """Save all accounts to JSON file.

        Args:
            data_folder: Optional data folder path. If not provided, uses settings.data_folder
        """
        folder = Path(settings.data_folder)
        folder.mkdir(parents=True, exist_ok=True)

        accounts_file = folder / "accounts.json"

        accounts_data = {
            organization_uuid: account.to_dict()
            for organization_uuid, account in self._accounts.items()
        }

        with open(accounts_file, "w") as f:
            json.dump(accounts_data, f, indent=2)

        logger.info(f"Saved {len(accounts_data)} accounts to {accounts_file}")

    async def load_accounts(self) -> None:
        """Load accounts from JSON file.

        Args:
            data_folder: Optional data folder path. If not provided, uses settings.data_folder
        """
        folder = Path(settings.data_folder)
        accounts_file = folder / "accounts.json"

        if not accounts_file.exists():
            logger.info(f"No accounts file found at {accounts_file}")
            return

        try:
            with open(accounts_file, "r") as f:
                accounts_data = json.load(f)

            for organization_uuid, account_data in accounts_data.items():
                account = Account.from_dict(account_data)
                self._accounts[organization_uuid] = account

                # Rebuild cookie mapping
                if account.cookie_value:
                    self._cookie_to_uuid[account.cookie_value] = organization_uuid

            logger.info(f"Loaded {len(accounts_data)} accounts from {accounts_file}")

        except Exception as e:
            logger.error(f"Failed to load accounts from {accounts_file}: {e}")

    def __repr__(self) -> str:
        """String representation of the AccountManager."""
        return f"<AccountManager accounts={len(self._accounts)} sessions={len(self._session_accounts)}>"


account_manager = AccountManager()
