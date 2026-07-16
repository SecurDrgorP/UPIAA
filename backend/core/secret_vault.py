"""
Secret Vault — Fernet + PBKDF2HMAC encryption for storing secrets in PostgreSQL.

Resolution order:
  1. Environment variable UPIAA_SECRET_<KEY_NAME>
  2. PostgreSQL secrets table (Fernet-encrypted)
  3. Redis (legacy fallback)
"""

import base64
import logging
import os
import re
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# ── Derive Fernet key from vault passphrase ──────────────────────────────────
_VAULT_KEY = os.getenv("SECRET_VAULT_KEY", "change-me-in-production")
_SALT = b"UPIAA-secret-vault"  # Static salt (key is already unique per deployment)

_DEFAULT_VAULT_KEY = "change-me-in-production"
if _VAULT_KEY == _DEFAULT_VAULT_KEY:
    logger.warning(
        "⚠️  SECRET_VAULT_KEY is set to the default insecure value. "
        "All secrets stored in the vault are only weakly protected. "
        "Set a strong, unique SECRET_VAULT_KEY environment variable before deploying to production."
    )

_kdf = PBKDF2HMAC(
    algorithm=hashes.SHA256(),
    length=32,
    salt=_SALT,
    iterations=480_000,
)
_fernet = Fernet(base64.urlsafe_b64encode(_kdf.derive(_VAULT_KEY.encode())))


class SecretVault:
    SECRET_PATTERN = re.compile(r"^\{\{\s*\$secret\.([a-zA-Z0-9_]+)\s*\}\}$")

    @classmethod
    def encrypt(cls, plaintext: str) -> str:
        """Encrypt a plaintext value for storage."""
        return _fernet.encrypt(plaintext.encode()).decode()

    @classmethod
    def decrypt(cls, cipher_text: str) -> str:
        """Decrypt a stored cipher text."""
        return _fernet.decrypt(cipher_text.encode()).decode()

    @classmethod
    async def resolve(cls, ref: str, user_id: Optional[str] = None) -> Optional[str]:
        """
        Resolve a secret reference {{$secret.<key_name>}}.

        Resolution order:
          1. Environment variable UPIAA_SECRET_<KEY_NAME>
          2. PostgreSQL secrets table
          3. Redis (legacy)
        """
        match = cls.SECRET_PATTERN.match(ref)
        if not match:
            return None

        key_name = match.group(1)

        # 1. Environment variable
        env_key = f"UPIAA_SECRET_{key_name.upper()}"
        env_val = os.getenv(env_key)
        if env_val is not None:
            return env_val

        # 2. PostgreSQL secrets table
        try:
            from core.database import AsyncSessionLocal
            from core.db_models import SecretModel
            from sqlalchemy import select

            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(SecretModel).where(SecretModel.key_name == key_name)
                )
                secret = result.scalar_one_or_none()
                if secret:
                    return cls.decrypt(secret.cipher_text)
        except Exception as e:
            logger.error(f"Error reading secret from database: {e}")

        # 3. Redis (legacy fallback)
        try:
            import redis.asyncio as redis_pkg
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            redis_client = redis_pkg.from_url(redis_url)
            if user_id:
                redis_key = f"secrets:{user_id}:{key_name}"
            else:
                redis_key = f"secrets:global:{key_name}"
            redis_val = await redis_client.get(redis_key)
            await redis_client.close()
            if redis_val:
                return redis_val.decode("utf-8") if isinstance(redis_val, bytes) else redis_val
        except Exception as e:
            logger.debug(f"Redis secret lookup failed: {e}")

        logger.error(f"Could not resolve secret reference: {ref}")
        return None

    @classmethod
    async def resolve_config(
        cls, config_dict: Dict[str, Any], user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Recursively resolve all {{$secret.<key>}} references in a config dict."""
        resolved_config = {}
        for k, v in config_dict.items():
            if isinstance(v, str) and cls.SECRET_PATTERN.match(v):
                resolved_value = await cls.resolve(v, user_id)
                resolved_config[k] = resolved_value if resolved_value is not None else v
            elif isinstance(v, dict):
                resolved_config[k] = await cls.resolve_config(v, user_id)
            elif isinstance(v, list):
                resolved_list = []
                for item in v:
                    if isinstance(item, str) and cls.SECRET_PATTERN.match(item):
                        res = await cls.resolve(item, user_id)
                        resolved_list.append(res if res is not None else item)
                    elif isinstance(item, dict):
                        resolved_list.append(await cls.resolve_config(item, user_id))
                    else:
                        resolved_list.append(item)
                resolved_config[k] = resolved_list
            else:
                resolved_config[k] = v
        return resolved_config

    @classmethod
    async def store(cls, key_name: str, plaintext: str) -> None:
        """Store an encrypted secret in PostgreSQL."""
        from core.database import AsyncSessionLocal
        from core.db_models import SecretModel
        from sqlalchemy import select
        from datetime import datetime, timezone

        cipher_text = cls.encrypt(plaintext)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SecretModel).where(SecretModel.key_name == key_name)
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.cipher_text = cipher_text
                existing.updated_at = datetime.now(timezone.utc)
            else:
                session.add(SecretModel(key_name=key_name, cipher_text=cipher_text))

            await session.commit()

    @classmethod
    async def delete(cls, key_name: str) -> bool:
        """Delete a secret from PostgreSQL."""
        from core.database import AsyncSessionLocal
        from core.db_models import SecretModel
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SecretModel).where(SecretModel.key_name == key_name)
            )
            existing = result.scalar_one_or_none()
            if existing:
                await session.delete(existing)
                await session.commit()
                return True
            return False

    @classmethod
    async def list_keys(cls) -> list[str]:
        """List all stored secret key names."""
        from core.database import AsyncSessionLocal
        from core.db_models import SecretModel
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(SecretModel.key_name))
            return [row[0] for row in result.all()]


# Singleton
secret_vault = SecretVault()
