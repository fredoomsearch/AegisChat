# app/db/deps.py
from __future__ import annotations

import logging
from typing import Generator

from sqlalchemy.orm import Session

from app.db.base import SessionLocal

logger = logging.getLogger(__name__)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that provides a scoped SQLAlchemy Session.

    Usage in routes:
        from fastapi import Depends
        from app.db.deps import get_db

        @router.get("/items")
        def list_items(db: Session = Depends(get_db)):
            ...

    Behavior:
      - Abre una sesión nueva por request.
      - Si el endpoint termina sin lanzar excepción -> commit().
      - Si lanza excepción -> rollback() y re-lanza.
      - En todos los casos -> close() al final.
    """
    db: Session = SessionLocal()
    try:
        yield db
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            # Si el commit falla, hacemos rollback para no dejar la sesión sucia
            db.rollback()
            logger.exception("get_db: commit failed, rolled back.")
            raise
    except Exception:
        # Cualquier excepción en el endpoint ya ha hecho rollback arriba,
        # por si acaso volvemos a intentarlo.
        try:
            db.rollback()
        except Exception:
            logger.exception("get_db: rollback failed after exception.")
        raise
    finally:
        try:
            db.close()
        except Exception:
            logger.exception("get_db: failed to close DB session.")
