"""Tenant isolation tests for sensitive browser/login diagnostic artifacts."""

import os
import stat

import pytest

from src.vendor.easel import web_publisher, xhs_publish


def test_web_publisher_artifacts_follow_tenant_profile_root(tmp_path):
    tenant_a_profile = tmp_path / "tenant-a" / "data" / "_profiles"
    tenant_b_profile = tmp_path / "tenant-b" / "data" / "_profiles"

    path_a = web_publisher._login_artifact_dir(str(tenant_a_profile))
    path_b = web_publisher._login_artifact_dir(str(tenant_b_profile))

    assert path_a == (tmp_path / "tenant-a" / "data" / "_login").resolve()
    assert path_b == (tmp_path / "tenant-b" / "data" / "_login").resolve()
    assert path_a != path_b


def test_xhs_diagnostic_artifacts_follow_tenant_profile_root(tmp_path):
    tenant_a_profile = tmp_path / "tenant-a" / "data" / "_profiles"
    tenant_b_profile = tmp_path / "tenant-b" / "data" / "_profiles"

    path_a = xhs_publish._login_artifact_dir(str(tenant_a_profile))
    path_b = xhs_publish._login_artifact_dir(str(tenant_b_profile))

    assert path_a == (tmp_path / "tenant-a" / "data" / "_login").resolve()
    assert path_b == (tmp_path / "tenant-b" / "data" / "_login").resolve()
    assert path_a != path_b



@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable to Windows")
def test_login_artifact_directories_and_files_are_private(tmp_path):
    profile = tmp_path / "tenant-a" / "data" / "_profiles"
    web_dir = web_publisher._login_artifact_dir(str(profile))
    xhs_dir = xhs_publish._login_artifact_dir(str(profile))

    web_file = web_dir / "qr.png"
    xhs_file = xhs_dir / "publish-fail.png"
    web_file.write_bytes(b"test")
    xhs_file.write_bytes(b"test")
    web_publisher._secure_login_artifact(web_file)
    xhs_publish._secure_login_artifact(xhs_file)

    assert stat.S_IMODE(web_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(xhs_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(web_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(xhs_file.stat().st_mode) == 0o600
