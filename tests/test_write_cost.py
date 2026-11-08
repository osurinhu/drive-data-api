"""Custo em consultas das operações de escrita.

Regressão de uma otimização **medida**. O `BaseRepository.add`/`update` fazia
`flush()` seguido de `refresh()`. O `refresh()` era desperdício porque o
`commit()` que vem logo depois expira todos os atributos de qualquer forma: o
objeto era recarregado duas vezes, não uma.

Números medidos neste mesmo cenário (Postgres 16, SQLAlchemy 2.1):

| cenário                          | com refresh | sem refresh |
|----------------------------------|-------------|-------------|
| create + serializar (rota)       | 2 SELECT    | 1 SELECT    |
| update + serializar (rota)       | 3 SELECT    | 2 SELECT    |
| create_many de 5 (total de SQL)  | 10 comandos | 5 comandos  |

Se alguém reintroduzir o `refresh()`, as contagens sobem e estes testes
reprovam.
"""

VIN = "93YBSR7RHEJ298116"


def _selects(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().upper().startswith("SELECT")]


def test_criar_sem_ler_o_objeto_de_volta_nao_consulta(app, sql_log, make_user):
    """Um INSERT e nada mais."""
    user = make_user()
    sql_log.clear()

    with app.app_context():
        app.container.vehicle_service().create(user["id"], vin=VIN)

    assert _selects(sql_log) == []
    assert len(sql_log) == 1


def test_criar_e_serializar_custa_uma_consulta(app, sql_log, make_user):
    """O SELECT aqui é inevitável e legítimo: `commit()` expira os atributos
    (`expire_on_commit=True`), então ler qualquer campo recarrega a linha. O
    ganho foi deixar de fazer isso **duas** vezes."""
    user = make_user()
    sql_log.clear()

    with app.app_context():
        veiculo = app.container.vehicle_service().create(user["id"], vin=VIN)

        # Os defaults de servidor ficam disponíveis sob demanda.
        assert veiculo.id is not None
        assert veiculo.created_at is not None
        assert veiculo.last_update is not None
        # TIMESTAMPTZ: o valor volta ciente de fuso, não naive.
        assert veiculo.created_at.tzinfo is not None

    assert len(_selects(sql_log)) == 1


def test_atualizar_e_serializar_custa_duas_consultas(app, sql_log, make_user):
    """Uma para carregar o registro e validar a posse, outra para recarregar
    depois do commit."""
    user = make_user()
    with app.app_context():
        vehicle_id = app.container.vehicle_service().create(user["id"], vin=VIN).id

    sql_log.clear()
    with app.app_context():
        veiculo = app.container.vehicle_service().update(vehicle_id, user["id"], nickname="depois")
        assert veiculo.nickname == "depois"

    assert len(_selects(sql_log)) == 2


def test_criar_em_lote_emite_apenas_os_inserts(app, sql_log, make_user):
    """Era o pior caso: o refresh multiplicava o custo por registro do lote."""
    user = make_user()
    sql_log.clear()

    with app.app_context():
        criados = app.container.vehicle_service().create_many(
            user["id"], [{"vin": f"{VIN[:-1]}{i}"} for i in range(5)]
        )

    assert len(criados) == 5
    assert _selects(sql_log) == []
    assert len(sql_log) == 5
