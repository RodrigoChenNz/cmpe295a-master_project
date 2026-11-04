#!/usr/bin/env python3
"""Create private Ed25519 signing identities and a locally trusted test CA.

Does not install a CA into the OS/browser or overwrite existing keys/certs.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import ipaddress
import secrets
import os

from cryptography import x509
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID


def save_key(path, key):
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)


def main():
    root = Path("secrets/ucp")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("business", "platform"):
        if not (root / (name + ".pem")).exists():
            save_key(root / (name + ".pem"), ed25519.Ed25519PrivateKey.generate())
    ca_path = root / "ca.pem"
    ca_key_path = root / "ca.key"
    if ca_path.exists() != ca_key_path.exists():
        raise SystemExit(
            "Incomplete test CA: preserve and repair the existing files before retrying."
        )
    now = datetime.now(timezone.utc)
    if not ca_path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        save_key(ca_key_path, key)
        name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "ShelfIQ local HTTPS test CA")]
        )
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(key, hashes.SHA256())
        )
        ca_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    if not (root / "server.pem").exists():
        ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
        ca_key = serialization.load_pem_private_key(
            ca_key_path.read_bytes(), password=None
        )
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        save_key(root / "server.key", key)
        cert = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
            )
            .issuer_name(ca.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=90))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        (root / "server.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    env = Path(".env.deploy.local")
    if not env.exists():
        env.write_text(
            "STORE_DOMAIN=localhost\nPUBLIC_URL=https://localhost:8443\nSCHEMA_ORIGIN=https://schemas.shelfiq.test:8443\nDB_PASSWORD="
            + secrets.token_urlsafe(36)
            + "\nSHELFIQ_BOOTSTRAP_PASSWORD="
            + secrets.token_urlsafe(36)
            + "\nCOMMERCE_WEBHOOK_KEY="
            + secrets.token_urlsafe(48)
            + "\n"
        )
        env.chmod(0o600)
    current = env.read_text()
    for name, value in [("RUN_UID", os.getuid()), ("RUN_GID", os.getgid())]:
        if not any(line.startswith(name + "=") for line in current.splitlines()):
            current += f"{name}={value}\n"
    env.write_text(current)
    env.chmod(0o600)
    print(
        "Private UCP identities, local HTTPS certificates and .env.deploy.local are ready. CA trust was not installed. Use secrets/ucp/ca.pem explicitly in test clients."
    )


if __name__ == "__main__":
    main()
