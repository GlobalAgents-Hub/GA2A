"""Unit tests for the MCP grants (consent-based authorization) module."""

import base64
import json
import time

import pytest

from a2a.mcp.grants import (
    AccessGrant,
    AccessRequest,
    DEFAULT_GRANT_TTL,
    GrantManager,
)


# ---------------------------------------------------------------------------
# AccessRequest
# ---------------------------------------------------------------------------


class TestAccessRequest:
    def test_creation_defaults(self):
        req = AccessRequest(
            request_id="r1",
            from_agent="x",
            to_agent="y",
            zone="lab",
            interest="collaborate",
        )
        assert req.tool == ""
        assert req.created_at == 0.0
        assert req.status == "pending"

    def test_to_dict_round_trip(self):
        req = AccessRequest(
            request_id="r1",
            from_agent="x",
            to_agent="y",
            zone="lab",
            interest="collaborate",
            tool="analyze",
            created_at=123.0,
            status="approved",
        )
        restored = AccessRequest.from_dict(req.to_dict())
        assert restored == req

    def test_from_dict_missing_optional_fields(self):
        d = {
            "request_id": "r1",
            "from_agent": "x",
            "to_agent": "y",
            "zone": "lab",
            "interest": "collaborate",
        }
        req = AccessRequest.from_dict(d)
        assert req.tool == ""
        assert req.status == "pending"
        assert req.created_at == 0.0


# ---------------------------------------------------------------------------
# AccessGrant.covers()
# ---------------------------------------------------------------------------


class TestAccessGrantCovers:
    def _grant(self, scope):
        return AccessGrant(
            grant_id="g1",
            from_agent="x",
            to_agent="y",
            zone="lab",
            scope=scope,
            issued_at=0.0,
            expires_at=time.time() + 1000,
            signature="sig",
        )

    def test_wildcard_covers_everything(self):
        g = self._grant("*")
        assert g.covers("analyze") is True
        assert g.covers("anything") is True
        assert g.covers("") is True

    def test_exact_tool_match(self):
        g = self._grant("analyze")
        assert g.covers("analyze") is True

    def test_exact_tool_mismatch(self):
        g = self._grant("analyze")
        assert g.covers("delete") is False

    def test_zone_scope_covers_any_tool(self):
        g = self._grant("zone:lab")
        assert g.covers("analyze") is True
        assert g.covers("delete") is True
        assert g.covers("") is True

    def test_empty_tool_against_named_scope(self):
        g = self._grant("analyze")
        assert g.covers("") is False


class TestAccessGrantExpiry:
    def test_not_expired(self):
        g = AccessGrant("g", "x", "y", "z", "*", time.time(), time.time() + 100, "s")
        assert g.is_expired() is False

    def test_expired(self):
        g = AccessGrant("g", "x", "y", "z", "*", time.time() - 100, time.time() - 1, "s")
        assert g.is_expired() is True

    def test_grant_dict_round_trip(self):
        g = AccessGrant("g", "x", "y", "z", "zone:z", 1.0, 2.0, "sig")
        restored = AccessGrant.from_dict(g.to_dict())
        assert restored == g


# ---------------------------------------------------------------------------
# Request submission
# ---------------------------------------------------------------------------


class TestSubmitRequest:
    def test_submit_creates_pending(self):
        mgr = GrantManager("owner", "secret")
        req = mgr.submit_request("x", "lab", "collaborate", tool="analyze")
        assert req.status == "pending"
        assert req.from_agent == "x"
        assert req.to_agent == "owner"
        assert req.tool == "analyze"
        assert req.created_at > 0
        assert len(req.request_id) > 0

    def test_submit_appears_in_pending_list(self):
        mgr = GrantManager("owner", "secret")
        req = mgr.submit_request("x", "lab", "collaborate")
        pending = mgr.list_pending()
        assert req.request_id in [r.request_id for r in pending]

    def test_get_request(self):
        mgr = GrantManager("owner", "secret")
        req = mgr.submit_request("x", "lab", "collaborate")
        assert mgr.get_request(req.request_id) is req
        assert mgr.get_request("unknown") is None

    def test_unique_request_ids(self):
        mgr = GrantManager("owner", "secret")
        r1 = mgr.submit_request("x", "lab", "a")
        r2 = mgr.submit_request("x", "lab", "b")
        assert r1.request_id != r2.request_id


