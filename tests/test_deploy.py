import pytest

nebius = pytest.importorskip("nebius")

from deploy.nebius_deploy import TARGETS, endpoint_spec, job_spec, load_env_file  # noqa: E402


def test_reader_spec_matches_api_schema():
    s = endpoint_spec(TARGETS["reader-omni"], "cr/omni:1", {"HF_TOKEN": "h", "READER_OMNI_TOKEN": "t", "KHATTI_DATA_KEY": "x"})
    assert s.platform == "gpu-l40s-a" and s.auth_token == "t"
    assert [v.name for v in s.environment_variables] == ["HF_TOKEN"]  # only its own keys are forwarded
    assert s.ports[0].container_port == 8000 and s.disk.size_bytes >= 100 * 1024**3


def test_worker_and_job_specs():
    w = endpoint_spec(TARGETS["worker"], "cr/api:1", {"KHATTI_DATABASE_URL": "pg"})
    assert (w.container_command, w.args) == ("python", "-m khatti.worker") and len(w.ports) == 0
    j = job_spec("cr/jobs:1", "jobs.eval", ["--run-name", "v 1"], {}, "cpu-e2", "8vcpu-32gb", 6)
    assert j.args == "-m jobs.eval --run-name 'v 1'" and j.timeout.total_seconds() == 6 * 3600


def test_env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# c\nA=1\nB = two=2\n")
    assert load_env_file(p) == {"A": "1", "B": "two=2"}
