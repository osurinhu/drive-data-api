# syntax=docker/dockerfile:1

# --- Estágio de build ---------------------------------------------------------
# Instala as dependências num virtualenv isolado, que é copiado pronto para a
# imagem final — assim o compilador e os caches do pip não vão para produção.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Instala do lock, não do requirements.txt: o requirements.txt usa faixas de
# versão, então builds de dias diferentes poderiam produzir imagens diferentes.
COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install -r /tmp/requirements.lock.txt

# --- Imagem de produção -------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    # `app/` é a raiz de imports da aplicação (mesma convenção do dev local).
    PYTHONPATH=/srv/app

COPY --from=builder /opt/venv /opt/venv

RUN useradd --create-home --uid 10001 appuser

WORKDIR /srv

# COPY explícito em vez de `COPY . /srv`: a imagem de produção leva somente o
# que roda. Testes e ferramentas de desenvolvimento ficam no estágio `dev`.
COPY --chown=appuser:appuser app/ /srv/app/
COPY --chown=appuser:appuser migrations/ /srv/migrations/
COPY --chown=appuser:appuser docker/ /srv/docker/
COPY --chown=appuser:appuser settings.toml alembic.ini /srv/

RUN chmod +x /srv/docker/entrypoint.sh /srv/docker/setup.sh

# Nunca rodar como root.
USER appuser

EXPOSE 5000

# O entrypoint aplica as migrações antes de subir o servidor (RUN_MIGRATIONS=1, o
# padrão). No compose quem migra é o job `setup`, e os processos sobem com 0.
# O processo (api, bronze_api, consumer, setup) é escolhido por FLASK_PROCESS.
ENTRYPOINT ["/srv/docker/entrypoint.sh"]
# Sem `--workers`: o gunicorn lê WEB_CONCURRENCY do ambiente, então o número de
# workers é configurável sem reconstruir a imagem.
# Não use `--preload` sem descartar o engine no post_fork: os workers herdariam
# o mesmo socket de conexão do pool e corromperiam a comunicação com o Postgres.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--timeout", "30", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     # `req=` vem do header de resposta que ext/observability.py define. Sem ele,
     # o id só apareceria nas linhas de erro — e rastrear uma requisição
     # qualquer pelo id relatado pelo cliente não funcionaria.
     "--access-logformat", "%(h)s \"%(r)s\" %(s)s %(b)s %(D)sus req=%({x-request-id}o)s", \
     "app:create_app()"]

# --- Estágio de desenvolvimento ----------------------------------------------
# Estende a imagem de produção com as ferramentas de teste. Usado pelo serviço
# `test` do docker-compose; a imagem de produção acima fica intacta.
FROM runtime AS dev

USER root
COPY requirements.txt requirements-dev.txt /tmp/
RUN pip install -r /tmp/requirements-dev.txt
COPY --chown=appuser:appuser tests/ /srv/tests/
COPY --chown=appuser:appuser pyproject.toml /srv/
# /srv é do root (criado pelo WORKDIR); sem isto o pytest não consegue gravar
# o próprio diretório de cache.
RUN chown appuser:appuser /srv
USER appuser

ENTRYPOINT []
CMD ["pytest"]
