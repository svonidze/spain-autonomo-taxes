from importlib.metadata import version
from pathlib import Path

def main(argv=None):
    if version("spain-autonomo-taxes") != version("spain-autonomo-taxes-ui"):
        raise SystemExit("Core and optional UI package versions must match")
    from autonomo_taxes.local_web import main as serve
    return serve(argv, static_root=Path(__file__).resolve().parent / "dist")

if __name__ == "__main__":
    raise SystemExit(main())
