from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_fixed

from app.core.config import settings
from app.core.exceptions import AppError, NoResponseError
from app.dependencies.auth import AuthDep
from app.models.claude import MessagesAPIRequest
from app.processors.claude_ai import ClaudeAIContext
from app.processors.claude_ai.pipeline import ClaudeAIPipeline

router = APIRouter()


def is_retryable_error(exception):
    """Check if the exception is an AppError with retryable=True"""
    return isinstance(exception, AppError) and exception.retryable


@router.post("/messages", response_model=None)
@retry(
    retry=retry_if_exception(is_retryable_error),
    stop=stop_after_attempt(settings.retry_attempts),
    wait=wait_fixed(settings.retry_interval),
)
async def create_message(
    request: Request, messages_request: MessagesAPIRequest, _: AuthDep
) -> StreamingResponse | JSONResponse:
    context = ClaudeAIContext(
        original_request=request,
        messages_api_request=messages_request,
    )

    context = await ClaudeAIPipeline().process(context)

    if not context.response:
        raise NoResponseError()

    return context.response