# ---------------------------------------------------------------------------
# Manual approve / deny
# ---------------------------------------------------------------------------


class TestApproveDeny:
    def test_approve_issues_grant(self):
        mgr = GrantManager("owner", "secret")
        req = mgr.submit_request("x", "lab", "collaborate", tool="analyze")
        grant = mgr.approve(req.request_id)
        assert grant is not None
        assert grant.from_agent == "x"
        assert grant.to_agent == "owner"
        assert grant.zone == "lab"
        assert grant.scope == "analyze"  # defaults to requested tool
        assert mgr.get_request(req.request_id).status == "approved"

    def test_approve_defaults_to_wildcard_without_tool(self):
        mgr = GrantManager("owner", "secret")
        req = mgr.submit_request("x", "lab", "collaborate")
        grant = mgr.approve(req.request_id)
        assert grant.scope == "*"

    def test_approve_with_explicit_scope(self):
        mgr = GrantManager("owner", "secret")
        req = mgr.submit_request("x", "lab", "collaborate", tool="analyze")
        grant = mgr.approve(req.request_id, scope="zone:lab")
        assert grant.scope == "zone:lab"

    def test_approve_with_custom_ttl(self):
        mgr = GrantManager("owner", "secret")
        req = mgr.submit_request("x", "lab", "collaborate")
        grant = mgr.approve(req.request_id, ttl=60.0)
        assert grant.expires_at - grant.issued_at == pytest.approx(60.0, abs=1.0)

    def test_approve_unknown_request_returns_none(self):
        mgr = GrantManager("owner", "secret")
        assert mgr.approve("nope") is None

    def test_approve_twice_returns_none(self):
        mgr = GrantManager("owner", "secret")
        req = mgr.submit_request("x", "lab", "collaborate")
        assert mgr.approve(req.request_id) is not None
        assert mgr.approve(req.request_id) is None  # no longer pending

    def test_deny(self):
        mgr = GrantManager("owner", "secret")
        req = mgr.submit_request("x", "lab", "collaborate")
        assert mgr.deny(req.request_id) is True
        assert mgr.get_request(req.request_id).status == "denied"

    def test_deny_unknown_returns_false(self):
        mgr = GrantManager("owner", "secret")
        assert mgr.deny("nope") is False

    def test_deny_after_approve_returns_false(self):
        mgr = GrantManager("owner", "secret")
        req = mgr.submit_request("x", "lab", "collaborate")
        mgr.approve(req.request_id)
        assert mgr.deny(req.request_id) is False


# ---------------------------------------------------------------------------
# Grant issuance + serialization round-trip
# ---------------------------------------------------------------------------


class TestGrantSerialization:
    def test_serialize_format(self):
        mgr = GrantManager("owner", "secret")
        grant = mgr.issue_grant("x", "lab", "analyze")
        token = mgr.serialize_grant(grant)
        assert "." in token
        payload_b64, sig = token.split(".", 1)
        assert len(sig) == 64  # sha256 hex
        payload = json.loads(base64.b64decode(payload_b64).decode("utf-8"))
        assert payload["from_agent"] == "x"
        assert payload["to_agent"] == "owner"
        assert payload["scope"] == "analyze"
        assert "signature" not in payload

    def test_round_trip(self):
        mgr = GrantManager("owner", "secret")
        grant = mgr.issue_grant("x", "lab", "analyze")
        token = mgr.serialize_grant(grant)
        restored = mgr.deserialize_grant(token)
        assert restored is not None
        assert restored == grant

    def test_deserialize_malformed_no_dot(self):
        mgr = GrantManager("owner", "secret")
        assert mgr.deserialize_grant("nodots") is None

    def test_deserialize_bad_base64(self):
        mgr = GrantManager("owner", "secret")
        assert mgr.deserialize_grant("!!!bad!!!." + "a" * 64) is None

    def test_deserialize_bad_json(self):
        mgr = GrantManager("owner", "secret")
        bad = base64.b64encode(b"not json").decode("ascii")
        assert mgr.deserialize_grant(f"{bad}.{'a' * 64}") is None

    def test_deserialize_missing_fields(self):
        mgr = GrantManager("owner", "secret")
        payload = base64.b64encode(json.dumps({"grant_id": "g"}).encode()).decode("ascii")
        assert mgr.deserialize_grant(f"{payload}.{'a' * 64}") is None

    def test_deserialize_wrong_signature(self):
        mgr = GrantManager("owner", "secret")
        grant = mgr.issue_grant("x", "lab", "analyze")
        token = mgr.serialize_grant(grant)
        payload_b64 = token.split(".", 1)[0]
        assert mgr.deserialize_grant(f"{payload_b64}.{'0' * 64}") is None

    def test_tampered_payload_rejected(self):
        mgr = GrantManager("owner", "secret")
        grant = mgr.issue_grant("x", "lab", "analyze")
        token = mgr.serialize_grant(grant)
        _, sig = token.split(".", 1)
        payload = grant.to_dict()
        payload.pop("signature")
        payload["scope"] = "*"  # escalate privileges
        tampered_b64 = base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        assert mgr.deserialize_grant(f"{tampered_b64}.{sig}") is None


