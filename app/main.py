from fastapi import FastAPI
from app.database import Base, engine
from app.payments import router as payments_router
from app.webhook import router as webhook_router

# Create DB tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Stripe FastAPI Backend")

# Register routes
app.include_router(payments_router)
app.include_router(webhook_router)
