"""Build the core toolkit independently of any optional frontend artifacts."""
from pathlib import Path
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py

class BuildCore(build_py):
    def run(self):
        output = Path(self.build_lib) / "autonomo_taxes/web_ui"
        if output.resolve().is_relative_to((Path(__file__).parent / "src").resolve()):
            raise RuntimeError("build_lib must not overwrite source")
        if output.exists():
            shutil.rmtree(output)
        super().run()

setup(cmdclass={"build_py": BuildCore})
