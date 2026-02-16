import stripe
from fastapi import APIRouter, Request, HTTPException
from app.config import STRIPE_WEBHOOK_SECRET
from app.database import SessionLocal
from app.models import Payment

router = APIRouter(prefix="/webhook", tags=["Webhook"])

@router.post("/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook")

    db = SessionLocal()

    intent = event["data"]["object"]
    order_id = intent["metadata"].get("order_id")

    payment = db.query(Payment).filter(
        Payment.order_id == order_id
    ).first()

    if not payment:
        return {"status": "ignored"}

    # ✅ PAYMENT SUCCESS
    if event["type"] == "payment_intent.succeeded":
        payment.status = "success"

        customer_id = intent.get("customer")
        payment_method = intent.get("payment_method")

        # 🔥 SET DEFAULT CARD (CRITICAL STEP)
        if customer_id and payment_method:
            stripe.Customer.modify(
                customer_id,
                invoice_settings={
                    "default_payment_method": payment_method
                }
            )

    # ❌ PAYMENT FAILED
    elif event["type"] == "payment_intent.payment_failed":
        payment.status = "failed"

    db.commit()
    return {"status": "ok"}
