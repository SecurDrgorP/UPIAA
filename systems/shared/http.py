"""
Systems Shared — Authenticated internal HTTP client.

All cross-container communication uses this module to attach
the INTERNAL_SECRET token to requests.
"""

import os
import httpx

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-internal")
INTERNAL_HEADERS = {"X-Internal-Token": INTERNAL_SECRET}


async def internal_post(url: str, json: dict = None) -> dict:
    """POST to an internal backend endpoint with auth."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=json, headers=INTERNAL_HEADERS)
        resp.raise_for_status()
        return resp.json()


async def internal_get(url: str, params: dict = None) -> dict:
    """GET from an internal backend endpoint with auth."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params, headers=INTERNAL_HEADERS)
        resp.raise_for_status()
        return resp.json()


async def internal_delete(url: str) -> dict:
    """DELETE on an internal backend endpoint with auth."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(url, headers=INTERNAL_HEADERS)
        resp.raise_for_status()
        return resp.json()
