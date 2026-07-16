"""
Encrypted credential bundles.

Each credential stores a provider name plus an encrypted JSON object of fields.
Nodes can include credential_id in config; the orchestrator injects those fields
just before handler execution.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from core.database import AsyncSessionLocal
from core.db_models import CredentialModel
from core.secret_vault import secret_vault


class CredentialStore:
    @staticmethod
    def _public(model: CredentialModel) -> dict[str, Any]:
        return {
            "id": model.id,
            "name": model.name,
            "provider": model.provider,
            "created_at": model.created_at.isoformat() if model.created_at else None,
            "updated_at": model.updated_at.isoformat() if model.updated_at else None,
        }

    async def upsert(
        self,
        *,
        name: str,
        provider: str,
        fields: dict[str, Any],
        credential_id: str | None = None,
    ) -> dict[str, Any]:
        cred_id = credential_id or f"cred_{uuid.uuid4().hex[:12]}"
        cipher_text = secret_vault.encrypt(json.dumps(fields))

        async with AsyncSessionLocal() as session:
            existing = await session.get(CredentialModel, cred_id)
            if existing:
                existing.name = name
                existing.provider = provider
                existing.cipher_text = cipher_text
                existing.updated_at = datetime.now(timezone.utc)
                model = existing
            else:
                model = CredentialModel(
                    id=cred_id,
                    name=name,
                    provider=provider,
                    cipher_text=cipher_text,
                )
                session.add(model)
            await session.commit()
            return self._public(model)

    async def list(self) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(CredentialModel).order_by(CredentialModel.updated_at.desc()))
            return [self._public(model) for model in result.scalars().all()]

    async def get_fields(self, credential_id: str) -> dict[str, Any] | None:
        async with AsyncSessionLocal() as session:
            model = await session.get(CredentialModel, credential_id)
            if not model:
                return None
            fields = json.loads(secret_vault.decrypt(model.cipher_text))
            return fields if isinstance(fields, dict) else {}

    async def delete(self, credential_id: str) -> bool:
        async with AsyncSessionLocal() as session:
            model = await session.get(CredentialModel, credential_id)
            if not model:
                return False
            await session.delete(model)
            await session.commit()
            return True

    async def inject(self, config: dict[str, Any]) -> dict[str, Any]:
        credential_id = config.get("credential_id")
        if not credential_id:
            return config

        credential_id = str(credential_id).strip()
        if not credential_id:
            return config

        # Guard: credential IDs should start with 'cred_'.  If the value looks
        # like a bot token or API key (contains ':' or is very long without the
        # prefix) the user probably pasted the secret into the wrong field.
        if not credential_id.startswith("cred_"):
            raise ValueError(
                f"credential_id '{credential_id[:20]}...' does not look like a "
                f"valid credential bundle ID (expected format: cred_xxxx). "
                f"Did you paste a bot token or API key into the Credential ID "
                f"field by mistake? Paste it into the Bot Token / API Key field "
                f"instead, or create a credential bundle first."
            )

        fields = await self.get_fields(credential_id)
        if fields is None:
            raise ValueError(f"credential_id not found: {credential_id}")

        merged = dict(fields)
        for key, value in config.items():
            if key == "credential_id":
                merged[key] = value
                continue
            if value not in (None, "", [], {}):
                merged[key] = value
        return merged


credential_store = CredentialStore()
