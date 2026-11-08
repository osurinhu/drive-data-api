---
name: smoke-api
description: Sobe a API real no Docker com o código atual e a confere por HTTP de ponta a ponta — health, OpenAPI, cadastro, login JWT, renovação e logout, CRUD de veículo, os motoristas, o envio de lote de telemetria (202 e a mensagem na fila), a matriz 401/403/404/409/422 e o X-Request-ID no header, no envelope de erro e no log de acesso — num container avulso com banco e vhost do RabbitMQ descartáveis, sem tocar nos dados do banco de desenvolvimento, na fila de desenvolvimento nem no api que já está rodando. Use para ver uma mudança funcionando na aplicação de verdade (não só nos testes); antes de concluir mudança em rota, autenticação, configuração, Dockerfile, entrypoint, dependências ou migração; e quando pedirem para rodar, subir ou testar a API.
allowed-tools: Bash(bash .claude/skills/smoke-api/smoke.sh *)
---

# /smoke-api

O `/verificar` prova o código dentro do pytest. Este smoke prova outra coisa: a imagem de
produção sobe (entrypoint, migrações do zero, gunicorn, configuração do compose) e responde por
HTTP de verdade. Rode a partir da raiz do repositório:

```bash
bash .claude/skills/smoke-api/smoke.sh
```

Leva cerca de 40 s. O roteiro:

1. reconstrói as imagens `api` e `setup` com o código atual (as mesmas do `docker compose up`);
2. sobe o `db` e o `rabbitmq` do compose e aplica a topologia num vhost descartável
   (`drive-data-smoke`) com o `flask rabbitmq setup` de verdade, no serviço `setup`
   (`RABBITMQ_VHOST`, uma interpolação do compose). É ali que nasce a permissão do
   `drive_producer` nesse vhost: o usuário é do broker, com a senha do `.env`;
3. cria o banco `<POSTGRES_DB>_smoke` e sobe um container avulso (`base-api-smoke`, porta
   aleatória em 127.0.0.1) apontado para ele e para o vhost. O container roda no perfil `api` e
   migra pelo entrypoint (`RUN_MIGRATIONS=1`), sem o job `setup`. O health confere o perfil:
   `database` e `rabbitmq` ok, sem chave `mongo`;
4. cria um usuário por papel com `flask create-user` e senha aleatória;
5. roda a matriz, cada requisição com um `X-Request-ID` próprio (`smoke-<execução>-NN`). A
   telemetria envia um lote misto (o VIN do gestor e um alheio): 202 com `accepted: 1` e o
   alheio em `rejected`, e a fila `bronze.telemetry` do vhost com 1 mensagem. A contagem de
   fila quorum no `rabbitmqctl` atrasa alguns segundos, por isso o smoke espera até 15 s;
6. confere que o id volta no header e no envelope de erro e aparece no log de acesso;
7. apaga o container, o banco e o vhost, também quando falha ou é interrompido.

O `api`, o banco de desenvolvimento e o vhost `drive-data` não são tocados. Mas a imagem `api` fica reconstruída:
o próximo `docker compose up -d` recria o `api` com o código que estava no disco.

## Lendo o resultado

| Saída | Significa | O que fazer |
|---|---|---|
| `RESULTADO: OK — N verificações` (código 0) | a aplicação real responde como a matriz espera | relate o resultado |
| `FALHOU` numa linha da matriz (código 1) | status diferente do esperado; a linha traz o corpo da resposta e, abaixo, as linhas do log com o mesmo `req=` | trate como bug da aplicação: reproduza com um teste (`/verificar testes ...`) antes de corrigir |
| `a aplicação não subiu` (código 1) | o container parou ou o health não deu 200 em 90 s; vem o fim do log | migração que falha num banco vazio, import quebrado ou configuração obrigatória faltando |
| `houve 5xx` | erro interno; o fim do log traz o traceback com `[req=...]` | o traceback aponta o arquivo |
| `FALHOU  flask rabbitmq setup` | a topologia não se aplicou no vhost descartável; vem o log do comando | a API de gerenciamento recusou (o motivo vem na mensagem) ou falta configuração no serviço `setup` |
| `INFRA:` (código 2) | Docker parado, build quebrado, banco fora do ar | resolva o ambiente; não é falha do código |

## Quando a matriz precisa mudar

A matriz cobre o cadastro e a sessão (renovação, reuso, logout), `Vehicle`, que é o molde (VIN
único entre todos os donos, 403 da `silver`, escopo do admin), os motoristas e o envio de lote (202 parcial com a mensagem na fila, 403 da
`silver`, 422 do lote vazio). Entidade nova com rota própria ganha as linhas dela,
copiando as de veículo: criação, 403 da `silver`, 404 do alheio e escopo do admin.
Mantenha a matriz curta. O pytest cobre os detalhes; o smoke prova que a aplicação de verdade
responde. Mudou uma regra de status (uma rota passou a ser pública, por exemplo)? Ajuste a linha
correspondente na mesma mudança.

## Deixar a API rodando para uso manual

O smoke não mexe no `api` de desenvolvimento. Se o pedido era subir ou rodar a API, depois do
smoke verde rode `docker compose up -d --build --wait api bronze-api`. O compose roda antes o
job `setup`, que migra o banco de desenvolvimento. Indique o Swagger em
http://localhost:5000/api/v1/ (`api`) e http://localhost:5001/api/v1/ (`bronze-api`). Para
criar um usuário ali:
`docker compose exec -T api flask create-user --name <nome> --password <senha> --role <papel>`.
Peça a senha ao usuário e nunca a grave em arquivo.
