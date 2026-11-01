"""Tenant isolation tests for sensitive browser/login diagnostic artifacts."""

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
