---
name: azure-credential-guard
description: Fix and prevent DefaultAzureCredential hanging for minutes on a laptop while working correctly on Azure. Diagnoses the managed-identity IMDS probe that hangs instead of failing, supplies a drop-in build_credential() that works in BOTH directions (laptop and Container Apps/Functions/VM), and warns about the ThreadPoolExecutor timeout that silently does not time out. Use when an azure-identity, azure-storage-blob, Key Vault, or Azure SDK call hangs, stalls, times out, or "works on Azure but not locally"; when adding blob/Key Vault access to a service that must also run locally; or when someone reaches for a timeout wrapper around a hanging Azure call.
---

# azure-credential-guard

`DefaultAzureCredential()` is documented as "tries each credential in order."
The part that costs people an afternoon is what "tries" means for one of them.

## The symptom

A blob or Key Vault call that works fine in the portal hangs on a laptop —
often for **several minutes** — then may or may not succeed. The same image
deployed to Azure Container Apps works instantly. No exception, no log line,
no traceback to search for. Adding a `timeout=` to the SDK call does not help.

## The cause

`DefaultAzureCredential` includes `ManagedIdentityCredential`, which probes the
**Instance Metadata Service** at `http://169.254.169.254`. That address is a
link-local address that only means anything inside Azure compute.

Off Azure, packets to it are not refused — they go nowhere. A refused
connection returns in milliseconds; a black hole returns when something times
out. On a VPN this gets worse, because the VPN client may accept and hold the
route rather than dropping it fast.

So the credential is not broken. It is waiting, exactly as designed, for an
answer that will never come.

## The fix

Decide whether managed identity is even possible **before** offering it, using
the environment variables Azure's own hosts inject. If none is present, you are
not on Azure compute, and the IMDS probe can only waste time.

```python
"""Build an Azure credential that cannot hang off-Azure."""
from __future__ import annotations

import os
from typing import Any

from azure.identity import DefaultAzureCredential

#: Set by the host when a managed identity is actually available.
#: IDENTITY_ENDPOINT — App Service / Functions / Container Apps
#: MSI_ENDPOINT      — older App Service
#: AZURE_CLIENT_ID   — an explicitly configured user-assigned identity
AZURE_IDENTITY_MARKERS = ("IDENTITY_ENDPOINT", "MSI_ENDPOINT", "AZURE_CLIENT_ID")

#: Bounds the Azure CLI / PowerShell credentials, which shell out.
CREDENTIAL_PROCESS_TIMEOUT = 15


def managed_identity_available() -> bool:
    return any(os.environ.get(name) for name in AZURE_IDENTITY_MARKERS)


def build_credential() -> Any:
    """DefaultAzureCredential with the IMDS probe excluded when off Azure.

    On Azure the managed identity is the ONLY credential that will work, so it
    must stay enabled there. Off Azure it is the only one that can hang, so it
    is excluded. One function, correct in both directions.
    """
    on_azure = managed_identity_available()
    return DefaultAzureCredential(
        exclude_managed_identity_credential=not on_azure,
        process_timeout=CREDENTIAL_PROCESS_TIMEOUT,
    )
```

Use `build_credential()` everywhere instead of `DefaultAzureCredential()`.

## The trap people hit while debugging this

The instinct is to wrap the hanging call in a timeout. The obvious way does not
work:

```python
# WRONG — this still blocks for the full hang.
with ThreadPoolExecutor(max_workers=1) as pool:
    future = pool.submit(slow_azure_call)
    return future.result(timeout=15)
```

`future.result(timeout=15)` raises on schedule, but leaving the `with` block
calls `pool.shutdown(wait=True)`, which blocks again on the very thread that
just timed out. The timeout appears to do nothing.

If you genuinely need a hard bound (a health probe, a startup check), use a
daemon thread and walk away from it:

```python
import threading
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def bounded(fn: Callable[[], T], seconds: float) -> T | None:
    """Run ``fn``, or give up after ``seconds``. Returns None on timeout.

    A daemon thread, deliberately: the abandoned thread cannot keep the process
    alive at exit, which is the property ThreadPoolExecutor will not give you.
    """
    box: list[Any] = []
    thread = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    thread.start()
    thread.join(seconds)
    return box[0] if box else None
```

Treat this as a seatbelt, not the fix. If a bound is firing routinely, the
credential is still wrong.

## Verify BOTH directions

Fixing the laptop by disabling managed identity everywhere is the failure mode
this skill exists to prevent — it moves the outage to production, where it is
harder to see. Check both:

**Laptop** — should return promptly, not after minutes:

```bash
python -c "
import time
from your_pkg.azure_credential import build_credential, managed_identity_available
print('managed identity available:', managed_identity_available())
t = time.time(); build_credential().get_token('https://storage.azure.com/.default')
print(f'token in {time.time()-t:.1f}s')
"
```

**On Azure** — exercise a real call through the deployed app (a blob listing, a
Key Vault read). `managed_identity_available()` must be `True` there. If the
call now fails on Azure, the marker list is wrong for that host: log
`os.environ` keys matching `IDENTITY|MSI|AZURE_` and add the one it uses.

## Notes

- Private endpoints are a **separate** failure. If the storage account or vault
  is private-endpointed, you also need the corporate VPN or you will get a DNS
  or connection error — a fast, honest error, not a hang. Different symptom,
  different fix; do not conflate them.
- `exclude_managed_identity_credential` is a `DefaultAzureCredential` argument.
  If you construct `ManagedIdentityCredential` directly, this does not apply —
  you have already chosen it.
- `process_timeout` bounds the CLI-based credentials. It does nothing for IMDS,
  which is why exclusion, not a timeout, is the actual fix.
