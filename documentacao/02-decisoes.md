# Decisões

Cada entrada tem a mesma forma: **a escolha**, *por quê*, e o que foi **descartado**.
Se você discordar de alguma, o que está escrito aqui é o que precisa ser refutado.

O contexto geral: este projeto é a síntese de dois projetos reais (`gestor-phishing-backend`
e `ocs-api`). Várias decisões são correções de problemas que os dois tinham.

---

## Estrutura

### Camadas rota → serviço → repositório → modelo
Cada camada tem um motivo para existir: a rota isola o protocolo HTTP, o serviço concentra
a regra de negócio, o repositório isola o SQL. Dá para testar regra de negócio sem subir
requisição, e trocar de ORM sem reescrever regra.
**Descartado:** lógica na rota. Funciona até a segunda rota que precisa da mesma regra.

### Registro dinâmico de módulos via `settings.toml`
As listas `EXTENSIONS` e `API_V1_ROUTES` guardam caminhos de módulo, importados com
`importlib`. Estender a aplicação passa a ser **aditivo**: uma linha de configuração, sem
tocar no arquivo de inicialização — que é onde conflitos de merge e regressões se
concentram.
**Descartado:** imports fixos no `create_app()`. Mais explícito, mas todo recurso novo
mexe no mesmo arquivo.

### `app/` como raiz do `sys.path`, não pacote instalável
Mantém os imports curtos (`from ext import config`) e preserva a convenção dos projetos de
origem.
**Custo aceito:** esse fato fica codificado em cinco lugares (ver fim do
[01-arquitetura.md](01-arquitetura.md)). Transformar em pacote instalável resolveria, ao
preço de reescrever todos os imports.

### Classes genéricas (`BaseRepository[Item]`)
O type checker passa a saber que `get_by_id()` devolve `Item | None`. Isso pega camada
trocada em tempo de análise, não em produção.
**Motivo concreto:** o mypy revelou que `BaseRepository` usava `self.model.id` sem que
nada declarasse `id`. A chave primária foi para o `BaseModel` e a suposição implícita virou
contrato.

---

## Bibliotecas

### flask-smorest, não flask-restx
Ativamente mantida, baseada em marshmallow/apispec, OpenAPI 3 nativo. O flask-restx tem
manutenção irregular e atrito com versões recentes de Flask/Werkzeug.

### Container `dependency-injector`
A rota declara o serviço que precisa; o container decide como construí-lo. O retorno real
é teste: trocar uma camada por um dublê sem gambiarra —
`tests/test_container.py` prova que funciona.
**Descartado:** instanciar o serviço dentro da rota. Menos cerimônia, mas acopla
construção a uso e dificulta isolar camadas.

### PostgreSQL
Tipos mais ricos, índice parcial, `TIMESTAMPTZ` nativo. O acesso é via SQLAlchemy, então a
troca custa pouco.
**Descartado:** MySQL (o que os projetos de origem usavam) — foi herança, não escolha.

---

## Dados

### Timestamps com fuso (`TIMESTAMPTZ`)
Sem fuso na coluna, o instante é ambíguo: o container roda em UTC e o cliente não, e
ninguém sabe o que a coluna representa.
**Por que agora:** com a tabela vazia custa reescrever uma migração; com a tabela cheia
custa um `ALTER TYPE` caro. A regra `DTZ` do ruff impede `datetime` naive entrar depois.

### Exclusão lógica (`deleted`), não física
Registro excluído por engano é recuperável, e histórico referenciado por outras tabelas não
quebra. O repositório filtra `deleted = false` por padrão em toda consulta.

### Índice único **parcial** (`WHERE deleted = false`)
A unicidade vale só entre registros ativos. Sem o `WHERE`, um registro excluído
logicamente bloquearia o nome para sempre — bug sutil e difícil de diagnosticar.

### Restrição no banco, não só no serviço
A unicidade é garantida pelo PostgreSQL; o serviço traduz o `IntegrityError` em HTTP 409.
Validar apenas na aplicação deixa brecha em condição de corrida e em escrita por outro
caminho.
**Descartado:** o modelo dos projetos de origem, que validava só na camada de serviço.

