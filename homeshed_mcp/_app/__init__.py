"""Development shim only, never in the wheel (the wheel's include list leaves homeshed_mcp/ out). In a wheel the
server's modules live in homeshed_mcp/_app/; in a checkout they sit at the repo root, so an editable install
(pip install -e ., with pyproject's dev-mode-dirs) maps this package onto the root and the homeshed-mcp command
(homeshed_mcp._app.cli) finds them there."""
import os

__path__ = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))]
