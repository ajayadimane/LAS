"""Emailing a new customer's login details."""

import json
import smtplib

import httpx
import pytest
from pydantic import SecretStr

from app.services.credentials_mail import credentials_mail
from app.services.mailer import Mail, Mailer, MailSendError
from tests.conftest import ack, error_envelope
from tests.test_bd import AUTH, BY_PERSON_ID, REAL_PERSON
from tests.test_bd_onboarding import ADD_USER, ADMIN_TOKEN, REGISTER, SUPERADMIN, WALLET
from tests.test_bd_onboarding import FORM as NEW_CUSTOMER


class FakeMailer:
    configured = True

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[Mail] = []
        self.fail = fail

    async def send(self, mail: Mail) -> None:
        if self.fail:
            raise MailSendError("SMTPServerDisconnected")
        self.sent.append(mail)


@pytest.fixture(autouse=True)
def admin_route(vendor):
    return vendor.post(SUPERADMIN).mock(
        return_value=httpx.Response(200, json=ack(accessToken=ADMIN_TOKEN))
    )


@pytest.fixture
def login_route(vendor):
    return vendor.post(ADD_USER).mock(return_value=httpx.Response(201, json=ack()))


@pytest.fixture
def mailer(client):
    fake = FakeMailer()
    client.app.state.mailer = fake
    return fake


@pytest.fixture
def onboarding(vendor, admin_route):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json=ack()))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json=ack()))
    return vendor.post(ADD_USER).mock(return_value=httpx.Response(201, json=ack()))


def test_registration_emails_the_login_details(client, mailer, onboarding):
    response = client.post("/api/v1/bd/customers", json=NEW_CUSTOMER, headers=AUTH)

    body = response.json()
    assert body["credentials_emailed"] is True
    assert body["credentials_email_error"] is None
    (mail,) = mailer.sent
    sent_password = json.loads(onboarding.calls.last.request.read())["password"]
    assert mail.to == NEW_CUSTOMER["email_address"]
    assert NEW_CUSTOMER["mobile_number"] in mail.text
    assert sent_password in mail.text and sent_password in mail.html
    assert "change your password" in mail.text


def test_a_failed_email_still_creates_the_customer(client, onboarding):
    client.app.state.mailer = FakeMailer(fail=True)

    response = client.post("/api/v1/bd/customers", json=NEW_CUSTOMER, headers=AUTH)

    assert response.status_code == 201
    body = response.json()
    assert body["login_created"] is True
    assert body["temporary_password"]  # the BD can still pass it on
    assert body["credentials_emailed"] is False
    assert "could not be emailed" in body["credentials_email_error"]


def test_without_smtp_settings_nothing_is_sent(client, onboarding):
    response = client.post("/api/v1/bd/customers", json=NEW_CUSTOMER, headers=AUTH)
    body = response.json()
    assert body["credentials_emailed"] is False
    assert "not set up" in body["credentials_email_error"]


def test_no_email_when_the_login_was_not_created(client, mailer, vendor, admin_route):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json=ack()))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json=ack()))
    vendor.post(ADD_USER).mock(return_value=httpx.Response(500))

    body = client.post("/api/v1/bd/customers", json=NEW_CUSTOMER, headers=AUTH).json()

    assert body["login_created"] is False
    assert body["credentials_emailed"] is False
    assert mailer.sent == []


def test_the_login_retry_emails_the_new_details(client, mailer, vendor, login_route):
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=REAL_PERSON))

    body = client.post("/api/v1/bd/customers/9000000013/login", headers=AUTH).json()

    assert body["credentials_emailed"] is True
    (mail,) = mailer.sent
    assert mail.to == "email@nomail.com"
    assert body["temporary_password"] in mail.text


def test_the_html_escapes_what_the_bd_typed():
    mail = credentials_mail(
        to="a@b.co",
        first_name="<script>x</script>",
        user_id="9000000013",
        password="p&ss<w>rd",
        sign_in_url="https://las.example/signin",
    )
    assert "<script>" not in mail.html
    assert "p&amp;ss&lt;w&gt;rd" in mail.html


async def test_the_mailer_reports_an_smtp_failure(settings, monkeypatch):
    def boom(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"Authentication Failed")

    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)
    mailer = Mailer(settings.model_copy(update={"smtp_password": SecretStr("x")}))
    mail = Mail(to="a@b.co", subject="s", text="t", html="<p>t</p>")
    with pytest.raises(MailSendError):
        await mailer.send(mail)


# The real body, 2026-10-29: Keycloak's 409 wrapped in the IAM's 400.
DUPLICATE_EMAIL = 'Error: 409 CONFLICT {"errorMessage":"User exists with same email"}'


def test_a_login_email_already_in_use_is_explained(client, mailer, vendor):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json=ack()))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json=ack()))
    vendor.post(ADD_USER).mock(
        return_value=httpx.Response(
            400, json=error_envelope(status=400, message=DUPLICATE_EMAIL)
        )
    )

    body = client.post("/api/v1/bd/customers", json=NEW_CUSTOMER, headers=AUTH).json()

    assert body["login_created"] is False
    error = body["login_error"]
    assert error["code"] == "EMAIL_ALREADY_IN_USE"
    assert "already used" in error["message"]
    assert "CONFLICT" not in json.dumps(body)  # the raw upstream text stays in logs
    assert mailer.sent == []
