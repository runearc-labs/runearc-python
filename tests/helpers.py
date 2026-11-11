"""Small helpers shared by the test modules."""

from __future__ import annotations

import random
import string
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Stack:
    """The running API and the facts the tests need about it."""

    api_url: str
    api_key: str
    db_url: str
    org_id: str
    domain: str
    domain_id: str
    test_domain: str
    shared_domain: str

    def test_address(self, local: str = "delivered") -> str:
        """A simulated recipient: delivered (or bounced/complained) without an MTA."""
        return f"{local}@{self.test_domain}"

    def sender(self, local: str = "app") -> str:
        return f"SDK Tests <{local}@{self.domain}>"


def psql(db_url: str, sql: str) -> str:
    out = subprocess.run(
        ["psql", db_url, "-v", "ON_ERROR_STOP=1", "-tA", "-c", sql],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def unique(prefix: str) -> str:
    tail = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{prefix} {tail}"


def eventually(fn: Callable[[], Optional[T]], seconds: float = 15.0) -> T:
    """Polls ``fn`` until it returns something other than ``None``."""
    until = time.monotonic() + seconds
    last: Optional[Exception] = None
    while time.monotonic() < until:
        try:
            value = fn()
            if value is not None:
                return value
        except Exception as exc:
            last = exc
        time.sleep(0.15)
    detail = f": {last}" if last else ""
    raise AssertionError(f"condition not met in {seconds} s{detail}")
