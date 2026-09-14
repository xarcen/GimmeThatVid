"""Launch GimmeThatVid without a console window (run with pythonw.exe)."""
import multiprocessing
import sys
from pathlib import Path

# Each download runs in a child process that starts through this file again.
# The guard keeps those children from opening a second app, and freeze_support()
# lets them start inside the packaged GimmeThatVid.exe.
if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    from gimmethatvid.main import run

    run()
