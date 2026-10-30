"""Generate local development CA and per-role mTLS identities, never overwrite keys."""

from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
private = root / "secrets"
if private.exists():
    raise SystemExit("secrets already exists; refusing to overwrite broker identities")
private.mkdir(mode=0o700)
certs = private / "certs"
certs.mkdir(mode=0o755)


def run(*args):
    subprocess.run(
        ["openssl", *map(str, args)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


run(
    "req",
    "-x509",
    "-newkey",
    "rsa:2048",
    "-nodes",
    "-keyout",
    certs / "ca.key",
    "-out",
    certs / "ca.pem",
    "-days",
    "365",
    "-subj",
    "/CN=ShelfIQ Development CA",
)
for role in ["server", "api", "simulator", "forecaster", "pricing", "device"]:
    run(
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        certs / f"{role}.key",
        "-out",
        certs / f"{role}.csr",
        "-subj",
        f"/CN={role}",
    )
    ext = certs / f"{role}.ext"
    ext.write_text(
        "subjectAltName=DNS:mosquitto,DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n"
        if role == "server"
        else "extendedKeyUsage=clientAuth\n"
    )
    run(
        "x509",
        "-req",
        "-in",
        certs / f"{role}.csr",
        "-CA",
        certs / "ca.pem",
        "-CAkey",
        certs / "ca.key",
        "-CAcreateserial",
        "-out",
        certs / f"{role}.pem",
        "-days",
        "365",
        "-extfile",
        ext,
    )
    # The containing secrets/ directory stays 0700 on the host. Docker mounts
    # the nested certs/ directory read-only for its non-root service users.
    (certs / f"{role}.key").chmod(0o644)
(certs / "ca.key").chmod(0o600)
(private / "mosquitto.conf").write_text("""persistence true
persistence_location /mosquitto/data/
log_dest stdout
listener 8883
cafile /mosquitto/certs/ca.pem
certfile /mosquitto/certs/server.pem
keyfile /mosquitto/certs/server.key
require_certificate true
use_identity_as_username true
allow_anonymous false
tls_version tlsv1.3
acl_file /mosquitto/config/acl
""")
(private / "acl").write_text("""user api
topic read retail/proposal/+
topic read retail/explain/+
topic read retail/inventory/+
topic read retail/state/+
topic read retail/shelf/+
topic write retail/price/+
topic write retail/state/+
topic write retail/guardrail/+
topic write retail/decision/+
topic write retail/label/+/+
user simulator
topic write retail/state/+
user forecaster
topic read retail/state/+
topic write retail/inventory/+
user pricing
topic read retail/state/+
topic read retail/inventory/+
topic read retail/guardrail/+
topic read retail/price/+
topic read retail/decision/+
topic write retail/proposal/+
topic write retail/explain/+
user device
topic read retail/price/+
topic read retail/label/+/+
topic write retail/shelf/+
""")
for file in [private / "acl", private / "mosquitto.conf"]:
    file.chmod(0o644)
print(
    "Created secrets/ with local TLS 1.3 identities. Keep this directory private and uncommitted."
)
