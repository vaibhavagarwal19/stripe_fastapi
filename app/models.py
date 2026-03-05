from sqlalchemy import Column, Integer, String
from app.database import Base

class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String, unique=True, index=True)
    amount = Column(Integer)
    currency = Column(String, default="inr")
    status = Column(String, default="created")
    provider = Column(String, default="stripe", index=True)
    provider_order_id = Column(String, nullable=True, index=True)
    provider_payment_id = Column(String, nullable=True, index=True)
    provider_refund_id = Column(String, nullable=True)
    raw_response = Column(String, nullable=True)
    stripe_payment_intent = Column(String)
    stripe_customer_id = Column(String, nullable=True)
