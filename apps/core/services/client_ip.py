from __future__ import annotations

from ipaddress import ip_address, ip_network

from django.conf import settings


def _normalized_ip(value) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        return ip_address(candidate).compressed
    except ValueError:
        return None


def _trusted_proxy_networks():
    configured = getattr(settings, "TRUSTED_PROXY_IPS", ()) or ()
    if isinstance(configured, str):
        configured = configured.split(",")
    networks = []
    for value in configured:
        candidate = str(value or "").strip()
        if not candidate:
            continue
        try:
            networks.append(ip_network(candidate, strict=False))
        except ValueError:
            continue
    return networks


def _is_trusted_proxy(value: str, trusted_networks) -> bool:
    address = ip_address(value)
    return any(address.version == network.version and address in network for network in trusted_networks)


def _client_ip_from_unix_proxy_headers(request) -> str | None:
    real_ip_header = request.META.get("HTTP_X_REAL_IP", "")
    if str(real_ip_header or "").strip():
        return _normalized_ip(real_ip_header)

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if not str(forwarded_for or "").strip():
        return None
    forwarded_chain = [_normalized_ip(item) for item in forwarded_for.split(",")]
    if not forwarded_chain or not all(forwarded_chain):
        return None
    # Nginx uses $proxy_add_x_forwarded_for, so its normalized $remote_addr is
    # the final address in the chain. Return only that address, never the chain.
    return forwarded_chain[-1]


def resolve_client_ip(request) -> str | None:
    """Return one normalized client IP, honoring proxy headers only from trusted peers."""
    if request is None:
        return None

    remote_addr = _normalized_ip(request.META.get("REMOTE_ADDR"))
    if remote_addr is None:
        if not getattr(settings, "TRUST_UNIX_SOCKET_PROXY", False):
            return None
        return _client_ip_from_unix_proxy_headers(request)

    trusted_networks = _trusted_proxy_networks()
    if not trusted_networks or not _is_trusted_proxy(remote_addr, trusted_networks):
        return remote_addr

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        forwarded_chain = [_normalized_ip(item) for item in forwarded_for.split(",")]
        if not all(forwarded_chain):
            return remote_addr
        candidates = [*forwarded_chain, remote_addr]
        while len(candidates) > 1 and _is_trusted_proxy(candidates[-1], trusted_networks):
            candidates.pop()
        return candidates[-1]

    real_ip = _normalized_ip(request.META.get("HTTP_X_REAL_IP"))
    return real_ip or remote_addr
