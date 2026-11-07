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

### Um nível de pasta por papel, outro por tecnologia
O primeiro nível de pasta é o papel (`repository/`). O segundo nível é a tecnologia
(`repository/sql/`) e só existe quando o mesmo papel usa mais de uma. Uma pasta também só ganha
subpasta quando os arquivos dela seguem regras próprias: outra biblioteca, outros imports
permitidos ou outra regra em `.claude/rules/`. Assim a regra, o mypy e os testes de fronteira
miram um prefixo de pacote, e um arquivo novo herda a regra da pasta onde nasce. Os repositórios
da base foram para `repository/sql/` antes de o projeto ganhar um segundo armazenamento, para que
o SQL não ficasse espalhado entre a raiz e a subpasta.
**Descartado:** tudo plano, com a tecnologia no nome do arquivo (a regra vira uma lista de nomes
que precisa ser mantida à mão); um `adapters/` no topo, à moda hexagonal (quebraria os nomes de
camada da base e as receitas); pastas por processo de deploy (o mesmo módulo serve mais de um
processo, e a composição já é decidida no `settings.toml`).
**Custo aceito:** imports um nível mais longos (`from repository.sql.user_repository import ...`).

### Um perfil por processo, escolhido por `FLASK_PROCESS`
A mesma imagem roda o `api`, o `bronze-api`, o job `setup` e, adiante, o consumer da fila. Cada
processo tem uma seção no `settings.toml` com as próprias `EXTENSIONS` e `API_V1_ROUTES`. O que
fica fora da lista não é importado, não exige configuração e não entra no health. Assim a queda
do Mongo não derruba o `api`, e o consumer sobe sem Postgres e sem chave JWT. O `[default]` é a
união dos perfis, para que a suíte exercite todos os módulos.
*Como:* `environments=True` e `env_switcher="FLASK_PROCESS"` no `FlaskDynaconf`. Sem a variável,
vale só o `[default]`.
**Rede de segurança:** um `FLASK_PROCESS` que não é seção do arquivo reprova a subida. Sozinho,
o Dynaconf cairia calado no `[default]`, e o processo subiria com tudo. O
`tests/unit/test_composicao.py` fixa as fronteiras de cada perfil.
**Custo aceito:** módulo novo entra em duas listas, a do `[default]` e a do processo que o usa.
O teste de composição reprova se as duas divergirem.
**Descartado:** uma imagem ou um repositório por processo, que seria prematuro: a separação que
importa é a dos imports, e ela é verificada. Também uma variável de ambiente para ligar ou
desligar cada extensão: a composição ficaria espalhada entre o compose e o código, sem um lugar
só para revisar.

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

### pika para o RabbitMQ, PyMongo para o Mongo
São os clientes de referência de cada serviço e são síncronos, como o Flask. O pika expõe o AMQP
sem camada por cima. Publisher confirms, ack manual e prefetch são exatamente o que o pipeline
usa.
**Descartado:** o kombu, que abstrai o broker e esconde justamente confirms e ack. Também o
aio-pika e o Motor, assíncronos, sem ganho numa aplicação síncrona. E um ODM sobre o Mongo, como
o MongoEngine: o bronze guarda o documento cru, e uma camada de modelo só atrapalharia.

### API de gerenciamento do RabbitMQ com a stdlib
Vhosts e permissões são aplicados pela API HTTP de gerenciamento, com PUTs idempotentes
(`messaging/management.py`, com `urllib`).
**Descartado:** o `requests`, que seria uma dependência de runtime nova só para alguns PUTs.
Também o `rabbitmqadmin` e o `rabbitmqctl`, CLIs que precisariam estar na imagem ou exigiriam
entrar no container do broker.

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

### Um VIN, um cadastro ativo, de qualquer dono
`uq_vehicles_vin_active` é único sobre `vin` entre os veículos ativos, sem o dono na chave
(o nome do item é único por dono).
**Por quê:** o VIN identifica o carro, e é por ele que a posse é consultada no envio
(`VehicleRepository.authorized_vins`). Dois cadastros ativos do mesmo VIN seriam dois
gestores para o mesmo carro, os dois enviando dados dele.
**Descartado:** a unicidade por dono, no molde do `Item`.
**Custo aceito:** quem cadastra primeiro bloqueia o VIN (409), mesmo que o carro não seja
dele; a disputa é resolvida pelo `admin`, que tem os escopos de veículo de todos. O 409
revela que o VIN está cadastrado, o que não é segredo: o VIN vem gravado no próprio carro.

