"""As etapas de caos do /smoke-pipeline, rodadas dentro do container do api (só stdlib).

Quem para e sobe os serviços é o smoke.sh; cada chamada daqui é uma etapa, com o
serviço já no estado que ela confere. Os ids aceitos ficam num arquivo do container,
para a última etapa conferir a contagem: aceitos no 202 = documentos no bronze, sem
duplicados. A senha chega pelo ambiente (SMOKE_SENHA) e nunca é impressa.

Uso: python - <etapa> [quantos] < caos.py
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
SENHA = os.environ["SMOKE_SENHA"]
ACEITOS = "/tmp/caos-aceitos.json"
#: Os registros que o fluxo.py deixou no bronze antes do caos.
DO_FLUXO = 4
PRAZO = 180

falhas = 0


def chamar(metodo, url, corpo=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    dados = None if corpo is None else json.dumps(corpo).encode()
    pedido = urllib.request.Request(url, method=metodo, headers=headers, data=dados)
    try:
        with urllib.request.urlopen(pedido, timeout=20) as resposta:
            return resposta.status, resposta.headers, json.load(resposta)
    except urllib.error.HTTPError as erro:
        return erro.code, erro.headers, json.loads(erro.read() or b"{}")


def confere(condicao, descricao, detalhe=""):
    global falhas
    if condicao:
        print(f"OK      {descricao}")
    else:
        falhas += 1
        print(f"FALHOU  {descricao}" + (f" — {str(detalhe)[:300]}" if detalhe else ""))


def token(base, nome):
    codigo, _, corpo = chamar("POST", f"{base}/auth/token", {"name": nome, "password": SENHA})
    if codigo != 200:
        print(f"FALHOU  login de {nome}: {codigo}")
        sys.exit(1)
    return corpo["access_token"]


def lote():
    registro = {
        "id": str(uuid.uuid4()),
        "vin": VIN,
        "collected_at": "2026-11-07T12:00:00.482Z",
        "user": "smoke-gestora",
        "readings": [{"metric": "rpm", "unit": "rpm", "available": True, "value": 900.0}],
    }
    return registro["id"], {"records": [registro]}


def aceitos():
    try:
        with open(ACEITOS) as arquivo:
            return json.load(arquivo)
    except FileNotFoundError:
        return []


def enviar(quantos, gestora):
    ids, recusados = aceitos(), []
    for _ in range(quantos):
        record_id, corpo = lote()
        codigo, _, recibo = chamar("POST", f"{API}/telemetry/batches", corpo, gestora)
        if codigo == 202 and recibo.get("accepted") == 1:
            ids.append(record_id)
        else:
            recusados.append((codigo, recibo))
    with open(ACEITOS, "w") as arquivo:
        json.dump(ids, arquivo)
    return recusados


def ler_tudo(silver):
    itens, cursor, mais = [], None, True
    while mais:
        url = f"{BRONZE_API}/bronze/records?limit=1000" + (f"&cursor={cursor}" if cursor else "")
        codigo, _, pagina = chamar("GET", url, token=silver)
        if codigo != 200:
            return None
        itens += pagina["items"]
        cursor, mais = pagina["next_cursor"], pagina["has_more"]
    return itens


etapa = sys.argv[1]
quantos = int(sys.argv[2]) if len(sys.argv) > 2 else 1
gestora = token(API, "smoke-gestora") if etapa != "conferir" else None

if etapa == "enviar":
    recusados = enviar(quantos, gestora)
    confere(not recusados, f"202  {quantos} lotes aceitos", recusados[:1])

elif etapa == "rabbit-fora":
    codigo, headers, corpo = chamar("POST", f"{API}/telemetry/batches", lote()[1], gestora)
    confere(
        codigo == 503 and headers.get("Retry-After") == "30",
        f"{codigo}  broker fora: o envio responde 503 com Retry-After",
        (headers.get("Retry-After"), corpo),
    )
    codigo, _, saude = chamar("GET", f"{API}/health/")
    confere(
        codigo == 200 and saude.get("status") == "degraded" and saude.get("rabbitmq") != "ok",
        f"{codigo}  broker fora: o api continua no ar, degradado",
        saude,
    )
    codigo, _, _ = chamar("GET", f"{API}/vehicle/", token=gestora)
    confere(codigo == 200, f"{codigo}  broker fora: o resto da API segue")

elif etapa == "mongo-fora":
    recusados = enviar(quantos, gestora)
    confere(not recusados, f"202  Mongo fora: o api aceita {quantos} lotes", recusados[:1])
    codigo, _, saude = chamar("GET", f"{BRONZE_API}/health/")
    confere(codigo == 503 and saude.get("mongo") != "ok", f"{codigo}  health do bronze-api", saude)
    silver = token(BRONZE_API, "smoke-silver")
    codigo, headers, corpo = chamar("GET", f"{BRONZE_API}/bronze/records", token=silver)
    confere(codigo == 503, f"{codigo}  Mongo fora: a leitura da silver responde 503", corpo)

elif etapa == "conferir":
    silver = token(BRONZE_API, "smoke-silver")
    esperados = aceitos()
    inicio = time.monotonic()
    while True:
        itens = ler_tudo(silver) or []
        if len(itens) >= DO_FLUXO + len(esperados) or time.monotonic() - inicio > PRAZO:
            break
        time.sleep(2)
    lidos = [i["record_id"] for i in itens]
    faltando = set(esperados) - set(lidos)
    confere(
        not faltando,
        f"todos os {len(esperados)} lotes aceitos no caos chegaram ao bronze "
        f"(em {time.monotonic() - inicio:.0f} s)",
        f"faltam {len(faltando)}",
    )
    confere(
        len(lidos) == len(set(lidos)) == DO_FLUXO + len(esperados),
        f"aceitos = documentos, sem duplicados ({len(lidos)} no bronze)",
        f"{len(lidos)} lidos, {len(set(lidos))} distintos",
    )

else:
    print(f"FALHOU  etapa desconhecida: {etapa}")
    sys.exit(1)

sys.exit(1 if falhas else 0)
