"""The application boundary shared by CLI and optional HTTP transport."""

from dataclasses import dataclass
from pathlib import Path
from ..legacy_paths import LegacyPathResolver
from .queries import QueryService
from .contacts import ContactsService
from .financials import FinancialsService
from .invoices import InvoiceService
from .intake_service import IntakeService
from .settings import SettingsService


@dataclass(frozen=True)
class RuntimeConfig:
    project_root: Path
    database: Path
    inbox_root: Path | None
    archive_root: Path | None
    cache_root: Path
    read_only_document_roots: tuple[Path, ...] = ()
    legacy_path_map_file: Path | None = None
    private_root: Path | None = None


class AccountingService(
    QueryService,
    ContactsService,
    FinancialsService,
    InvoiceService,
    IntakeService,
    SettingsService,
):
    def __init__(self, config: RuntimeConfig):
        if not config.database.is_file():
            raise FileNotFoundError("Accounting database is unavailable")
        self.config = config
        self.legacy_path_resolver = LegacyPathResolver.from_json_file(
            config.legacy_path_map_file
        )
