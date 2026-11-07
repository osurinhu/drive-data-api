---
paths:
  - "Dockerfile"
  - "docker-compose.yml"
  - "docker/**"
  - ".github/workflows/**"
  - "requirements*.txt"
  - "pyproject.toml"
  - ".dockerignore"
  - ".gitattributes"
  - ".vscode/settings.json"
---

# Infraestrutura, dependências e CI

Decisões em 02-decisoes, seções Operação e Qualidade; o runbook é o `README.md`.

- O serviço `api` do compose precisa de `target: runtime`: sem ele o Docker constrói o último
  estágio (`dev`) e a aplicação sobe rodando pytest.
- O estágio `runtime` usa `COPY` explícito (só o que roda); o `dev` acrescenta testes e
  ferramentas. `tests/` não pode entrar no `.dockerignore`.
- Dependência nova: edite `requirements.txt` (faixa de versão) e regenere `requirements.lock.txt`
  com o comando do cabeçalho dele — o Docker instala o lock. No Git Bash, troque `${PWD}` por
  `$(pwd -W)` e prefixe `MSYS_NO_PATHCONV=1`, senão o `-w /srv` vira caminho do Windows.
- O fato de `app/` ser a raiz do `sys.path` está codificado em cinco lugares: `pythonpath` e `src`
  no `pyproject.toml`, `PYTHONPATH` no `Dockerfile`, `sys.path.insert` em `migrations/env.py` e
  `python.analysis.extraPaths` em `.vscode/settings.json`. Mudar o layout exige mudar os cinco.
- O CI (`.github/workflows/ci.yml`) e `.claude/skills/verificar/verificar.sh` executam as mesmas
  etapas: mudou um, mude o outro.
- Workers vêm de `WEB_CONCURRENCY`; ao mudar, refaça a conta
  `workers × (pool_size + max_overflow) ≤ max_connections`. Nada de `--preload` sem descartar o
  engine no `post_fork`.
- Scripts `.sh` ficam em LF (`.gitattributes`): com CRLF, o `entrypoint.sh` derruba o container.
- A porta 5432 do `db` e o bind mount de `./migrations` no `api` são conveniências de
  desenvolvimento e saem antes de produção (README, "Antes de ir para produção").
