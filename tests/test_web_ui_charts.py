from pathlib import Path
from autonomo_taxes.ui_assets import UiAssets


def test_chart_resources_are_part_of_the_built_application():
    assets=UiAssets(Path(__file__).resolve().parents[1] / "src/autonomo_taxes/web_ui")
    assert any(name.endswith(".js") for name in assets.files)