### Alembic, não `create_all()`
Migração versionada é o único caminho de evolução de schema em base com dados.
**Descartado:** `db.create_all()` (um dos projetos de origem) e SQL mantido à mão (o
outro) — os dois quebram na primeira alteração. O comando `flask create-db` continua, como
atalho de desenvolvimento.
**Rede de segurança:** o CI roda `alembic check`, que reprova se os modelos divergirem das
migrações — divergência que a suíte de testes não pegaria, porque ela usa `create_all()`.

---

## Contratos da API

### Schema é a única fonte de verdade da saída
Os modelos não têm mixin de serialização; quem decide o que sai é o schema marshmallow.
**Descartado:** `safe_fields` no serviço (como num dos projetos de origem) **e** schema na
rota. Dois lugares decidindo a mesma coisa é uma divergência esperando acontecer.

### Envelope de paginação no corpo, como dataclass tipada
O cliente recebe `items` + metadados no mesmo JSON. E o serviço devolve
`service.pagination.Page`, não um dicionário.
*Por quê a dataclass:* as mesmas chaves existem no `PageSchema`. Com dicionário, renomear
um lado fazia o campo desaparecer da resposta sem erro nenhum. `tests/test_contracts.py`
reprova se os dois divergirem.
**Descartado:** `@blp.paginate()` do smorest, que põe os metadados em header.

### Paginação declarada por schema de query
Permite manter o envelope no corpo **e** ter documentação OpenAPI correta, com os limites
validados num só lugar (`app/schemas/pagination_schema.py`).

### Exceções de domínio próprias + mapa central
`NotFoundError`, `ConflictError`, `BusinessRuleError`... traduzidas em status por
`ERROR_STATUS`.
**Descartado:** reaproveitar exceções embutidas com significado trocado — os projetos de
origem usavam `PermissionError` para "não encontrado" e `FileExistsError` para
"duplicado", e por isso cada rota tinha um `try/except` gigante. Aqui as rotas não têm
nenhum.
**Rede de segurança:** `tests/test_contracts.py` reprova se uma exceção nova ficar fora do
mapa — sem isso ela cairia no handler genérico e viraria um 500 silencioso.

### Nome vazio: o schema pega a string vazia, o serviço pega o espaço em branco
`validate.Length(min=1)` reprova `""` com 422 de **erro por campo**; o
`BaseService._clean_text` apara as pontas e reprova `"   "` com 422 de **mensagem solta**.
Mesmo erro do usuário, duas formas de resposta.
**Por quê não só no schema:** o `strip` lá deixaria a CLI (`flask create-user`, que não
passa por schema nenhum) sem a validação.
**Por quê não só no serviço:** é o schema que publica o limite no OpenAPI.
**Custo aceito:** a assimetria, anotada no docstring do helper — que é também o lugar onde
a regra existe uma única vez, para todos os serviços.

### Handler de `Exception` que **não** engole `HTTPException`
Um handler genérico que capturasse `HTTPException` destruiria as respostas 422 de validação
do smorest e todo `abort()`, trocando-as por um 500 opaco.

---

## Segurança

### Proteção por padrão
A exigência de token é global no spec OpenAPI e aplicada por classe (`decorators`). As
públicas abrem mão explicitamente.
**Por quê:** antes, proteger era opt-in — esquecer `@jwt_required()` numa rota nova a
deixava aberta, sem nenhum aviso. Agora esquecer **fecha**.

