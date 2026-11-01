import io
import json

from tripo_fleet import cli


def test_report_works_with_legacy_windows_stdout(tmp_path, monkeypatch):
    fleet_path = tmp_path / "fleet.json"
    fleet_path.write_text(json.dumps({"vehicles": []}), encoding="utf-8")
    output = tmp_path / "output"
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252")
    monkeypatch.setattr(cli.sys, "stdout", stream)

    assert cli.main(["--fleet", str(fleet_path), "--output", str(output), "report"]) == 0

    stream.flush()
    printed = buffer.getvalue().decode("utf-8")
    assert "created_at → completed_at" in printed
    assert (output / "report.md").read_text(encoding="utf-8").strip() == printed.replace("\r\n", "\n").strip()
