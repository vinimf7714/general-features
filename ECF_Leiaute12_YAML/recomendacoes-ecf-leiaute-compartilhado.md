# ECF — Hierarquia do Bloco M e leiaute compartilhado entre Lambdas

> Recomendações para reduzir o custo de manutenção da árvore pai/filho (código sequência hierarquia / código sequência hierarquia pai) na geração do SPED-ECF, mantendo a arquitetura de microsserviços (Lambdas em repositórios separados).

## Resumo

**Sim, é possível sair da árvore de ponteiros (hash e hash pai), e é isso que resolve o problema de manutenção.** Só centralizar o dicionário melhora, mas os lambdas continuam calculando hierarquia. O ganho grande vem de tirar dos produtores a responsabilidade pela hierarquia:

1. A hierarquia passa a estar no **leiaute versionado**, e não nos dados nem nos produtores.
2. A **sort key em formato de caminho** (caminho materializado) dá a ordem do arquivo de graça.
3. A **library no CodeArtifact** distribui o leiaute e os helpers com versão fixada e testada.

Os microsserviços continuam separados, mas a mudança de hierarquia passa a acontecer num lugar só.

---

## 1. Qual é o problema de verdade

Hoje cada lambda precisa saber três coisas:

1. Que registro ele produz (correto).
2. Quem é o pai desse registro no leiaute (vazou da camada de layout).
3. Qual é o hash do pai que outro lambda gerou (acoplamento entre peças).

Os itens 2 e 3 obrigam a mexer em vários repositórios a cada mudança. No SPED, a hierarquia **não está no arquivo como ponteiro**: ela aparece só pela **ordem das linhas** (o pai vem antes, os filhos logo abaixo) e pelo nível de cada registro definido no leiaute. Então o gerador não precisa de uma árvore de hashes. Precisa só de duas coisas:

- receber os registros **na ordem certa**;
- saber, pelo leiaute, qual registro é filho de qual.

### Níveis do Bloco M (referência)

| Registro | Descrição | Nível |
|---|---|---|
| M001 | Abertura do Bloco M | 1 |
| M010 | Identificação da conta na Parte B do e-Lalur e do e-Lacs | 2 |
| M030 | Identificação dos períodos e formas de apuração | 2 |
| M300 | Lançamentos da Parte A do e-Lalur | 3 |
| M305 | Conta da Parte B do e-Lalur | 4 |
| M310 | Contas contábeis relacionadas ao lançamento da Parte A | 4 |
| M312 | Números dos lançamentos relacionados à conta contábil | 5 |
| M315 | Processos judiciais e administrativos do lançamento | 4 |
| M350 | Lançamentos da Parte A do e-Lacs (filhos M355, M360, M362, M365 espelham M305, M310, M312, M315) | 3 |
| M410 | Lançamentos na Parte B sem reflexo na Parte A | 3 |
| M415 | Processos judiciais e administrativos do lançamento na Parte B | 4 |
| M500 | Controle de saldos das contas da Parte B | 3 |
| M510 | Controle de saldos das contas padrão da Parte B | 3 |
| M990 | Encerramento do Bloco M | 1 |

> As fontes de leiaute consultadas informam o **nível hierárquico**, mas não nomeiam explicitamente o pai de cada registro. As relações pai/filho acima foram deduzidas dos níveis e da ordem dos registros. **Confirme no manual oficial da Receita na versão de leiaute vigente.**

---

## 2. Como sair da árvore: uma sort key que guarda o caminho até o registro

A sort key de cada item passa a ser o **caminho de negócio** até ele, montado com as chaves naturais de cada nível, e não com hashes:

```
PK = 341#20250131
SK = M030=A00#M300=0093                                   → M300
SK = M030=A00#M300=0093#M305=CTAB-001                     → M305 (filho)
SK = M030=A00#M300=0093#M310=1.01.01.01|CC01              → M310 (filho)
SK = M030=A00#M300=0093#M310=1.01.01.01|CC01#M312=000123  → M312
SK = M030=A00#M300=0093#M315=1|0001234                    → M315
```

### O que você ganha