# ---------------------------------------------------------------------------
# Grant validation
# ---------------------------------------------------------------------------


class TestValidateGrant:
    def test_valid_grant(self):
        mgr = GrantManager("owner", "secret")
        grant = mgr.issue_grant("x", "lab", "analyze")
        token = mgr.serialize_grant(grant)
        result = mgr.validate_grant(token, tool="analyze")
        assert result is not None
        assert result.grant_id == grant.grant_id

    def test_valid_wildcard_grant_any_tool(self):
        mgr = GrantManager("owner", "secret")
        grant = mgr.issue_grant("x", "lab", "*")
        token = mgr.serialize_grant(grant)
        assert mgr.validate_grant(token, tool="whatever") is not None

    def test_scope_mismatch_returns_none(self):
        mgr = GrantManager("owner", "secret")
        grant = mgr.issue_grant("x", "lab", "analyze")
        token = mgr.serialize_grant(grant)
        assert mgr.validate_grant(token, tool="delete") is None

    def test_zone_scope_covers_tool(self):
        mgr = GrantManager("owner", "secret")
        grant = mgr.issue_grant("x", "lab", "zone:lab")
        token = mgr.serialize_grant(grant)
        assert mgr.validate_grant(token, tool="anything") is not None

    def test_expired_grant_returns_none(self):
        mgr = GrantManager("owner", "secret", grant_ttl=-1.0)
        grant = mgr.issue_grant("x", "lab", "*")
        token = mgr.serialize_grant(grant)
        assert mgr.validate_grant(token, tool="x") is None

    def test_revoked_grant_returns_none(self):
        mgr = GrantManager("owner", "secret")
        grant = mgr.issue_grant("x", "lab", "*")
        token = mgr.serialize_grant(grant)
        assert mgr.validate_grant(token) is not None
        mgr.revoke(grant.grant_id)
        assert mgr.validate_grant(token) is None

    def test_wrong_owner_returns_none(self):
        """A grant issued by another agent must not validate here."""
        other = GrantManager("other-owner", "secret")
        grant = other.issue_grant("x", "lab", "*")
        token = other.serialize_grant(grant)

        # Same secret, but a manager owned by a different agent.
        mine = GrantManager("owner", "secret")
        assert mine.validate_grant(token) is None

    def test_wrong_secret_returns_none(self):
        signer = GrantManager("owner", "secret-a")
        grant = signer.issue_grant("x", "lab", "*")
        token = signer.serialize_grant(grant)

        verifier = GrantManager("owner", "secret-b")
        assert verifier.validate_grant(token) is None

    def test_malformed_token_returns_none(self):
        mgr = GrantManager("owner", "secret")
        assert mgr.validate_grant("garbage") is None


# ---------------------------------------------------------------------------
# Auto-approval policies
# ---------------------------------------------------------------------------


