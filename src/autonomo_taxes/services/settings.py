"""Account/backup application services shared by CLI and HTTP."""

from typing import Any
from ..account_settings import read_settings, save_profile, save_backups


class SettingsService:
    def settings(self) -> dict[str, Any]:
        return read_settings(self.config.database, self.config.private_root)

    def edit_profile(self, payload: dict, *, actor: str | None = None) -> dict:
        return save_profile(self.config.database, payload, actor=actor)

    def save_backup_settings(self, payload: dict) -> dict:
        return save_backups(self.config.database, self.config.private_root, payload)
