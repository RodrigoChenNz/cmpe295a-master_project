"""Optional real-broker TLS 1.3 and publisher ACL tests."""

import asyncio
import os
from pathlib import Path
import aiomqtt
import pytest
from retail.mqtt import options


@pytest.fixture
def tls_port():
    port = os.getenv("TLS_TEST_PORT")
    if not port:
        pytest.skip("Set TLS_TEST_PORT after starting the TLS test broker")
    return int(port)


def client(port, role=None):
    certs = Path("secrets/certs")
    return aiomqtt.Client(
        "localhost",
        port=port,
        **options(
            tls=True,
            cafile=str(certs / "ca.pem"),
            certfile=str(certs / f"{role}.pem") if role else None,
            keyfile=str(certs / f"{role}.key") if role else None,
        ),
    )


async def test_tls_rejects_missing_client_certificate(tls_port):
    with pytest.raises((aiomqtt.MqttError, OSError)):
        async with client(tls_port):
            pytest.fail("Broker accepted an unauthenticated client")


async def test_only_api_can_publish_approved_prices(tls_port):
    async with client(tls_port, "device") as device:
        await device.subscribe("retail/price/SKU-999", qos=1)
        async with client(tls_port, "api") as api:
            await api.publish("retail/price/SKU-999", "authorized", qos=1)
        msg = await asyncio.wait_for(anext(device.messages), 2)
        assert bytes(msg.payload) == b"authorized"
        async with client(tls_port, "pricing") as agent:
            try:
                await agent.publish("retail/price/SKU-999", "unauthorized", qos=1)
            except aiomqtt.MqttError:
                pass
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(device.messages), 0.3)


async def test_api_can_forward_states_but_pricing_cannot(tls_port):
    async with client(tls_port, "forecaster") as subscriber:
        await subscriber.subscribe("retail/state/SKU-999", qos=1)
        async with client(tls_port, "api") as api:
            await api.publish("retail/state/SKU-999", "authorized-store-input", qos=1)
        msg = await asyncio.wait_for(anext(subscriber.messages), 2)
        assert bytes(msg.payload) == b"authorized-store-input"
        async with client(tls_port, "pricing") as agent:
            try:
                await agent.publish("retail/state/SKU-999", "unauthorized", qos=1)
            except aiomqtt.MqttError:
                pass
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(subscriber.messages), 0.3)
