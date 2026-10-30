import os
import pytest_asyncio
import pytest


@pytest_asyncio.fixture
async def database(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    from urllib.parse import urlparse

    if not urlparse(url).path.endswith("_test"):
        raise RuntimeError(
            "Refusing to reset a database whose name does not end in _test"
        )
    from app import db, migrations, bus
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", url)
    await db.connect()
    await migrations.apply()
    await db.execute(
        "TRUNCATE products,users,store_settings,change_limits,message_outbox,pending_explanations RESTART IDENTITY CASCADE"
    )
    await db.execute(
        "INSERT INTO store_settings(id,mqtt_host,mqtt_port,mqtt_tls) VALUES(TRUE,'localhost',11883,FALSE)"
    )
    await db.execute(
        "INSERT INTO change_limits(id,max_changes_per_day,min_hours_between) VALUES(TRUE,1000,0)"
    )
    await db.execute("""INSERT INTO products(product_id,name,display_name,unit_cost,min_price,max_price,auto_approve_pct,stock_on_hand)
        VALUES('SKU-001','Test milk','MILK',8,8.4,15.2,5,200)""")
    await db.execute("""INSERT INTO price_history(product_id,price,reason_tag,approval,approved_by,ts)
        VALUES('SKU-001',10.40,'Initial price','manager','fixture',now()-interval '1 day')""")
    await db.execute(
        "INSERT INTO shelf_labels(label_id,kind,product_id) VALUES('SHLF-001','lcd','SKU-001')"
    )
    bus._client = None
    bus._broker_state["connected"] = False
    yield db
    await db.disconnect()
