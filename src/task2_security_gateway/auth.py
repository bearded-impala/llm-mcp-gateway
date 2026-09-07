"""Bearer token authentication and role resolution."""

from fastapi import Header, HTTPException, status
from pydantic import BaseModel


class UserContext(BaseModel):
    token: str
    role: str


# Static token-to-role lookup table + prefix fallback for dev/testing
KNOWN_TOKENS: dict[str, str] = {
    "admin-secret-token-xyz": "admin",
    "viewer-readonly-token-abc": "viewer",
    "admin": "admin",
    "viewer": "viewer",
}


def extract_role_from_token(token: str) -> str | None:
    token = token.strip()
    if token in KNOWN_TOKENS:
        return KNOWN_TOKENS[token]

    lower = token.lower()
    if lower.startswith("admin"):
        return "admin"
    if lower.startswith("viewer"):
        return "viewer"

    return None


def get_current_user(authorization: str | None = Header(None)) -> UserContext:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    role = extract_role_from_token(token)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or unrecognized Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return UserContext(token=token, role=role)