- **A ordenação lexicográfica do DynamoDB já devolve tudo na ordem do arquivo.** Um único `Query` em `PK = 341#20250131` (ou `begins_with(SK, "M")` para o bloco M) traz os itens prontos para serializar. O gerador vira um laço que escreve linha por linha, sem montar árvore em memória.
- **Nenhum produtor precisa conhecer o hash do pai.** O caminho sai dos dados de negócio que o lambda já tem (período, código do lançamento, conta). Um lambda que precisa do resultado do anterior consulta por prefixo (`begins_with(SK, "M030=A00#M300=")`) em vez de procurar hashes.
- **Idempotência de graça.** A chave é determinística, então um retry do Step Functions sobrescreve o mesmo item e não cria duplicata.
- **Mudança de hierarquia vira mudança no leiaute**, não em N lambdas.

### Cuidados práticos

- **Ordem entre irmãos:** o número do registro (305 < 310 < 315) já ordena os filhos do M300. Mesmo assim, guarde um campo `ordem` explícito no leiaute para os casos em que a numeração não bater com a ordem que o manual exige.
- **Padding:** códigos numéricos precisam ter tamanho fixo (`0093`, não `93`). Senão `100` fica antes de `93` na ordenação.
- **Separadores:** reserve `#`, `=` e `|` e garanta que eles não aparecem nas chaves de negócio (ou escape).
- **Reprocessamento:** se uma conta deixa de existir, o item antigo continua lá. Grave um `execution_id` em cada item e faça o gerador filtrar, ou apague o prefixo do bloco antes de regravar.

### Exemplo de item no DynamoDB (tabela 001)

```json
{
  "PK": "341#20250131",
  "SK": "M030=A00#M300=0093#M310=1.01.01.01|CC01",
  "registro": "M310",
  "nivel": 4,
  "leiaute": "ECF-v11",
  "execution_id": "arn:aws:states:sa-east-1:123456789012:execution:ecf-pipeline:abc123",
  "dados": {
    "COD_CTA": "1.01.01.01",
    "COD_CCUS": "CC01",
    "VL_CTA": "15000.00",
    "IND_VL_CTA": "D"
  }
}
```

---

## 3. Onde fica o dicionário

Com o caminho materializado, o dicionário deixa de ser "quem é pai de quem para eu calcular o hash" e passa a ser **a especificação do leiaute**: nível, pai, ordem e campos-chave de cada registro. Os produtores só usam uma função `build_path(...)`, e quem usa o leiaute de fato é o gerador.

### Exemplo de especificação de leiaute

> Nomes de campos ilustrativos: confira no manual do leiaute vigente.

```json
{
  "leiaute": "ECF-v11",
  "vigencia": { "ano_calendario_inicio": 2024, "ano_calendario_fim": null },
  "separadores": { "nivel": "#", "chave": "=", "composta": "|" },
  "registros": {
    "M001": { "nivel": 1, "pai": null,   "ordem": 1,   "chave": [] },
    "M010": { "nivel": 2, "pai": "M001", "ordem": 10,  "chave": ["COD_CTA_B"] },
    "M030": { "nivel": 2, "pai": "M001", "ordem": 30,  "chave": ["PER_APUR"] },
    "M300": { "nivel": 3, "pai": "M030", "ordem": 300, "chave": ["CODIGO"], "pad": { "CODIGO": 4 } },
    "M305": { "nivel": 4, "pai": "M300", "ordem": 305, "chave": ["COD_CTA_B"] },
    "M310": { "nivel": 4, "pai": "M300", "ordem": 310, "chave": ["COD_CTA", "COD_CCUS"] },
    "M312": { "nivel": 5, "pai": "M310", "ordem": 312, "chave": ["NUM_LCTO"] },
    "M315": { "nivel": 4, "pai": "M300", "ordem": 315, "chave": ["IND_PROC", "NUM_PROC"] },
    "M990": { "nivel": 1, "pai": null,   "ordem": 990, "chave": [], "calculado_pelo_gerador": true }
  }
}
```

### Comparando as opções de distribuição

