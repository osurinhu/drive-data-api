---
name: smoke-api
description: Sobe a API real no Docker com o código atual e a confere por HTTP de ponta a ponta — health, OpenAPI, login JWT, CRUD de item, a matriz 401/403/404/409/422 e o X-Request-ID no header, no envelope de erro e no log de acesso — num container avulso com banco descartável, sem tocar nos dados do banco de desenvolvimento nem no api que já está rodando. Use para ver uma mudança funcionando na aplicação de verdade (não só nos testes); antes de concluir mudança em rota, autenticação, configuração, Dockerfile, entrypoint, dependências ou migração; e quando pedirem para rodar, subir ou testar a API.
allowed-tools: Bash(bash .claude/skills/smoke-api/smoke.sh *)
---

# /smoke-api

O `/verificar` prova o código dentro do pytest. Este smoke prova outra coisa: a imagem de
produção sobe (entrypoint, migrações do zero, gunicorn, configuração do compose) e responde por
HTTP de verdade. Rode a partir da raiz do repositório:

```bash
bash .claude/skills/smoke-api/smoke.sh
```

Leva cerca de 30 s. O roteiro:

1. reconstrói a imagem `api` com o código atual (é a mesma do `docker compose up`);
2. cria o banco `<POSTGRES_DB>_smoke` no container `db` e sobe um container avulso
   (`base-api-smoke`, porta aleatória em 127.0.0.1) apontado para ele;
3. cria um usuário por papel com `flask create-user` e senha aleatória;
4. roda a matriz, cada requisição com um `X-Request-ID` próprio (`smoke-<execução>-NN`);
5. confere que o id volta no header e no envelope de erro e aparece no log de acesso;
6. apaga o container e o banco, também quando falha ou é interrompido.

O `api` e o banco de desenvolvimento não são tocados. Mas a imagem `api` fica reconstruída:
o próximo `docker compose up -d` recria o `api` com o código que estava no disco.

## Lendo o resultado

| Saída | Significa | O que fazer |
|---|---|---|
| `RESULTADO: OK — N verificações` (código 0) | a aplicação real responde como a matriz espera | relate o resultado |
| `FALHOU` numa linha da matriz (código 1) | status diferente do esperado; a linha traz o corpo da resposta e, abaixo, as linhas do log com o mesmo `req=` | trate como bug da aplicação: reproduza com um teste (`/verificar testes ...`) antes de corrigir |
| `a aplicação não subiu` (código 1) | o container parou ou o health não deu 200 em 90 s; vem o fim do log | migração que falha num banco vazio, import quebrado ou configuração obrigatória faltando |
| `houve 5xx` | erro interno; o fim do log traz o traceback com `[req=...]` | o traceback aponta o arquivo |
| `INFRA:` (código 2) | Docker parado, build quebrado, banco fora do ar | resolva o ambiente; não é falha do código |

## Quando a matriz precisa mudar

A matriz cobre `Item`, que é o molde. Entidade nova com rota própria ganha as linhas dela,
copiando as de item: criação, 403 do `leitor`, 404 do alheio, escopo do gerente e do admin.
Mantenha a matriz curta. O pytest cobre os detalhes; o smoke prova que a aplicação de verdade
responde. Mudou uma regra de status (uma rota passou a ser pública, por exemplo)? Ajuste a linha
correspondente na mesma mudança.

## Deixar a API rodando para uso manual

O smoke não mexe no `api` de desenvolvimento. Se o pedido era subir ou rodar a API, depois do
smoke verde rode `docker compose up -d --build --wait api`. A imagem já está pronta, então é
rápido. Indique o Swagger em http://localhost:5000/api/v1/. Para criar um usuário ali:
`docker compose exec -T api flask create-user --name <nome> --password <senha> --role <papel>`.
Peça a senha ao usuário e nunca a grave em arquivo.
