from pathlib import Path
from autonomo_taxes.ui_assets import UiAssets


def test_chart_resources_are_part_of_the_built_application():
    assets=UiAssets(Path(__file__).resolve().parents[1] / "packages/ui/src/autonomo_taxes_ui/dist")
    assert any(name.endswith(".js") for name in assets.files)


# Requires the separately built optional UI assets.
import pytest
pytestmark = pytest.mark.web
