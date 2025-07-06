import base64
import hashlib
import secrets
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs

from curl_cffi.requests import AsyncSession
from loguru import logger

from app.core.config import settings
from app.core.account import Account, AuthType


class OAuthAuthenticator:
    """OAuth authenticator for Claude accounts using cookies."""

    def __init__(self, proxy: Optional[str] = None):
        self.proxy = proxy
        self.session: Optional[AsyncSession] = None
        self.client_id = settings.oauth_client_id
        self.claude_endpoint = settings.claude_ai_url.encoded_string()
        self.authorize_url = settings.oauth_authorize_url
        self.token_url = settings.oauth_token_url
        self.redirect_uri = settings.oauth_redirect_uri

    async def initialize(self):
        """Initialize session."""
        if not self.session:
            self.session = AsyncSession(
                timeout=30,
                impersonate="chrome",
                proxy=self.proxy,
            )

    async def cleanup(self):
        """Cleanup resources."""
        if self.session:
            await self.session.close()
            self.session = None

    def _generate_pkce(self) -> Tuple[str, str]:
        """Generate PKCE verifier and challenge."""
        verifier = (
            base64.urlsafe_b64encode(secrets.token_bytes(32))
            .decode("utf-8")
            .rstrip("=")
        )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest())
            .decode("utf-8")
            .rstrip("=")
        )
        return verifier, challenge

    def _build_headers(self, cookie: str) -> Dict[str, str]:
        """Build request headers."""
        return {
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Cookie": cookie,
            "Origin": self.claude_endpoint,
            "Referer": f"{self.claude_endpoint}/new",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    async def get_organization_uuid(self, cookie: str) -> Optional[str]:
        """Get organization UUID."""
        await self.initialize()

        url = f"{self.claude_endpoint}/api/organizations"
        headers = self._build_headers(cookie)

        try:
            response = await self.session.get(url, headers=headers)

            if response.status_code != 200:
                logger.error(f"Failed to get organizations: {response.status_code}")
                return None

            org_data = response.json()
            if org_data and len(org_data) > 0:
                organization_uuid = org_data[0].get("uuid")
                logger.debug(f"Got organization UUID: {organization_uuid}")
                return organization_uuid

        except Exception as e:
            logger.error(f"Error getting organization UUID: {e}")

        return None

    async def authorize_with_cookie(
        self, cookie: str, organization_uuid: str
    ) -> Optional[Tuple[str, str]]:
        """
        Use Cookie to automatically get authorization code.
        Returns: (authorization code, verifier) or None if failed
        """
        await self.initialize()

        verifier, challenge = self._generate_pkce()
        state = (
            base64.urlsafe_b64encode(secrets.token_bytes(32))
            .decode("utf-8")
            .rstrip("=")
        )

        authorize_url = self.authorize_url.format(organization_uuid=organization_uuid)

        payload = {
            "response_type": "code",
            "client_id": self.client_id,
            "organization_uuid": organization_uuid,
            "redirect_uri": self.redirect_uri,
            "scope": "user:profile user:inference",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

        headers = self._build_headers(cookie)
        headers["Content-Type"] = "application/json"

        try:
            logger.debug(f"Requesting authorization from: {authorize_url}")

            response = await self.session.post(
                authorize_url, json=payload, headers=headers
            )

            if response.status_code != 200:
                logger.error(f"Authorization failed: {response.status_code}")
                return None

            auth_response = response.json()
            redirect_uri = auth_response.get("redirect_uri")

            if not redirect_uri:
                logger.error("No redirect_uri in authorization response")
                return None

            logger.info(f"Got redirect URI: {redirect_uri}")

            parsed_url = urlparse(redirect_uri)
            query_params = parse_qs(parsed_url.query)

            if "code" not in query_params:
                logger.error("No authorization code in redirect_uri")
                return None

            auth_code = query_params["code"][0]
            response_state = query_params.get("state", [None])[0]

            logger.info(f"Extracted authorization code: {auth_code[:20]}...")

            if response_state:
                full_code = f"{auth_code}#{response_state}"
            else:
                full_code = auth_code

            return full_code, verifier

        except Exception as e:
            logger.error(f"Error during authorization: {e}")
            return None

    async def exchange_token(self, code: str, verifier: str) -> Optional[Dict]:
        """Exchange authorization code for access token."""
        await self.initialize()

        parts = code.split("#")
        auth_code = parts[0]
        state = parts[1] if len(parts) > 1 else None

        data = {
            "code": auth_code,
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
        }

        if state:
            data["state"] = state

        try:
            response = await self.session.post(
                self.token_url, json=data, headers={"Content-Type": "application/json"}
            )

            if response.status_code != 200:
                logger.error(f"Token exchange failed: {response.status_code}")
                return None

            token_data = response.json()
            return token_data

        except Exception as e:
            logger.error(f"Error exchanging token: {e}")
            return None

    async def refresh_access_token(self, refresh_token: str) -> Optional[Dict]:
        """Refresh access token."""
        await self.initialize()

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }

        try:
            response = await self.session.post(
                self.token_url, json=data, headers={"Content-Type": "application/json"}
            )

            if response.status_code != 200:
                logger.error(f"Token refresh failed: {response.status_code}")
                return None

            token_data = response.json()
            return token_data

        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            return None

    async def authenticate_account(self, account: Account) -> bool:
        """
        Authenticate an account using OAuth.
        Returns True if successful, False otherwise.
        """
        if not account.cookie_value:
            logger.error("Account has no cookie value")
            return False

        try:
            # Get organization UUID
            org_uuid = await self.get_organization_uuid(account.cookie_value)
            if not org_uuid:
                logger.error("Failed to get organization UUID")
                return False

            # Get authorization code
            auth_result = await self.authorize_with_cookie(
                account.cookie_value, org_uuid
            )
            if not auth_result:
                logger.error("Failed to get authorization code")
                return False

            auth_code, verifier = auth_result

            # Exchange for tokens
            token_data = await self.exchange_token(auth_code, verifier)
            if not token_data:
                logger.error("Failed to exchange tokens")
                return False

            # Update account with OAuth tokens
            account.access_token = token_data["access_token"]
            account.refresh_token = token_data["refresh_token"]
            account.expires_at = time.time() + token_data["expires_in"]
            account.auth_type = AuthType.BOTH

            logger.info(
                f"Successfully authenticated account with OAuth: {account.organization_uuid[:8]}..."
            )
            return True

        except Exception as e:
            logger.error(f"OAuth authentication failed: {e}")
            return False

    async def refresh_account_token(self, account: Account) -> bool:
        """
        Refresh OAuth token for an account.
        Returns True if successful, False otherwise.
        """
        if not account.refresh_token:
            logger.error("Account has no refresh token")
            return False

        token_data = await self.refresh_access_token(account.refresh_token)
        if not token_data:
            return False

        account.access_token = token_data["access_token"]
        account.refresh_token = token_data["refresh_token"]
        account.expires_at = time.time() + token_data["expires_in"]

        logger.info(
            f"Successfully refreshed OAuth token for account: {account.organization_uuid[:8]}..."
        )
        return True


oauth_authenticator = OAuthAuthenticator()