### VIN no formato ISO 3779, sem o dígito verificador
`vin.py` aceita 17 letras e dígitos sem I, O e Q, em qualquer caixa e com espaços nas
pontas, e grava a forma canônica (maiúsculas, sem os espaços). O schema recusa o formato
com erro por campo; o serviço normaliza e recusa de novo, para quem não passa por schema,
no mesmo arranjo do nome vazio (acima). O VIN não muda depois do cadastro: o PATCH só
aceita o apelido, e o serviço recusa a troca.
**Por quê sem o dígito:** ele só é obrigatório na América do Norte. O VIN do payload de
exemplo, `93YBSR7RHEJ298116` (Renault do Brasil), seria recusado.
**Atenção:** o texto precisa ser ASCII antes do `upper()`, porque `"ſ".upper()` é `"S"` e
`"ß".upper()` é `"SS"`; `tests/unit/test_vin.py` fixa os dois casos.
**Descartado:** conferir o dígito verificador; aceitar qualquer texto até 17 caracteres
(a posse seria consultada por uma chave sem forma definida).

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
de cinco linhas, e não caçar verificações espalhadas pelas rotas. A hierarquia entre papéis
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

### O papel `silver` fica fora da hierarquia, e o `admin` não lê o bronze
A conta de sistema da camada silver tem só `bronze:ler` e `bronze:ler-de-todos`, num ramo que
não parte de papel nenhum (`_SILVER` em `authz.py`). E nenhum papel da hierarquia lê o bronze.
**Por quê:** a silver é um sistema, não uma pessoa: não cadastra veículo nem envia
telemetria. O bronze guarda dado pessoal (onde e como cada um dirige), e o `admin` precisa
resolver disputas de VIN, não ler a telemetria de todos: menor privilégio dos dois lados.
`tests/test_authz.py` reprova se os dois conjuntos se cruzarem.
**Descartado:** a silver como `usuario` com o bronze a mais (poderia cadastrar e enviar);
o bronze dentro do `admin` (a hierarquia daria a leitura a quem só administra).
**Consequência:** o papel só nasce pela CLI (`flask create-user --role silver`). O cadastro
público, quando existir, cria sempre `usuario`.

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

### `/health/` verifica cada dependência do processo; 503 só para as críticas
Processo no ar sem as suas dependências não serve requisição nenhuma, então o health verifica
cada uma de verdade, e não só o processo. Cada extensão que conecta um serviço registra a sua
sonda (`instrumentation/probes.py`), e o `/health/` executa as do processo:
`{"status": "ok", "database": "ok", "rabbitmq": "ok"}`. Com alguma fora, o `status` vira
`degraded`. O HTTP só vira 503 quando a dependência fora está em `HEALTH_CRITICAL`, que é
definido por perfil: `["database"]` no `api`, `["database", "mongo"]` no `bronze-api`.
*Por quê:* o 503 tira o processo do balanceador. O `api` sem RabbitMQ ainda serve login e
cadastro, então o broker aparece no health sem derrubá-lo; o `bronze-api` sem Mongo não serve
nada. A rota não importa a extensão de serviço externo nenhum e informa exatamente o que o
processo carregou.
`/health/live` não consulta nada: é o alvo do liveness probe, que reinicia o processo, e
reiniciar não traz uma dependência de volta.
*Prazo:* as sondas do Mongo e do RabbitMQ desistem em `HEALTH_PROBE_TIMEOUT_SECONDS` (2 s),
abaixo do timeout do healthcheck do compose (5 s). A do banco usa o pool do SQLAlchemy, como
antes.
**Rede de segurança:** o `test_contracts.py` reprova sonda registrada sem campo no
`HealthSchema`. Sem o campo, a chave sumiria da resposta, porque o schema é a única fonte da
saída. O `test_composicao.py` reprova um `HEALTH_CRITICAL` com dependência que o perfil não
carrega.
**Descartado:** o `health.py` importar `ext.mongo` e `ext.rabbitmq`: a rota passaria a exigir
os dois em todo processo. Também responder 503 com qualquer dependência fora, o que tiraria o
`api` do ar por causa do broker.

### A sonda do RabbitMQ abre uma conexão nova a cada chamada
A conexão bloqueante do pika não é segura entre threads e só atende o heartbeat quando é usada.
Guardada entre chamadas do health, ela ficaria ociosa até o broker derrubá-la. Uma conexão nova
não guarda estado e prova numa ida só que o broker atende, aceita a credencial e tem o vhost.
**Custo aceito:** um handshake AMQP por chamada do health, desprezível na frequência de um
healthcheck. Por isso o `/health/` não pode ficar aberto na internet. Ele é público, e cada
chamada anônima abriria uma conexão e poderia prender um worker até o prazo das sondas
(README, "Antes de ir para produção").
**Descartado:** guardar a conexão e reabrir quando cair. Seria estado compartilhado, e a sonda
passaria a dizer se a conexão guardada vive, não se o broker atende.