### Permissões nomeadas, agrupadas em papéis
Uma permissão é uma capacidade (`item:escrever`); um papel é um pacote de permissões; o
usuário guarda o papel. A rota declara a **permissão** que exige, nunca o papel, e o mapa
`ROLE_PERMISSIONS` (em `app/authz.py`) é a única fonte de verdade.
**Por quê:** separa *quem pode* de *quem é*. Mudar a regra passa a ser reescrever um mapa
de quatro linhas, e não caçar verificações espalhadas pelas rotas. A hierarquia entre papéis
fica sendo inclusão de conjuntos, o que a torna verificável em vez de convencionada.
**Os quatro que acompanham a base:** `leitor` (só leitura), `usuario`, `gerente` e
`admin`. O `leitor` não é enfeite: sem um papel sem permissão de escrita, o **403** só
seria alcançável remendando o mapa dentro do teste, e a configuração que a base entrega
não demonstraria o próprio caminho de negação.
**Descartado:** o booleano `is_admin` que o projeto tinha — não expressa nível nenhum, e o
segundo caso de uso já pede um segundo booleano, momento em que "esse usuário pode?" passa
a ter duas respostas possíveis.
**Descartado:** níveis numéricos ordenados (`USUARIO=10, GERENTE=50, ADMIN=100`, com o
decorator exigindo "este nível ou acima"). É menos código, mas quebra no primeiro
requisito do tipo "o gerente vê tudo e altera só o que é dele" — exatamente o que
`GERENTE` faz aqui. Quando isso acontece, ou se inventa um degrau intermediário sem
significado, ou se fura o modelo.
**Descartado:** RBAC em tabelas (`roles`, `permissions`, `role_permissions`), administrável
em tempo de execução. São três tabelas, três modelos, três repositórios, CRUD, cache e
dois JOINs por requisição — para uma flexibilidade que, numa base, ninguém configura. Em
código, o grant é revisável no diff, testável e não exige migração para mudar.

### O papel mora só no banco, nunca no token
A autorização lê `current_user.role`, da linha que o `user_lookup_loader` já carregou.
Nenhuma claim de papel é assinada.
**Por quê:** antes o papel estava na coluna **e** na claim, com garantias de atualidade
diferentes na mesma requisição — excluir um usuário cortava o acesso na hora, rebaixá-lo
não tinha efeito nenhum até o token expirar. Agora as duas valem na requisição seguinte.
O SELECT já era pago de propósito (ver abaixo), então a leitura não custa nada a mais.
**Descartado:** uma claim `role` no token. Mais rápida em teoria, mas reintroduz a
divergência — e uma permissão concedida por engano continuaria valendo até o último token
emitido expirar.

### 403 para a operação, 404 para o recurso de outro usuário
O papel sem a permissão da operação responde **403** — a decisão é tomada no decorator,
antes de qualquer consulta, e não revela a existência de recurso nenhum. O recurso que
existe mas é de outro dono continua respondendo **404**.
**Por quê:** são perguntas diferentes. Um 403 no segundo caso confirmaria ao solicitante
que o recurso existe; um 404 no primeiro esconderia do próprio usuário que a rota existe e
que o problema é o perfil dele — que é informação que ele pode agir sobre.

### Escopo de ownership por chamada, sem valor padrão
O escopo é parâmetro de cada método do serviço e é obrigatório.
**Por quê sem padrão:** um default silencioso de "sem filtro" viraria vazamento de dados no
primeiro esquecimento. O filtro só é dispensado por `current_owner_scope(permissão)` — um
lugar só, e com a permissão de escopo explícita no ponto de chamada.
**Criar exige dono concreto:** `ANY_OWNER` na criação levanta `TypeError` — 500 com
traceback e `request_id` no log —, e não exceção de domínio: nenhuma requisição chega lá
(a rota de criação passa `current_user.id`), então é defeito de quem chamou, e um 422 daria
ao cliente algo sobre o que ele não pode agir.
**Descartado:** passar o usuário no construtor do serviço (como num dos projetos de
origem) — incompatível com um container de DI, que cria fábricas sem contexto de
requisição.

### Claim `sub` do JWT só com o id, em string
Exigência da RFC 7519, e versões recentes do Flask-JWT-Extended rejeitam outro formato. O
token não carrega nenhuma outra claim da aplicação (ver a decisão sobre o papel, acima).
**Descartado:** um dicionário dentro do `sub` (o que um dos projetos de origem fazia) —
funcionava, até a atualização da biblioteca.

### `current_user` custa uma consulta por requisição autenticada
O `user_lookup_loader` é chamado em toda verificação de token.
**Preço aceito deliberadamente:** é o que garante que um usuário excluído — ou rebaixado —
perca o acesso na hora, em vez de continuar entrando até o token expirar. Se o volume
justificar, a alternativa é ler o id de `get_jwt_identity()` — ao custo de precisar de uma
lista de revogação.
**Consequência:** a verificação do token é marcada em `flask.g` e acontece **uma vez por
requisição**, mesmo quando um método exige mais que o piso da classe e `permission_required`
aparece duas vezes. Sem essa marca, o usuário seria carregado duas vezes e a frase acima
deixaria de ser verdade. `tests/test_authz.py` fixa a contagem.

