"""Signed public order delivery over pinned, public-address-only TLS 1.3."""

import asyncio
import http.client
import ipaddress
import json
import socket
import ssl
import time
from urllib.parse import urlsplit

from ..config import settings
from .signatures import sign


def deliver_https(url, raw, headers):
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.fragment
        or parts.port not in (None, 443)
    ):
        raise ValueError("Webhook needs public HTTPS port 443.")
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(
        not ipaddress.ip_address(item[4][0]).is_global for item in addresses
    ):
        raise ValueError("Private/special webhook destinations are forbidden.")
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    connection = http.client.HTTPSConnection(parts.hostname, timeout=10, context=context)
    connection.sock = context.wrap_socket(
        socket.create_connection((addresses[0][4][0], 443), timeout=10),
        server_hostname=parts.hostname,
    )
    try:
        connection.request(
            "POST",
            parts.path + ("?" + parts.query if parts.query else "") or "/",
            body=raw,
            headers=headers,
        )
        response = connection.getresponse()
        response.read(65536)
        if not 200 <= response.status < 300:
            raise ValueError(
                f"Webhook delivery returned HTTP {response.status}; redirects are not followed."
            )
    finally:
        connection.close()


async def deliver(job):
    raw = json.dumps(job["payload"], sort_keys=True, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "Webhook-Id": str(job["event_id"]),
        "Webhook-Timestamp": str(int(time.time())),
    }
    headers.update(
        sign(
            raw,
            {
                "@method": "POST",
                "@target-uri": job["target_url"],
                "webhook-id": headers["Webhook-Id"],
                "webhook-timestamp": headers["Webhook-Timestamp"],
            },
            settings.ucp_signing_key_file,
            kid="shelfiq-business",
        )
    )
    await asyncio.to_thread(deliver_https, job["target_url"], raw, headers)
