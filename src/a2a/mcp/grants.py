"""
Consent-based peer-to-peer interaction authorization for the GA2A protocol.

In GA2A, *discovery* is open: any agent may see that another agent exists.
*Interaction*, however, is gated by consent. Before agent X may invoke a
capability on agent Y, Y must issue X a signed authorization (an
:class:`AccessGrant`). Grants are scoped (to a specific tool, to a whole
zone, or to everything) and time-limited (TTL).

The trust model mirrors ``auth.py``: grants are HMAC-SHA256 signed by the
grantor's secret and serialized as ``base64(json_payload).signature_hex``.
Because only the owning agent knows its secret, only that agent can mint
grants that its own :class:`GrantManager` will later accept.

Typical flow::

    manager = GrantManager("agent-y", secret="y-secret")

    # X asks to interact
    req = manager.submit_request(from_agent="agent-x", zone="lab",
                                 interest="run analysis", tool="analyze")

    # Y approves manually (or a policy auto-approves)
    grant = manager.approve(req.request_id)
    token = manager.serialize_grant(grant)

    # ... token travels to X, then back to Y on an invocation ...
    valid = manager.validate_grant(token, tool="analyze")  # -> AccessGrant or None
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

# Default grant time-to-live: 30 minutes.
DEFAULT_GRANT_TTL: float = 1800.0


@dataclass
class AccessRequest:
    """A request from one agent to interact with another.

    Attributes:
        request_id: Unique identifier for this request (uuid4 hex).
        from_agent: The requesting agent's name.
        to_agent: The target agent's name (the prospective grantor/owner).
        zone: The zone the interaction takes place in.
        interest: A human-readable reason for wanting to interact.
        tool: Optional name of a specific tool the requester wants to invoke.
        created_at: Unix timestamp when the request was created.
        status: One of ``"pending"``, ``"approved"``, or ``"denied"``.
    """

    request_id: str
    from_agent: str
    to_agent: str
    zone: str
    interest: str
    tool: str = ""
    created_at: float = 0.0
    status: str = "pending"

    def to_dict(self) -> dict:
        """Serialize this request to a plain dict."""
        return {
            "request_id": self.request_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "zone": self.zone,
            "interest": self.interest,
            "tool": self.tool,
            "created_at": self.created_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AccessRequest":
        """Reconstruct an :class:`AccessRequest` from a dict."""
        return cls(
            request_id=d["request_id"],
            from_agent=d["from_agent"],
            to_agent=d["to_agent"],
            zone=d["zone"],
            interest=d["interest"],
            tool=d.get("tool", ""),
            created_at=float(d.get("created_at", 0.0)),
            status=d.get("status", "pending"),
        )


@dataclass
class AccessGrant:
    """A signed authorization allowing ``from_agent`` to invoke a scope on ``to_agent``.

    Attributes:
        grant_id: Unique identifier for this grant (uuid4 hex).
        from_agent: The agent that is permitted to invoke.
        to_agent: The agent that granted permission (the owner).
        zone: The zone the grant applies to.
        scope: What is allowed. Either a specific tool name, ``"zone:<name>"``
            for a zone-wide grant, or ``"*"`` for everything.
        issued_at: Unix timestamp when the grant was issued.
        expires_at: Unix timestamp after which the grant is invalid.
        signature: HMAC-SHA256 hex signature over the grant's fields.
    """

    grant_id: str
    from_agent: str
    to_agent: str
    zone: str
    scope: str
    issued_at: float
    expires_at: float
    signature: str

    def to_dict(self) -> dict:
        """Serialize this grant to a plain dict (including signature)."""
        return {
            "grant_id": self.grant_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "zone": self.zone,
            "scope": self.scope,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AccessGrant":
        """Reconstruct an :class:`AccessGrant` from a dict."""
        return cls(
            grant_id=d["grant_id"],
            from_agent=d["from_agent"],
            to_agent=d["to_agent"],
            zone=d["zone"],
            scope=d["scope"],
            issued_at=float(d["issued_at"]),
            expires_at=float(d["expires_at"]),
            signature=d.get("signature", ""),
        )

    def is_expired(self) -> bool:
        """Return True if the grant has passed its expiry time."""
        return time.time() > self.expires_at

    def covers(self, tool: str) -> bool:
        """Return True if this grant's scope authorizes the given tool.

        Rules:
            - ``"*"`` covers everything.
            - A scope starting with ``"zone:"`` covers any tool (zone-level grant).
            - An exact match between ``scope`` and ``tool`` is covered.
            - Anything else is not covered.

        Args:
            tool: The tool name being invoked. May be empty.

        Returns:
            True if the invocation is permitted by this grant's scope.
        """
        if self.scope == "*":
            return True
        if self.scope.startswith("zone:"):
            return True
        return self.scope == tool


class GrantManager:
    """Manages access requests and grants for a single agent (the grantor/owner).

    Each agent that hosts capabilities owns a :class:`GrantManager`. It:

    - receives access requests (queued as ``pending``),
    - lets the owner approve/deny them manually,
    - supports auto-approval policies,
    - issues signed :class:`AccessGrant` tokens on approval,
    - validates incoming grants when an invocation arrives, and
    - revokes grants.

    All public methods are thread-safe.
    """

    def __init__(
        self,
        owner_agent: str,
        secret: str,
        grant_ttl: float = DEFAULT_GRANT_TTL,
    ) -> None:
        """Initialize a GrantManager.

        Args:
            owner_agent: The name of the agent that owns this manager.
            secret: The owner's signing secret (used for HMAC-SHA256).
            grant_ttl: Default time-to-live for issued grants, in seconds.
        """
        self._owner = owner_agent
        self._secret = secret
        self._grant_ttl = grant_ttl
        self._pending: dict[str, AccessRequest] = {}
        self._grants: dict[str, AccessGrant] = {}
        self._grant_by_request: dict[str, str] = {}
        self._policies: list[dict] = []
        self._revoked: set[str] = set()
        self._lock = threading.Lock()

    @property
    def owner_agent(self) -> str:
        """The name of the agent that owns this manager."""
        return self._owner

    @property
    def grant_ttl(self) -> float:
        """The default grant TTL in seconds."""
        return self._grant_ttl

    # --- Requests ---

    def submit_request(
        self,
        from_agent: str,
        zone: str,
        interest: str,
        tool: str = "",
    ) -> AccessRequest:
        """Register a new access request.

        Creates a pending request, then checks auto-approval policies. If a
        policy matches, the request is auto-approved: its status becomes
        ``"approved"``, a grant is issued and stored, and the caller can fetch
        that grant via :meth:`get_grant_for_request` or :meth:`list_grants`.

        Args:
            from_agent: The requesting agent's name.
            zone: The zone of the interaction.
            interest: Human-readable reason for the request.
            tool: Optional specific tool being requested.

        Returns:
            The created :class:`AccessRequest` (check its ``status`` to see if
            it was auto-approved).
        """
        request = AccessRequest(
            request_id=uuid.uuid4().hex,
            from_agent=from_agent,
            to_agent=self._owner,
            zone=zone,
            interest=interest,
            tool=tool,
            created_at=time.time(),
            status="pending",
        )
        with self._lock:
            self._pending[request.request_id] = request
            if self._matches_policy(request):
                request.status = "approved"
                scope = tool if tool else "*"
                grant = self._issue_grant_locked(from_agent, zone, scope, self._grant_ttl)
                self._grant_by_request[request.request_id] = grant.grant_id
        return request

    def list_pending(self) -> list[AccessRequest]:
        """Return all requests that are still pending approval."""
        with self._lock:
            return [r for r in self._pending.values() if r.status == "pending"]

    def get_request(self, request_id: str) -> Optional[AccessRequest]:
        """Return the request with the given id, or None if unknown."""
        with self._lock:
            return self._pending.get(request_id)

    def get_grant_for_request(self, request_id: str) -> Optional[AccessGrant]:
        """Return the grant issued for a given (auto-)approved request, if any."""
        with self._lock:
            grant_id = self._grant_by_request.get(request_id)
            if grant_id is None:
                return None
            return self._grants.get(grant_id)

    def approve(
        self,
        request_id: str,
        scope: Optional[str] = None,
        ttl: Optional[float] = None,
    ) -> Optional[AccessGrant]:
        """Approve a pending request and issue a grant.

        Args:
            request_id: The id of the pending request to approve.
            scope: The scope to grant. Defaults to the requested tool if the
                request specified one, otherwise ``"*"``.
            ttl: Optional TTL override for the grant, in seconds.

        Returns:
            The issued :class:`AccessGrant`, or None if the request does not
            exist or is not pending.
        """
        with self._lock:
            request = self._pending.get(request_id)
            if request is None or request.status != "pending":
                return None
            if scope is None:
                scope = request.tool if request.tool else "*"
            effective_ttl = ttl if ttl is not None else self._grant_ttl
            grant = self._issue_grant_locked(
                request.from_agent, request.zone, scope, effective_ttl
            )
            request.status = "approved"
            self._grant_by_request[request_id] = grant.grant_id
            return grant

    def deny(self, request_id: str) -> bool:
        """Deny a pending request.

        Args:
            request_id: The id of the pending request to deny.

        Returns:
            True if a pending request was found and denied, else False.
        """
        with self._lock:
            request = self._pending.get(request_id)
            if request is None or request.status != "pending":
                return False
            request.status = "denied"
            return True

    # --- Policies (auto-approval) ---

    def add_policy(
        self,
        from_agent: Optional[str] = None,
        zone: Optional[str] = None,
        interest: Optional[str] = None,
        tool: Optional[str] = None,
    ) -> None:
        """Add an auto-approval rule.

        A request is auto-approved if, for every field of the policy that is
        not None, the request's corresponding field is equal. Fields left as
        None act as wildcards. An empty policy (all None) matches everything.

        Args:
            from_agent: Match only requests from this agent.
            zone: Match only requests in this zone.
            interest: Match only requests with this exact interest.
            tool: Match only requests for this tool.
        """
        with self._lock:
            self._policies.append(
                {
                    "from_agent": from_agent,
                    "zone": zone,
                    "interest": interest,
                    "tool": tool,
                }
            )

    def _matches_policy(self, request: AccessRequest) -> bool:
        """Return True if any registered policy matches the request.

        A policy matches when every non-None field equals the request's
        corresponding field. Must be called while holding ``self._lock``.
        """
        for policy in self._policies:
            if policy["from_agent"] is not None and policy["from_agent"] != request.from_agent:
                continue
            if policy["zone"] is not None and policy["zone"] != request.zone:
                continue
            if policy["interest"] is not None and policy["interest"] != request.interest:
                continue
            if policy["tool"] is not None and policy["tool"] != request.tool:
                continue
            return True
        return False

    # --- Grants ---

    def issue_grant(
        self,
        from_agent: str,
        zone: str,
        scope: str,
        ttl: Optional[float] = None,
    ) -> AccessGrant:
        """Create, sign, and store a grant.

        Args:
            from_agent: The agent that will be allowed to invoke.
            zone: The zone the grant applies to.
            scope: The scope (tool name, ``"zone:<name>"``, or ``"*"``).
            ttl: Optional TTL override, in seconds.

        Returns:
            The newly created and stored :class:`AccessGrant`.
        """
        effective_ttl = ttl if ttl is not None else self._grant_ttl
        with self._lock:
            return self._issue_grant_locked(from_agent, zone, scope, effective_ttl)

    def _issue_grant_locked(
        self,
        from_agent: str,
        zone: str,
        scope: str,
        ttl: float,
    ) -> AccessGrant:
        """Create, sign, and store a grant. Must hold ``self._lock``."""
        now = time.time()
        grant = AccessGrant(
            grant_id=uuid.uuid4().hex,
            from_agent=from_agent,
            to_agent=self._owner,
            zone=zone,
            scope=scope,
            issued_at=now,
            expires_at=now + ttl,
            signature="",
        )
        grant.signature = self._sign(grant)
        self._grants[grant.grant_id] = grant
        return grant

    def validate_grant(self, grant_token: str, tool: str = "") -> Optional[AccessGrant]:
        """Validate a serialized grant token for an incoming invocation.

        The grant is accepted only if all of the following hold:

        - the token is well-formed and its signature verifies against the
          owner's secret,
        - it is not expired,
        - its ``grant_id`` has not been revoked,
        - it was issued by this owner (``to_agent == owner``), and
        - its scope covers the requested ``tool``.

        Args:
            grant_token: The serialized grant string.
            tool: The tool being invoked (checked against the grant's scope).

        Returns:
            The valid :class:`AccessGrant`, or None if any check fails.
        """
        grant = self.deserialize_grant(grant_token)
        if grant is None:
            return None
        if grant.to_agent != self._owner:
            return None
        if grant.is_expired():
            return None
        with self._lock:
            if grant.grant_id in self._revoked:
                return None
        if not grant.covers(tool):
            return None
        return grant

    def revoke(self, grant_id: str) -> bool:
        """Revoke a grant by id.

        Args:
            grant_id: The id of the grant to revoke.

        Returns:
            True if the grant was known and is now revoked, False if it was
            already revoked or never issued by this manager.
        """
        with self._lock:
            if grant_id in self._revoked:
                return False
            if grant_id not in self._grants:
                return False
            self._revoked.add(grant_id)
            return True

    def list_grants(self) -> list[AccessGrant]:
        """Return all grants issued by this manager (including revoked ones)."""
        with self._lock:
            return list(self._grants.values())

    # --- Serialization ---

    def _sign(self, grant: AccessGrant) -> str:
        """Compute the HMAC-SHA256 signature for a grant.

        The signed message is
        ``"{grant_id}:{from_agent}:{to_agent}:{zone}:{scope}:{issued_at}:{expires_at}"``.
        """
        message = (
            f"{grant.grant_id}:{grant.from_agent}:{grant.to_agent}:"
            f"{grant.zone}:{grant.scope}:{grant.issued_at}:{grant.expires_at}"
        )
        return hmac.new(
            self._secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def serialize_grant(self, grant: AccessGrant) -> str:
        """Serialize a grant to ``base64(json_payload).signature_hex``.

        The JSON payload excludes the signature (which is appended separately),
        mirroring the token format used in ``auth.py``.
        """
        payload = {
            "grant_id": grant.grant_id,
            "from_agent": grant.from_agent,
            "to_agent": grant.to_agent,
            "zone": grant.zone,
            "scope": grant.scope,
            "issued_at": grant.issued_at,
            "expires_at": grant.expires_at,
        }
        payload_json = json.dumps(payload, separators=(",", ":"))
        payload_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")
        return f"{payload_b64}.{grant.signature}"

    def deserialize_grant(self, token: str) -> Optional[AccessGrant]:
        """Parse a serialized grant token and verify its signature.

        Args:
            token: The serialized grant string.

        Returns:
            The reconstructed :class:`AccessGrant` if the token is well-formed
            and its signature verifies against the owner's secret, else None.
        """
        try:
            parts = token.split(".", 1)
            if len(parts) != 2:
                return None
            payload_b64, provided_signature = parts
            payload_json = base64.b64decode(payload_b64).decode("utf-8")
            payload = json.loads(payload_json)

            grant = AccessGrant(
                grant_id=payload["grant_id"],
                from_agent=payload["from_agent"],
                to_agent=payload["to_agent"],
                zone=payload["zone"],
                scope=payload["scope"],
                issued_at=float(payload["issued_at"]),
                expires_at=float(payload["expires_at"]),
                signature=provided_signature,
            )
        except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return None

        expected_signature = self._sign(grant)
        if not hmac.compare_digest(provided_signature, expected_signature):
            return None
        return grant
