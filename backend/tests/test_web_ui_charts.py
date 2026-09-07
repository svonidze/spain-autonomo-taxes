from autonomo_test_support.paths import REPO_ROOT
from pathlib import Path
from autonomo_taxes.ui_assets import UiAssets


def test_chart_resources_are_part_of_the_built_application():
    assets=UiAssets(REPO_ROOT / "backend/src/autonomo_taxes/web_ui")
    assert any(name.endswith(".js") for name in assets.files)
