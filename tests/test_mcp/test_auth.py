"""Unit tests for MCP auth module."""

import time

import pytest

from a2a.mcp.auth import AuthProvider, AuthToken, AuthValidator, DEFAULT_TOKEN_TTL


class TestAuthToken:
    """Tests for the AuthToken dataclass."""

    def test_creation(self):
        token = AuthToken(
            agent_name="agent-a",
            zone_name="science-zone",
            issued_at=1000.0,
            expires_at=4600.0,
            signature="abc123",
        )
        assert token.agent_name == "agent-a"
        assert token.zone_name == "science-zone"
        assert token.issued_at == 1000.0
        assert token.expires_at == 4600.0
        assert token.signature == "abc123"

    def test_equality(self):
        t1 = AuthToken("a", "z", 1.0, 2.0, "sig")
        t2 = AuthToken("a", "z", 1.0, 2.0, "sig")
        assert t1 == t2

    def test_inequality(self):
        t1 = AuthToken("a", "z", 1.0, 2.0, "sig1")
        t2 = AuthToken("a", "z", 1.0, 2.0, "sig2")
        assert t1 != t2


class TestAuthProvider:
    """Tests for AuthProvider token generation."""

    def test_generate_token_returns_string(self):
        provider = AuthProvider("agent-a", {"zone1": "secret123"})
        token_str = provider.generate_token("zone1")
        assert isinstance(token_str, str)
        assert "." in token_str

    def test_generate_token_format(self):
        provider = AuthProvider("agent-a", {"zone1": "secret123"})
        token_str = provider.generate_token("zone1")
        parts = token_str.split(".", 1)
        assert len(parts) == 2
        # First part is base64, second is hex signature (64 chars for sha256)
        assert len(parts[1]) == 64

    def test_generate_token_unknown_zone_raises(self):
        provider = AuthProvider("agent-a", {"zone1": "secret123"})
        with pytest.raises(ValueError, match="No secret configured for zone"):
            provider.generate_token("unknown-zone")

    def test_agent_name_property(self):
        provider = AuthProvider("my-agent", {})
        assert provider.agent_name == "my-agent"

    def test_token_ttl_property_default(self):
        provider = AuthProvider("agent-a", {})
        assert provider.token_ttl == DEFAULT_TOKEN_TTL

    def test_token_ttl_property_custom(self):
        provider = AuthProvider("agent-a", {}, token_ttl=120.0)
        assert provider.token_ttl == 120.0

    def test_generate_token_contains_agent_info(self):
        """Token payload should contain the agent name and zone name."""
        import base64
        import json

        provider = AuthProvider("research-bot", {"lab": "s3cr3t"})
        token_str = provider.generate_token("lab")
        payload_b64 = token_str.split(".", 1)[0]
        payload = json.loads(base64.b64decode(payload_b64).decode("utf-8"))
        assert payload["agent_name"] == "research-bot"
        assert payload["zone_name"] == "lab"
        assert payload["expires_at"] - payload["issued_at"] == pytest.approx(
            DEFAULT_TOKEN_TTL, abs=1.0
        )