### Cliente do Mongo sem conexão até o primeiro uso, e um novo depois de um fork
O `MongoClient` não sobrevive a um fork: o pool e as threads de monitoramento ficam no processo
que o abriu, e o PyMongo recomenda que cada processo tenha o seu. A extensão cria o cliente na
subida com `connect=False`. Assim o PyMongo valida a URI e as opções, mas não abre conexão nem
thread e não consulta o DNS até a primeira operação. Se o pid mudou, a extensão cria outro.
Montar a aplicação não conecta a nada, e um comando da CLI que não usa o Mongo não abre conexão.
O cliente devolve datas com fuso (`tz_aware=True`), pelo mesmo motivo do `TIMESTAMPTZ`.
**URI malformada reprova a subida com mensagem própria:** a mensagem do PyMongo pode repetir
pedaços da senha. Uma senha com `/` sem codificação vira caminho na URI, e o erro cita o
caminho. O mesmo vale para a URL do RabbitMQ (`messaging/connection.py`).
**Descartado:** criar o cliente só no primeiro uso, sem nada na subida. Uma URI malformada só
apareceria na primeira requisição, com a mensagem do PyMongo. Também criar o cliente já
conectado no `init_app`: funciona com o gunicorn sem `--preload`, que monta a aplicação em cada
worker, mas quebra em silêncio no dia em que alguém ligar o `--preload`.

### Migrações num job `setup`, não na subida de cada processo
Com dois processos HTTP, e réplicas deles, migrar na subida faria vários `alembic upgrade`
disputarem o mesmo banco vazio. O serviço `setup` do compose (`docker/setup.sh`) migra e
termina. O `api` e o `bronze-api` sobem com `RUN_MIGRATIONS=0` e só depois que o `setup`
termina com sucesso (`service_completed_successfully`). É também onde entram a topologia do
RabbitMQ e os índices do Mongo, quando existirem.
**O padrão do entrypoint continua `RUN_MIGRATIONS=1`:** um container avulso da imagem, como o
do `/smoke-api`, migra sozinho, como sempre fez.
**Descartado:** migrar na subida de cada processo com uma trava no banco
(`pg_advisory_lock`). Resolve a disputa, mas mantém a migração na subida de toda réplica, e uma
migração demorada atrasaria cada uma.

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
*Dependência nova não atualiza as outras:* o comando do cabeçalho do lock instala o lock atual
junto com o `requirements.txt` antes do `pip freeze`. Só a dependência nova (e o que ela traz)
entra com versão nova. Para atualizar tudo, tira-se o `-r requirements.lock.txt` do comando, de
propósito e numa mudança só disso.

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
O mesmo vale para o Mongo e o RabbitMQ: a suíte usa os serviços reais, mas no banco
`<MONGO_DATABASE>_test` e no vhost `drive-data-test`, que ela mesma cria pela API de
gerenciamento. Os dados de desenvolvimento ficam intactos.
**Rede de segurança:** a limpeza do Mongo entre testes recusa rodar num banco cujo nome não
termine em `_test`.

### Testes separados pela infraestrutura que exigem
A raiz de `tests/` usa Postgres e HTTP, `tests/unit/` não usa serviço nenhum, `tests/mongo/` e
`tests/rabbitmq/` usam o serviço do nome, e `tests/e2e/` usa os três. O `conftest.py` de cada
pasta prepara só a sua infraestrutura. As pastas que não usam Postgres anulam o `_clean_tables`
autouse da raiz, e a limpeza do Mongo e o vhost de testes são autouse só nas pastas deles. As
fixtures compartilhadas ficam no `conftest.py` da raiz, sem autouse. Elas também não puxam outra
infraestrutura: o `mongo_db` sai de uma aplicação mínima, só com `ext.mongo`. Na raiz, as sondas
do Mongo e do RabbitMQ do `test_health.py` são dublês; o health com as três de verdade está em
`tests/e2e/`.
*Por quê:* um teste de Postgres não depende do Mongo, e um teste unitário não toca em serviço
nenhum: `/verificar testes tests/unit` mostra em segundos se a lógica pura passa.
**Custo aceito:** sem `__init__.py` nas pastas, o pytest exige nomes de arquivo únicos na suíte
inteira; dois `test_writer.py` reprovam a coleta.
**Descartado:** marcadores (`@pytest.mark.mongo`) numa pasta só. Precisariam ser lembrados em
cada teste, e as fixtures autouse continuariam valendo para todos.

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
Configurado só em `service`, `repository`, `domain` e `schemas`, onde a tipagem é limpa, e nos
pacotes neutros `messaging` e `instrumentation`, que não usam esses decorators.
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
