# app/db/crud_conversation.py
from __future__ import annotations

from typing import Optional, List
from uuid import uuid4
import logging

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import desc

from app.db import models

logger = logging.getLogger(__name__)


def create_conversation(db: Session, user_id: Optional[str] = None) -> str:
    """
    Create a new Conversation row and return its ID as a string.
    API and behavior untouched.
    """
    conv = models.Conversation(
        id=str(uuid4()),
        summary="",
    )

    try:
        db.add(conv)
        db.commit()
        db.refresh(conv)
        return conv.id
    except SQLAlchemyError as e:
        db.rollback()
        logger.exception(
            "create_conversation: failed to create conversation (user_id=%s): %s",
            user_id, e,
        )
        raise


def get_conversation(db: Session, conv_id: str) -> Optional[models.Conversation]:
    """
    Fetch a Conversation by ID.
    Returns None if it doesn't exist.
    """
    try:
        return (
            db.query(models.Conversation)
            .filter(models.Conversation.id == conv_id)
            .one_or_none()
        )
    except SQLAlchemyError as e:
        logger.exception(
            "get_conversation: error fetching conv_id=%s: %s",
            conv_id, e,
        )
        return None


def append_message(db: Session, conv_id: str, role: str, content: str) -> models.Message:
    """
    Add a new Message to a conversation.
    If conversation does not exist, create it automatically.
    """
    try:
        conv = get_conversation(db, conv_id)

        if conv is None:
            conv = models.Conversation(id=str(uuid4()), summary="")
            db.add(conv)
            db.commit()
            db.refresh(conv)

        msg = models.Message(
            conversation_id=conv.id,
            role=role,
            content=content,
        )

        db.add(msg)
        db.commit()
        db.refresh(msg)
        return msg

    except SQLAlchemyError as e:
        db.rollback()
        logger.exception(
            "append_message: failed for conv_id=%s role=%s: %s",
            conv_id, role, e,
        )
        raise


def get_recent_messages(
    db: Session,
    conv_id: str,
    limit: int = 20,
) -> List[models.Message]:
    """
    Return the 'limit' most recent messages, ordered old→new.
    """
    try:
        conv = get_conversation(db, conv_id)
        if conv is None:
            return []

        msgs = (
            db.query(models.Message)
            .filter(models.Message.conversation_id == conv.id)
            .order_by(desc(models.Message.created_at))
            .limit(limit)
            .all()
        )

        # reverse to chronological order
        return list(reversed(msgs))

    except SQLAlchemyError as e:
        logger.exception(
            "get_recent_messages: error fetching conv_id=%s: %s",
            conv_id, e,
        )
        return []
