import uuid

import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Literal

from app.config import (
    PAYPAL_CLIENT_ID,
    PAYPAL_CLIENT_SECRET,
    PAYPAL_MODE,
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
)
from app.database import SessionLocal
from app.models import Payment

try:
    import razorpay
except Exception:  # pragma: no cover - optional dependency at runtime
    razorpay = None

try:
    from paypalcheckoutsdk.core import LiveEnvironment, PayPalHttpClient, SandboxEnvironment
    from paypalcheckoutsdk.orders import OrdersCaptureRequest, OrdersCreateRequest
    from paypalcheckoutsdk.payments import CapturesRefundRequest
    from paypalhttp import HttpError as PayPalHttpError
except Exception:  # pragma: no cover - optional dependency at runtime
    PayPalHttpClient = None
    SandboxEnvironment = None
    LiveEnvironment = None
    OrdersCreateRequest = None
    OrdersCaptureRequest = None
    CapturesRefundRequest = None
    PayPalHttpError = Exception


class PaymentRequest(BaseModel):
    amount: int
    provider: Literal["stripe", "razorpay", "paypal"]
    currency: str = "INR"


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


def get_or_create_customer(user_id: str):
    customers = stripe.Customer.search(query=f"metadata['user_id']:'{user_id}'")
    if customers.data:
        return customers.data[0].id

    customer = stripe.Customer.create(metadata={"user_id": user_id})
    return customer.id


def get_razorpay_client():
    if razorpay is None:
        raise HTTPException(status_code=500, detail="razorpay SDK is not installed")
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=500, detail="Razorpay credentials are not configured")
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


def get_paypal_client():
    if PayPalHttpClient is None:
        raise HTTPException(status_code=500, detail="paypalcheckoutsdk is not installed")
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="PayPal credentials are not configured")

    environment = (
        SandboxEnvironment(client_id=PAYPAL_CLIENT_ID, client_secret=PAYPAL_CLIENT_SECRET)
        if PAYPAL_MODE == "sandbox"
        else LiveEnvironment(client_id=PAYPAL_CLIENT_ID, client_secret=PAYPAL_CLIENT_SECRET)
    )
    return PayPalHttpClient(environment)


def minor_to_major(amount: int) -> str:
    return f"{amount / 100:.2f}"


@router.post("/create")
def create_payment(request: PaymentRequest, db: Session = Depends(get_db)):
    amount = request.amount
    currency = request.currency.upper()
    provider = request.provider.lower()
    order_id = str(uuid.uuid4())

    if provider == "stripe":
        user_id = "user_123"
        customer_id = get_or_create_customer(user_id)

        ephemeral_key = stripe.EphemeralKey.create(
            customer=customer_id,
            stripe_version="2023-10-16",
        )

        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency=currency.lower(),
            customer=customer_id,
            automatic_payment_methods={"enabled": True},
            metadata={"order_id": order_id},
        )

        payment = Payment(
            order_id=order_id,
            amount=amount,
            currency=currency.lower(),
            status="initiated",
            provider="stripe",
            provider_order_id=intent.id,
            stripe_payment_intent=intent.id,
            stripe_customer_id=customer_id,
            raw_response=str(intent),
        )
        db.add(payment)
        db.commit()

        return {
            "provider": "stripe",
            "paymentIntent": intent.client_secret,
            "customer": customer_id,
            "ephemeralKey": ephemeral_key.secret,
            "orderId": order_id,
        }

    if provider == "razorpay":
        client = get_razorpay_client()
        rp_order = client.order.create(
            {
                "amount": amount,
                "currency": currency,
                "receipt": order_id,
                "notes": {"order_id": order_id},
            }
        )

        payment = Payment(
            order_id=order_id,
            amount=amount,
            currency=currency.lower(),
            status="initiated",
            provider="razorpay",
            provider_order_id=rp_order["id"],
            raw_response=str(rp_order),
        )
        db.add(payment)
        db.commit()

        return {
            "provider": "razorpay",
            "orderId": order_id,
            "razorpay_order_id": rp_order["id"],
            "amount": amount,
            "currency": currency,
            "key_id": RAZORPAY_KEY_ID,
        }

    if provider == "paypal":
        client = get_paypal_client()

        order_request = OrdersCreateRequest()
        order_request.prefer("return=representation")
        order_request.request_body(
            {
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "custom_id": order_id,
                        "reference_id": order_id,
                        "amount": {
                            "currency_code": currency,
                            "value": minor_to_major(amount),
                        },
                    }
                ],
            }
        )

        try:
            response = client.execute(order_request)
        except PayPalHttpError as exc:
            raise HTTPException(status_code=400, detail=f"PayPal order create failed: {exc}") from exc
        paypal_order_id = response.result.id

        approve_url = None
        for link in response.result.links:
            if link.rel == "approve":
                approve_url = link.href
                break

        payment = Payment(
            order_id=order_id,
            amount=amount,
            currency=currency.lower(),
            status="initiated",
            provider="paypal",
            provider_order_id=paypal_order_id,
            raw_response=str(response.result),
        )
        db.add(payment)
        db.commit()

        return {
            "provider": "paypal",
            "orderId": order_id,
            "paypal_order_id": paypal_order_id,
            "approval_url": approve_url,
        }

    raise HTTPException(status_code=400, detail="Unsupported provider")


