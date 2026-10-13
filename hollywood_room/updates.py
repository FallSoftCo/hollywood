"""Authenticated, immutable Android update artifacts from an owner-published directory."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field


class Release(BaseModel):
    model_config = ConfigDict(extra="forbid")
    packageName: str = Field(pattern=r"^co\.fallsoft\.losangelex$")
    versionCode: int = Field(ge=1)
    versionName: str = Field(min_length=1, max_length=100)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(gt=0)
    signingCertificateSha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Updates:
    def __init__(self, directory):
        self.directory = Path(directory).resolve() if directory else None
        self.verified = {}

    def release(self, digest=None):
        if self.directory is None:
            return None
        if digest is not None and (
            len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("Invalid update digest")
        manifest = self.directory / (f"{digest}.json" if digest else "current.json")
        if digest is None and not manifest.exists():
            return None
        if manifest.is_symlink():
            raise ValueError("Update manifest must be a regular file")
        with manifest.open("rb") as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError("Update manifest is too large")
        release = Release.model_validate_json(raw)
        if digest and release.sha256 != digest:
            raise ValueError("Update manifest digest mismatch")
        apk = self.directory / f"{release.sha256}.apk"
        if apk.is_symlink():
            raise ValueError("Update artifact must be a regular file")
        stat = apk.stat()
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if stat.st_size != release.size:
            raise ValueError("Update artifact size mismatch")
        if self.verified.get(release.sha256) != signature:
            digest_hash = hashlib.sha256()
            with apk.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest_hash.update(chunk)
            if digest_hash.hexdigest() != release.sha256:
                raise ValueError("Update artifact checksum mismatch")
            # Cache only the latest verification, not an unbounded release history.
            self.verified = {release.sha256: signature}
        return release, apk

    def manifest(self):
        result = self.release()
        if result is None:
            return {"available": False}
        release, _ = result
        return {
            "available": True,
            **release.model_dump(),
            "apkPath": f"/hollywood/v2/update/apk/{release.sha256}",
        }


def _publish_snapshot(apk, directory, *, aapt, apksigner, expected_signer):
    """Verify package/signature locally, then atomically publish a content-addressed APK."""
    import re

    apk, directory = Path(apk).resolve(), Path(directory).resolve()
    signature = subprocess.run(
        [str(apksigner), "verify", "--print-certs", str(apk)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    certificates = re.findall(
        r"Signer #\d+ certificate SHA-256 digest: ([a-fA-F0-9]{64})", signature
    )
    if len(certificates) != 1 or certificates[0].lower() != expected_signer.lower():
        raise ValueError("APK signer does not match the expected installed-app certificate")
    badging = subprocess.run(
        [str(aapt), "dump", "badging", str(apk)], capture_output=True, text=True, check=True
    ).stdout
    package = re.search(
        r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", badging
    )
    if not package:
        raise ValueError("Could not read APK package/version")
    digest = hashlib.sha256()
    with apk.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    release = Release(
        packageName=package[1],
        versionCode=int(package[2]),
        versionName=package[3],
        sha256=digest.hexdigest(),
        size=apk.stat().st_size,
        signingCertificateSha256=certificates[0].lower(),
    )
    directory.mkdir(parents=True, exist_ok=True)
    previous = directory / "current.json"
    if previous.exists():
        current = Release.model_validate_json(previous.read_bytes())
        if current.versionCode > release.versionCode or (
            current.versionCode == release.versionCode and current.sha256 != release.sha256
        ):
            raise ValueError("Refusing a downgrade or changed APK with the same versionCode")
    target = directory / f"{release.sha256}.apk"
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            with apk.open("rb") as source:
                shutil.copyfileobj(source, stream)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.chmod(0o444)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    raw = release.model_dump_json(indent=2) + "\n"
    for path in (directory / f"{release.sha256}.json", previous):
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
                temporary.chmod(0o444)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return Updates(directory).manifest()


def publish(apk, directory, *, aapt, apksigner, expected_signer):
    # Verify a private snapshot so a concurrent build cannot change bytes after signing checks.
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hollywood-apk-") as temporary:
        snapshot = Path(temporary) / "release.apk"
        shutil.copyfile(apk, snapshot)
        with FileLock(str(directory / ".publish.lock"), timeout=10):
            return _publish_snapshot(
                snapshot, directory, aapt=aapt, apksigner=apksigner, expected_signer=expected_signer
            )


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--aapt", type=Path, required=True)
    parser.add_argument("--apksigner", type=Path, required=True)
    parser.add_argument("--expected-signer", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            publish(
                args.apk,
                args.directory,
                aapt=args.aapt,
                apksigner=args.apksigner,
                expected_signer=args.expected_signer,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
