"""
Secrets Routes — CRUD for encrypted secrets.
"""

from fastapi import APIRouter, HTTPException
from core.schemas import CredentialCreate, CredentialResponse, SecretCreate, SecretResponse
from core.credential_store import credential_store
from core.secret_vault import secret_vault

router = APIRouter(prefix="/secrets", tags=["secrets"])


@router.post("/", response_model=SecretResponse)
async def create_secret(secret: SecretCreate):
    """Store a new encrypted secret."""
    await secret_vault.store(secret.key_name, secret.value)
    return SecretResponse(key_name=secret.key_name)


@router.get("/")
async def list_secrets():
    """List all secret key names (values are never exposed)."""
    keys = await secret_vault.list_keys()
    return [{"key_name": k} for k in keys]


@router.delete("/{key_name}")
async def delete_secret(key_name: str):
    """Delete a secret."""
    deleted = await secret_vault.delete(key_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Secret not found")
    return {"message": f"Secret '{key_name}' deleted"}


@router.post("/credentials", response_model=CredentialResponse)
async def create_credential(credential: CredentialCreate):
    """Store an encrypted credential bundle."""
    created = await credential_store.upsert(
        credential_id=credential.id,
        name=credential.name,
        provider=credential.provider,
        fields=credential.fields,
    )
    return CredentialResponse(**created)


@router.get("/credentials")
async def list_credentials():
    """List credential metadata. Secret fields are never returned."""
    return await credential_store.list()


@router.delete("/credentials/{credential_id}")
async def delete_credential(credential_id: str):
    """Delete an encrypted credential bundle."""
    deleted = await credential_store.delete(credential_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Credential not found")
    return {"message": f"Credential '{credential_id}' deleted"}
