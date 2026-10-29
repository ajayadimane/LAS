"""Wallet balance, with the vendor mocked by respx."""

import json

import httpx

from tests.conftest import error_envelope, make_jwt

BALANCE = "/walletInfoRetrieval/walletTransferService/wallet/walletBalance"
BD_ID = "97315"
AUTH = {"Authorization": f"Bearer {make_jwt({'preferred_username': BD_ID})}"}

# Shape from the user's spec, 2026-10-27.
REAL_BALANCE = {"totalAmount": 0.0, "lockedAmount": 0.0, "availableAmount": 0.0}


def test_returns_the_signed_in_users_balance(client, vendor):
    route = vendor.post(BALANCE).mock(
        return_value=httpx.Response(
            200,
            json={
                "totalAmount": 1500.5,
                "lockedAmount": 500,
                "availableAmount": 1000.5,
            },
        )
    )

    response = client.get("/api/v1/wallet/balance", headers=AUTH)

    assert response.status_code == 200
    assert json.loads(route.calls.last.request.read()) == {
        "walletId": BD_ID,
        "tagId": "tag1",
        "programManagerId": "LAS",
    }
    assert response.json() == {
        "wallet_id": BD_ID,
        "tag_id": "tag1",
        "total_amount": 1500.5,
        "available_amount": 1000.5,
        "locked_amount": 500.0,
    }


def test_an_empty_wallet_is_zero(client, vendor):
    vendor.post(BALANCE).mock(return_value=httpx.Response(200, json=REAL_BALANCE))

    body = client.get(f"/api/v1/wallet/balance?wallet_id={BD_ID}", headers=AUTH).json()

    assert (body["total_amount"], body["available_amount"], body["locked_amount"]) == (
        0.0,
        0.0,
        0.0,
    )


def test_a_missing_amount_is_null_not_zero(client, vendor):
    vendor.post(BALANCE).mock(
        return_value=httpx.Response(200, json={"totalAmount": 10, "lockedAmount": "x"})
    )

    body = client.get("/api/v1/wallet/balance", headers=AUTH).json()

    assert body["total_amount"] == 10.0
    assert body["available_amount"] is None
    assert body["locked_amount"] is None


def test_someone_elses_wallet_is_forbidden_without_a_vendor_call(client, vendor):
    route = vendor.post(BALANCE).mock(
        return_value=httpx.Response(200, json=REAL_BALANCE)
    )

    response = client.get("/api/v1/wallet/balance?wallet_id=99999", headers=AUTH)

    assert response.status_code == 403
    assert not route.called


def test_a_token_is_required(client, vendor):
    route = vendor.post(BALANCE).mock(
        return_value=httpx.Response(200, json=REAL_BALANCE)
    )

    assert client.get("/api/v1/wallet/balance").status_code == 401
    assert not route.called


def test_an_unknown_wallet_is_a_404(client, vendor):
    vendor.post(BALANCE).mock(
        return_value=httpx.Response(404, json=error_envelope(404, "WALLET_NOT_FOUND"))
    )

    response = client.get("/api/v1/wallet/balance", headers=AUTH)

    assert response.status_code == 404
    assert "internal stack trace" not in response.text


def test_a_server_error_is_retried_because_the_read_is_safe(client, vendor):
    route = vendor.post(BALANCE).mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json=REAL_BALANCE)]
    )

    response = client.get("/api/v1/wallet/balance", headers=AUTH)

    assert response.status_code == 200
    assert route.call_count == 2
