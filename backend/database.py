"""Moteur de base de données + sessions.

SQLite par défaut (fichier myapps.db dans le dossier de données). Pour pointer
vers Postgres, définir DATABASE_URL (ex: postgresql+psycopg://user:pass@host:5432/myapps).
"""
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from config import DATABASE_URL, ensure_dirs

_is_sqlite = DATABASE_URL.startswith("sqlite")
if _is_sqlite:
    ensure_dirs()

connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args,
                       **({"pool_size": 10, "max_overflow": 20} if _is_sqlite else {}))

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        # WAL + busy_timeout : lectures et écritures concurrentes sans « database is locked ».
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()


def init_db() -> None:
    import models  # noqa: F401  enregistre les tables sur SQLModel.metadata

    SQLModel.metadata.create_all(engine)
    _ensure_columns()


# Migrations légères : colonnes ajoutées après coup sur une base existante (SQLite).
# Format : {table: [(colonne, DDL)]}. create_all ne modifie jamais une table existante.
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {}


def _ensure_columns() -> None:
    from sqlalchemy import text

    with engine.connect() as conn:
        for table, cols in _MIGRATIONS.items():
            try:
                existing = {r[1] for r in conn.execute(text(f'PRAGMA table_info("{table}")'))}
            except Exception:
                continue
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl}'))
        conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