### Papel como VARCHAR com restrição `CHECK`, e não `ENUM` nativo do Postgres
A coluna `users.role` é `VARCHAR(20)` com `CHECK (role IN (...))`, declarada no modelo por
`sa.Enum(..., native_enum=False, create_constraint=True)`.
**Por quê:** a validação no banco é idêntica, mas acrescentar um papel vira uma troca de
restrição — DDL comum e transacional — em vez de `ALTER TYPE ... ADD VALUE`, que não pode
ser usado na mesma transação que o insere. Como acrescentar papel é a extensão mais
provável, a armadilha ficaria justo no caminho mais percorrido.
**Atenção:** `create_constraint=True` é obrigatório (o padrão do SQLAlchemy 2.0 é `False`,
e sem ele a coluna seria um VARCHAR livre), e `values_callable` também — sem ele o
SQLAlchemy grava o *nome* do membro (`"ADMIN"`), não o valor (`"admin"`).

### Segredos só por variável de ambiente
`settings.toml` é versionado e não contém segredo; valores sensíveis vêm do ambiente com
prefixo `FLASK_`, carregados do `.env` (que está no `.gitignore`).
**Por quê:** os dois projetos de origem tinham chaves e senhas de banco em texto plano no
repositório — e segredo que entrou no histórico do Git continua lá depois de "removido".

### Id de requisição do cliente é tratado como entrada hostil
O `X-Request-ID` recebido é filtrado e truncado em 64 caracteres antes de ir para o log.
*(Quebra de linha não é o risco aqui: o protocolo HTTP não permite, e o Werkzeug rejeita
antes de chegar à aplicação. O que passa são espaços, pontuação e caracteres de controle.)*

---

## Operação

### `/health/` verifica o banco, não só o processo
Processo no ar sem banco não serve requisição nenhuma. A sonda executa `SELECT 1` e
responde 503 se falhar — é o alvo do healthcheck do container e do probe do orquestrador.

### Id de requisição no log **e** no corpo do erro
Fecha o laço: o cliente relata o `request_id` que recebeu, e você reconstrói o que
aconteceu com um `grep`. Sem isso, um 500 em produção dá ao cliente "Erro interno no
servidor." e nenhuma pista.

O id entra em três lugares — header de resposta, envelope de erro e log. No log, em **toda
linha de acesso**, via `--access-logformat` do gunicorn com `%({x-request-id}o)s`. Só nas
linhas de erro não bastaria: rastrear uma requisição que não falhou (lenta, ou com status
inesperado) seria impossível.

### Lock de dependências versionado
`requirements.txt` é a lista editada à mão (com faixas); `requirements.lock.txt` congela o
conjunto exato e **é o que o Docker instala**. Sem lock, o build de amanhã pode puxar uma
versão diferente da testada hoje.

### Workers por `WEB_CONCURRENCY`, não por flag na imagem
O gunicorn lê a variável nativamente, então ajustar a concorrência não exige reconstruir a
imagem. A conta que importa está no `settings.toml`:
`workers × (pool_size + max_overflow) ≤ max_connections`.

### `COPY` explícito na imagem de produção
O estágio `runtime` copia só o que roda; o estágio `dev` acrescenta testes e ferramentas.
**Cuidado herdado de um bug real:** num Dockerfile multi-stage, `build: .` sem `target`
constrói o **último** estágio. Sem o `target: runtime` no compose, a aplicação subia
rodando pytest.

---

## Qualidade

### Testes contra PostgreSQL de verdade, não SQLite
Índice parcial, `ilike`, `TIMESTAMPTZ` e defaults de servidor se comportam diferente entre
dialetos — e são exatamente esses detalhes que precisam de cobertura. O banco de testes é
criado automaticamente na primeira subida do compose.

### Testes de contrato
Além dos funcionais, `tests/test_contracts.py` transforma duplicação de conhecimento em
falha de CI: exceção sem status mapeado, envelope de paginação ou de erro divergente do
schema, permissão concedida a papel nenhum, operação protegida sem permissão declarada e
rota aberta que ninguém declarou pública. São os erros que nenhum teste funcional pegaria,
porque não quebram nada — só silenciam.

