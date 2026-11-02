"""产品边界回归：系统只生成草稿，发布由用户手动完成，不得调用 vendor 的发布子命令。"""
import re
from pathlib import Path

import pytest

from src.core import account_login

ROOT = Path(__file__).resolve().parents[1]
PUBLISH_SUBCOMMANDS = ("publish", "publish-video", "publish-image", "publish-article")


@pytest.mark.parametrize("platform", list(account_login.PLATFORMS))
def test_login_commands_pass_vendor_guard(platform, tmp_path):
    cmd = account_login.build_cmd(platform, timeout_seconds=60, data_dir=tmp_path)
    assert account_login.guard_vendor_cmd(cmd) is cmd


@pytest.mark.parametrize("sub", PUBLISH_SUBCOMMANDS)
def test_vendor_guard_rejects_publish(sub):
    cmd = ["python", "src/vendor/easel/xhs_publish.py", sub, "--exec"]
    with pytest.raises(account_login.VendorCommandBlocked):
        account_login.guard_vendor_cmd(cmd)


def test_product_code_never_invokes_vendor_publish():
    """vendor 目录之外的业务代码不得拼出 publish 子命令或调用 verify_*_publish。"""
    pattern = re.compile(
        r"""(_publish\.py|web_publisher\.py)["']\)?\s*,\s*["'](publish[\w-]*)["']"""
        r"""|verify_\w+_publish\("""
        r"""|["']--exec["']"""
    )
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        if "vendor" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert offenders == []
