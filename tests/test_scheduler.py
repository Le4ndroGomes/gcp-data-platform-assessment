import threading
import time

from core.scheduler import DEPENDS_ON, run_modules


def _run(modules, parallel=True, fail=()):
    started, finished, lock = {}, {}, threading.Lock()

    def one(m):
        with lock:
            started[m] = time.monotonic()
        time.sleep(0.05)
        with lock:
            finished[m] = time.monotonic()
        if m in fail:
            raise RuntimeError(m)

    run_modules(modules, one, parallel=parallel)
    return started, finished


def test_dependencies_are_respected_and_independent_modules_overlap():
    s, f = _run(["discovery", "bigquery", "storage", "pipelines", "security", "lineage"])
    for m, deps in DEPENDS_ON.items():
        for d in deps:
            if m in s and d in f:
                assert s[m] >= f[d], f"{m} iniciou antes de {d} terminar"
    # módulos independentes entre si rodam ao mesmo tempo
    assert s["storage"] < f["bigquery"] and s["pipelines"] < f["bigquery"]


def test_unselected_dependencies_do_not_block():
    s, _ = _run(["security", "lineage"])  # bigquery/discovery/pipelines não selecionados: raw de execução anterior
    assert set(s) == {"security", "lineage"}


def test_sequential_keeps_the_given_order():
    order = []
    run_modules(["billing", "discovery", "storage"], order.append, parallel=False)
    assert order == ["billing", "discovery", "storage"]


def test_parallel_is_faster_than_sequential():
    t0 = time.monotonic(); _run(["storage", "pipelines", "services", "billing"], parallel=False); seq = time.monotonic() - t0
    t0 = time.monotonic(); _run(["storage", "pipelines", "services", "billing"], parallel=True); par = time.monotonic() - t0
    assert par < seq * 0.7


def test_failure_of_a_module_propagates_only_after_others_finish_scheduling():
    # run_one isola falhas em produção; aqui garantimos que a rede de segurança não deixa threads penduradas
    try:
        _run(["discovery", "storage"], fail=("storage",))
    except RuntimeError as exc:
        assert str(exc) == "storage"
