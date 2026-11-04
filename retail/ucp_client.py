"""Small independent REST platform client for interoperability acceptance.

Uses public wire messages and published JWKs, never the server's implementation
helpers or database. It only supports the advertised Ed25519 signature profile.
"""

import base64
import hashlib
import json
import re
import ssl
import time
from pathlib import Path
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class UCPClient:
    def __init__(self, base_url, profile_url, key_file, ca_file, *, token=None):
        self.base = base_url.rstrip("/")
        self.profile = profile_url
        self.key = serialization.load_pem_private_key(
            Path(key_file).read_bytes(), password=None
        )
        if not isinstance(self.key, Ed25519PrivateKey):
            raise ValueError("An Ed25519 platform key is required.")
        context = ssl.create_default_context(cafile=ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        self.http = httpx.Client(
            base_url=self.base, verify=context, timeout=20, follow_redirects=False
        )
        self.token = token or str(uuid4()) + str(uuid4())
        discovery = self.http.get("/.well-known/ucp")
        discovery.raise_for_status()
        self.merchant = discovery.json()
        self.verify(discovery)
        services = self.merchant["ucp"]["services"]["dev.ucp.shopping"]
        self.endpoint = next(
            s["endpoint"] for s in services if s["transport"] == "rest"
        )
        if not self.endpoint.startswith(self.base + "/"):
            raise ValueError(
                "Merchant endpoint must remain on the configured business origin."
            )

    def verify(self, response, request_id=None):
        raw = response.headers.get("signature-input", "")
        match = re.fullmatch(
            r'sig1=(\((?:"[^"]+" ?)+\);created=([0-9]+);keyid="([^"]+)";alg="ed25519")',
            raw,
        )
        if not match or abs(time.time() - int(match[2])) > 300:
            raise ValueError("Missing or expired signed business response.")
        components = re.findall('"([^"]+)"', match[1].split(")")[0])
        digest = (
            "sha-256=:"
            + base64.b64encode(hashlib.sha256(response.content).digest()).decode()
            + ":"
        )
        if response.headers.get("content-digest") != digest:
            raise ValueError("Response content digest mismatch.")
        values = {
            "@status": str(response.status_code),
            "content-digest": digest,
            "request-id": response.headers.get("request-id", ""),
        }
        if request_id and values["request-id"] != request_id:
            raise ValueError("Response trace identity mismatch.")
        required = {"@status", "content-digest"} | (
            {"request-id"} if request_id else set()
        )
        if len(components) != len(set(components)) or not required.issubset(components):
            raise ValueError("Response signature lacks required components.")
        material = (
            "\n".join('"' + c + '": ' + values[c] for c in components)
            + '\n"@signature-params": '
            + match[1]
        )
        jwk = next(
            k
            for k in self.merchant["keys"]
            if k["kid"] == match[3] and k["crv"] == "Ed25519" and k["kty"] == "OKP"
        )
        public = Ed25519PublicKey.from_public_bytes(
            base64.urlsafe_b64decode(jwk["x"] + "=" * (-len(jwk["x"]) % 4))
        )
        encoded = response.headers["signature"]
        public.verify(
            base64.b64decode(
                encoded.removeprefix("sig1=:").removesuffix(":"), validate=True
            ),
            material.encode(),
        )

    def request(self, method, path, body=None, *, idempotency_key=None, signed=True):
        url = self.endpoint + path
        raw = (
            json.dumps(body, separators=(",", ":")).encode()
            if body is not None
            else b""
        )
        request_id = str(uuid4())
        agent = 'profile="' + self.profile + '"'
        headers = {
            "UCP-Agent": agent,
            "Request-Id": request_id,
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key or str(uuid4()),
        }
        if signed:
            digest = (
                "sha-256=:"
                + base64.b64encode(hashlib.sha256(raw).digest()).decode()
                + ":"
            )
            params = f'("@method" "@target-uri" "ucp-agent" "request-id" "content-digest");created={int(time.time())};keyid="shelfiq-platform";alg="ed25519"'
            material = f'"@method": {method}\n"@target-uri": {url}\n"ucp-agent": {agent}\n"request-id": {request_id}\n"content-digest": {digest}\n"@signature-params": {params}'
            headers.update(
                {
                    "Content-Digest": digest,
                    "Signature-Input": "sig1=" + params,
                    "Signature": "sig1=:"
                    + base64.b64encode(self.key.sign(material.encode())).decode()
                    + ":",
                }
            )
        response = self.http.request(method, url, content=raw, headers=headers)
        self.verify(response, request_id)
        return response

    def close(self):
        self.http.close()
