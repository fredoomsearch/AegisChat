# app/db/models.py
from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Conversation(Base):
    __tablename__ = "conversations"

    # UUID string primary key with default
    id = Column(
        String(36),
        primary_key=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Optional user id (your CRUD already expects this)
    user_id = Column(String, index=True, nullable=True)

    summary = Column(Text, nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # One-to-many: Conversation -> Message
    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Conversation id={self.id!r} user_id={self.user_id!r}>"


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)

    conversation_id = Column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    token_count = Column(Integer, default=0, nullable=False)

    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    # Many-to-one: Message -> Conversation
    conversation = relationship(
        "Conversation",
        back_populates="messages",
        lazy="joined",
    )

    # One-to-many: Message -> Embedding (optional)
    embeddings = relationship(
        "Embedding",
        back_populates="message",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Message id={self.id} conv_id={self.conversation_id!r} role={self.role!r}>"


class Embedding(Base):
    __tablename__ = "embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)

    message_id = Column(
        Integer,
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    vector = Column(LargeBinary, nullable=False)
    dim = Column(Integer, nullable=False)
    score = Column(Float, nullable=True)

    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    # Many-to-one: Embedding -> Message
    message = relationship(
        "Message",
        back_populates="embeddings",
        lazy="joined",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Embedding id={self.id} msg_id={self.message_id}>"
