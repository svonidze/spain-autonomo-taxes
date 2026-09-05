import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("ui_healthcheck", Path(__file__).resolve().parents[1] / "ops/healthcheck.py")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


@pytest.mark.parametrize("script,style", [("/app.js", "/styles.css"), ("/ui-assets/app-hash.js", "/ui-assets/app-hash.css")])
def test_old_and_new_shells_are_checked_from_their_resource_references(script, style):
    requested = []
    class Response:
        status = 200
        def __init__(self, url): self.headers = {"Content-Type": "text/javascript" if url.endswith(".js") else "text/css"}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return b"resource"
    class Opener:
        def open(self, url, timeout):
            requested.append(url)
            return Response(url)
    health.check_resources(Opener(), "https://example.invalid", f'<script src="{script}"></script><link rel="stylesheet" href="{style}">')
    assert requested == ["https://example.invalid" + script, "https://example.invalid" + style]


def test_external_script_reference_is_rejected_without_request():
    class Opener:
        def open(self, *_args, **_kwargs): pytest.fail("External request attempted")
    with pytest.raises(RuntimeError, match="external"):
        health.check_resources(Opener(), "https://example.invalid", '<script src="https://other.invalid/app.js"></script>')