### Testes de custo em consultas
`tests/test_write_cost.py` fixa o número de consultas das escritas, com os números medidos
no próprio arquivo. Regressão de performance deixa de ser invisível.

### mypy restrito às camadas de lógica pura
Configurado só em `service`, `repository`, `domain` e `schemas`, onde a tipagem é limpa.
**Por quê não em tudo:** blueprints e extensões dependem de decorators de
Flask/smorest/dependency-injector com tipagem imperfeita; incluí-los geraria ruído, e um
mypy ruidoso é um mypy que se aprende a ignorar. Amplie o escopo conforme as camadas forem
ficando tipadas.

### Sem `refresh()` depois do `flush()`
**Medido, não suposto.** O `commit()` do serviço expira todos os atributos
(`expire_on_commit=True`), então ler qualquer campo já recarrega a linha — com o
`refresh()`, o registro era buscado duas vezes.

| cenário | com `refresh()` | sem |
|---|---|---|
| criar + serializar (rota) | 2 SELECT | 1 SELECT |
| atualizar + serializar (rota) | 3 SELECT | 2 SELECT |
| criar 5 em lote | 10 comandos | 5 comandos |

*(A hipótese original era que o `RETURNING` do INSERT populava os defaults. A medição
refutou isso — o ganho vem de outro motivo. Os números estão no cabeçalho do arquivo de
teste.)*

---

## Apêndice: a herança dos projetos de origem

O que foi aproveitado de cada um:

- **Do `gestor-phishing-backend`:** o `BaseRepository` com CRUD completo, o `BaseService`
  com ownership, exclusão lógica e ganchos `before_*`, os campos de controle no
  `BaseModel`, a extensão de autenticação JWT, os comandos CLI, o handler global de erros,
  o envelope de paginação no corpo da resposta e as dependências com versão pinada.
- **Do `ocs-api`:** a camada flask-smorest (`MethodView` + schemas marshmallow + OpenAPI
  automático), o container `dependency-injector`, o `load_routes` que aninha blueprints sob
  um pai versionado e o pacote `schemas/` dedicado.
- **Dos dois:** o factory enxuto e o registro dinâmico de módulos via `settings.toml` — o
  esqueleto que ambos compartilham, mantido intacto.

E os problemas que os dois tinham, aqui corrigidos:

| Problema | Correção |
|---|---|
| `if value != None or value != ''` no filtro `LIKE` — condição sempre verdadeira, filtro vazio virava `LIKE '%%'` | Checagem única de valor não-vazio |
| `update` descartava valores falsy (`if value:`), impossibilitando gravar `""`, `0`, `False` ou `null` | Aplica exatamente o que o cliente enviou |
| Claim `sub` do JWT com um dicionário — viola a RFC 7519 e é rejeitado por versões recentes do Flask-JWT-Extended | `sub` só com o id, em string; nenhuma outra claim da aplicação |
| `admin_required` sem `functools.wraps` — perdia metadados e atrapalhava a introspecção do OpenAPI | `@wraps` aplicado no `permission_required`, que o substituiu |
| Handler de `Exception` engolindo `HTTPException` — destruiria os 422 de validação do smorest | `HTTPException` repassado; 500 só para exceções não-HTTP |
| Exceções embutidas com significado trocado (`PermissionError` para "não encontrado") | Exceções próprias, mapeadas em um só lugar |
| Segredos reais commitados em texto plano | Tudo via variável de ambiente |
| Sem migrações (`create_all()` ou SQL manual) | Alembic, com `alembic check` no CI |
| Sem testes, sem CI, sem Dockerfile da aplicação | Suíte de testes, CI e imagem multi-stage com usuário não-root |
| `Session.flush([objeto])` — API depreciada no SQLAlchemy 2.x | `flush()` sem argumentos |
| Chave de configuração derivada implicitamente (`api.spec.title + api.spec.version`) | Chave explícita, passada como parâmetro |
| Dependências declaradas e nunca usadas (`flask-cors` não inicializado, `WTForms` sem uso) | Só o que é efetivamente usado |

Cada linha dessa tabela tem um teste correspondente na suíte. Elas não são hipóteses: são
bugs que existiam.
