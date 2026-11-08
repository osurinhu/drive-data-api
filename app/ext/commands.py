"""Comandos de linha de comando (``flask <comando>``)."""

import click
from flask import Flask, current_app
from flask.cli import with_appcontext

from domain.authz import Role
from ext.database import db
from service.errors import BusinessRuleError, ConflictError, NotFoundError

#: Opção reutilizada pelos comandos que recebem um papel. `click.Choice` sai da
#: própria enumeração: acrescentar um papel em `domain/authz.py` acrescenta a opção
#: aqui, sem segunda lista para manter em dia.
PAPEIS = click.Choice([role.value for role in Role])


@click.command("create-db")
@with_appcontext
def create_db() -> None:
    """Cria as tabelas direto dos modelos (atalho de desenvolvimento).

    Para qualquer ambiente com dados, use as migrações: `alembic upgrade head`.
    """
    db.create_all()
    click.echo("Tabelas criadas.")


@click.command("drop-db")
@click.confirmation_option(prompt="Isso apaga TODAS as tabelas. Continuar?")
@with_appcontext
def drop_db() -> None:
    """Remove todas as tabelas."""
    db.drop_all()
    click.echo("Tabelas removidas.")


@click.command("create-user")
@click.option("--name", prompt=True, help="Nome de login do usuário.")
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Senha do usuário.",
)
@click.option("--role", type=PAPEIS, default=Role.USUARIO.value, help="Papel do usuário.")
@with_appcontext
def create_user(name: str, password: str, role: str) -> None:
    """Cria um usuário (use para criar o primeiro administrador)."""
    service = current_app.container.user_service()
    try:
        user = service.register(name=name, password=password, role=Role(role))
    except (ConflictError, BusinessRuleError) as error:
        raise click.ClickException(str(error)) from error

    click.echo(f"Usuário '{user.name}' (id={user.id}) criado com o papel '{user.role.value}'.")


@click.command("set-role")
@click.option("--name", prompt=True, help="Nome de login do usuário.")
@click.option("--role", type=PAPEIS, prompt=True, help="Novo papel.")
@with_appcontext
def set_role(name: str, role: str) -> None:
    """Troca o papel de um usuário existente.

    Passa a valer na requisição seguinte, mesmo com o token já emitido.
    """
    service = current_app.container.user_service()
    try:
        user = service.set_role(name, Role(role))
    except NotFoundError as error:
        raise click.ClickException(str(error)) from error

    click.echo(f"Usuário '{user.name}' agora tem o papel '{user.role.value}'.")


@click.command("set-password")
@click.option("--name", prompt=True, help="Nome de login do usuário.")
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Nova senha.",
)
@with_appcontext
def set_password(name: str, password: str) -> None:
    """Troca a senha de um usuário e encerra todas as sessões dele."""
    service = current_app.container.user_service()
    try:
        user = service.set_password(name, password)
    except (NotFoundError, BusinessRuleError) as error:
        raise click.ClickException(str(error)) from error

    click.echo(f"Senha de '{user.name}' trocada; as sessões da conta foram encerradas.")


@click.command("prune-tokens")
@with_appcontext
def prune_tokens() -> None:
    """Apaga os tokens de renovação expirados."""
    removed = current_app.container.token_service().prune()
    click.echo(f"{removed} token(s) expirado(s) apagado(s).")


def init_app(app: Flask) -> None:
    for command in (create_db, drop_db, create_user, set_role, set_password, prune_tokens):
        app.cli.add_command(command)
