"""Authenticated agent chat endpoint for the Reporting Bureau."""

from dataclasses import dataclass, field
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import Field

from telescreen.api.common.auth import Principal, require_scope
from telescreen.api.common.context import ApiContext
from telescreen.assistant.chat import ChatMessage, ChatRole, ChatService
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ApiScope, ErrorCode


@pydantic.dataclasses.dataclass
class ChatMessageBody:
    """One request transcript message, with content hidden from repr."""

    role: ChatRole
    content: Annotated[str, Field(max_length=20000)] = field(repr=False)


@pydantic.dataclasses.dataclass
class ChatBody:
    """A bounded client-provided transcript."""

    messages: Annotated[list[ChatMessageBody], Field(min_length=1, max_length=50)]


@dataclass(frozen=True)
class ChatRouterDeps:
    """Dependencies required by the chat endpoint."""

    ctx: ApiContext
    chat: ChatService


def build_chat_router(deps: ChatRouterDeps) -> APIRouter:
    """Build the read-scoped agent chat route."""
    router: APIRouter = APIRouter()

    @router.post("/api/chat")
    async def chat(
        body: ChatBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
    ) -> JSONResponse:
        """Answer a user transcript without persisting conversation history."""
        del principal
        messages: list[ChatMessage] = [
            ChatMessage(message.role, message.content) for message in body.messages
        ]
        try:
            answer: str = await deps.chat.answer(messages)
        except TelescreenError as error:
            if error.code is not ErrorCode.LLM_UNAVAILABLE:
                raise
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": ErrorCode.LLM_UNAVAILABLE.value,
                        "detail": "managed LLM is unavailable",
                    }
                },
            )
        return JSONResponse(content={"answer": answer})

    return router
