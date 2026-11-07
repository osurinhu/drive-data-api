# Documentação

| Arquivo | Para quê | Leia quando |
|---|---|---|
| [01-arquitetura.md](01-arquitetura.md) | Como o projeto é organizado: camadas, fluxo de uma requisição, e o que cada camada **pode e não pode** fazer | Ao chegar no projeto, antes de escrever a primeira linha |
| [02-decisoes.md](02-decisoes.md) | Por que cada escolha foi feita, e qual alternativa foi descartada | Antes de mudar algo estrutural — ou quando algo parecer estranho |
| [03-como-estender.md](03-como-estender.md) | Passo a passo para cada tipo de mudança (entidade, rota, consulta, regra, extensão, campo, comando, configuração, autorização) | Toda vez que for implementar algo |

O [README da raiz](../README.md) é o runbook: como subir, rodar testes, migrar, configurar.
Esta pasta é o resto: entender e estender.

---

## Em uma página

```
Cliente HTTP
   │
   ├─► Rota         exige a permissão, valida a entrada, chama o serviço
   │    └─► Serviço     aplica regra de negócio e ownership
   │         └─► Repositório   monta e executa a consulta
   │              └─► Modelo       mapeia a tabela
   │                   └─► PostgreSQL
   └─◄ Schema      serializa a saída
```

Cada camada só conhece a de baixo.

E, atravessando tudo, o segundo eixo: a aplicação **não** sabe quais extensões e rotas
existem — ela lê duas listas do `settings.toml` e importa cada módulo. Por isso
**estender é sempre aditivo**: uma linha nova na configuração, nunca uma alteração no
arquivo de inicialização.
