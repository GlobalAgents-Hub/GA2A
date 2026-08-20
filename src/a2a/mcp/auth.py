"""
Zone-scoped authentication for MCP connections.

Provides HMAC-SHA256 token generation and validation to ensure that only
agents within the same zone can communicate via MCP. Tokens are serialized
as base64-encoded JSON payloads with an appended hex signature.

Token format: base64(json_payload).signature_hex
Signature: HMAC-SHA256(zone_secret, "{agent_name}:{zone_name}:{issued_at}:{expires_at}")
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

# Default token time-to-live: 1 hour (3600 seconds)
DEFAULT_TOKEN_TTL: float = 3600.0


@dataclass
class AuthToken:
    """Authentication token for MCP connections."""

    agent_name: str
    zone_name: str
    issued_at: float
    expires_at: float
    signature: str


class AuthProvider:
    """Generates authentication tokens for outgoing MCP requests.

    Stores the agent's name and a mapping of zone_name -> zone_secret.
    Tokens are HMAC-SHA256 signed and base64 encoded for transport.
    """

    def __init__(
        self,
        agent_name: str,
        zone_secrets: dict[str, str],
        token_ttl: float = DEFAULT_TOKEN_TTL,
    ) -> None:
        self._agent_name = agent_name
        self._zone_secrets = zone_secrets
        self._token_ttl = token_ttl

    @property
    def agent_name(self) -> str:
        return self._agent_name

    @property
    def token_ttl(self) -> float:
        return self._token_ttl

    def generate_token(self, zone_name: str) -> str:
        """Generate a signed token for the given zone.

        Args:
            zone_name: The zone to generate a token for.

        Returns:
            A serialized token string in the format: base64(json_payload).signature_hex

        Raises:
            ValueError: If no secret is configured for the given zone.
        """
        if zone_name not in self._zone_secrets:
            raise ValueError(f"No secret configured for zone: {zone_name}")

        now = time.time()
        issued_at = now
        expires_at = now + self._token_ttl

        signature = _compute_signature(
            self._zone_secrets[zone_name],
            self._agent_name,
            zone_name,
            issued_at,
            expires_at,
        )

        payload = {
            "agent_name": self._agent_name,
            "zone_name": zone_name,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }

        payload_json = json.dumps(payload, separators=(",", ":"))
        payload_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")

        return f"{payload_b64}.{signature}"


class AuthValidator:
    """Validates authentication tokens on incoming MCP requests.

    Validates tokens for a single zone using the zone's shared secret.
    """

    def __init__(self, zone_name: str, zone_secret: str) -> None:
        self._zone_name = zone_name
        self._zone_secret = zone_secret

    @property
    def zone_name(self) -> str:
        return self._zone_name

    def validate_token(self, token_str: str) -> AuthToken | None:
        """Validate a serialized token string.

        Decodes the base64 payload, verifies the HMAC signature, and checks
        that the token has not expired.

        Args:
            token_str: The serialized token string (base64_payload.signature_hex).

        Returns:
            An AuthToken if valid, or None if the token is malformed, has an
            invalid signature, or is expired.
        """
        try:
            parts = token_str.split(".", 1)
            if len(parts) != 2:
                return None

            payload_b64, provided_signature = parts

            # Decode the payload
            payload_json = base64.b64decode(payload_b64).decode("utf-8")
            payload = json.loads(payload_json)

            agent_name = payload["agent_name"]
            zone_name = payload["zone_name"]
            issued_at = float(payload["issued_at"])
            expires_at = float(payload["expires_at"])

        except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
            return None

        # Verify HMAC signature
        expected_signature = _compute_signature(
            self._zone_secret,
            agent_name,
            zone_name,
            issued_at,
            expires_at,
        )

        if not hmac.compare_digest(provided_signature, expected_signature):
            return None

        # Check expiry
        if time.time() > expires_at:
            return None

        return AuthToken(
            agent_name=agent_name,
            zone_name=zone_name,
            issued_at=issued_at,
            expires_at=expires_at,
            signature=provided_signature,
        )

    def is_zone_member(self, token: AuthToken) -> bool:
        """Check if the token's zone matches this validator's zone.

        Args:
            token: A validated AuthToken.

        Returns:
            True if the token's zone_name matches this validator's zone_name.
        """
        return token.zone_name == self._zone_name


def _compute_signature(
    zone_secret: str,
    agent_name: str,
    zone_name: str,
    issued_at: float,
    expires_at: float,
) -> str:
    """Compute HMAC-SHA256 signature for token fields.

    The signed message is: "{agent_name}:{zone_name}:{issued_at}:{expires_at}"
    """
    message = f"{agent_name}:{zone_name}:{issued_at}:{expires_at}"
    return hmac.new(
        zone_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
