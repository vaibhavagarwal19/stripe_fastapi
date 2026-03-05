from fastapi import FastAPI
from app.database import Base, engine, ensure_payment_schema
from app.payments import router as payments_router
from app.webhook import router as webhook_router

# Create DB tables
Base.metadata.create_all(bind=engine)
ensure_payment_schema()

app = FastAPI(title="Multi-Gateway FastAPI Backend")

@app.get("/health")
def health_check():
    return {"status": "ok"}

# Register routes
app.include_router(payments_router)
app.include_router(webhook_router)