class TestPolicies:
    def test_policy_auto_approves_matching_request(self):
        mgr = GrantManager("owner", "secret")
        mgr.add_policy(from_agent="trusted", zone="lab")
        req = mgr.submit_request("trusted", "lab", "collaborate", tool="analyze")
        assert req.status == "approved"
        grant = mgr.get_grant_for_request(req.request_id)
        assert grant is not None
        assert grant.from_agent == "trusted"
        assert grant.scope == "analyze"

    def test_policy_no_match_stays_pending(self):
        mgr = GrantManager("owner", "secret")
        mgr.add_policy(from_agent="trusted")
        req = mgr.submit_request("stranger", "lab", "collaborate")
        assert req.status == "pending"
        assert mgr.get_grant_for_request(req.request_id) is None

    def test_policy_partial_field_mismatch(self):
        mgr = GrantManager("owner", "secret")
        mgr.add_policy(from_agent="trusted", zone="lab")
        # right agent, wrong zone
        req = mgr.submit_request("trusted", "other-zone", "collaborate")
        assert req.status == "pending"

    def test_empty_policy_matches_everything(self):
        mgr = GrantManager("owner", "secret")
        mgr.add_policy()  # all wildcards
        req = mgr.submit_request("anyone", "anywhere", "any reason")
        assert req.status == "approved"

    def test_policy_matches_on_interest(self):
        mgr = GrantManager("owner", "secret")
        mgr.add_policy(interest="read-only")
        approved = mgr.submit_request("x", "lab", "read-only")
        pending = mgr.submit_request("x", "lab", "write-access")
        assert approved.status == "approved"
        assert pending.status == "pending"

    def test_policy_matches_on_tool(self):
        mgr = GrantManager("owner", "secret")
        mgr.add_policy(tool="analyze")
        approved = mgr.submit_request("x", "lab", "work", tool="analyze")
        pending = mgr.submit_request("x", "lab", "work", tool="delete")
        assert approved.status == "approved"
        assert pending.status == "pending"

    def test_auto_approved_grant_validates(self):
        mgr = GrantManager("owner", "secret")
        mgr.add_policy(from_agent="trusted")
        req = mgr.submit_request("trusted", "lab", "work", tool="analyze")
        grant = mgr.get_grant_for_request(req.request_id)
        token = mgr.serialize_grant(grant)
        assert mgr.validate_grant(token, tool="analyze") is not None


# ---------------------------------------------------------------------------
# Revocation and listing
# ---------------------------------------------------------------------------


class TestRevocation:
    def test_revoke_known_grant(self):
        mgr = GrantManager("owner", "secret")
        grant = mgr.issue_grant("x", "lab", "*")
        assert mgr.revoke(grant.grant_id) is True

    def test_revoke_twice_returns_false(self):
        mgr = GrantManager("owner", "secret")
        grant = mgr.issue_grant("x", "lab", "*")
        assert mgr.revoke(grant.grant_id) is True
        assert mgr.revoke(grant.grant_id) is False

    def test_revoke_unknown_returns_false(self):
        mgr = GrantManager("owner", "secret")
        assert mgr.revoke("unknown") is False

    def test_list_grants_includes_issued(self):
        mgr = GrantManager("owner", "secret")
        g1 = mgr.issue_grant("x", "lab", "*")
        g2 = mgr.issue_grant("y", "lab", "analyze")
        ids = {g.grant_id for g in mgr.list_grants()}
        assert g1.grant_id in ids
        assert g2.grant_id in ids


# ---------------------------------------------------------------------------
# Manager properties / defaults
# ---------------------------------------------------------------------------


class TestManagerBasics:
    def test_owner_property(self):
        mgr = GrantManager("owner", "secret")
        assert mgr.owner_agent == "owner"

    def test_default_ttl(self):
        mgr = GrantManager("owner", "secret")
        assert mgr.grant_ttl == DEFAULT_GRANT_TTL
        grant = mgr.issue_grant("x", "lab", "*")
        assert grant.expires_at - grant.issued_at == pytest.approx(
            DEFAULT_GRANT_TTL, abs=1.0
        )

    def test_custom_ttl(self):
        mgr = GrantManager("owner", "secret", grant_ttl=90.0)
        assert mgr.grant_ttl == 90.0
