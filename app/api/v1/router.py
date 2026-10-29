from fastapi import APIRouter

from app.api.v1 import bd, catalog, custom_fields, customer, identity, persons, wallet

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(identity.router)
api_router.include_router(persons.router)
api_router.include_router(bd.router)
api_router.include_router(wallet.router)
api_router.include_router(customer.router)
# custom_fields also owns person-scoped paths (/persons/{id}/custom-fields).
# Those never collide with the persons router: no two routes share a
# (method, path) shape. Registration order is not load-bearing, and
# test_custom_fields.py pins that both resolve.
api_router.include_router(custom_fields.router)
api_router.include_router(catalog.router)
