"""MQTT v5 connection options, optionally TLS 1.3 with client certificates."""

import os
import ssl
import aiomqtt


def options(
    *, tls=None, cafile=None, certfile=None, keyfile=None, username=None, password=None
):
    enabled = os.getenv("MQTT_TLS", "false").lower() == "true" if tls is None else tls
    context = None
    if enabled:
        context = ssl.create_default_context(
            cafile=cafile or os.getenv("MQTT_CA_FILE") or None
        )
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        certfile = certfile or os.getenv("MQTT_CERT_FILE")
        keyfile = keyfile or os.getenv("MQTT_KEY_FILE")
        if certfile:
            context.load_cert_chain(certfile, keyfile)
    return dict(
        protocol=aiomqtt.ProtocolVersion.V5,
        tls_context=context,
        username=username or os.getenv("MQTT_USERNAME"),
        password=password or os.getenv("MQTT_PASSWORD"),
    )
