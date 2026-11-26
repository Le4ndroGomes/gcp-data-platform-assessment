from utils.logging import _scrub
from utils.redaction import Redactor


def test_principal_pseudonym_stable_and_typed():
    r = Redactor(anonymize=True, salt="s")
    a = r.principal("maria.silva@hospital.com.br")
    assert a == r.principal("MARIA.SILVA@hospital.com.br")
    assert "maria" not in a and a.endswith("@hospital.com.br")
    sa = r.principal("etl@proj.iam.gserviceaccount.com")
    assert sa.startswith("serviceAccount:sa_") and "etl" not in sa
    agent = r.principal("service-123@gcp-sa-bigquerydatatransfer.iam.gserviceaccount.com")
    assert agent.endswith("@gcp-sa-bigquerydatatransfer.iam.gserviceaccount.com")
    assert r.principal("allUsers") == "allUsers"


def test_no_anonymize_passthrough():
    assert Redactor(anonymize=False).principal("a@b.com") == "a@b.com"


def test_secrets_always_scrubbed():
    r = Redactor(anonymize=False)
    out = r.scrub_secrets({"password": "x", "nested": {"client_secret": "y", "ok": "Server=a;Password=abc;"}})
    assert out["password"] == "***" and out["nested"]["client_secret"] == "***" and "abc" not in out["nested"]["ok"]
    assert "abc" not in r.text("jdbc:x;password=abc")


def test_log_scrubber():
    assert "tok123" not in _scrub("Authorization: Bearer tok123")
    assert "s3cr3t" not in _scrub("client_secret=s3cr3t")
