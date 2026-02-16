import uuid
import stripe
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import SessionLocal
from app.models import Payment

class PaymentRequest(BaseModel):
    amount: int

class SaveCardRequest(BaseModel):
    payment_method_id: str
    user_id: str

router = APIRouter(prefix="/payments", tags=["Payments"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# @router.post("/create")
# def create_payment(request: PaymentRequest, db: Session = Depends(get_db)):
#     amount = request.amount
#     order_id = str(uuid.uuid4())

#     # Fake user id for practice
#     user_id = "user_123"

#     customer_id = get_or_create_customer(user_id)

#     intent = stripe.PaymentIntent.create(
#         amount=amount,
#         currency="inr",
#         customer=customer_id,                     # 🔥 REQUIRED
#         setup_future_usage="off_session",         # 🔥 SAVES CARD
#         automatic_payment_methods={"enabled": True},
#         metadata={"order_id": order_id}
#     )

#     payment = Payment(
#         order_id=order_id,
#         amount=amount,
#         status="initiated",
#         stripe_payment_intent=intent.id,
#         stripe_customer_id=customer_id
#     )

#     db.add(payment)
#     db.commit()

#     return {
#         "order_id": order_id,
#         "client_secret": intent.client_secret
#     }
@router.post("/create")
def create_payment(request: PaymentRequest, db: Session = Depends(get_db)):
    amount = request.amount
    order_id = str(uuid.uuid4())
    user_id = "user_123"
 
    customer_id = get_or_create_customer(user_id)
 
    # ✅ Create Ephemeral Key
    ephemeral_key = stripe.EphemeralKey.create(
        customer=customer_id,
        stripe_version="2023-10-16"  # MUST match your Stripe version
    )
 
    # ✅ Create PaymentIntent
    intent = stripe.PaymentIntent.create(
        amount=amount,
        currency="inr",
        customer=customer_id,
        # setup_future_usage="off_session",
        automatic_payment_methods={"enabled": True},
        metadata={"order_id": order_id}
    )
 
    payment = Payment(
        order_id=order_id,
        amount=amount,
        status="initiated",
        stripe_payment_intent=intent.id,
        stripe_customer_id=customer_id
    )
 
    db.add(payment)
    db.commit()
 
    return {
        "paymentIntent": intent.client_secret,
        "customer": customer_id,
        "ephemeralKey": ephemeral_key.secret,
        "orderId": order_id
    }
 

@router.get("/status/{order_id}")
def get_payment_status(order_id: str, db: Session = Depends(get_db)):
    payment = db.query(Payment).filter(
        Payment.order_id == order_id
    ).first()

    if not payment:
        return {"error": "Order not found"}

    return {
        "order_id": payment.order_id,
        "status": payment.status,
        "amount": payment.amount
    }

@router.post("/refund/{order_id}")
def refund_payment(order_id: str, db: Session = Depends(get_db)):
    payment = db.query(Payment).filter(
        Payment.order_id == order_id
    ).first()

    if not payment or payment.status != "success":
        return {"error": "Refund not allowed"}

    stripe.Refund.create(
        payment_intent=payment.stripe_payment_intent
    )

    payment.status = "refunded"
    db.commit()

    return {"status": "refunded"}


# def get_or_create_customer(user_id: str):
#     # In real app, fetch from users table
#     customer = stripe.Customer.create(
#         metadata={"user_id": user_id}
#     )
#     return customer.id

def get_or_create_customer(user_id: str):
    customers = stripe.Customer.search(
        query=f"metadata['user_id']:'{user_id}'"
    )

    if customers.data:
        return customers.data[0].id

    customer = stripe.Customer.create(
        metadata={"user_id": user_id}
    )
    return customer.id


@router.post("/charge-saved-card/{order_id}")
def charge_saved_card(order_id: str, db: Session = Depends(get_db)):

    payment = db.query(Payment).filter(
        Payment.order_id == order_id
    ).first()

    if not payment or not payment.stripe_customer_id:
        return {"error": "No saved card found"}

    # 🔥 Get customer from Stripe
    customer = stripe.Customer.retrieve(payment.stripe_customer_id)

    if not customer.invoice_settings.default_payment_method:
        return {"error": "No default payment method found"}

    # 🔥 Use default payment method explicitly
    intent = stripe.PaymentIntent.create(
        amount=payment.amount,
        currency="inr",
        customer=payment.stripe_customer_id,
        payment_method=customer.invoice_settings.default_payment_method,
        off_session=True,
        confirm=True
    )

    payment.status = "charged_with_saved_card"
    db.commit()

    return {
        "status": "success",
        "payment_intent": intent.id
    }

@router.post("/save-card")
def save_card(request: SaveCardRequest):

    customer_id = get_or_create_customer(request.user_id)

    # Attach card to customer
    stripe.PaymentMethod.attach(
        request.payment_method_id,
        customer=customer_id
    )

    # Set as default
    stripe.Customer.modify(
        customer_id,
        invoice_settings={
            "default_payment_method": request.payment_method_id
        }
    )

    return {
        "status": "card_saved",
        "customer_id": customer_id
    }