@router.post("/paypal/capture/{order_id}")
def capture_paypal_order(order_id: str, db: Session = Depends(get_db)):
    payment = db.query(Payment).filter(Payment.order_id == order_id).first()
    if not payment or payment.provider != "paypal" or not payment.provider_order_id:
        raise HTTPException(status_code=404, detail="PayPal order not found")

    client = get_paypal_client()
    capture_request = OrdersCaptureRequest(payment.provider_order_id)
    capture_request.prefer("return=representation")
    try:
        capture_response = client.execute(capture_request)
    except PayPalHttpError as exc:
        raise HTTPException(status_code=400, detail=f"PayPal capture failed: {exc}") from exc

    capture_id = None
    status = getattr(capture_response.result, "status", None)
    if getattr(capture_response.result, "purchase_units", None):
        purchase_unit = capture_response.result.purchase_units[0]
        captures = purchase_unit.payments.captures if getattr(purchase_unit, "payments", None) else []
        if captures:
            capture_id = captures[0].id
            status = captures[0].status

    payment.provider_payment_id = capture_id
    payment.raw_response = str(capture_response.result)
    payment.status = "success" if status == "COMPLETED" else "failed"
    db.commit()

    return {
        "provider": "paypal",
        "order_id": order_id,
        "capture_id": capture_id,
        "status": payment.status,
    }


@router.get("/status/{order_id}")
def get_payment_status(order_id: str, db: Session = Depends(get_db)):
    payment = db.query(Payment).filter(Payment.order_id == order_id).first()

    if not payment:
        return {"error": "Order not found"}

    return {
        "order_id": payment.order_id,
        "provider": payment.provider,
        "status": payment.status,
        "amount": payment.amount,
    }


@router.post("/refund/{order_id}")
def refund_payment(order_id: str, db: Session = Depends(get_db)):
    payment = db.query(Payment).filter(Payment.order_id == order_id).first()
    if not payment or payment.status != "success":
        return {"error": "Refund not allowed"}

    if payment.provider == "stripe":
        refund = stripe.Refund.create(payment_intent=payment.stripe_payment_intent)
        payment.provider_refund_id = refund.id

    elif payment.provider == "razorpay":
        if not payment.provider_payment_id:
            return {"error": "Missing Razorpay payment id"}
        client = get_razorpay_client()
        refund = client.payment.refund(payment.provider_payment_id, {"amount": payment.amount})
        payment.provider_refund_id = refund.get("id")

    elif payment.provider == "paypal":
        if not payment.provider_payment_id:
            return {"error": "Missing PayPal capture id"}
        client = get_paypal_client()
        refund_request = CapturesRefundRequest(payment.provider_payment_id)
        refund_request.request_body(
            {
                "amount": {
                    "value": minor_to_major(payment.amount),
                    "currency_code": payment.currency.upper(),
                }
            }
        )
        try:
            refund_response = client.execute(refund_request)
        except PayPalHttpError as exc:
            raise HTTPException(status_code=400, detail=f"PayPal refund failed: {exc}") from exc
        payment.provider_refund_id = refund_response.result.id
    else:
        return {"error": "Unsupported provider"}

    payment.status = "refunded"
    db.commit()

    return {"status": "refunded", "provider_refund_id": payment.provider_refund_id}


@router.post("/charge-saved-card/{order_id}")
def charge_saved_card(order_id: str, db: Session = Depends(get_db)):
    payment = db.query(Payment).filter(Payment.order_id == order_id).first()
    if not payment or payment.provider != "stripe" or not payment.stripe_customer_id:
        return {"error": "Stripe saved card is not available for this order"}

    customer = stripe.Customer.retrieve(payment.stripe_customer_id)
    if not customer.invoice_settings.default_payment_method:
        return {"error": "No default payment method found"}

    intent = stripe.PaymentIntent.create(
        amount=payment.amount,
        currency=payment.currency,
        customer=payment.stripe_customer_id,
        payment_method=customer.invoice_settings.default_payment_method,
        off_session=True,
        confirm=True,
    )

    payment.status = "charged_with_saved_card"
    payment.stripe_payment_intent = intent.id
    db.commit()

    return {"status": "success", "payment_intent": intent.id}


@router.post("/save-card")
def save_card(request: SaveCardRequest):
    customer_id = get_or_create_customer(request.user_id)

    stripe.PaymentMethod.attach(request.payment_method_id, customer=customer_id)

    stripe.Customer.modify(
        customer_id,
        invoice_settings={"default_payment_method": request.payment_method_id},
    )

    return {"status": "card_saved", "customer_id": customer_id}

