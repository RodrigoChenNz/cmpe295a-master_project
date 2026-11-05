"""RFC 9421 Ed25519 profile binding the request to its advertised platform."""

import base64
import hashlib
import hmac
import re
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from fastapi import HTTPException


def b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def private(path):
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("UCP signing key must be Ed25519.")
    return key


def jwk(path, kid):
    key = (
        private(path)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": b64url(key),
        "kid": kid,
        "use": "sig",
        "alg": "EdDSA",
    }


def content_digest(body):
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"


def base(components, values, params):
    return (
        "\n".join('"' + name + '": ' + values[name] for name in components)
        + '\n"@signature-params": '
        + params
    ).encode()


def sign(body, values, key_path, *, kid, created=None):
    created = int(time.time()) if created is None else created
    components = list(values)
    values = {**values, "content-digest": content_digest(body)}
    components.append("content-digest")
    params = (
        "("
        + " ".join('"' + name + '"' for name in components)
        + f');created={created};keyid="{kid}";alg="ed25519"'
    )
    signature = private(key_path).sign(base(components, values, params))
    return {
        "Content-Digest": values["content-digest"],
        "Signature-Input": "sig1=" + params,
        "Signature": "sig1=:" + base64.b64encode(signature).decode() + ":",
    }


def verify(body, values, headers, keys):
    headers = {name.lower(): value for name, value in headers.items()}
    raw = headers.get("signature-input", "")
    match = re.fullmatch(
        r'sig1=(\((?:"[^"\r\n]+" ?)+\);created=([0-9]{1,12});keyid="([^"\r\n]{1,200})";alg="ed25519")',
        raw,
    )
    if not match or abs(time.time() - int(match[2])) > 300:
        raise HTTPException(401, "signature_invalid: missing, malformed or expired signature.")
    components = re.findall(r'"([^"\r\n]+)"', match[1].split(")")[0])
    if (
        len(set(components)) != len(components)
        or not set(values).issubset(components)
        or "content-digest" not in components
    ):
        raise HTTPException(
            401, "signature_invalid: required message components were not signed."
        )
    digest = headers.get("content-digest", "")
    if not digest.isascii() or not hmac.compare_digest(digest, content_digest(body)):
        raise HTTPException(400, "digest_mismatch")
    values = {**values, "content-digest": digest}
    if any(name not in values for name in components):
        raise HTTPException(401, "signature_invalid: unsupported covered component.")
    key = next((key for key in keys if key.get("kid") == match[3]), None)
    if not key or key.get("kty") != "OKP" or key.get("crv") != "Ed25519":
        raise HTTPException(401, "key_not_found")
    try:
        public = Ed25519PublicKey.from_public_bytes(
            base64.urlsafe_b64decode(key["x"] + "=" * (-len(key["x"]) % 4))
        )
        signature = headers.get("signature", "")
        if not re.fullmatch(r"sig1=:[A-Za-z0-9+/]+={0,2}:", signature):
            raise ValueError("Invalid encoding")
        public.verify(
            base64.b64decode(signature[6:-1], validate=True), base(components, values, match[1])
        )
    except (InvalidSignature, ValueError, KeyError):
        raise HTTPException(401, "signature_invalid")
