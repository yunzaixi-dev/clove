"""HTTP client abstraction layer that supports both curl_cffi and httpx."""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Tuple, AsyncIterator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
    before_sleep_log,
)
from loguru import logger
import json

from app.core.config import settings

# Try to import curl_cffi, fall back to httpx if not available
try:
    from curl_cffi.requests import (
        AsyncSession as CurlAsyncSession,
        Response as CurlResponse,
    )
    from curl_cffi.requests.exceptions import RequestException as CurlRequestException

    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False

# Always try to import httpx as fallback
try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

if not CURL_CFFI_AVAILABLE and not HTTPX_AVAILABLE:
    raise ImportError(
        "Neither curl_cffi nor httpx is installed. Please install at least one of them."
    )


class Response(ABC):
    """Abstract response class."""

    @property
    @abstractmethod
    def status_code(self) -> int:
        """Get response status code."""
        pass

    @abstractmethod
    def json(self) -> Any:
        """Parse response as JSON."""
        pass

    @abstractmethod
    async def ajson(self) -> Any:
        """Parse response as JSON asynchronously."""
        pass

    @property
    @abstractmethod
    def headers(self) -> Dict[str, str]:
        """Get response headers."""
        pass

    @abstractmethod
    def aiter_bytes(self, chunk_size: Optional[int] = None) -> AsyncIterator[bytes]:
        """Iterate over response bytes."""
        pass


class CurlResponseWrapper(Response):
    """curl_cffi response wrapper."""

    def __init__(self, response: "CurlResponse", stream: bool = False):
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    def json(self) -> Any:
        return self._response.json()

    async def ajson(self) -> Any:
        content = ""
        async for chunk in self._response.aiter_content():
            content += chunk.decode("utf-8")
        return json.loads(content)

    @property
    def headers(self) -> Dict[str, str]:
        return self._response.headers

    async def aiter_bytes(
        self, chunk_size: Optional[int] = None
    ) -> AsyncIterator[bytes]:
        async for chunk in self._response.aiter_content(chunk_size):
            yield chunk
        await self._response.aclose()


class HttpxResponse(Response):
    """httpx response wrapper."""

    def __init__(self, response: httpx.Response):
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    def json(self) -> Any:
        return self._response.json()

    async def ajson(self) -> Any:
        await self._response.aread()
        return self._response.json()

    @property
    def headers(self) -> Dict[str, str]:
        return self._response.headers

    async def aiter_bytes(
        self, chunk_size: Optional[int] = None
    ) -> AsyncIterator[bytes]:
        async for chunk in self._response.aiter_bytes(chunk_size):
            yield chunk
        await self._response.aclose()


class AsyncSession(ABC):
    """Abstract async session class."""

    @abstractmethod
    async def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json: Optional[Any] = None,
        data: Optional[Any] = None,
        stream: bool = False,
        **kwargs,
    ) -> Response:
        """Make an HTTP request."""
        pass

    @abstractmethod
    async def close(self):
        """Close the session."""
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


if CURL_CFFI_AVAILABLE:

    class CurlAsyncSessionWrapper(AsyncSession):
        """curl_cffi async session wrapper."""

        def __init__(
            self,
            timeout: int = settings.request_timeout,
            impersonate: str = "chrome",
            proxy: Optional[str] = settings.proxy_url,
            follow_redirects: bool = True,
        ):
            if not CURL_CFFI_AVAILABLE:
                raise ImportError("curl_cffi is not installed")

            self._session = CurlAsyncSession(
                timeout=timeout,
                impersonate=impersonate,
                proxy=proxy,
                allow_redirects=follow_redirects,
            )

        @retry(
            stop=stop_after_attempt(settings.request_retries),
            wait=wait_fixed(settings.request_retry_interval),
            retry=retry_if_exception_type(CurlRequestException),
            before_sleep=before_sleep_log(logger, "WARNING"),
            reraise=True,
        )
        async def request(
            self,
            method: str,
            url: str,
            headers: Optional[Dict[str, str]] = None,
            json: Optional[Any] = None,
            data: Optional[Any] = None,
            stream: bool = False,
            **kwargs,
        ) -> Response:
            logger.debug(f"Making {method} request to {url}")
            response = await self._session.request(
                method=method,
                url=url,
                headers=headers,
                json=json,
                data=data,
                stream=stream,
                **kwargs,
            )
            return CurlResponseWrapper(response)

        async def close(self):
            await self._session.close()


