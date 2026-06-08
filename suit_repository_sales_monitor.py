import importlib.util
import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RP5_PATHS = [
    Path(os.getenv("RP5_SCRIPT_PATH", "")) if os.getenv("RP5_SCRIPT_PATH") else None,
    Path(r"C:\RP\rp_v5.0.py"),
    BASE_DIR / "rp_v5.0.py",
]


def resolve_rp5_path():
    for path in DEFAULT_RP5_PATHS:
        if path and path.exists():
            return path
    candidates = ", ".join(str(path) for path in DEFAULT_RP5_PATHS if path)
    raise FileNotFoundError(f"RP5 script not found. Checked: {candidates}")


def main():
    rp5_path = resolve_rp5_path()
    spec = importlib.util.spec_from_file_location("rp_v5_0", rp5_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


if __name__ == "__main__":
    raise SystemExit(main())
