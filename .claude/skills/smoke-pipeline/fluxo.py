"""O roteiro HTTP do /smoke-pipeline, rodado dentro do container do api (só stdlib).

Fala com o api (o próprio container) e com o bronze-api (pela rede do projeto), como
o app e a silver falariam. Imprime uma linha por verificação e sai com 0 (tudo OK)
ou 1. As senhas chegam pelo ambiente (SMOKE_SENHA) e nunca são impressas.

Uso: python - <execução> < fluxo.py
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

API = "http://localhost:5000/api/v1"
BRONZE_API = "http://bronze-api:5000/api/v1"
VIN = "93YBSR7RHEJ298116"
ALHEIO = "9BWZZZ377VT004251"
SENHA = os.environ["SMOKE_SENHA"]
EXECUCAO = sys.argv[1] if len(sys.argv) > 1 else "manual"
#: Quanto esperar o registro chegar à silver: fila, consumer, bronze e a janela.
PRAZO = 60

falhas = 0
total = 0


def chamar(metodo, url, corpo=None, token=None, request_id=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if request_id:
        headers["X-Request-ID"] = request_id
    dados = None if corpo is None else json.dumps(corpo).encode()
    pedido = urllib.request.Request(url, method=metodo, headers=headers, data=dados)
    try:
        with urllib.request.urlopen(pedido, timeout=15) as resposta:
            return resposta.status, json.load(resposta)
    except urllib.error.HTTPError as erro:
        texto = erro.read()
        try:
            return erro.code, json.loads(texto)
        except ValueError:
            return erro.code, {"_corpo": texto[:300].decode(errors="replace")}


def confere(condicao, descricao, detalhe=""):
    global falhas, total
    total += 1
    if condicao:
        print(f"OK      {descricao}")
    else:
        falhas += 1
        print(f"FALHOU  {descricao}" + (f" — {str(detalhe)[:300]}" if detalhe else ""))
    return condicao


def status(esperado, descricao, metodo, url, corpo=None, token=None):
    codigo, resposta = chamar(metodo, url, corpo, token)
    confere(codigo == esperado, f"{codigo}  {descricao}", f"esperado {esperado}: {resposta}")
    return resposta


def token(base, nome):
    codigo, corpo = chamar("POST", f"{base}/auth/token", {"name": nome, "password": SENHA})
    if not confere(codigo == 200, f"login de {nome} em {base.split('/')[2]}", corpo):
        sys.exit(1)
    return corpo["access_token"]


def registro(vin):
    return {
        "id": str(uuid.uuid4()),
        "vin": vin,
        "collected_at": "2026-11-07T12:00:00.482Z",
        "user": "smoke-gestora",
        "readings": [{"metric": "rpm", "unit": "rpm", "available": True, "value": 900.0}],
    }


def ler(silver, cursor=None, limit=2):
    """Lê como a silver, página a página, até ``has_more`` ser falso."""
    itens, chamadas, mais = [], 0, True
    while mais:
        url = f"{BRONZE_API}/bronze/records?limit={limit}"
        if cursor:
            url += f"&cursor={cursor}"
        codigo, pagina = chamar("GET", url, token=silver)
        if codigo != 200:
            return None, cursor, chamadas
        itens += pagina["items"]
        cursor, mais = pagina["next_cursor"], pagina["has_more"]
        chamadas += 1
    return itens, cursor, chamadas


def esperar_na_silver(silver, quantos, cursor=None):
    """Lê do ``cursor`` até chegarem ``quantos`` registros, ou o prazo acabar."""
    inicio = time.monotonic()
    while True:
        itens, proximo, chamadas = ler(silver, cursor)
        if itens is not None and len(itens) >= quantos:
            return itens, proximo, chamadas, time.monotonic() - inicio
        if time.monotonic() - inicio > PRAZO:
            return itens or [], proximo, chamadas, time.monotonic() - inicio
        time.sleep(1)


# --- O processo de cada lado ----------------------------------------------------

codigo, saude = chamar("GET", f"{API}/health/")
confere(
    codigo == 200 and saude.get("rabbitmq") == "ok" and "mongo" not in saude,
    "health do api: database e rabbitmq, sem mongo",
    saude,
)
codigo, saude = chamar("GET", f"{BRONZE_API}/health/")
confere(
    codigo == 200 and saude.get("mongo") == "ok" and "rabbitmq" not in saude,
    "health do bronze-api: database e mongo, sem rabbitmq",
    saude,
)

gestora = token(API, "smoke-gestora")
motorista = token(API, "smoke-motorista")
silver = token(BRONZE_API, "smoke-silver")
_, eu = chamar("GET", f"{API}/auth/me", token=gestora)
_, eu_motorista = chamar("GET", f"{API}/auth/me", token=motorista)

status(404, "o api não serve a leitura do bronze", "GET", f"{API}/bronze/records", token=silver)
status(
    404,
    "o bronze-api não recebe lote",
    "POST",
    f"{BRONZE_API}/telemetry/batches",
    {"records": []},
    gestora,
)
status(401, "leitura sem token", "GET", f"{BRONZE_API}/bronze/records")
status(403, "a gestora não lê o bronze", "GET", f"{BRONZE_API}/bronze/records", token=gestora)
status(
    422, "cursor malformado", "GET", f"{BRONZE_API}/bronze/records?cursor=lixo", token=silver
)

# --- O fluxo --------------------------------------------------------------------

veiculo = status(
    201, "a gestora cadastra o veículo", "POST", f"{API}/vehicle/", {"vin": VIN}, gestora
)
status(
    201,
    "a gestora adiciona o motorista",
    "POST",
    f"{API}/vehicle/{veiculo.get('id')}/drivers",
    {"user_name": "smoke-motorista"},
    gestora,
)
aceitos = [registro(VIN) for _ in range(3)]
lote = {"records": [*aceitos, registro(ALHEIO)]}
request_id = f"smoke-pipeline-{EXECUCAO}"

codigo, recibo = chamar("POST", f"{API}/telemetry/batches", lote, gestora, request_id)
confere(
    codigo == 202 and recibo.get("accepted") == 3 and len(recibo.get("rejected", [])) == 1,
    f"{codigo}  lote misto: 3 aceitos, o VIN alheio em rejected",
    recibo,
)

itens, cursor, chamadas, espera = esperar_na_silver(silver, 3)
confere(
    sorted(i["record_id"] for i in itens) == sorted(r["id"] for r in aceitos),
    f"a silver lê os 3 aceitos (em {espera:.0f} s, {chamadas} páginas de 2)",
    [i["record_id"] for i in itens],
)
confere(
    all(
        i["user_id"] == eu["id"]
        and i["sender_relation"] == "owner"
        and i["request_id"] == request_id
        and i["message_id"] == recibo.get("batch_id")
        and i["raw"] == next((r for r in aceitos if r["id"] == i["record_id"]), None)
        for i in itens
    ),
    "cada registro com o remetente, o vínculo, o request_id, o batch_id e o bruto do envio",
    itens[:1],
)

# O app reenvia o mesmo lote, e depois o motorista envia um lote novo de um
# registro. Com um consumer só, a fila entrega na ordem: quando o registro novo
# aparece, o reenvio já passou.
codigo, _ = chamar("POST", f"{API}/telemetry/batches", lote, gestora)
confere(codigo == 202, f"{codigo}  reenvio do mesmo lote")
novo = registro(VIN)
codigo, recibo_novo = chamar("POST", f"{API}/telemetry/batches", {"records": [novo]}, motorista)
confere(
    codigo == 202 and recibo_novo.get("accepted") == 1,
    f"{codigo}  lote novo, de um registro, pelo motorista",
    recibo_novo,
)

depois, cursor_depois, _, espera = esperar_na_silver(silver, 1, cursor)
confere(
    [i["record_id"] for i in depois] == [novo["id"]],
    f"do cursor guardado, só o registro novo (em {espera:.0f} s): o reenvio não repetiu nada",
    [i["record_id"] for i in depois],
)
confere(
    [(i["user_id"], i["sender_relation"]) for i in depois] == [(eu_motorista["id"], "driver")],
    "o registro do motorista chega com o remetente e o vínculo dele",
    depois[:1],
)
vazia, cursor_vazio, _ = ler(silver, cursor_depois)
confere(
    vazia == [] and cursor_vazio == cursor_depois,
    "a página vazia devolve o cursor recebido",
    vazia,
)
tudo, _, _ = ler(silver, limit=1000)
confere(len(tudo or []) == 4, "relendo do começo: 4 registros no bronze", len(tudo or []))

print(f"RESUMO: {total - falhas} de {total}")
sys.exit(1 if falhas else 0)
