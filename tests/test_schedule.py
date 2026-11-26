from utils.schedule import frequency_label, runs_per_day


def test_cron():
    assert runs_per_day("0 3 * * *") == 1
    assert runs_per_day("*/15 * * * *") == 96
    assert runs_per_day("0 */4 * * *") == 6
    assert abs(runs_per_day("15 3 * * 1-5") - 5 / 7) < 1e-3
    assert runs_per_day("0 6 1 * *") < 0.05


def test_appengine_and_presets():
    assert runs_per_day("every 24 hours") == 1
    assert runs_per_day("every 15 minutes") == 96
    assert runs_per_day("every day 03:00") == 1
    assert abs(runs_per_day("every mon,wed 09:00") - 2 / 7) < 1e-3
    assert runs_per_day("@hourly") == 24
    assert runs_per_day({"__type": "TimeDelta", "days": 0, "seconds": 3600}) == 24
    assert runs_per_day(None) is None and runs_per_day("garbage expr") is None


def test_labels():
    assert frequency_label(None).startswith("desconhecida")
    assert frequency_label(96) .startswith("contínua")
    assert frequency_label(1) == "diária"
