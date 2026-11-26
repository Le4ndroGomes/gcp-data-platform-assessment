"""Execução dos módulos de coleta respeitando dependências, com módulos independentes em paralelo.

Dependências (só entre módulos selecionados; dados de execuções anteriores continuam valendo via raw/):
  discovery  -> resolve projetos e serviços habilitados usados pelos demais
  security   -> lê a lista de datasets coletada por bigquery
  lineage    -> combina bigquery (jobs) e pipelines (Dataform, DTS...)
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable

DEPENDS_ON: dict[str, tuple[str, ...]] = {
    "discovery": (),
    "bigquery": ("discovery",),
    "storage": ("discovery",),
    "pipelines": ("discovery",),
    "services": ("discovery",),
    "billing": ("discovery",),
    "security": ("discovery", "bigquery"),
    "lineage": ("bigquery", "pipelines"),
    "powerbi": (),
}


def run_modules(modules: list[str], run_one: Callable[[str], None], parallel: bool = True, max_workers: int = 6) -> None:
    """Executa run_one(módulo) para cada módulo. Um módulo só inicia quando suas dependências selecionadas terminaram
    (com sucesso ou não: a falha de um módulo nunca bloqueia os demais). parallel=False mantém a ordem informada."""
    if not parallel or len(modules) <= 1:
        for m in modules:
            run_one(m)
        return
    pending = list(dict.fromkeys(modules))
    selected = set(pending)
    done: set[str] = set()
    running: dict = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        while pending or running:
            for m in list(pending):
                if all(d in done for d in DEPENDS_ON.get(m, ()) if d in selected):
                    pending.remove(m)
                    running[pool.submit(run_one, m)] = m
            if not running:  # dependência circular ou inesperada: não trava, executa o restante em ordem
                for m in pending:
                    run_one(m)
                return
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for fut in finished:
                m = running.pop(fut)
                done.add(m)
                exc = fut.exception()
                if exc is not None:  # run_one já isola falhas; isto é só uma rede de segurança
                    raise exc
