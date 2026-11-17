from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .settings import DATABASE_URL
class Base(DeclarativeBase): pass
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
def db_session():
    with Session() as db: yield db


def ensure_schema():
    """Idempotent upgrade for databases created before inventory counting was added.

    New databases get everything from Base.metadata.create_all. Existing ones only need
    the additive columns; the count tables are created by create_all beforehand.
    """
    statements = [
        "ALTER TABLE samples ADD COLUMN IF NOT EXISTS quantity_version integer NOT NULL DEFAULT 0",
        "ALTER TABLE transfers ADD COLUMN IF NOT EXISTS invalidated boolean NOT NULL DEFAULT false",
        "ALTER TABLE transfers ADD COLUMN IF NOT EXISTS invalid_reason varchar(300) NOT NULL DEFAULT ''",
        "ALTER TABLE transfers ADD COLUMN IF NOT EXISTS invalidated_at timestamptz",
        "ALTER TABLE transfers ADD COLUMN IF NOT EXISTS invalidated_by_count_id integer",
        """DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'transfers_invalidated_by_count_id_fkey') THEN
                ALTER TABLE transfers ADD CONSTRAINT transfers_invalidated_by_count_id_fkey
                    FOREIGN KEY (invalidated_by_count_id) REFERENCES inventory_counts(id);
            END IF;
        END $$""",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