| | Library no CodeArtifact | JSON no S3 | AWS AppConfig |
|---|---|---|---|
| Mudança sem redeploy | ❌ | ✅ | ✅ |
| Testável no CI de cada lambda | ✅ | fraco | médio |
| Risco de uma edição errada quebrar todos os lambdas de uma vez | baixo (versão fixada) | **alto** | médio (tem validador e rollback) |
| Reprodutibilidade ("qual mapa gerou o arquivo de jan/25?") | ✅ versão no lock | precisa disciplina | ✅ versão de deployment |
| Comporta lógica (`build_path`, validação) | ✅ | ❌ só dado | ❌ só dado |

### Recomendação: library no CodeArtifact

Um repositório próprio (`ecf-layout`), contendo:

- os arquivos de leiaute como dados empacotados, **um arquivo imutável por versão de leiaute** (`v10.json`, `v11.json`...). Isso atende o requisito de não alterar versões anteriores: nunca se edita uma versão publicada, cria-se a próxima;
- `resolve_layout(ano_calendario)`, `build_path(registro, dados)` e `validate_item(item)`;
- testes no CI que checam a consistência do leiaute (todo pai existe, níveis coerentes, ordem sem colisão).

**Por que não o S3 puro:** na prática, quando a Receita muda o leiaute, quase sempre muda também a lógica de cálculo de algum lambda. Ou seja, vai haver deploy de qualquer jeito, e ter o mapa versionado junto com o código é mais seguro que um JSON que muda sem ninguém rodar teste.

- Use o **AppConfig** só se aparecer uma necessidade real de mudar algo em runtime sem deploy.
- Evite **Lambda Layer**: é mais difícil de versionar e de compartilhar entre contas no modelo de data mesh. O CodeArtifact aceita domain policy cross-account.
- Para não esquecer lambdas em versão antiga, use **Dependabot ou Renovate** abrindo PR automático de bump nos repositórios.

---

## 4. O caso M300, M305 e M310 no elalur-elax

Como o elalur-elax gera o M300 **e** seus N filhos na mesma execução, a relação pai-filho é local:

```python
layout = resolve_layout(ano_calendario)

m300_path = build_path("M300", {"PER_APUR": per, "CODIGO": cod}, layout)
itens = [item(m300_path, m300_dados)]

for conta in contas_parte_b:
    itens.append(item(build_path("M305", {"COD_CTA_B": conta.cod}, layout, parent=m300_path), conta))

for conta in contas_contabeis:
    itens.append(item(build_path("M310", {"COD_CTA": conta.cod, "COD_CCUS": conta.ccus}, layout, parent=m300_path), conta))

batch_write(itens)   # chaves determinísticas → idempotente
```

- Não há busca de pai entre lambdas nem hash para propagar.
- Para N grande, use `BatchWriteItem` em lotes de 25, com retry dos itens não processados.
- O `TransactWriteItems` (até 100 itens) só vale se for preciso que o M300 e os filhos sejam gravados de forma tudo-ou-nada.
- Regra de negócio a validar: o VALOR do M300 deve bater com a soma dos M305 e/ou M310 conforme o `IND_RELACAO`.
- Bônus: o mesmo leiaute permite ao gerador calcular sozinho os totais (M990, 9900, 9999), e ninguém mais precisa manter contador.

---

## 5. Validação antes de gerar

Coloque um estado no Step Functions **antes** do gerador que percorre o prefixo e, usando o `validate_item` da library, aponta:

- órfãos (caminho cujo pai não existe);
- registros obrigatórios ausentes;
- chaves fora do padrão.

Isso conversa direto com o logging de ocorrências de negócio: cada inconsistência vira uma ocorrência por execução e por registro, visível no front.

---

## 6. Migração sem big bang

1. Publique a `ecf-layout` com o leiaute atual.
2. Adapte o gerador para ler pelo caminho materializado, mantendo o modo antigo por trás de uma flag.
3. Migre um lambda por vez, começando pelo elalur-elax, que é o que mais dói. Durante a transição, ele pode gravar os dois formatos.
4. Compare o TXT gerado pelos dois caminhos byte a byte nos testes integrados. Diferença zero libera a remoção do modo antigo.
5. No fim, remova o `codigo_sequencia_hierarquia` e o `codigo_sequencia_hierarquia_pai`.
