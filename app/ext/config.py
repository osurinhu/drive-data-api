"""Configuração e carregamento dinâmico de módulos.

Em vez de imports fixos, as duas listas do settings.toml (``EXTENSIONS`` e
``API_V1_ROUTES``) guardam caminhos de módulo importados na inicialização.
Estender a aplicação passa a ser aditivo: uma linha nova na configuração.

A mesma imagem roda mais de um processo, e cada processo tem o seu perfil: uma
seção do settings.toml, escolhida pela variável ``FLASK_PROCESS``, que se
sobrepõe ao ``[default]`` (ver o cabeçalho do settings.toml).
"""

import tomllib
from importlib import import_module
from pathlib import Path

from dynaconf import FlaskDynaconf
from flask import Flask
from flask.config import Config
from flask_smorest import Api

# Caminho absoluto, para que a descoberta do settings.toml não dependa do
# diretório de trabalho.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_FILE = PROJECT_ROOT / "settings.toml"

#: Variável que escolhe o perfil do processo. O Dynaconf também a carrega como
#: ``PROCESS``, pelo prefixo ``FLASK_``: é por essa chave que o código pergunta
#: em que processo está.
PROCESS_ENVVAR = "FLASK_PROCESS"


def init_app(app: Flask) -> None:
    """Carrega a configuração. Precedência (maior primeiro): variável de
    ambiente ``FLASK_*`` > .env > settings.toml — é assim que os segredos ficam
    fora do controle de versão."""
    FlaskDynaconf(
        app,
        envvar_prefix="FLASK",
        settings_file=[str(SETTINGS_FILE)],
        environments=True,
        env_switcher=PROCESS_ENVVAR,
        load_dotenv=True,
        dotenv_path=str(PROJECT_ROOT / ".env"),
    )
    _check_process(app)


def _check_process(app: Flask) -> None:
    """Reprova um ``FLASK_PROCESS`` que não seja seção do settings.toml.

    O Dynaconf não reclama de perfil desconhecido: ele cai calado no
    ``[default]``, que carrega tudo. Um erro de digitação faria o consumer subir
    como a aplicação inteira, exigindo segredos que ele não deveria conhecer.
    """
    process = str(app.config.get("PROCESS") or "").strip().lower()
    if not process:
        return

    with SETTINGS_FILE.open("rb") as arquivo:
        perfis = {nome.lower() for nome in tomllib.load(arquivo)}
    if process not in perfis:
        raise RuntimeError(
            f"{PROCESS_ENVVAR}={process!r} não é um perfil do settings.toml. "
            f"Perfis disponíveis: {', '.join(sorted(perfis))}."
        )


def build_config() -> Config:
    """Carrega só a configuração, sem montar a aplicação.

    Usado pelas migrações e pela suíte: aplicar um `alembic upgrade` precisa
    apenas da URL do banco, não de JWT, CORS e rotas inicializados.
    """
    probe = Flask("config-probe")
    init_app(probe)
    return probe.config


def load_extensions(app: Flask) -> None:
    """Importa cada módulo de ``EXTENSIONS`` e chama seu ``init_app(app)``."""
    for module_path in app.config.get("EXTENSIONS", []):
        module = import_module(module_path)
        module.init_app(app)


def load_routes(app: Flask, api: Api, url_prefix: str, config_key: str) -> None:
    """Registra no ``api`` os blueprints de ``config_key``, abaixo de ``url_prefix``
    (ex.: ``/api/v1``).

    Cada um é registrado direto, sem blueprint pai: o flask-smorest agrupa o Swagger pelo
    blueprint registrado no ``Api``, e com um pai tudo cairia no grupo dele.
    """
    for module_path in app.config.get(config_key, []):
        blp = import_module(module_path).blp
        api.register_blueprint(blp, url_prefix=f"{url_prefix}{blp.url_prefix or ''}")
