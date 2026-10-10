"""Real texting through Twilio.

Inbound: Twilio POSTs each text to /webhooks/twilio/sms. The signature is
checked, an empty TwiML response goes back right away, and the text is
handled in the background: the AI can take longer than Twilio's 15s
webhook timeout, so replies go out through the REST API instead of TwiML.
Twilio retries are deduped on MessageSid by the router.

Outbound: `transport_from_settings` returns the function messaging.send_text
delivers through. Delivery receipts come back to /webhooks/twilio/status.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response
from sqlalchemy import select

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import Message
from ..router import handle_inbound

log = logging.getLogger(__name__)
router = APIRouter()

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
STATUS_MAP = {"sent": "sent", "delivered": "delivered", "undelivered": "failed", "failed": "failed"}
SMS_PATH, STATUS_PATH = "/webhooks/twilio/sms", "/webhooks/twilio/status"


def twilio_configured(settings: Settings) -> bool:
    return bool(settings.twilio_account_sid and settings.twilio_auth_token
                and (settings.twilio_from_number or settings.twilio_messaging_service_sid))


async def _verified_form(request: Request) -> dict[str, str] | None:
    """The POSTed form, or None when it didn't come from Twilio.

    Railway terminates HTTPS in front of the app, so the URL Twilio signed
    is rebuilt from PUBLIC_BASE_URL rather than taken from the request.
    """
    settings = get_settings()
    if not settings.twilio_auth_token:
        return None
    from twilio.request_validator import RequestValidator

    form = {key: str(value) for key, value in (await request.form()).items()}
    url = settings.public_base_url.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    signature = request.headers.get("X-Twilio-Signature", "")
    return form if RequestValidator(settings.twilio_auth_token).validate(url, form, signature) else None


def process_inbound(phone: str, body: str, sid: str | None) -> None:
    try:
        with session_scope() as session:
            handle_inbound(session, phone, body, provider_sid=sid)
    except Exception:  # never let one bad text take the worker down; Twilio won't retry after our 200
        log.exception("inbound text %s failed", sid)


@router.post(SMS_PATH)
async def inbound_sms(request: Request, background: BackgroundTasks) -> Response:
    form = await _verified_form(request)
    if form is None:
        return Response("invalid signature", status_code=403)
    if form.get("From"):
        background.add_task(process_inbound, form["From"], form.get("Body", ""), form.get("MessageSid"))
    return Response(EMPTY_TWIML, media_type="application/xml")


@router.post(STATUS_PATH)
async def delivery_status(request: Request) -> Response:
    form = await _verified_form(request)
    if form is None:
        return Response("invalid signature", status_code=403)
    status = STATUS_MAP.get(form.get("MessageStatus", ""))
    if status and form.get("MessageSid"):
        with session_scope() as session:
            msg = session.scalar(select(Message).where(Message.provider_sid == form["MessageSid"]))
            if msg is not None and msg.status != "delivered":
                msg.status = status
            if status == "failed":
                log.warning("text %s not delivered (error %s)", form["MessageSid"], form.get("ErrorCode"))
    return Response(status_code=204)


def transport_from_settings(settings: Settings):
    """A messaging transport that sends through Twilio, or None when Twilio isn't configured."""
    if not twilio_configured(settings):
        return None
    from twilio.rest import Client

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    sender = ({"messaging_service_sid": settings.twilio_messaging_service_sid}
              if settings.twilio_messaging_service_sid else {"from_": settings.twilio_from_number})
    status_callback = settings.public_base_url.rstrip("/") + STATUS_PATH

    def send(phone: str, body: str) -> str:
        return client.messages.create(to=phone, body=body, status_callback=status_callback, **sender).sid

    return send
