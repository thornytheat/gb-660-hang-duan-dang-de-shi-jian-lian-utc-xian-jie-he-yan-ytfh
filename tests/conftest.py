import os
import tempfile
from pathlib import Path

# 测试不得写入仓库内的 data/；统一落到临时库，避免污染工作区。
os.environ.setdefault("TZ", "UTC")
_TEST_DB = Path(tempfile.mkdtemp(prefix="flightclock-tests-")) / "runs.sqlite3"
os.environ["DATABASE_PATH"] = str(_TEST_DB)
