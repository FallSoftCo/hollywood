import hashlib
import json
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession, web

from hollywood_room.server import Service, application
from hollywood_room.store import Store
from hollywood_room.updates import Release, Updates, publish

SIGNER = "a" * 64


def fixture_release(directory, data=b"fixture-apk", version=8):
    directory.mkdir(exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    release = Release(
        packageName="co.fallsoft.losangelex",
        versionCode=version,
        versionName="0.4.0",
        sha256=digest,
        size=len(data),
        signingCertificateSha256=SIGNER,
    )
    (directory / f"{digest}.apk").write_bytes(data)
    raw = release.model_dump_json()
    (directory / f"{digest}.json").write_text(raw)
    (directory / "current.json").write_text(raw)
    return release


@pytest.fixture
async def endpoint(tmp_path):
    directory = tmp_path / "updates"
    store = Store(tmp_path / "room.db")
    runner = web.AppRunner(application(Service(store, "test-token", update_directory=directory)))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    async with ClientSession() as client:
        yield (
            directory,
            client,
            f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/hollywood/v2",
        )
    await runner.cleanup()
    store.close()


async def test_update_auth_integrity_and_immutable_download(endpoint):
    directory, client, base = endpoint
    first = fixture_release(directory)
    for path in ("/update", f"/update/apk/{first.sha256}"):
        response = await client.get(base + path)
        assert response.status == 401
    client.headers["Authorization"] = "Bearer test-token"
    response = await client.get(base + "/update")
    manifest = await response.json()
    assert manifest == {
        "available": True,
        **first.model_dump(),
        "apkPath": f"/hollywood/v2/update/apk/{first.sha256}",
    }
    assert response.headers["Cache-Control"] == "private, no-store"
    fixture_release(directory, b"new-apk", version=9)
    # A manifest already fetched remains downloadable after publication changes.
    response = await client.get(base + f"/update/apk/{first.sha256}")
    assert response.status == 200
    assert await response.read() == b"fixture-apk"
    assert response.headers["Content-Type"] == "application/vnd.android.package-archive"
    (directory / f"{first.sha256}.apk").write_bytes(b"corrupt-apk")
    response = await client.get(base + f"/update/apk/{first.sha256}")
    assert response.status == 503
    assert b"corrupt-apk" not in await response.read()


async def test_unpublished_bad_manifest_and_arbitrary_paths_fail_safe(endpoint):
    directory, client, base = endpoint
    client.headers["Authorization"] = "Bearer test-token"
    response = await client.get(base + "/update")
    assert await response.json() == {"available": False}
    fixture_release(directory)
    (directory / "current.json").write_text('{"apkPath":"/etc/passwd"}')
    response = await client.get(base + "/update")
    assert response.status == 503
    for digest in ("not-a-sha", "b" * 64):
        response = await client.get(base + f"/update/apk/{digest}")
        assert response.status in (404, 503)


def test_manifest_rejects_package_signer_and_size(tmp_path):
    directory = tmp_path / "updates"
    release = fixture_release(directory)
    for key, value in (
        ("packageName", "other.app"),
        ("signingCertificateSha256", "no"),
        ("size", -1),
    ):
        raw = {**release.model_dump(), key: value}
        (directory / "current.json").write_text(json.dumps(raw))
        with pytest.raises(ValueError):
            Updates(directory).manifest()


def test_publisher_verifies_signature_and_rejects_downgrade(tmp_path, monkeypatch):
    apk = tmp_path / "source.apk"
    apk.write_bytes(b"signed-apk")
    directory = tmp_path / "updates"

    def execute(command, **kwargs):
        if "verify" in command:
            return Mock(stdout=f"Signer #1 certificate SHA-256 digest: {SIGNER}\n")
        return Mock(
            stdout="package: name='co.fallsoft.losangelex' versionCode='8' versionName='0.4.0'\n"
        )

    monkeypatch.setattr("hollywood_room.updates.subprocess.run", execute)
    with pytest.raises(ValueError, match="signer"):
        publish(apk, directory, aapt="aapt", apksigner="apksigner", expected_signer="b" * 64)
    assert not (directory / "current.json").exists()
    result = publish(apk, directory, aapt="aapt", apksigner="apksigner", expected_signer=SIGNER)
    assert result["versionCode"] == 8
    assert (
        publish(apk, directory, aapt="aapt", apksigner="apksigner", expected_signer=SIGNER)
        == result
    )
    previous = (directory / "current.json").read_bytes()
    apk.write_bytes(b"different-signed-apk")
    with pytest.raises(ValueError, match="same versionCode"):
        publish(apk, directory, aapt="aapt", apksigner="apksigner", expected_signer=SIGNER)
    assert (directory / "current.json").read_bytes() == previous
