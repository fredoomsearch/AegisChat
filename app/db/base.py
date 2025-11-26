# app/db/base.py
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# Configuración de la base de datos
# ---------------------------------------------------------
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./aegis.db").strip()

_IS_SQLITE = DATABASE_URL.lower().startswith("sqlite")
connect_args = {"check_same_thread": False} if _IS_SQLITE else {}

_engine_echo = os.getenv("SQL_ECHO", "").lower() in ("1", "true", "yes")

# Engine principal
engine: Engine = create_engine(
    DATABASE_URL,
    future=True,
    connect_args=connect_args,
    pool_pre_ping=True,
    echo=_engine_echo,
)

# Session factory
SessionLocal = sessionmaker(
    bind=engine,
    future=True,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

# Importar Base y modelos desde models.py
from app.db.models import Base, Conversation, Message, Embedding  # noqa: F401  (side-effect import)


# ---------------------------------------------------------
# Init DB
# ---------------------------------------------------------
def init_db(bind_engine: Optional[Engine] = None) -> None:
    """
    Crea todas las tablas en la base de datos usando el engine dado
    (o el engine global si no se pasa ninguno).

    Usado por main.py en el evento de startup.
    """
    _engine = bind_engine or engine
    try:
        Base.metadata.create_all(bind=_engine)
        logger.info("Tablas de la base de datos creadas correctamente.")
    except Exception as e:  # noqa: BLE001
        logger.exception("Error al crear las tablas: %s", e)
        raise


def init_db_manual() -> None:
    """
    Función para crear tablas manualmente desde CLI.

    Uso:
        python -m app.db.base
    """
    try:
        print("Creando tablas manualmente...")
        Base.metadata.create_all(bind=engine)
        print("Tablas creadas correctamente.")
    except Exception as e:  # noqa: BLE001
        print(f"Error al crear tablas: {e}")
        raise


# ---------------------------------------------------------
# Context manager de sesión (útil para scripts/CLI)
# ---------------------------------------------------------
@contextmanager
def get_db() -> Generator[Session, None, None]:
    """
    Proporciona una sesión de base de datos que se cierra automáticamente.

    Ejemplo:
        from app.db.base import get_db

        with get_db() as db:
            db.query(...)

    * Ojo: en FastAPI estás usando app.db.deps.get_db como dependencia.
    * Este helper es más para scripts, tareas offline, etc.
    """
    db: Session = SessionLocal()
    try:
        yield db
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("get_db (base): commit failed, rollback executed.")
            raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            logger.exception("get_db (base): rollback failed after exception.")
        raise
    finally:
        try:
            db.close()
        except Exception:
            logger.exception("get_db (base): failed to close DB session.")


if __name__ == "__main__":
    init_db_manual()
