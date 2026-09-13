"""CLI tests: every factoryctl command, exit codes, demo artifacts."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from factory_core.cli import main
from factory_core.jobqueue import DEFAULT_DB_PATH

PKG_ROOT = Path(__file__).resolve().parents[1]


def test_init_db_creates_schema(tmp_path, capsys):
    db_path = str(tmp_path / "cli.db")
    assert main(["--db", db_path, "init-db"]) == 0
    out = capsys.readouterr().out
    assert "schema v1 ready" in out
    assert "events, jobs" in out
    assert Path(db_path).exists()


def test_add_defaults_to_db_path_and_prints_id(tmp_path, capsys):
    db_path = str(tmp_path / "cli.db")
    assert main(["--db", db_path, "add", "--brand", "WTF Gyms",
                 "--pillar", "fitness-transformation", "--language", "hindi"]) == 0
    job_id = capsys.readouterr().out.strip()
    assert job_id.startswith("job_")


def test_add_json_and_script_file(tmp_path, capsys):
    db_path = str(tmp_path / "cli.db")
    script_file = tmp_path / "script.txt"
    script_file.write_text("Hook: 3am gym stories.\n", encoding="utf-8")
    assert main(["--db", db_path, "add", "--brand", "Reboot", "--pillar", "business",
                 "--language", "english", "--script-file", str(script_file), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["brand"] == "Reboot"
    assert payload["script"].startswith("Hook: 3am")


def test_full_cli_lifecycle(tmp_path, capsys):
    db_path = str(tmp_path / "cli.db")
    main(["--db", db_path, "add", "--brand", "WTF Gyms", "--pillar", "fitness",
          "--language", "hindi", "--title", "lifecycle"])
    job_id = capsys.readouterr().out.strip()

    assert main(["--db", db_path, "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [j["id"] for j in listed] == [job_id]

    assert main(["--db", db_path, "run", "--once"]) == 0
    assert "gated" in capsys.readouterr().out

    assert main(["--db", db_path, "show", job_id, "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["job"]["status"] == "gated"
    assert any(e["event"] == "stage_done" for e in shown["events"])

    assert main(["--db", db_path, "approve", job_id, "--notes", "ok"]) == 0
    assert "approved" in capsys.readouterr().out

    assert main(["--db", db_path, "publish"]) == 0
    assert "published" in capsys.readouterr().out

    assert main(["--db", db_path, "stats"]) == 0
    stats = capsys.readouterr().out
    assert "published  1" in stats


def test_events_command(tmp_path, capsys):
    db_path = str(tmp_path / "cli.db")
    main(["--db", db_path, "add", "--brand", "b", "--pillar", "p", "--language", "l"])
    job_id = capsys.readouterr().out.strip()
    assert main(["--db", db_path, "events", "--job", job_id]) == 0
    assert "created" in capsys.readouterr().out


def test_domain_errors_exit_3(tmp_path, capsys):
    db_path = str(tmp_path / "cli.db")
    main(["--db", db_path, "add", "--brand", "b", "--pillar", "p", "--language", "l"])
    job_id = capsys.readouterr().out.strip()
    # approve a queued (not gated) job
    assert main(["--db", db_path, "approve", job_id]) == 3
    assert "illegal transition" in capsys.readouterr().err
    # unknown job
    assert main(["--db", db_path, "show", "job_missing"]) == 3
    assert "no such job" in capsys.readouterr().err
    # requeue a non-failed job
    assert main(["--db", db_path, "requeue", job_id]) == 3


def test_reject_flow(tmp_path, capsys):
    db_path = str(tmp_path / "cli.db")
    main(["--db", db_path, "add", "--brand", "b", "--pillar", "p", "--language", "l"])
    job_id = capsys.readouterr().out.strip()
    main(["--db", db_path, "run"])
    capsys.readouterr()
    assert main(["--db", db_path, "reject", job_id, "--notes", "no"]) == 0
    assert "rejected" in capsys.readouterr().out


def test_run_on_empty_queue_is_ok(tmp_path, capsys):
    db_path = str(tmp_path / "cli.db")
    assert main(["--db", db_path, "run"]) == 0
    assert "(no queued jobs)" in capsys.readouterr().out


def test_bad_arguments_exit_2():
    with pytest.raises(SystemExit) as excinfo:
        main(["nonsense-command"])
    assert excinfo.value.code == 2


def test_doctor_reports_dark_flags(tmp_path, capsys):
    db_path = str(tmp_path / "cli.db")
    assert main(["--db", db_path, "doctor"]) == 0
    out = capsys.readouterr().out
    assert "DARK=True LIVE_ENABLED=False NETWORK_ENABLED=False" in out
    assert "result:    OK" in out


def test_demo_writes_log_and_receipt(tmp_path):
    out_dir = tmp_path / "demo-out"
    assert main(["demo", "--dir", str(out_dir)]) == 0
    log = (out_dir / "demo.log").read_text(encoding="utf-8")
    receipt = json.loads((out_dir / "demo_receipt.json").read_text(encoding="utf-8"))

    assert "STATUS: SUCCESS" in log
    assert "## 5. Self-audit" in log
    # the self-audit block is the tail of the log
    assert log.index("## 5. Self-audit") > log.index("STATUS: SUCCESS")
    assert log.rstrip().endswith("- status: SUCCESS")
    assert "live calls: 0" in log

    assert receipt["status"] == "SUCCESS"
    assert receipt["live_calls"] == 0
    assert all(receipt["checks"].values())
    statuses = sorted(j["status"] for j in receipt["jobs"].values())
    assert statuses == ["gated", "published", "published", "rejected"]

    # demo db is self-contained
    assert (out_dir / "demo.db").exists()
    # rerunning replaces the demo cleanly
    assert main(["demo", "--dir", str(out_dir)]) == 0


def test_module_invocation_works(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PKG_ROOT)
    proc = subprocess.run(
        [sys.executable, "-m", "factory_core.cli", "--db", str(tmp_path / "m.db"), "doctor"],
        cwd=str(PKG_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "result:" in proc.stdout


def test_contract_default_db_path_flagged_for_review(tmp_path):
    # sanity: the CLI default points at the shared contract path
    assert str(DEFAULT_DB_PATH).endswith("/wtf/_data/factory.db")
