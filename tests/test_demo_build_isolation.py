from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def test_production_entry_does_not_install_mock_api():
    production_entry = (WEB / "src" / "main.tsx").read_text(encoding="utf-8")
    production_html = (WEB / "index.html").read_text(encoding="utf-8")
    demo_entry = (WEB / "src" / "demo" / "main.tsx").read_text(encoding="utf-8")

    assert "mockApi" not in production_entry
    assert "installDemoApi" not in production_entry
    assert "/src/main.tsx" in production_html
    assert 'from "./mockApi"' in demo_entry
    assert "installDemoApi()" in demo_entry


def test_demo_build_has_an_explicit_separate_html_entry():
    demo_html = (WEB / "demo.html").read_text(encoding="utf-8")
    demo_config = (WEB / "vite.demo.config.ts").read_text(encoding="utf-8")
    production_config = (WEB / "vite.config.ts").read_text(encoding="utf-8")

    assert "/src/demo/main.tsx" in demo_html
    assert 'new URL("./demo.html"' in demo_config
    assert "demo.html" not in production_config
