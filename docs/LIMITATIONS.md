# Limitações conhecidas

## BigQuery
- `INFORMATION_SCHEMA.JOBS*` retém **180 dias**; janelas maiores são truncadas. Jobs anteriores à migração do projeto para a organização não aparecem.
- As visões são **regionais**: cada região/multi-região é consultada separadamente; datasets em regiões não listadas não são vistos.
- `TABLE_STORAGE` reflete o estado atual; crescimento histórico vem do Cloud Monitoring (`stored_bytes`) e só cobre o período retornado pela API. Nada é extrapolado.
- `ROW_ACCESS_POLICIES` e `PARTITIONS` exigem qualificador de dataset; são avaliados até `max_datasets_dataset_level_views` datasets por região.
- No modo `features` (padrão) o texto SQL não sai do BigQuery: lineage de views depende de JOBS / Data Lineage API, e subqueries correlacionadas/joins não-equi só são detectados no modo `full`.
- Fingerprints usam `query_info.query_hashes.normalized_literals` quando disponível; caso contrário, uma normalização por regex (literais e números).
- Custos de consultas de metadados: INFORMATION_SCHEMA cobra no mínimo 10 MB por consulta (on-demand); em projetos movimentados, consultas sobre JOBS podem varrer dezenas de GB. O preflight estima o custo e o runner bloqueia consultas acima do limite.
- Concorrência máxima é estimada por minuto (jobs ativos no minuto = limite superior).

- Os números de componentes sem visibilidade (Permission Denied, Not Available, Not Assessed) são **limite inferior** ou inexistentes; o relatório os mostra como `n/d (sem visibilidade)` em vez de zero.
- Concorrência de jobs é limite superior; o relatório **não** estima número de clusters Databricks a partir dela.

## Pipelines
- Dataflow `jobMetadata` lista fontes detectadas pelo SDK; destinos (sinks) nem sempre são reportados.
- A REST API do Airflow depende da versão do Composer e da configuração de autenticação; sem acesso, os ambientes são listados sem DAGs.
- Data Fusion: pipelines CDAP são listados, mas agendamentos e plugins não são detalhados.
- Execuções de Cloud Scheduler não têm histórico na API (apenas última tentativa).
- Regiões de APIs regionais são obtidas do Cloud Asset Inventory; sem ele, usa `gcp.regions` + regiões dos datasets.

## Power BI
- O Scanner retorna tabelas/medidas/expressões somente com as configurações de tenant de metadata scanning habilitadas.
- O Scanner não retorna relacionamentos nem modo de armazenamento por tabela de forma documentada; o modo é inferido de `targetStorageMode` (rotulado como "inferido"). PBIP/TMDL/PBIT (offline) fornecem modo, relacionamentos e RLS exatos.
- Histórico de refresh por dataset exige que o service principal seja membro do workspace; `capacities/refreshables` cobre apenas capacidades dedicadas.
- Activity log: até ~30 dias, um dia UTC por chamada.
- `.pbix` não é suportado no modo offline (modelo binário). Use PBIP ou PBIT.
- Correlação Power BI × BigQuery é heurística (padrões de principal, horários de refresh, referências declaradas) e sempre acompanhada de nível de confiança.

## FinOps
- Custos faturados exigem Billing Export já configurado; a ferramenta não cria exportações.
- Estimativas usam preços de lista configuráveis e não consideram descontos contratuais.

## Segurança
- Classificação de dados sensíveis usa somente nomes de colunas e perfis do Sensitive Data Protection já existentes — nunca o conteúdo.
- VPC-SC e Org Policy de organização exigem permissões no nível da organização.

## Databricks
- A disponibilidade de recursos por região vem de um snapshot da documentação (`config/databricks_gcp_regions.yaml`); revalide antes de decisões.
- Slots não são convertidos em DBUs; o sizing exige benchmarks da POC.
