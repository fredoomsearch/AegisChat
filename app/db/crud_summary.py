# app/db/crud_summary.py
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import models

logger = logging.getLogger(__name__)


def update_summary(
    db: Session,
    conv_id: str,
    summary: str,
    commit: bool = True,
) -> Optional[models.Conversation]:
    """
    Update the Conversation.summary for the given conv_id.

    Args:
        db: SQLAlchemy Session.
        conv_id: conversation id (string UUID).
        summary: new summary text.
        commit: if True, commit the transaction here.
                If False, caller is responsible for committing.

    Returns:
        The updated Conversation instance, or None if conversation not found.

    Behavior:
        - Hace rollback si algo falla.
        - Loggea cualquier excepción sin silenciarla.
        - No cambia la API original.
    """
    try:
        conv = (
            db.query(models.Conversation)
            .filter(models.Conversation.id == conv_id)
            .one_or_none()
        )
        if conv is None:
            logger.debug("update_summary: conversation not found conv_id=%s", conv_id)
            return None

        conv.summary = summary
        db.add(conv)

        if commit:
            try:
                db.commit()
            except SQLAlchemyError as e:
                db.rollback()
                logger.exception(
                    "update_summary: commit failed for conv_id=%s: %s", conv_id, e
                )
                raise

            try:
                db.refresh(conv)
            except Exception:
                # refresh failing is non-fatal; log and continue
                logger.exception(
                    "update_summary: refresh failed for conv_id=%s", conv_id
                )
        else:
            logger.debug(
                "update_summary: update applied but not committed (conv_id=%s)",
                conv_id,
            )

        return conv

    except Exception as exc:  # noqa: BLE001
        # Catch-all para no reventar al caller sin rollback.
        try:
            db.rollback()
        except Exception:
            logger.exception(
                "update_summary: rollback failed after exception for conv_id=%s",
                conv_id,
            )
        logger.exception(
            "update_summary: unexpected error for conv_id=%s: %s", conv_id, exc
        )
        raise