class TestAuthValidator:
    """Tests for AuthValidator token validation."""

    def _make_valid_token(self, zone_name="zone1", secret="secret123", agent="agent-a"):
        provider = AuthProvider(agent, {zone_name: secret})
        return provider.generate_token(zone_name)

    def test_validate_valid_token(self):
        token_str = self._make_valid_token()
        validator = AuthValidator("zone1", "secret123")
        token = validator.validate_token(token_str)
        assert token is not None
        assert token.agent_name == "agent-a"
        assert token.zone_name == "zone1"

    def test_validate_token_returns_auth_token(self):
        token_str = self._make_valid_token()
        validator = AuthValidator("zone1", "secret123")
        token = validator.validate_token(token_str)
        assert isinstance(token, AuthToken)

    def test_validate_wrong_secret_returns_none(self):
        token_str = self._make_valid_token(secret="correct-secret")
        validator = AuthValidator("zone1", "wrong-secret")
        assert validator.validate_token(token_str) is None

    def test_validate_expired_token_returns_none(self):
        # Create a provider with very short TTL
        provider = AuthProvider("agent-a", {"zone1": "secret123"}, token_ttl=-1.0)
        token_str = provider.generate_token("zone1")
        validator = AuthValidator("zone1", "secret123")
        assert validator.validate_token(token_str) is None

    def test_validate_malformed_token_no_dot(self):
        validator = AuthValidator("zone1", "secret123")
        assert validator.validate_token("nodotshere") is None

    def test_validate_malformed_token_bad_base64(self):
        validator = AuthValidator("zone1", "secret123")
        assert validator.validate_token("!!!invalid-base64!!!.abcdef1234") is None

    def test_validate_malformed_token_bad_json(self):
        import base64

        bad_payload = base64.b64encode(b"not json").decode("ascii")
        validator = AuthValidator("zone1", "secret123")
        assert validator.validate_token(f"{bad_payload}.{'a' * 64}") is None

    def test_validate_malformed_token_missing_fields(self):
        import base64
        import json

        payload = base64.b64encode(json.dumps({"agent_name": "a"}).encode()).decode("ascii")
        validator = AuthValidator("zone1", "secret123")
        assert validator.validate_token(f"{payload}.{'a' * 64}") is None

    def test_validate_empty_string(self):
        validator = AuthValidator("zone1", "secret123")
        assert validator.validate_token("") is None

    def test_validate_token_tampered_payload(self):
        """Modifying the payload should invalidate the signature."""
        import base64
        import json

        token_str = self._make_valid_token()
        payload_b64, sig = token_str.split(".", 1)

        # Tamper with the payload
        payload = json.loads(base64.b64decode(payload_b64).decode("utf-8"))
        payload["agent_name"] = "evil-agent"
        tampered_b64 = base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")

        validator = AuthValidator("zone1", "secret123")
        assert validator.validate_token(f"{tampered_b64}.{sig}") is None

    def test_zone_name_property(self):
        validator = AuthValidator("my-zone", "secret")
        assert validator.zone_name == "my-zone"


class TestIsZoneMember:
    """Tests for AuthValidator.is_zone_member()."""

    def test_same_zone_returns_true(self):
        validator = AuthValidator("zone1", "secret123")
        token = AuthToken("agent-a", "zone1", 1000.0, 5000.0, "sig")
        assert validator.is_zone_member(token) is True

    def test_different_zone_returns_false(self):
        validator = AuthValidator("zone1", "secret123")
        token = AuthToken("agent-a", "zone2", 1000.0, 5000.0, "sig")
        assert validator.is_zone_member(token) is False


class TestRoundTrip:
    """End-to-end tests: provider generates, validator validates."""

    def test_generate_and_validate_same_zone(self):
        secret = "shared-secret-42"
        provider = AuthProvider("bot-alpha", {"research": secret})
        validator = AuthValidator("research", secret)

        token_str = provider.generate_token("research")
        token = validator.validate_token(token_str)

        assert token is not None
        assert token.agent_name == "bot-alpha"
        assert token.zone_name == "research"
        assert validator.is_zone_member(token) is True

    def test_cross_zone_token_rejected(self):
        """A token for zone-a should not validate with a validator for zone-b."""
        provider = AuthProvider("agent-x", {"zone-a": "secret-a", "zone-b": "secret-b"})
        validator_b = AuthValidator("zone-b", "secret-b")

        token_str = provider.generate_token("zone-a")
        # The validator for zone-b uses secret-b, but the token was signed with secret-a
        assert validator_b.validate_token(token_str) is None

    def test_multiple_agents_same_zone(self):
        secret = "zone-secret"
        provider_a = AuthProvider("agent-a", {"z": secret})
        provider_b = AuthProvider("agent-b", {"z": secret})
        validator = AuthValidator("z", secret)

        token_a = validator.validate_token(provider_a.generate_token("z"))
        token_b = validator.validate_token(provider_b.generate_token("z"))

        assert token_a is not None
        assert token_b is not None
        assert token_a.agent_name == "agent-a"
        assert token_b.agent_name == "agent-b"
        assert validator.is_zone_member(token_a)
        assert validator.is_zone_member(token_b)

    def test_custom_ttl(self):
        provider = AuthProvider("agent", {"z": "s"}, token_ttl=60.0)
        validator = AuthValidator("z", "s")

        token_str = provider.generate_token("z")
        token = validator.validate_token(token_str)

        assert token is not None
        assert token.expires_at - token.issued_at == pytest.approx(60.0, abs=1.0)
