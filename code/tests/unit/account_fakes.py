"""The merchant accounts' test doubles (`test_accounts.py`, `test_platform_accounts.py`,
`test_session_guard.py`)."""

from merchant_platform.accounts import MemoryOwnerStore


class ForgettingStore(MemoryOwnerStore):
    """An owner store whose merchant can be deleted, as `merchant_owners` loses its row when the
    registry's does (ON DELETE CASCADE)."""

    def forget(self, slug: str) -> None:
        self._owners.pop(slug, None)
