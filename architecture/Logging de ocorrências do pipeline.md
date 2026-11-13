Sumário

1. [Contexto e objetivo](#contexto)
2. [Requisitos e premissas](#requisitos)
3. [Decisão de arquitetura](#decisao)
4. [Arquitetura](#arquitetura)
5. [Fluxo de uma execução](#fluxo)
6. [Modelagem DynamoDB](#modelo)
7. [Shared library](#lib)
8. [Contrato e ASL](#contrato)
9. [Progresso e ETA](#progresso)
10. [API de leitura e polling](#leitura)
11. [Segurança e auditoria](#seguranca)
12. [Riscos e decisões em aberto](#riscos)
13. [Plano de implementação](#plano)

Proposta técnica · v1 · 13/11/2026

# Logging de ocorrências do pipeline com shared library

Registrar warnings, erros e o andamento de cada etapa do pipeline orquestrado por Step Functions, com rastreabilidade por execução, empresa, etapa e registro, e exibir o progresso ao cliente em tempo real, sem expor dados de conta.

**Autor:** Vinicius Custodio (tech lead) **Status:** rascunho para revisão do time **Stack:** Step Functions Standard · Lambda (Python) · DynamoDB · KMS · S3

## 01Contexto e objetivo

O front-end cadastra os dados das empresas (bloco zero). A partir do bloco M, uma Step Function executa uma sequência de Lambdas: a Lambda A lê o bloco zero e cria registros; a B lê os registros pais e gera os filhos; a C consolida, e assim por diante. Cada Lambda pode produzir WARNING (o processamento segue) ou ERROR (a etapa e o fluxo falham).

A Step Function recebe um payload com os blocos declarados para a execução. Novos blocos vão entrar no meio do fluxo ao longo do tempo.

**Objetivo desta proposta:** definir como registrar essas ocorrências e o ciclo de vida de cada etapa numa tabela DynamoDB, de forma que o front consiga consultar o histórico e acompanhar o progresso enquanto a execução roda, com acoplamento baixo, garantia de entrega e controles adequados a um ambiente bancário regulado.

## 02Requisitos e premissas

### Requisitos funcionais

- Registrar início, fim, warnings, erros de negócio e falhas técnicas de cada etapa.
- Consultar ocorrências por execução, por empresa, por etapa e por severidade.
- Rastrear em qual Lambda, em qual registro (pai/filho) e em qual execução cada ocorrência aconteceu.
- Identificar contas inconsistentes sem gravar o número da conta no log.
- Mostrar ao cliente, em tempo real, o andamento, o percentual e o tempo estimado restante.
- Acrescentar etapas ao fluxo sem alterar front, polling ou cálculo de progresso.

### Requisitos não funcionais

- Nenhuma perda silenciosa de log: se o registro falha, a etapa falha.
- Dados de cliente fora do payload da Step Function, do histórico de execução e da trilha imutável.
- Trilha de auditoria imutável, com retenção definida pelo compliance.
- Idempotência sob retry da Step Function e redrive.

### Premissas (confirmar)

| Tema | Premissa | Se mudar |
| --- | --- | --- |
| Tipo de workflow | Standard | Express é *at-least-once* e não tem API de histórico. A idempotência da lib já cobre, mas revisar. |
| Volume | Centenas de execuções por dia, até \~1.000 registros e \< 200 ocorrências por etapa | Acima disso, revisar limite de escrita de progresso e shard do GSI de severidade. |
| Ordem dos blocos | Sequencial, na ordem do payload | Com dependências (blocos em paralelo), o ETA passa a ser o caminho crítico. Ver seção 9. |
| Consumidores | Só o front | Com alertas, BI ou notificações, avaliar publicar eventos (EventBridge) a partir do DynamoDB Streams. |
| Retenção | Longa, com trilha imutável. Prazo a definir com compliance. | Ajusta TTL da janela quente e retenção do Object Lock. |
| Classificação do número de conta | Dado pessoal coberto por sigilo bancário. Nível de classificação interno a confirmar com Segurança da Informação/DPO. | Pode exigir controles adicionais no cofre e na API de leitura. |

## 03Decisão de arquitetura

**Decisão:** cada Lambda de negócio registra as próprias ocorrências por meio de uma **shared library** (pacote Python publicado como Lambda Layer). A Step Function só registra o que a Lambda não consegue registrar sozinha: a falha técnica (crash, timeout) no `Catch` e o status final. Uma regra do EventBridge cobre execuções abortadas ou com timeout geral.

| Opção | Acoplamento | Garantia de entrega | Dado sensível fora da Step Function | Peças a manter | Veredito |
| --- | --- | --- | --- | --- | --- |
| Eventos (EventBridge/SNS/SQS) | Baixo | Alta, com DLQ | Sim | Muitas | Exagerado para um único consumidor |
| Cada Lambda grava direto, sem lib | Alto | Média | Sim | Código repetido | Descartada |
| Lambda logger assíncrona (`InvocationType: Event`) | Médio | **Baixa**: perda silenciosa sem DLQ, atraso de até horas | Não (ocorrências passam pelo payload) | +1 Lambda | Descartada |
| Lambda logger síncrona | Baixo | Alta | Só se a lib mascarar antes do retorno | +1 Lambda + lib | Viável |
| Contrato + `dynamodb:putItem` nativo | Baixo | Alta | Só se a lib mascarar antes | ASL extenso e frágil | Viável, difícil de manter |
| CloudWatch estruturado + subscription filter | Baixo | Média, com atraso | Não | Médio | Só para log técnico |
| **Shared library + Catch nativo (híbrido)** | Baixo (encapsulado na lib) | Alta | **Sim**: ocorrências não saem da Lambda | 1 lib | **Escolhida** |

**Por que o híbrido:**

- As ocorrências nunca passam pelo payload nem pelo histórico da Step Function. Nem mascaradas.
- Schema, conexão, mascaramento, HMAC e validação de catálogo ficam num único pacote versionado. As Lambdas só escrevem regra de negócio.
- O ASL fica curto: uma Task, um Catch e um Choice por etapa.
- A falha técnica, que a Lambda não consegue registrar, fica com o `Catch`, que é garantido pela Step Function.

**Custo da escolha:** várias Lambdas escrevem na tabela (mudança de schema exige subir a nova versão da lib em todas) e cada role de Lambda precisa de permissão de escrita na tabela de ocorrências.

**Log técnico** (stack trace, debug) continua no CloudWatch via Powertools, com o nome da execução como correlation id. Ele não substitui a tabela de ocorrências.

## 04Arquitetura

Só a tabela de negócio e o cofre guardam o número da conta. A Step Function transporta chaves técnicas, as Lambdas gravam ocorrências pela lib, o Catch cobre crash e timeout, e o EventBridge cobre o que nem o Catch vê.

### Componentes

| Componente | Responsabilidade |
| --- | --- |
| API de início | Valida acesso à empresa, cria o `RESUMO` com o plano tirado de `payload.blocos`, chama `StartExecution`. |
| Step Functions | Orquestra as etapas, injeta o contexto de rastreio (`_rastro`) em cada Task, registra falha técnica no `Catch` e o status final. |
| Lambdas de negócio + lib | Executam a regra e registram início, fim, progresso, warnings e erros pela lib. |
| `pipeline-rastro` (shared library) | Escrita na tabela, mascaramento, HMAC, cofre, validação de catálogo, limite de escrita de progresso, estatísticas de duração. |
| `tb_ocorrencias` | RESUMO por execução, eventos, falhas técnicas. Janela quente com TTL. |
| `tb_pipeline_config` | Catálogo de códigos de ocorrência e estatísticas de duração por etapa. |
| `tb_cofre_contas` + chave HMAC no KMS | `contaRef → conta`, CMK própria, acesso para poucos perfis, retenção igual à do log. |
| API de leitura | Autoriza por empresa, lê RESUMO e eventos novos, calcula progresso e ETA, resolve conta completa só para perfil autorizado. |
| Regra EventBridge + Lambda rede | Marca `FALHA` no RESUMO quando a execução falha, expira ou é abortada sem passar pelo Catch. Dispara alerta. |
| Streams → Pipes → Firehose → S3 | Arquiva cada item no momento da inserção, com Object Lock, para auditoria. |

## 05Fluxo de uma execução

| # | Momento | Quem grava | O que é gravado |
| --- | --- | --- | --- |
| 1 | Front dispara o processo | API de início | `RESUMO` com `status = INICIADO`, plano (blocos do payload, todos `PENDENTE`), `qtdRegistros` |
| 2 | Lambda A começa | lib | Evento ETAPA_INICIADA; RESUMO: etapa `EM_ANDAMENTO`, execução `EM_ANDAMENTO` |
| 3 | Lambda A termina | lib | Evento ETAPA_FINALIZADA com duração; RESUMO: etapa `CONCLUIDA`; estatística da etapa atualizada |
| 4 | Lambda B começa | lib | ETAPA_INICIADA |
| 5 | Lambda B encontra inconsistências | lib (`rastro.warning`) | Eventos WARNING com `registroId`, conta mascarada e `contaRef`; contador `qtdWarnings` |
| 6 | Lambda B processando | lib (`rastro.progresso`) | RESUMO: `processados / total` da etapa, no máximo a cada 5 s ou 5% |
| 7 | Lambda B termina | lib | ETAPA_FINALIZADA |
| 8 | Erro de regra em qualquer etapa | lib (`rastro.erro`) | Evento ERROR; etapa e execução `FALHA`. A Lambda devolve `FALHA` e o Choice leva ao Fail. |
| 9 | Crash ou timeout da Lambda | lib (melhor esforço) + Catch | A lib grava o evento FALHA_TECNICA da tentativa. Esgotados os retries, o Catch grava o item `FALHA#` definitivo e marca o RESUMO. |
| 10 | Todas as etapas concluídas | Último estado da Step Function | RESUMO: `status = CONCLUIDO`, `concluidoEm` |
| 11 | Execução abortada ou com timeout geral | Lambda rede de segurança | RESUMO: `status = FALHA`, `motivo = ABORTED \| TIMED_OUT`; alerta |

Em paralelo, o front consulta a API de leitura a cada poucos segundos e para quando o status for `CONCLUIDO` ou `FALHA`.

## 06Modelagem DynamoDB

### `tb_ocorrencias`: tipos de item

| Item | PK | SK | Escrito por |
| --- | --- | --- | --- |
| Resumo da execução | `EXEC#<execucao>` | `RESUMO` | API de início, lib, Catch, estado final, rede |
| Evento (ciclo de vida, warning, erro, falha de tentativa) | `EXEC#<execucao>` | `EV#<ts ISO ms>#<etapa>#T<redrive>.<retry>#<seq>` | lib |
| Falha técnica definitiva | `EXEC#<execucao>` | `FALHA#<etapa>#R<redrive>` | Catch |

- A SK de evento começa pelo timestamp para ficar cronológica: o polling busca só o que é novo com `SK > cursor`.
- A SK usa o **código** da etapa, nunca a ordem. Inserir um bloco no meio não exige renumerar nada.
- `T<redrive>.<retry>` separa as gravações de cada tentativa. Um retry gera eventos novos (a tela mostra "tentativa 2"), e uma retentativa do SDK dentro da mesma tentativa sobrescreve o mesmo item.
- O nome da execução usa o `empresaId` interno e um ULID, nunca CNPJ: ele aparece em ARNs, logs e URLs.

### Exemplo de RESUMO

```
{
  "PK": "EXEC#EMP123-01JCX7Q4M2",
  "SK": "RESUMO",
  "status": "EM_ANDAMENTO",
  "empresaId": "EMP123",
  "plano": ["M", "N", "O"],
  "qtdRegistros": 1000,
  "iniciadoEm": "2026-11-13T13:40:00.000Z",
  "qtdWarnings": 3,
  "qtdErros": 0,
  "etapas": {
    "M": { "ordem": 1, "status": "CONCLUIDA", "iniciadoEm": "…", "duracaoSeg": 12 },
    "N": { "ordem": 2, "status": "EM_ANDAMENTO", "iniciadoEm": "…", "tentativa": "0.0",
           "processados": 340, "total": 1000 },
    "O": { "ordem": 3, "status": "PENDENTE" }
  },
  "GSI1PK": "EMP#EMP123",
  "GSI1SK": "EXEC#2026-11-13T13:40:00.000Z#EMP123-01JCX7Q4M2",
  "ttl": 1794657600
}
```

### Exemplo de evento de warning

```
{
  "PK": "EXEC#EMP123-01JCX7Q4M2",
  "SK": "EV#2026-11-13T13:41:07.312Z#N#T0.0#0007",
  "tipo": "OCORRENCIA",
  "severidade": "WARNING",
  "codigo": "CONTA-W-003",
  "mensagem": "Conta informada não encontrada na base de contas ativas",
  "etapa": "N",
  "lambda": "lambda-b-gerar-filho",
  "registroId": "BZ#EMP123#LINHA#0457",
  "registroPaiId": "PAI#000045",
  "contaMascarada": "****-1234",
  "contaRef": "hmac:9f2c…e81a",
  "detalhes": "{\"campo\":\"conta\"}",
  "criadoEm": "2026-11-13T13:41:07.312Z",
  "GSI1PK": "EMP#EMP123",
  "GSI1SK": "OC#2026-11-13T13:40:00.000Z#EMP123-01JCX7Q4M2#0007",
  "GSI2PK": "SEV#WARNING#2026-11-13",
  "GSI2SK": "ETAPA#N#2026-11-13T13:41:07.312Z#EMP123-01JCX7Q4M2",
  "GSI3PK": "CONTA#9f2c…e81a",
  "GSI3SK": "2026-11-13T13:41:07.312Z#EMP123-01JCX7Q4M2",
  "ttl": 1794657600
}
```

### Índices e access patterns

| Índice | PK | SK | Access patterns |
| --- | --- | --- | --- |
| Base | `EXEC#…` | `RESUMO` / `EV#…` / `FALHA#…` | Status da execução (`GetItem RESUMO`); linha do tempo completa; eventos novos desde o cursor; falhas técnicas (`begins_with FALHA#`) |
| GSI1 empresa | `EMP#<empresaId>` | `EXEC#<inicio>#…` (resumos) / `OC#<inicio>#…` (ocorrências) | Execuções de uma empresa por período; warnings e erros de uma empresa por período |
| GSI2 severidade | `SEV#<severidade>#<yyyy-mm-dd>` | `ETAPA#<etapa>#<ts>#…` | Todos os erros do dia; erros ou warnings de uma etapa no dia (`begins_with ETAPA#N`) |
| GSI3 conta (esparso) | `CONTA#<contaRef>` | `<ts>#<execucao>` | Histórico de inconsistências de uma conta, sem guardar o número |

- Os GSIs são esparsos: eventos de ciclo de vida (`ETAPA_INICIADA`, `ETAPA_FINALIZADA`) não recebem chaves de GSI. Só warnings, erros e falhas entram nos índices.
- A partição diária do GSI2 evita hot key. Se o volume de warnings por dia ficar alto, acrescentar um sufixo de shard (`#0` a `#9`).
- Chave de GSI não aceita string vazia: a lib só grava `GSI3PK` quando há conta.

### `tb_pipeline_config`

| Item | PK | SK | Conteúdo |
| --- | --- | --- | --- |
| Catálogo de códigos | `CATALOGO` | `<codigo>` | Severidade, texto para o front, campos permitidos em `detalhes` |
| Estatística da etapa | `ESTATISTICA#<etapa>` | `ATUAL` | `segPorRegistro`, `duracaoMediaSeg`, `p90Seg`, `escalaComVolume`, `pesoPadraoSeg`, `nomeExibicao` |

### TTL e arquivamento

O TTL controla só a janela quente consultada pelo front (prazo a definir). A retenção oficial fica no S3 com Object Lock: o DynamoDB Streams envia **cada inserção** para o EventBridge Pipes, que entrega ao Firehose, que grava no S3. O arquivo é feito na inserção, não na expiração. O TTL é calculado pela API de início e pela lib (`agora + janela`).

## 07Shared library `pipeline-rastro`

### API para quem escreve a Lambda

```
from pipeline_rastro import etapa_rastreada

@etapa_rastreada("N")
def handler(event, context, rastro):
    pais = carregar_pais(event["chaves"])
    rastro.definir_total(len(pais))

    for i, pai in enumerate(pais, 1):
        if not pai.data_base:
            rastro.warning("BLM-W-017", registro_id=pai.id, registro_pai_id=pai.pai_id,
                           campo="dataBase")
        if not conta_existe(pai.conta):
            rastro.erro("CONTA-E-003", registro_id=pai.id, conta=pai.conta)
            continue
        gerar_filho(pai)
        rastro.progresso(i)

    return {"chaves": chaves_dos_filhos()}   # só chaves técnicas
```

#### Para a Lambda

- `@etapa_rastreada(codigo)`
- `rastro.definir_total(n)`
- `rastro.progresso(processados)`
- `rastro.warning(codigo, registro_id, …, conta=None, **detalhes)`
- `rastro.erro(codigo, …)`: marca a etapa como FALHA

#### Feito pela lib

- Eventos de início, fim e falha de tentativa
- Atualização do RESUMO e contadores
- Máscara, HMAC e cofre da conta
- Validação contra o catálogo
- Escrita em lote e limite de progresso
- Estatística de duração da etapa

### Decorator

```
# pipeline_rastro/decorator.py
import functools
from .rastro import Rastro

def etapa_rastreada(codigo_etapa: str):
    def deco(handler):
        @functools.wraps(handler)
        def wrapper(event, context):
            rastro = Rastro.de_evento(event, codigo_etapa, context)
            rastro.iniciada()
            try:
                saida = handler(event, context, rastro) or {}
            except Exception as exc:
                rastro.falha_tecnica(exc)   # evento da tentativa, melhor esforço
                raise                        # Retry ou Catch da Step Function decide
            rastro.finalizada()             # grava pendências; se falhar, lança exceção
            return {"status": rastro.status, **saida}
        return wrapper
    return deco
```

### Núcleo

```
# pipeline_rastro/rastro.py (resumido)
class Rastro:
    INTERVALO_PROGRESSO_S = 5
    PASSO_PROGRESSO = 0.05

    @classmethod
    def de_evento(cls, event, etapa, context):
        ctx = ContextoExecucao.from_dict(event["_rastro"])   # execucao, inicio, redrive,
        return cls(ctx, etapa, context.function_name,        # tentativa, empresaId, plano
                   Repositorio.padrao(), ProtetorDados.padrao(), Catalogo.padrao())

    def iniciada(self):
        self.inicio = agora()
        if self.etapa not in self.ctx.plano:
            self._ocorrencia("PIPE-W-001", "WARNING")       # etapa fora do plano declarado
        self._evento("ETAPA_INICIADA")
        self.repo.atualizar_etapa(self.ctx, self.etapa, status="EM_ANDAMENTO",
                                  iniciadoEm=self.inicio, tentativa=self.ctx.tentativa)
        self._flush()

    def warning(self, codigo, registro_id, registro_pai_id="-", conta=None, **detalhes):
        self._ocorrencia(codigo, "WARNING", registro_id, registro_pai_id, conta, detalhes)

    def erro(self, codigo, registro_id="-", registro_pai_id="-", conta=None, **detalhes):
        self._ocorrencia(codigo, "ERROR", registro_id, registro_pai_id, conta, detalhes)
        self.status = "FALHA"

    def progresso(self, processados):
        if self._deve_gravar_progresso(processados):
            self.repo.atualizar_etapa(self.ctx, self.etapa,
                                      processados=processados, total=self.total)
            self._flush()

    def finalizada(self):
        duracao = (agora() - self.inicio).total_seconds()
        self._evento("ETAPA_FINALIZADA", duracaoSeg=duracao, status=self.status)
        self._flush()
        self.repo.finalizar_etapa(self.ctx, self.etapa, self.status, duracao,
                                  qtd_warnings=self.qtd_warnings, qtd_erros=self.qtd_erros)
        if self.status != "FALHA":
            self.repo.atualizar_estatistica(self.etapa, duracao, self.total)

    def falha_tecnica(self, exc):
        try:
            self._evento("FALHA_TECNICA", erro=type(exc).__name__,
                         mensagem=sanitizar(str(exc))[:500])
            self._flush()
        except Exception:
            logger.exception("falha ao registrar falha técnica")   # o Catch garante

    def _ocorrencia(self, codigo, severidade, registro_id="-", registro_pai_id="-",
                    conta=None, detalhes=None):
        entrada = self.catalogo.validar(codigo, severidade, detalhes or {})  # rejeita campo
        item = self._base_evento("OCORRENCIA") | {                           # fora da lista
            "severidade": severidade, "codigo": codigo, "mensagem": entrada.texto,
            "registroId": registro_id, "registroPaiId": registro_pai_id,
            "detalhes": json.dumps(entrada.detalhes_permitidos),
        } | self._chaves_gsi(severidade)
        if conta:
            item |= self.protetor.proteger(conta)   # contaMascarada, contaRef, GSI3
        self._buffer.append(item)
        if len(self._buffer) >= 25:
            self._flush()
```

### Proteção da conta

```
# pipeline_rastro/protecao.py
class ProtetorDados:
    def proteger(self, conta: str) -> dict:
        ref = self._cache.get(conta) or self._gerar_ref(conta)
        return {
            "contaMascarada": "****-" + conta[-4:],
            "contaRef": ref,
            "GSI3PK": f"CONTA#{ref}",
            "GSI3SK": f"{agora_iso()}#{self.ctx.execucao}",
        }

    def _gerar_ref(self, conta):
        mac = kms.generate_mac(KeyId=CHAVE_HMAC, Message=conta.encode(),
                               MacAlgorithm="HMAC_SHA_256")["Mac"]
        ref = "hmac:" + mac.hex()
        cofre.put_item(Item={"contaRef": ref, "conta": conta, "criadoEm": agora_iso()},
                       ConditionExpression="attribute_not_exists(contaRef)")  # trata
        self._cache[conta] = ref                                              # ConditionalCheckFailed
        return ref
```

A chave HMAC é uma chave HMAC do KMS (`GenerateMac`): ela nunca sai do KMS. O cache por invocação evita chamar o KMS duas vezes para a mesma conta. Se a latência do KMS pesar com volume alto, a alternativa é uma chave HMAC no Secrets Manager com cache em memória.

### Regras da lib

- **Falha ao gravar o log derruba a Lambda.** Itens não processados do `BatchWriteItem` são reenviados com backoff; se continuar falhando, a lib lança `ErroRegistroLog`. A única exceção é `falha_tecnica`, que já está num caminho de erro e é coberta pelo Catch.
- **Mensagem vem do catálogo.** A Lambda passa o código, não texto livre. `detalhes` só aceita os campos permitidos para aquele código.
- **Progresso com limite:** grava no máximo a cada 5 s ou 5% do total.
- **RESUMO só com `UpdateItem`** em campos específicos (`SET etapas.#e.status`, `ADD qtdWarnings`), nunca `PutItem` do item inteiro. Etapa fora do plano: a lib cria `etapas.#e` com `if_not_exists` antes de atualizar.
- **Ordem:** grava o dado de negócio antes do evento que se refere a ele.
- **Versão semântica.** Mudança de schema é versão major, e a lib grava `versaoLib` em cada item.
- **Testes** com DynamoDB local ou moto, incluindo retry e itens não processados.

## 08Contrato com a Step Function e ASL

### Entrada de cada Lambda

```
{
  "chaves": { "...": "somente chaves técnicas (registroId, empresaId)" },
  "_rastro": {
    "execucao": "EMP123-01JCX7Q4M2",
    "inicio": "2026-11-13T13:40:00.000Z",
    "redrive": 0,
    "tentativa": 0,
    "empresaId": "EMP123",
    "plano": ["M", "N", "O"]
  }
}
```

### Saída de cada Lambda

```
{ "status": "SUCESSO | SUCESSO_COM_ALERTAS | FALHA", "chaves": { "...": "para a próxima etapa" } }
```

Erro de regra de negócio retorna `FALHA`, sem exceção. Exceção fica reservada para falha técnica. Assim o Retry nunca repete um erro de negócio.

### Uma etapa em ASL

```
"N": {
  "Type": "Task",
  "Resource": "arn:aws:states:::lambda:invoke",
  "Parameters": {
    "FunctionName": "${LambdaN}",
    "Payload": {
      "chaves.$": "$.ultimaEtapa.chaves",
      "_rastro": {
        "execucao.$": "$$.Execution.Name",
        "inicio.$": "$$.Execution.StartTime",
        "redrive.$": "$$.Execution.RedriveCount",
        "tentativa.$": "$$.State.RetryCount",
        "empresaId.$": "$.empresaId",
        "plano.$": "$.plano"
      }
    }
  },
  "ResultSelector": { "status.$": "$.Payload.status", "chaves.$": "$.Payload.chaves" },
  "ResultPath": "$.ultimaEtapa",
  "Retry": [{
    "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException",
                    "Lambda.SdkClientException", "Lambda.TooManyRequestsException",
                    "ErroRegistroLog"],
    "IntervalSeconds": 2, "MaxAttempts": 3, "BackoffRate": 2, "JitterStrategy": "FULL"
  }],
  "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.falha", "Next": "N_Falha" }],
  "Next": "N_Status"
},

"N_Status": {
  "Type": "Choice",
  "Choices": [{ "Variable": "$.ultimaEtapa.status", "StringEquals": "FALHA", "Next": "Falhou" }],
  "Default": "O"
},

"N_Falha": {
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:dynamodb:transactWriteItems",
  "Parameters": {
    "TransactItems": [
      { "Put": {
          "TableName": "tb_ocorrencias",
          "Item": {
            "PK":         { "S.$": "States.Format('EXEC#{}', $$.Execution.Name)" },
            "SK":         { "S.$": "States.Format('FALHA#N#R{}', $$.Execution.RedriveCount)" },
            "tipo":       { "S": "FALHA_TECNICA" },
            "severidade": { "S": "ERROR" },
            "etapa":      { "S": "N" },
            "erro":       { "S.$": "$.falha.Error" },
            "empresaId":  { "S.$": "$.empresaId" },
            "criadoEm":   { "S.$": "$$.State.EnteredTime" },
            "GSI2PK":     { "S.$": "States.Format('SEV#ERROR#{}', States.ArrayGetItem(States.StringSplit($$.State.EnteredTime, 'T'), 0))" },
            "GSI2SK":     { "S.$": "States.Format('ETAPA#N#{}#{}', $$.State.EnteredTime, $$.Execution.Name)" },
            "ttl":        { "N.$": "States.Format('{}', $.ttl)" }
          } } },
      { "Update": {
          "TableName": "tb_ocorrencias",
          "Key": { "PK": { "S.$": "States.Format('EXEC#{}', $$.Execution.Name)" }, "SK": { "S": "RESUMO" } },
          "UpdateExpression": "SET #st = :falha, etapas.#et.#st = :falha, atualizadoEm = :agora ADD qtdErros :um",
          "ExpressionAttributeNames": { "#st": "status", "#et": "N" },
          "ExpressionAttributeValues": {
            ":falha": { "S": "FALHA" }, ":agora": { "S.$": "$$.State.EnteredTime" }, ":um": { "N": "1" }
          } } }
    ]
  },
  "Retry": [{ "ErrorEquals": ["States.ALL"], "IntervalSeconds": 1, "MaxAttempts": 5, "BackoffRate": 2 }],
  "ResultPath": null,
  "Next": "Falhou"
},

"Concluir": {
  "Type": "Task",
  "Resource": "arn:aws:states:::dynamodb:updateItem",
  "Parameters": {
    "TableName": "tb_ocorrencias",
    "Key": { "PK": { "S.$": "States.Format('EXEC#{}', $$.Execution.Name)" }, "SK": { "S": "RESUMO" } },
    "UpdateExpression": "SET #st = :ok, concluidoEm = :agora",
    "ExpressionAttributeNames": { "#st": "status" },
    "ExpressionAttributeValues": { ":ok": { "S": "CONCLUIDO" }, ":agora": { "S.$": "$$.State.EnteredTime" } }
  },
  "End": true
},

"Falhou": { "Type": "Fail", "Error": "Pipeline.Falhou", "Cause": "Detalhes em tb_ocorrencias" }
```

- O `Cause` bruto não é gravado: ele pode conter stack trace com dados. Ele fica no histórico e no CloudWatch, com acesso restrito. A lib grava o tipo da exceção e a mensagem sanitizada no evento da tentativa.
- O `ttl` e o `plano` entram no input da execução pela API de início.
- O bloco de cada etapa é igual, mudando só o código e a próxima etapa. Gerar o ASL a partir de um template (CDK ou script) a partir da lista de blocos evita erro de cópia. Se o fluxo virar totalmente guiado pelo payload, um `Map` sobre `$.blocos` com um despachante elimina a edição do ASL ao acrescentar blocos.

### API de início

```
def iniciar(payload, usuario):
    autorizar(usuario, payload["empresaId"])
    execucao = f"{payload['empresaId']}-{ulid()}"
    plano = [b["codigo"] for b in payload["blocos"]]
    ttl = epoch(agora() + JANELA_QUENTE)

    tabela.put_item(Item={
        "PK": f"EXEC#{execucao}", "SK": "RESUMO",
        "status": "INICIADO", "empresaId": payload["empresaId"], "plano": plano,
        "etapas": {c: {"ordem": i, "status": "PENDENTE"} for i, c in enumerate(plano, 1)},
        "qtdRegistros": payload.get("qtdRegistros"), "iniciadoEm": agora_iso(),
        "qtdWarnings": 0, "qtdErros": 0,
        "GSI1PK": f"EMP#{payload['empresaId']}",
        "GSI1SK": f"EXEC#{agora_iso()}#{execucao}", "ttl": ttl,
    }, ConditionExpression="attribute_not_exists(PK)")

    try:
        sfn.start_execution(stateMachineArn=ARN, name=execucao,
                            input=json.dumps({**payload, "plano": plano, "ttl": ttl}))
    except Exception:
        marcar_resumo(execucao, status="FALHA_AO_INICIAR")
        raise
    return {"execucao": execucao}
```

## 09Progresso e tempo estimado

O plano vem do payload (`blocos`), copiado para o RESUMO na API de início. Os pesos vêm de `tb_pipeline_config`. Contar só etapas concluídas não serve: se uma etapa leva 80% do tempo, a barra fica parada e depois pula.

### Fórmulas

| Grandeza | Cálculo |
| --- | --- |
| Duração esperada da etapa *i* | Se escala com volume: `Di = segPorRegistroi × qtdRegistros`. Se não: `Di = duracaoMediai`. Sem histórico: `pesoPadraoSeg`. |
| Fração da etapa atual | Com progresso informado: `f = processados / total`. Sem: `f = min(decorrido / Datual, 0,95)`. |
| Progresso | `(Σ Dconcluídas e puladas + Datual × f) / Σ Dtodas` |
| Tempo restante | `restanteatual + Σ Dpendentes`. Com progresso informado, `restanteatual = (decorrido / processados) × (total − processados)`, usando a taxa desta execução; sem, `Datual × (1 − f)`. |
| Faixa exibida | Calcular com a média (p50) e com o p90 e mostrar "cerca de X a Y min". |

```
def calcular(resumo, est, agora):
    plano, etapas = resumo["plano"], resumo["etapas"]
    D = {e: duracao_esperada(est[e], resumo["qtdRegistros"]) for e in plano}
    feito = restante = 0.0
    for e in plano:
        s = etapas[e]
        if s["status"] in ("CONCLUIDA", "PULADA"):
            feito += D[e]
        elif s["status"] == "EM_ANDAMENTO":
            decorrido = (agora - s["iniciadoEm"]).total_seconds()
            if s.get("processados"):
                f = s["processados"] / s["total"]
                rest = decorrido / s["processados"] * (s["total"] - s["processados"])
            else:
                f = min(decorrido / D[e], 0.95)
                rest = D[e] * (1 - f)
            feito += D[e] * f
            restante += rest
        else:
            restante += D[e]
    return feito / sum(D.values()), restante
```

### Estatísticas

- **Fase 1:** ao finalizar com sucesso, a lib atualiza uma média móvel exponencial (`nova = 0,8 × antiga + 0,2 × observada`) de `segPorRegistro` ou `duracaoMediaSeg`. Como as etapas são sequenciais, não há conflito de escrita.
- **Fase 2:** um job diário com Athena sobre o arquivo no S3 calcula p50 e p90 dos últimos 30 dias, sem distorção por execuções atípicas, e grava em `ESTATISTICA#<etapa>`.

### Regras de exibição

- Se a etapa passou do p90, mostrar "demorando mais que o normal" em vez de tempo negativo.
- A barra não volta. Num retry, mostrar "reprocessando etapa N (tentativa 2)" e manter o percentual.
- Etapa pulada por um Choice é marcada `PULADA` e conta como concluída.
- O cálculo fica na API de leitura. Mudar a fórmula exige deploy só do backend.
- Se os blocos passarem a ter dependências (paralelos), o tempo restante passa a ser o caminho mais longo entre os pendentes, não a soma.

## 10API de leitura e polling

`GET /execucoes/{execucao}/progresso?cursor=<sk>`

1. `GetItem` do RESUMO. Verifica se o usuário pode ver aquele `empresaId`.
2. `Query` com `PK = EXEC#…` e `SK BETWEEN :desde AND "EV#￿"`, onde `:desde` é o cursor recuado \~2 s (o relógio das Lambdas pode diferir um pouco). O front descarta duplicados pela SK.
3. Se o status for `FALHA`, `Query begins_with FALHA#` para o detalhe técnico.
4. Estatísticas das etapas do plano, com cache em memória de alguns minutos.
5. Calcula progresso e faixa de tempo e devolve `{ status, progresso, etaMin, etaMax, etapas, novosEventos, cursor }`.

- Eventos com conta mostram só `contaMascarada`. A conta completa vem de um endpoint separado (`GET /ocorrencias/{sk}/conta`), liberado só para perfis autorizados, que resolve pelo cofre ou pela base de negócio e registra quem consultou.
- Front: intervalo de 2–3 s enquanto há mudança; sem mudança, aumenta até \~10 s; para em `CONCLUIDO` ou `FALHA`.
- Custo por consulta: 1 `GetItem` + 1 `Query` pequena. Desprezível no volume previsto.
- Se um dia precisar de push em vez de polling: DynamoDB Streams → WebSocket (API Gateway) ou AppSync. O modelo de dados não muda.

## 11Segurança, dados sensíveis e auditoria

**Regra principal:** o número da conta existe só na base de negócio e no cofre. Payload da Step Function, histórico de execução, tabela de ocorrências, CloudWatch e S3 de auditoria guardam apenas `registroId`, conta mascarada e `contaRef` (HMAC).

### Por que a conta sem nome continua sendo dado pessoal

Para o banco, chegar do número da conta ao titular é uma consulta, então o dado é identificável e continua no escopo da LGPD, além de coberto por sigilo bancário. Hash simples (SHA-256 sem chave) também não protege: o espaço de números de conta é pequeno e pode ser quebrado por força bruta. Daí o HMAC com chave no KMS. Classificação interna e controles exigidos a confirmar com Segurança da Informação e DPO.

### Permissões (menor privilégio)

| Principal | Permissões |
| --- | --- |
| Role de cada Lambda de negócio | `BatchWriteItem`, `PutItem`, `UpdateItem` em `tb_ocorrencias`; `GetItem/Query` em `tb_pipeline_config`; `UpdateItem` na estatística; `kms:GenerateMac` na chave HMAC; `PutItem` no cofre. Sem leitura da tabela de ocorrências nem do cofre. |
| Role da Step Function | `lambda:InvokeFunction` nas Lambdas do fluxo; `PutItem` e `UpdateItem` em `tb_ocorrencias`; `kms:Decrypt/GenerateDataKey` na CMK da state machine. |
| API de início | `PutItem/UpdateItem` em `tb_ocorrencias`; `states:StartExecution`. |
| API de leitura | `GetItem/Query` em `tb_ocorrencias` e GSIs, `tb_pipeline_config`. Endpoint de conta completa: `GetItem` no cofre, com role separada. |
| Lambda rede de segurança | `UpdateItem/PutItem` em `tb_ocorrencias`; publicação no tópico de alerta. |
| Suporte e operação | `states:ListExecutions`, `DescribeStateMachine`. Sem `GetExecutionHistory` por padrão. |

A IAM do DynamoDB só restringe por chave de partição (`dynamodb:LeadingKeys`), não por SK. Por isso a imutabilidade dos eventos é garantida pelo arquivo no S3, não por política na tabela.

### Controles

- **Criptografia:** CMK própria para `tb_ocorrencias`, outra para o cofre, CMK na state machine (histórico de execução) e no S3.
- **Logging da Step Function:** nível `ERROR` e `includeExecutionData: false`.
- **Lambdas:** Powertools Logger sem log do evento completo. Proibir `print(event)` com regra de lint.
- **Auditoria:** CloudTrail data events em `tb_ocorrencias` e no cofre; PITR ativo; S3 com Object Lock em modo compliance e retenção definida pelo compliance.
- **LGPD (eliminação):** o S3 imutável só guarda HMAC e máscara. Para um pedido de eliminação, remove-se a linha do cofre (e o dado na base de negócio, conforme a regra aplicável), e o HMAC deixa de ser resolvível.
- **Retenção da base de origem:** se o bloco zero for apagado antes do fim da retenção do log, o `registroId` deixa de apontar para algo. O cofre, com a mesma retenção do log, resolve isso para a conta.

### Idempotência

- Retentativa do SDK dentro de uma tentativa: mesmo timestamp e sequência, mesma SK, sobrescreve.
- Retry da Step Function: nova tentativa (`T0.1`), novos eventos, visíveis como reprocessamento.
- Redrive: `RedriveCount` entra na SK de eventos (`T1.0`) e de falha (`FALHA#N#R1`), preservando o histórico anterior.
- Cofre: `attribute_not_exists`; se a conta já tem referência, segue em frente.
- RESUMO: contadores com `ADD` podem contar em dobro num retry. A tela mostra os contadores da tentativa mais recente, guardados em `etapas.<e>`, e o total geral é tratado como indicativo.

## 12Riscos e decisões em aberto

### Riscos

| Risco | Impacto | Mitigação |
| --- | --- | --- |
| Lambda nova sem a lib, ou usando versão antiga | Etapa some da tela ou grava schema errado | Template de Lambda com a lib; verificação no CI; evento `PIPE-W-001` para etapa fora do plano; `versaoLib` em cada item |
| Desenvolvedor passa dado de cliente em `detalhes` ou em `chaves` | Exposição no histórico e na trilha imutável | Lista de campos permitidos no catálogo; revisão de código; teste de contrato do payload |
| Payload declara blocos diferentes dos que o ASL executa | Progresso errado | Evento de etapa fora do plano; teste no CI comparando payload de referência e ASL |
| Throttling do DynamoDB em picos | Etapa falha por não conseguir logar | On-demand; limite de progresso; escrita em lote; Retry na Step Function |
| RESUMO preso em `EM_ANDAMENTO` | Front consulta para sempre | Regra do EventBridge; o front para após um tempo máximo e mostra "sem atualização" |
| Latência do KMS no HMAC com muitas contas | Etapa mais lenta | Cache por invocação; alternativa com chave no Secrets Manager |

### Decisões em aberto

- Prazo da janela quente (TTL) e da retenção no S3: **compliance**.
- Classificação do número de conta e perfis que podem ver a conta completa: **Segurança da Informação / DPO**.
- Blocos sempre sequenciais ou com dependência entre eles: **negócio / time**.
- ASL gerado por template ou fluxo guiado por payload (`Map` + despachante): **time**.
- Necessidade futura de outros consumidores (alertas por e-mail, BI): **produto**.

## 13Plano de implementação

| Fase | Entrega | Pronto quando |
| --- | --- | --- |
| 1. Fundação | Tabelas, GSIs, CMKs, chave HMAC, cofre, catálogo inicial; lib com início, fim, warning, erro e testes | Lib publicada como Layer, testes de retry e itens não processados passando |
| 2. Integração | API de início; ASL com `_rastro`, Catch, Choice e estado final; Lambdas A, B e C migradas para a lib | Execução completa e execução com falha registradas corretamente em dev |
| 3. Progresso | API de leitura, polling no front, fórmula de progresso e ETA com média móvel | Tela mostra andamento e faixa de tempo em homologação |
| 4. Segurança e auditoria | Regra do EventBridge, Streams → Pipes → Firehose → S3 Object Lock, CloudTrail data events, endpoint de conta completa com perfil | Revisão de Segurança da Informação aprovada |
| 5. Refinamento | Job diário de estatísticas (p50/p90), alarmes e métricas por código de ocorrência | Alarmes ativos e ETA com faixa calibrada |

### Observabilidade

- Métricas (Powertools/EMF): ocorrências por código e severidade, duração por etapa, falhas de escrita da lib.
- Alarmes: `ExecutionsFailed` e `ExecutionsTimedOut` da state machine, acionamentos da Lambda rede, erros da lib, throttling em `tb_ocorrencias`.
- Correlation id em todo log técnico: nome da execução + etapa + tentativa.