import hashlib
import json
from typing import List, Optional, Tuple, Dict
from loguru import logger

from app.models.claude import (
    Base64ImageSource,
    FileImageSource,
    ImageContent,
    InputMessage,
    ContentBlock,
    ServerToolUseContent,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
    URLImageSource,
    WebSearchToolResultContent,
)


class CacheService:
    """
    Service for managing prompt cache mapping to accounts.
    Ensures requests with cached prompts are sent to the same account.
    Uses incremental hashing for better performance.
    """

    def __init__(self):
        # Maps checkpoint hash -> account_id
        self._checkpoint_to_account: Dict[str, str] = {}

    def process_messages(
        self,
        model: str,
        messages: List[InputMessage],
        system: Optional[List[TextContent]] = None,
    ) -> Tuple[Optional[str], List[str]]:
        """
        Process messages to find cached account and extract new checkpoints.

        Args:
            messages: List of input messages
            system: Optional system messages

        Returns:
            Tuple of (account_id, checkpoints) where:
            - account_id: The account ID if a cached prompt was found, None otherwise
            - checkpoints: List of feature values for content blocks with cache_control
        """
        account_id: Optional[str] = None
        checkpoints: List[str] = []

        hasher = hashlib.sha256()

        self._update_hasher(hasher, {"model": model})

        if system:
            for text_content in system:
                content_block_data = self._content_block_to_dict(text_content)
                self._update_hasher(hasher, content_block_data)

                feature_value = hasher.hexdigest()

                if text_content.cache_control:
                    checkpoints.append(feature_value)

                if feature_value in self._checkpoint_to_account:
                    account_id = self._checkpoint_to_account[feature_value]

        for message in messages:
            # Add role marker
            self._update_hasher(hasher, {"role": message.role.value})

            if isinstance(message.content, str):
                self._update_hasher(hasher, {"type": "text", "text": message.content})
            elif isinstance(message.content, list):
                for content_block in message.content:
                    content_block_data = self._content_block_to_dict(content_block)
                    self._update_hasher(hasher, content_block_data)

                    feature_value = hasher.hexdigest()

                    if (
                        hasattr(content_block, "cache_control")
                        and content_block.cache_control
                    ):
                        checkpoints.append(feature_value)

                    if feature_value in self._checkpoint_to_account:
                        account_id = self._checkpoint_to_account[feature_value]

        if account_id:
            logger.debug(
                f"Cache hit: account_id={account_id}, feature={feature_value[:16]}..."
            )

        return account_id, checkpoints

    def add_checkpoints(self, checkpoints: List[str], account_id: str) -> None:
        """
        Add checkpoint mappings to the cache.

        Args:
            checkpoints: List of feature values to map
            account_id: Account ID to map to
        """
        for checkpoint in checkpoints:
            self._checkpoint_to_account[checkpoint] = account_id
            logger.debug(
                f"Added checkpoint mapping: {checkpoint[:16]}... -> {account_id}"
            )

        logger.debug(
            f"Cache updated: {len(checkpoints)} checkpoints added. "
            f"Total cache size: {len(self._checkpoint_to_account)}"
        )

    def _update_hasher(self, hasher: "hashlib._Hash", data: Dict) -> None:
        """
        Update the hasher with new data in a consistent way.

        Args:
            hasher: The hash object to update
            data: Dictionary data to add to the hash
        """
        # Serialize data in a consistent way
        json_str = json.dumps(data, sort_keys=True, separators=(",", ":"))

        # Add a delimiter to ensure proper separation between blocks
        hasher.update(b"\x00")  # NULL byte as delimiter
        hasher.update(json_str.encode("utf-8"))

    def _content_block_to_dict(self, content_block: ContentBlock) -> Dict:
        """
        Convert a ContentBlock to a dictionary for hashing.
        Only includes relevant fields for cache matching.
        """
        result = {"type": content_block.type}

        if isinstance(content_block, TextContent):
            result["text"] = content_block.text
        elif isinstance(content_block, ThinkingContent):
            result["thinking"] = content_block.thinking
        elif isinstance(content_block, ToolUseContent) or isinstance(
            content_block, ServerToolUseContent
        ):
            result["id"] = content_block.id
        elif isinstance(content_block, ToolResultContent) or isinstance(
            content_block, WebSearchToolResultContent
        ):
            result["tool_use_id"] = content_block.tool_use_id
        elif isinstance(content_block, ImageContent):
            result["source_type"] = content_block.source.type
            if isinstance(content_block.source, Base64ImageSource):
                result["source_data"] = content_block.source.data
            elif isinstance(content_block.source, URLImageSource):
                result["source_url"] = content_block.source.url
            elif isinstance(content_block.source, FileImageSource):
                result["source_file"] = content_block.source.file_uuid

        return result

    def clear_cache(self) -> None:
        """Clear all cached mappings and reset stats."""
        self._checkpoint_to_account.clear()
        logger.info("Cache cleared")


cache_service = CacheService()
