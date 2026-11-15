from __future__ import annotations

import csv
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from second_llm.models import RawSimodInput  # noqa: E402
from second_llm.simod_runner import _run_docker  # noqa: E402


class SimodRunnerDockerTests(unittest.TestCase):
    def _workdir(self, name: str) -> Path:
        repo_tmp = Path(__file__).resolve().parents[1] / ".tmp_test_dirs" / name
        if repo_tmp.exists():
            shutil.rmtree(repo_tmp)
        repo_tmp.mkdir(parents=True, exist_ok=True)
        return repo_tmp

    def _write_csv(self, directory: Path) -> Path:
        csv_path = directory / "event_log.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["case_id", "activity", "resource", "start_time", "end_time"])
            writer.writerow(["1", "Review", "Alice", "2026-01-01 08:00:00", "2026-01-01 09:00:00"])
        return csv_path

    def test_run_docker_one_shot_false_generates_configuration_file(self) -> None:
        base_dir = self._workdir("docker_full_mode")
        event_log = self._write_csv(base_dir)
        output_dir = base_dir / "output"
        captured: dict[str, object] = {}

        def fake_run(cmd, capture_output, text, timeout, env):  # type: ignore[no-untyped-def]
            captured["cmd"] = cmd
            captured["env"] = env
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch("second_llm.simod_runner.is_docker_available", return_value=True),
            patch(
                "second_llm.simod_runner._collect_simod_output",
                return_value=RawSimodInput(raw_text="ok", line_count=1, is_non_empty=True),
            ),
            patch("second_llm.simod_runner.subprocess.run", side_effect=fake_run),
        ):
            result = _run_docker(event_log, output_dir, one_shot=False)

        self.assertTrue(result.is_non_empty)
        self.assertIn("--configuration", captured["cmd"])
        self.assertNotIn("--one-shot", captured["cmd"])
        self.assertNotIn("--event-log", captured["cmd"])

        config_path = output_dir / "_input" / "configuration.yaml"
        self.assertTrue(config_path.exists())
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["version"], 5)
        self.assertEqual(config["common"]["train_log_path"], event_log.name)

    def test_run_docker_one_shot_true_preserves_existing_cli_path(self) -> None:
        base_dir = self._workdir("docker_one_shot_mode")
        event_log = self._write_csv(base_dir)
        output_dir = base_dir / "output"
        captured: dict[str, object] = {}

        def fake_run(cmd, capture_output, text, timeout, env):  # type: ignore[no-untyped-def]
            captured["cmd"] = cmd
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch("second_llm.simod_runner.is_docker_available", return_value=True),
            patch(
                "second_llm.simod_runner._collect_simod_output",
                return_value=RawSimodInput(raw_text="ok", line_count=1, is_non_empty=True),
            ),
            patch("second_llm.simod_runner.subprocess.run", side_effect=fake_run),
        ):
            result = _run_docker(event_log, output_dir, one_shot=True)

        self.assertTrue(result.is_non_empty)
        self.assertIn("--one-shot", captured["cmd"])
        self.assertIn("--event-log", captured["cmd"])
        self.assertNotIn("--configuration", captured["cmd"])


if __name__ == "__main__":
    unittest.main()
