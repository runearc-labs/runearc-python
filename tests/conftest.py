"""Starts a real Runearc API (and worker) against a throwaway Postgres database.

Nothing in the mail path is mocked: sends go to the platform's simulated test
recipients (``delivered@test.<platform domain>``), which the API records as
delivered without an MTA.

Needs Postgres on localhost (``createdb``, ``dropdb`` and ``psql`` on PATH) and the
Rust binaries, built with::

    CARGO_TARGET_DIR=../../target/agent-sdk cargo build -p runearc-api -p runearc-worker

Override the binary directory with ``RUNEARC_BIN_DIR``. Set
``RUNEARC_KEEP_TEST_DB=1`` to keep the database after the run.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import IO, Iterator, List

import pytest

from helpers import Stack, psql
from runearc import Runearc

REPO = Path(__file__).resolve().parents[3]
BIN_DIR = Path(os.environ.get("RUNEARC_BIN_DIR") or REPO / "target/agent-sdk/debug")
PG_BASE = "postgres://localhost"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _get_json(url: str) -> dict[str, str]:
    with urllib.request.urlopen(url, timeout=10) as res:
        data: dict[str, str] = json.loads(res.read())
        return data


def _wait_healthy(url: str, proc: subprocess.Popen[bytes], log: Path) -> None:
    until = time.monotonic() + 60
    while time.monotonic() < until:
        if proc.poll() is not None:
            raise RuntimeError(
                f"runearc-api exited ({proc.returncode}):\n{log.read_text()[-4000:]}"
            )
        try:
            with urllib.request.urlopen(f"{url}/healthz", timeout=2) as res:
                if res.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"runearc-api did not become healthy:\n{log.read_text()[-4000:]}")


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    for binary in ("runearc-api", "runearc-worker"):
        if not (BIN_DIR / binary).exists():
            pytest.exit(
                f"{BIN_DIR / binary} is missing. Build it: "
                f"CARGO_TARGET_DIR={REPO / 'target/agent-sdk'} "
                "cargo build -p runearc-api -p runearc-worker",
                returncode=2,
            )
    db_name = f"runearc_sdk_test_{secrets.token_hex(6)}"
    subprocess.run(["createdb", "-h", "localhost", db_name], check=True)
    db_url = f"{PG_BASE}/{db_name}"
    work = Path(tempfile.mkdtemp(prefix="runearc-sdk-"))
    blob_dir = work / "blobs"
    blob_dir.mkdir()
    port = _free_port()
    api_url = f"http://127.0.0.1:{port}"

    # A clean environment: none of the developer's RUNEARC_* or KUMO_* settings leak in.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(work)),
        "RUST_LOG": "warn",
        "DATABASE_URL": db_url,
        "BLOB_URL": f"file://{blob_dir}",
        "RUNEARC_SECRET_KEY": secrets.token_hex(32),
        "LISTEN_ADDR": f"127.0.0.1:{port}",
        "RUNEARC_ALLOW_LOCAL_WEBHOOKS": "1",
        # The platform refuses to start without a provider. Test recipients are recorded
        # as delivered without touching it, so this relay is never connected to.
        "RUNEARC_OUTBOUND_PROVIDER": "smtp",
        "RUNEARC_SMTP_RELAY": "127.0.0.1:1",
    }
    children: List[subprocess.Popen[bytes]] = []
    logs: List[IO[bytes]] = []

    def start(binary: str) -> Path:
        log = work / f"{binary}.log"
        handle = log.open("wb")
        logs.append(handle)
        children.append(
            subprocess.Popen(
                [str(BIN_DIR / binary)], env=env, stdout=handle, stderr=subprocess.STDOUT
            )
        )
        return log

    try:
        # Bootstrap first: it runs the migrations and prints the key once (never logged here).
        out = subprocess.run(
            [str(BIN_DIR / "runearc-api"), "bootstrap", f"sdk-{secrets.token_hex(3)}"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        key = re.search(r"api key \(shown once\): (\S+)", out)
        org = re.search(r"org: (org_\S+)", out)
        if not key or not org:
            raise RuntimeError("bootstrap did not print an org and a key")
        api_key, org_id = key.group(1), org.group(1)
        # Room for the CRUD tests: the free plan allows 3 inboxes and 3 domains.
        psql(db_url, f"UPDATE orgs SET plan = 'scale' WHERE id = '{org_id}'")

        api_log = start("runearc-api")
        _wait_healthy(api_url, children[0], api_log)
        start("runearc-worker")

        platform = _get_json(f"{api_url}/v1/platform")

        # One verified sending domain for the whole run, as the Rust tests do it: added
        # through the API, marked verified with one UPDATE (verification needs public DNS).
        domain = f"sdk-{secrets.token_hex(4)}.example.com"
        with Runearc(api_key, base_url=api_url) as admin:
            domain_id = admin.domains.create(domain)["id"]
        psql(
            db_url,
            f"UPDATE domains SET status = 'verified', verified_at = now() WHERE id = '{domain_id}'",
        )

        yield Stack(
            api_url=api_url,
            api_key=api_key,
            db_url=db_url,
            org_id=org_id,
            domain=domain,
            domain_id=domain_id,
            test_domain=platform["test_domain"],
            shared_domain=platform["shared_domain"],
        )
    finally:
        for child in children:
            child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        for handle in logs:
            handle.close()
        if os.environ.get("RUNEARC_KEEP_TEST_DB") != "1":
            subprocess.run(
                ["dropdb", "-h", "localhost", "--force", "--if-exists", db_name], check=False
            )
        shutil.rmtree(work, ignore_errors=True)


@pytest.fixture(scope="session")
def mb(stack: Stack) -> Iterator[Runearc]:
    with Runearc(stack.api_key, base_url=stack.api_url) as client:
        yield client
