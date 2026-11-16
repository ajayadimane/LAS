"""SQLAlchemy 2.0 models mirroring ``docs/schema.sql``.

The database (migrations, built from docs/schema.sql) is authoritative for CHECK constraints,
partial unique indexes, triggers and views. Models declare columns, types, nullability, keys and
server defaults; ``tests/integration/test_schema_parity.py`` checks they agree with the catalog.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TZ = TIMESTAMP(timezone=True)
NOW = text("now()")
Money = Numeric(12, 2)


class Base(DeclarativeBase):
    pass


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target)


# 1. BD master ---------------------------------------------------------------------------------


class BdPartner(Base):
    __tablename__ = "bd_partners"
    bd_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ibkart_bd_code: Mapped[str] = mapped_column(Text, unique=True)
    full_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    mobile: Mapped[str | None] = mapped_column(String(10))
    region: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'ACTIVE'"))
    daily_request_cap: Mapped[int] = mapped_column(Integer, server_default=text("50"))
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(TZ)
    created_at: Mapped[dt.datetime] = mapped_column(TZ, server_default=NOW)
    updated_at: Mapped[dt.datetime] = mapped_column(TZ, server_default=NOW)


class ServiceCatalog(Base):
    __tablename__ = "service_catalog"
    service_code: Mapped[str] = mapped_column(Text, primary_key=True)
    service_group: Mapped[int] = mapped_column(SmallInteger)
    display_name: Mapped[str] = mapped_column(Text)
    menu_order: Mapped[int] = mapped_column(SmallInteger)
    requires_re_referral: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    re_partner_code: Mapped[str | None] = mapped_column(Text)
    requires_input_file: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    sla_business_hours: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class InboundEmail(Base):
    __tablename__ = "inbound_emails"
    inbound_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[str] = mapped_column(Text, unique=True)
    provider_uid: Mapped[str | None] = mapped_column(Text)
    in_reply_to: Mapped[str | None] = mapped_column(Text)
    references_hdr: Mapped[str | None] = mapped_column(Text)
    from_address: Mapped[str] = mapped_column(CITEXT)
    from_display: Mapped[str | None] = mapped_column(Text)
    to_addresses: Mapped[list[str]] = mapped_column(ARRAY(CITEXT))
    cc_addresses: Mapped[list[str] | None] = mapped_column(ARRAY(CITEXT))
    subject: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[dt.datetime] = mapped_column(TZ)
    raw_eml_uri: Mapped[str] = mapped_column(Text)
    raw_sha256: Mapped[str] = mapped_column(CHAR(64))
    spf_result: Mapped[str | None] = mapped_column(Text)
    dkim_result: Mapped[str | None] = mapped_column(Text)
    dmarc_result: Mapped[str | None] = mapped_column(Text)
    is_auto_generated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    bd_id: Mapped[int | None] = mapped_column(BigInteger, _fk("bd_partners.bd_id"))
    sender_verdict: Mapped[str | None] = mapped_column(Text)
    primary_intent: Mapped[str | None] = mapped_column(Text)
    detected_language: Mapped[str | None] = mapped_column(Text)
    parse_method: Mapped[str | None] = mapped_column(Text)
    parse_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    parsed_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    processing_status: Mapped[str] = mapped_column(Text, server_default=text("'RECEIVED'"))
    error_detail: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[dt.datetime | None] = mapped_column(TZ)
    created_at: Mapped[dt.datetime] = mapped_column(TZ, server_default=NOW)


class EmailAttachment(Base):
    __tablename__ = "email_attachments"
    attachment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inbound_id: Mapped[int] = mapped_column(BigInteger, _fk("inbound_emails.inbound_id"))
    original_filename: Mapped[str] = mapped_column(Text)
    declared_mime: Mapped[str | None] = mapped_column(Text)
    detected_mime: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    storage_uri: Mapped[str] = mapped_column(Text)
    av_status: Mapped[str] = mapped_column(Text, server_default=text("'PENDING'"))
    is_password_protected: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    page_count: Mapped[int | None] = mapped_column(Integer)
    doc_class: Mapped[str | None] = mapped_column(Text)
    doc_class_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[dt.datetime] = mapped_column(TZ, server_default=NOW)


class MailAction(Base):
    __tablename__ = "mail_actions"
    action_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inbound_id: Mapped[int] = mapped_column(BigInteger, _fk("inbound_emails.inbound_id"))
    seq: Mapped[int] = mapped_column(SmallInteger)
    intent: Mapped[str] = mapped_column(Text)
    entities: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    grounded: Mapped[bool] = mapped_column(Boolean)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    risk_tier: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'PENDING'"))
    result_detail: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[dt.datetime | None] = mapped_column(TZ)
    created_at: Mapped[dt.datetime] = mapped_column(TZ, server_default=NOW)


class ServiceSynonym(Base):
    __tablename__ = "service_synonyms"
    service_code: Mapped[str] = mapped_column(
        Text, _fk("service_catalog.service_code"), primary_key=True
    )
    phrase: Mapped[str] = mapped_column(Text, primary_key=True)
    language: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("'en'"))


class AiModelPricing(Base):
    __tablename__ = "ai_model_pricing"
    pricing_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'USD'"))
    input_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    output_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cache_write_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cache_read_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    per_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    usage_unit: Mapped[str] = mapped_column(Text, server_default=text("'TOKEN'"))
    batch_discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default=text("0"))
    valid_from: Mapped[dt.date] = mapped_column(Date)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)


class AiUsage(Base):
    __tablename__ = "ai_usage"
    usage_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(TZ, server_default=NOW)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    pricing_id: Mapped[int | None] = mapped_column(BigInteger, _fk("ai_model_pricing.pricing_id"))
    purpose: Mapped[str] = mapped_column(Text)
    prompt_template: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    prompt_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    provider_request_id: Mapped[str | None] = mapped_column(Text)
    attempt_no: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    is_batch_api: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    input_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cache_read_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    units: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'USD'"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    bd_id: Mapped[int | None] = mapped_column(BigInteger, _fk("bd_partners.bd_id"))
    inbound_id: Mapped[int | None] = mapped_column(BigInteger, _fk("inbound_emails.inbound_id"))
    attachment_id: Mapped[int | None] = mapped_column(
        BigInteger, _fk("email_attachments.attachment_id")
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    audit_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(TZ, server_default=NOW)
    actor_type: Mapped[str] = mapped_column(Text)
    actor_ref: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(Text)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    prev_hash: Mapped[str | None] = mapped_column(CHAR(64))
    row_hash: Mapped[str] = mapped_column(CHAR(64))


class MailboxCheckpoint(Base):
    __tablename__ = "mailbox_checkpoints"
    mailbox: Mapped[str] = mapped_column(CITEXT, primary_key=True)
    folder: Mapped[str] = mapped_column(Text, primary_key=True)
    uidvalidity: Mapped[int] = mapped_column(BigInteger)
    last_uid: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    updated_at: Mapped[dt.datetime] = mapped_column(TZ, server_default=NOW)


class CoreIntakeHandoff(Base):
    """Core handoff mode: one processed mail awaiting (or past) delivery to Core LAS."""

    __tablename__ = "core_intake_handoffs"
    handoff_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inbound_id: Mapped[int] = mapped_column(
        BigInteger, _fk("inbound_emails.inbound_id"), unique=True
    )
    status: Mapped[str] = mapped_column(Text, server_default=text("'PENDING'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_attempt_at: Mapped[dt.datetime] = mapped_column(TZ, server_default=NOW)
    lease_until: Mapped[dt.datetime | None] = mapped_column(TZ)
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    core_intake_event_id: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[dt.datetime | None] = mapped_column(TZ)
    created_at: Mapped[dt.datetime] = mapped_column(TZ, server_default=NOW)
    updated_at: Mapped[dt.datetime] = mapped_column(TZ, server_default=NOW)
