---
name: smoke-pipeline
description: Sobe o pipeline de telemetria inteiro com os processos reais — db, mongo, rabbitmq, o job setup, o api, o bronze-api e o consumer, na imagem de produção com o código atual — num projeto do compose à parte, com volumes descartáveis e sem porta no host, e o confere de ponta a ponta por HTTP — o health de cada processo, a matriz da leitura do bronze (401, 403, 404, 422), o lote que vai do app à silver sem perda, o reenvio sem repetição e a página vazia com o mesmo cursor. Use depois de mudar qualquer peça do caminho app → producer → fila → consumer → bronze → bronze-api (rotas de telemetria e do bronze, serviços de service/telemetry, messaging, repository/mongo, perfis do settings.toml, compose, Dockerfile) e antes de concluir um passo do pipeline.
allowed-tools: Bash(bash .claude/skills/smoke-pipeline/smoke.sh *)
---

# /smoke-pipeline

O `/smoke-api` prova o processo `api` sozinho, num container avulso. Este smoke prova os três
processos juntos, como o `docker compose up` os sobe: o setup prepara os armazenamentos, o api
publica, o consumer grava e o bronze-api entrega à silver. Rode a partir da raiz do repositório:

```bash
bash .claude/skills/smoke-pipeline/smoke.sh
```

Leva de 4 a 5 minutos. A parte lenta é a subida (cerca de 2 min): a primeira inicialização de
um volume novo do RabbitMQ e o job `setup`. O roteiro:

1. reconstrói as imagens do projeto `smoke-pipeline` com o código atual;
2. sobe o compose inteiro nesse projeto, com o `compose.override.yml` desta pasta: volumes
   próprios, nenhuma porta no host e a janela de estabilização do bronze-api em 2 s
   (`FLASK_BRONZE_SETTLE_SECONDS`), para o smoke não esperar os 30 s do `settings.toml`;
3. cria a gestora e o motorista (`usuario`) e a silver (`silver`) com `flask create-user` e
   senha aleatória;
4. roda o `fluxo.py` dentro do container do api, que fala com o api (`localhost:5000`) e com o
   bronze-api (`bronze-api:5000`, pela rede do projeto):
   - o health de cada processo mostra só as dependências do perfil dele;
   - a leitura do bronze não existe no api (404), o envio não existe no bronze-api (404), e a
     leitura dá 401 sem token, 403 para a gestora e 422 com cursor malformado;
   - a gestora cadastra o veículo, adiciona o motorista e envia um lote misto: 202 com 3
     aceitos e o VIN alheio em `rejected`;
   - a silver lê página a página (de 2 em 2) até receber os 3, com o remetente, o vínculo, o
     `request_id`, o `batch_id` e o bruto do envio;
   - o app reenvia o mesmo lote, e depois o motorista envia um lote novo de 1 registro. Com um
     consumer só, a fila entrega na ordem: do cursor guardado, a silver recebe só o registro
     novo, com o remetente e o vínculo (`driver`) do motorista. É a prova de que o reenvio não
     repetiu nada;
   - a página vazia devolve o cursor recebido, e a releitura do começo tem 4 registros;
5. confere a fila e a DLQ vazias e nenhum 5xx ou traceback nos logs dos três processos;
6. apaga o projeto inteiro (containers, volumes e imagens), também quando falha ou é
   interrompido.

O ambiente de desenvolvimento (projeto `base-api`) não é tocado: nem os containers, nem os
volumes, nem as imagens. O `.env` é lido pelo compose, como no `docker compose up`, e nunca
impresso.

## Lendo o resultado

| Saída | Significa | O que fazer |
|---|---|---|
| `RESULTADO: OK` (código 0) | os três processos sobem e o registro vai do app à silver uma vez só | relate o resultado |
| `FALHOU` numa linha do roteiro (código 1) | a verificação da linha não bateu; vem o detalhe e, no fim, o log dos quatro processos | trate como bug: reproduza com um teste (`/verificar testes tests/e2e/test_pipeline.py`) antes de corrigir |
| `FALHOU  a subida dos processos` | o setup falhou ou um processo não ficou saudável em 240 s; vêm os logs | migração, topologia, índice ou configuração obrigatória do perfil |
| `a silver lê os 3 aceitos` falhou com lista vazia | o lote não chegou ao bronze no prazo (60 s) | o log do consumer diz se ele consumiu; a fila e a DLQ, abaixo, dizem onde a mensagem ficou |
| `houve 5xx ou traceback` | erro interno num dos processos | o log do processo traz o traceback |
| `INFRA:` (código 2) | Docker parado ou build quebrado | resolva o ambiente; não é falha do código |

## Quando o roteiro precisa mudar

O caos (consumer, RabbitMQ ou Mongo parados no meio do fluxo) chega na Fase 5, e a correlação
pelo `request_id` nos logs, na Fase 6: as duas entram aqui, como etapas novas do `fluxo.py` ou do
`smoke.sh`. Mantenha o roteiro curto: os detalhes são do pytest (`tests/e2e/test_pipeline.py`
cobre o mesmo fluxo no processo); o smoke prova que os processos de verdade, montados pelo
compose, fazem o mesmo.
