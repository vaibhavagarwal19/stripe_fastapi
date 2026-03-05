import hashlib
import hmac

import stripe
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from app.config import RAZORPAY_KEY_SECRET, STRIPE_WEBHOOK_SECRET
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

    payment = db.query(Payment).filter(Payment.order_id == order_id).first()

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


class RazorpayVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/razorpay/verify")
def verify_razorpay(request: RazorpayVerifyRequest):
    if not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=500, detail="Razorpay secret is not configured")

    body = f"{request.razorpay_order_id}|{request.razorpay_payment_id}"
    expected_signature = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        body.encode(),
        hashlib.sha256,
    ).hexdigest()

    db = SessionLocal()
    payment = db.query(Payment).filter(Payment.provider_order_id == request.razorpay_order_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Order not found")

    if hmac.compare_digest(expected_signature, request.razorpay_signature):
        payment.status = "success"
        payment.provider_payment_id = request.razorpay_payment_id
        db.commit()
        return {"status": "success"}

    payment.status = "failed"
    db.commit()
    raise HTTPException(status_code=400, detail="Invalid signature")


@router.post("/paypal")
async def paypal_webhook(request: Request):
    # For production, validate webhook signatures with PayPal transmission headers.
    payload = await request.json()

    event_type = payload.get("event_type")
    resource = payload.get("resource", {})

    order_id = resource.get("custom_id") or resource.get("invoice_id")
    capture_id = resource.get("id")

    db = SessionLocal()
    payment = None

    if order_id:
        payment = db.query(Payment).filter(Payment.order_id == order_id).first()
    if not payment and resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id"):
        provider_order_id = resource["supplementary_data"]["related_ids"]["order_id"]
        payment = db.query(Payment).filter(Payment.provider_order_id == provider_order_id).first()

    if not payment:
        return {"status": "ignored"}

    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        payment.status = "success"
        payment.provider_payment_id = capture_id
    elif event_type in {"PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.DECLINED"}:
        payment.status = "failed"
    elif event_type == "PAYMENT.CAPTURE.REFUNDED":
        payment.status = "refunded"
        payment.provider_refund_id = resource.get("id")

    payment.raw_response = str(payload)
    db.commit()
    return {"status": "ok"}
