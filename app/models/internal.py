from typing import List, Optional
from pydantic import BaseModel, Field
from .claude import Tool


class Attachment(BaseModel):
    extracted_content: str
    file_name: str
    file_type: str
    file_size: int

    @classmethod
    def from_text(cls, content: str) -> "Attachment":
        """Create text attachment."""
        return cls(
            extracted_content=content,
            file_name="paste.txt",
            file_type="txt",
            file_size=len(content),
        )


class ClaudeWebRequest(BaseModel):
    max_tokens_to_sample: int
    attachments: List[Attachment]
    files: List[str] = Field(default_factory=list)
    model: Optional[str] = None
    rendering_mode: str = "messages"
    prompt: str = ""
    timezone: str
    tools: List[Tool] = Field(default_factory=list)


class ConversationState(BaseModel):
    org_uuid: Optional[str] = None
    conv_uuid: Optional[str] = None
    paprika_mode: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)

    @property
    def is_pro(self) -> bool:
        """Check if user has pro capabilities."""
        pro_keywords = ["pro", "enterprise", "raven", "max"]
        return any(
            keyword in cap.lower()
            for cap in self.capabilities
            for keyword in pro_keywords
        )


class UploadResponse(BaseModel):
    file_uuid: str
