from dataclasses import dataclass
from typing import Optional

from app.models.claude import MessagesAPIRequest
from app.processors.base import BaseContext


@dataclass
class ClaudeContext(BaseContext):
    messages_api_request: Optional[MessagesAPIRequest] = None
