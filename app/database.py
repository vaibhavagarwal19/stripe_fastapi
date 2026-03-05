from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "sqlite:///./payments.db"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def ensure_payment_schema():
    required_columns = {
        "provider": "TEXT DEFAULT 'stripe'",
        "provider_order_id": "TEXT",
        "provider_payment_id": "TEXT",
        "provider_refund_id": "TEXT",
        "raw_response": "TEXT",
    }

    with engine.begin() as conn:
        existing_columns = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(payments)").fetchall()
        }

        for column_name, column_sql in required_columns.items():
            if column_name not in existing_columns:
                conn.exec_driver_sql(f"ALTER TABLE payments ADD COLUMN {column_name} {column_sql}")