if HTTPX_AVAILABLE:

    class HttpxAsyncSession(AsyncSession):
        """httpx async session wrapper."""

        def __init__(
            self,
            timeout: int = settings.request_timeout,
            impersonate: str = "chrome",
            proxy: Optional[str] = settings.proxy_url,
            follow_redirects: bool = True,
        ):
            if not HTTPX_AVAILABLE:
                raise ImportError("httpx is not installed")

            # Create a custom SSL context that doesn't verify certificates
            # Note: This should only be used for development/testing with trusted proxies
            import ssl

            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            self._client = httpx.AsyncClient(
                timeout=timeout,
                proxy=proxy,
                follow_redirects=follow_redirects,
                verify=ssl_context,  # Use custom SSL context
            )

        async def stream(
            self,
            method: str,
            url: str,
            headers: Optional[Dict[str, str]] = None,
            json: Optional[Any] = None,
            data: Optional[Any] = None,
            **kwargs,
        ) -> Response:
            """
            Alternative to `httpx.request()` that streams the response body
            instead of loading it into memory at once.

            **Parameters**: See `httpx.request`.

            See also: [Streaming Responses][0]

            [0]: /quickstart#streaming-responses
            """
            request = self._client.build_request(
                method=method,
                url=url,
                data=data,
                json=json,
                headers=headers,
                **kwargs,
            )
            response = await self._client.send(
                request=request,
                stream=True,
            )

            return response

        @retry(
            stop=stop_after_attempt(settings.request_retries),
            wait=wait_fixed(settings.request_retry_interval),
            retry=retry_if_exception_type(httpx.RequestError),
            before_sleep=before_sleep_log(logger, "WARNING"),
            reraise=True,
        )
        async def request(
            self,
            method: str,
            url: str,
            headers: Optional[Dict[str, str]] = None,
            json: Optional[Any] = None,
            data: Optional[Any] = None,
            stream: bool = False,
            **kwargs,
        ) -> Response:
            logger.debug(f"Making {method} request to {url}")
            if stream:
                response = await self.stream(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json,
                    data=data,
                    **kwargs,
                )
            else:
                response = await self._client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json,
                    data=data,
                    **kwargs,
                )

            return HttpxResponse(response)

        async def close(self):
            await self._client.aclose()

    def create_session(
        timeout: int = settings.request_timeout,
        impersonate: str = "chrome",
        proxy: Optional[str] = settings.proxy_url,
        follow_redirects: bool = True,
    ) -> AsyncSession:
        """Create an async session using the available HTTP client.

        Prefers curl_cffi if available, falls back to httpx.
        """
        if CURL_CFFI_AVAILABLE:
            logger.debug("Using curl_cffi as HTTP client")
            return CurlAsyncSessionWrapper(
                timeout=timeout,
                impersonate=impersonate,
                proxy=proxy,
                follow_redirects=follow_redirects,
            )
        else:
            logger.debug("Using httpx as HTTP client (curl_cffi not available)")
            return HttpxAsyncSession(
                timeout=timeout,
                impersonate=impersonate,
                proxy=proxy,
                follow_redirects=follow_redirects,
            )


async def download_image(url: str, timeout: int = 30) -> Tuple[bytes, str]:
    """Download an image from a URL and return content and content type.

    Uses curl_cffi if available, otherwise httpx.
    """
    if CURL_CFFI_AVAILABLE:
        async with CurlAsyncSession() as session:
            response = await session.get(url, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "image/jpeg")
            return response.content, content_type
    elif HTTPX_AVAILABLE:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "image/jpeg")
            return response.content, content_type
    else:
        raise ImportError("No HTTP client available for downloading images")


# Export the appropriate exception class
if CURL_CFFI_AVAILABLE:
    RequestException = CurlRequestException
else:
    RequestException = httpx.RequestError
