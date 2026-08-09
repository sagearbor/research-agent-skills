"""Drop-in Azure credential that cannot hang off-Azure.

Copy this file into a project as ``azure_credential.py`` and call
``build_credential()`` everywhere ``DefaultAzureCredential()`` appears.

Why it exists: ``DefaultAzureCredential`` includes ``ManagedIdentityCredential``,
which probes the Instance Metadata Service at ``169.254.169.254``. That is a
link-local address meaningful only inside Azure compute. Off Azure the packets
are not refused, they are dropped — so the probe HANGS (minutes, worse on a
VPN) instead of failing fast. On Azure the same credential is the only one that
can work. Hence: decide by environment, not by hope.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from azure.identity import DefaultAzureCredential

T = TypeVar("T")

#: Environment variables Azure hosts inject when a managed identity IS present.
#:
#: * ``IDENTITY_ENDPOINT`` — App Service, Functions, Container Apps
#: * ``MSI_ENDPOINT`` — older App Service revisions
#: * ``AZURE_CLIENT_ID`` — an explicitly configured user-assigned identity
#:
#: If a host you use is missing from this list the symptom is the reverse bug —
#: managed identity excluded ON Azure — so verify in both directions after any
#: change here.
AZURE_IDENTITY_MARKERS = ("IDENTITY_ENDPOINT", "MSI_ENDPOINT", "AZURE_CLIENT_ID")

#: Seconds allowed to the credentials that shell out (Azure CLI, PowerShell).
#: Does NOT bound the IMDS probe — nothing does; that one must be excluded.
CREDENTIAL_PROCESS_TIMEOUT = 15


def managed_identity_available() -> bool:
    """True when this process is running somewhere a managed identity exists."""
    return any(os.environ.get(name) for name in AZURE_IDENTITY_MARKERS)


def build_credential() -> Any:
    """A credential appropriate to wherever this code is actually running.

    On Azure: managed identity enabled (it is the only thing that will work).
    Off Azure: managed identity excluded (it is the only thing that can hang).
    """
    on_azure = managed_identity_available()
    return DefaultAzureCredential(
        exclude_managed_identity_credential=not on_azure,
        process_timeout=CREDENTIAL_PROCESS_TIMEOUT,
    )


def bounded(fn: Callable[[], T], seconds: float) -> T | None:
    """Run ``fn``, or abandon it after ``seconds``. ``None`` means it timed out.

    A raw DAEMON thread, deliberately. The obvious implementation is wrong::

        with ThreadPoolExecutor(max_workers=1) as pool:      # WRONG
            return pool.submit(fn).result(timeout=seconds)

    ``result(timeout=…)`` raises on schedule, but exiting the ``with`` block
    calls ``shutdown(wait=True)``, which blocks all over again on the thread
    that just timed out — so the timeout appears to do nothing at all. A daemon
    thread can simply be left behind, and will not hold up interpreter exit.

    This is a seatbelt for health probes and startup checks. If it fires during
    normal operation, the credential configuration is still wrong.
    """
    box: list[Any] = []
    thread = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    thread.start()
    thread.join(seconds)
    return box[0] if box else None


__all__ = [
    "AZURE_IDENTITY_MARKERS",
    "CREDENTIAL_PROCESS_TIMEOUT",
    "bounded",
    "build_credential",
    "managed_identity_available",
]
