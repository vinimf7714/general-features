# Comparação: `recomendacoes-ecf-leiaute-compartilhado.md` × base gerada do Manual ECF Leiaute 12

Este documento compara as recomendações de arquitetura (caminho materializado na sort key, leiaute versionado na library `ecf-layout`, validação antes de gerar) com o que foi produzido nesta sessão:
- a base YAML do manual (`registros/`, `blocos/`, `geral/`, `arvore_registros.*`);
- o gerador do Bloco M (`simulacoes/bloco_M/`).

Legenda: ✅ confirmado pela base · ⚠️ precisa de ajuste · ❌ diverge do manual · ➕ ponto que a recomendação não cobre.

---

## 1. Onde a recomendação e a base concordam

| Recomendação | Evidência na base / simulação |
|---|---|
| ✅ A hierarquia do SPED vem da **ordem das linhas e do nível**, não de ponteiros. | O manual define pai/filho só pelo nível na Tabela 4.2. A `arvore_registros.json` foi montada exatamente assim: o pai é o registro anterior de nível menor. O gerador do Bloco M emite as linhas em ordem e não guarda nenhum hash de pai. |
| ✅ Tabela de níveis do Bloco M (seção 1). | Todos os 18 registros conferem com o leiaute, inclusive M312 no nível 5 e M415 no nível 4, como filho de M410. |
| ✅ Chaves de M030, M300, M305, M310, M312 e M315 no exemplo de leiaute. | Iguais aos `campos_chave` do manual: `PER_APUR`, `CODIGO`, `COD_CTA_B`, `COD_CTA + COD_CCUS`, `NUM_LCTO`, `IND_PROC + NUM_PROC`. |
| ✅ N filhos M305/M310 sob um M300 gerados localmente, na mesma execução (seção 4). | Foi o que o gerador fez: 3 M305 no cenário padrão e 13 com `--n-contas-extra 10`. A relação pai-filho nunca precisou sair do processo. |
| ✅ O gerador calcula totais sozinho (M990 / 9900 / 9999). | `M990.QTD_LIN` é calculado pelo gerador (60 e 120 linhas nos cenários) e confere. |
| ✅ Validar o VALOR do M300 contra os filhos conforme o `IND_RELACAO`. | Implementado e testado: um cenário com erro de propósito foi detectado. Ver ⚠️ 2.4 sobre os sinais. |
| ✅ Leiaute versionado, imutável por versão, e testes de consistência no CI. | A base mostra por que isso é necessário: o próprio manual tem inconsistências (seção 3) que um teste "todo pai existe, níveis coerentes" pegaria. |
| ✅ Limites do DynamoDB citados (`BatchWriteItem` com 25 itens, `TransactWriteItems` com até 100). | Corretos. |

---

## 2. Ajustes necessários na recomendação

### 2.1 ❌ Versão do leiaute e vigência

O exemplo usa `"leiaute": "ECF-v11"` com vigência a partir do ano-calendário 2024. O manual analisado é o **leiaute 12**: `0000.COD_VER = "0012"` "para o ano-calendário 2025 e situações especiais de 2026".

**Consequência para `resolve_layout(ano_calendario)`:** só o ano-calendário não resolve a versão. Uma ECF de situação especial (cisão, fusão, extinção…) com `DT_FIN` em 2026 usa o leiaute 12, e uma ECF normal de 2026 usará o próximo leiaute. Sugestão de assinatura:

```python
resolve_layout(dt_fin: date, sit_especial: bool) -> Layout   # espelha a REGRA_LEIAUTE_VIGENTE do 0000.COD_VER
```

### 2.2 ❌ Chave do M010 está incompleta

| Registro | Na recomendação | No manual (base) |
|---|---|---|
| M010 | `["COD_CTA_B"]` | **`COD_CTA_B + COD_TRIBUTO`** |
| M500 / M510 | (ausentes) | `COD_CTA_B + COD_TRIBUTO` |

Na simulação, a mesma conta `PB001` existe duas vezes no M010, uma para IRPJ (`I`) e outra para CSLL (`C`). Com a chave da recomendação, as duas teriam o **mesmo SK**, e o segundo `put` sobrescreveria o primeiro em silêncio: a idempotência viraria perda de dado. O mesmo vale para o M500.

```
SK = M010=PB001|I
SK = M010=PB001|C
```

### 2.3 ⚠️ A ordenação lexicográfica não vale para o arquivo inteiro

A recomendação diz que um único `Query` em `PK` "traz os itens prontos para serializar". Isso vale **dentro de um bloco**: no Bloco M a ordem lexicográfica coincide com a do manual. **Não vale entre blocos**:

- A ordem dos blocos é `0, C, E, J, K, L, M, N, P, Q, S, T, U, V, W, X, Y, 9`.
- Em ASCII, `'9' < 'C'`. Com o SK começando pelo código do registro, o bloco 9 (9001…9999) sairia **logo depois do bloco 0** e antes do C. Conferido contra `arvore_registros.json`: a ordem global do manual ≠ ordem lexicográfica dos códigos.

**Sugestão:** prefixar o SK com a ordem do bloco, ou, melhor ainda, montar cada segmento com a `ordem` zero-padded do leiaute (`ordem_no_arquivo` já existe no índice da árvore):

```
SK = 07:M#0030:M030=A00#0300:M300=0093#0305:M305=PB001
```

Assim a ordem deixa de depender da numeração dos registros, que é justamente o "cuidado prático" que a própria recomendação levanta.

### 2.4 ⚠️ A validação do VALOR do M300 precisa de dados de fora da árvore

A recomendação resume a regra como "VALOR deve bater com a soma dos M305 e/ou M310". A REGRA_VALOR_DETALHADO do manual exige **conversão de sinais**:

- `TIPO_LANCAMENTO` A/L × E/P inverte o sinal;
- no M305, D/C;
- no M310, D/C **e a natureza da conta contábil (`J050.COD_NAT`)**: conta de resultado (4) tem um sinal, conta patrimonial (1–3) tem o inverso.

Ou seja, validar M300/M310 depende do **Bloco J**, que é produzido por outro lambda. Não é uma validação local do elalur-elax. O gerador do Bloco M implementa essa tabela de sinais (`sinal()` em `gerar_bloco_m.py`).

### 2.5 ⚠️ Padding do `CODIGO` do M300

`"pad": {"CODIGO": 4}` supõe códigos inteiros, mas a tabela dinâmica usa subcódigos com ponto. O manual cita linhas `91.40`, `166.35`, `167.01` e `173`, e avisa que "códigos do tipo XX.XX não são hierarquicamente subordinados a XX".

- O padding precisa tratar a parte inteira e a parte decimal separadamente (`0091.40`), nunca alterar o valor gravado em `dados`, e
- a ordem das linhas do M300 deveria vir da **sequência da tabela dinâmica** (planilha M300A/M350A), não do código. **A confirmar:** a planilha não está no PDF, então a base não tem como verificar.

### 2.6 ⚠️ Registros sem campo-chave

A recomendação monta o caminho com chaves naturais, mas 12 registros não têm `Campo(s) chave` no manual. No Bloco M, o caso é o **M410**: podem existir vários lançamentos da mesma conta no período, sem chave natural. Os outros são 9100, 9900, L200, Q100, V100, W250, W300, X280, Y600, Y620 e Y800.

Para esses, o leiaute precisa declarar uma **chave sintética determinística**, por exemplo `COD_CTA_B|COD_TRIBUTO|seq` com `seq` estável. Sem isso, perde-se a idempotência ou ocorrem colisões.

### 2.7 ⚠️ "Confirme no manual oficial": confirmado, com duas exceções fora do Bloco M

A recomendação ressalva que o pai foi deduzido dos níveis. A base confirma o método para o Bloco M, mas encontrou casos em que **deduzir pela Tabela 4.2 dá a árvore errada**:

| Caso | Tabela 4.2 | Leiaute do registro | Tratamento na base |
|---|---|---|---|
| X451 | nível 2 | nível 3 (filho de X450) | árvore usa o leiaute |
| Y650 | nível 2 | nível 3 (filho de Y640) | árvore usa o leiaute |
| 9990 | listado como "9099" | 9990 | normalizado |

O `ecf-layout` deve ser gerado a partir do leiaute de cada registro, e o CI deve comparar com a Tabela 4.2 e acusar divergências. É o que `_gerador/build.py` já faz ao imprimir `level divergences`.

---

## 3. Pontos que a recomendação não cobre

| # | Ponto | Por quê / evidência |
|---|---|---|
| ➕1 | **Integridade referencial, além de órfãos.** | 14 campos da base têm valores válidos que apontam para outro registro. Ex.: `M305.COD_CTA_B ∈ M010.COD_CTA_B`, `M310.COD_CTA ∈ J050.COD_CTA`, `M310.COD_CCUS ∈ J100`. Isso não é pai/filho: o `validate_item(item)` item a item não detecta. É preciso um validador de conjunto no estado do Step Functions. |
| ➕2 | **Cardinalidade.** | A base tem a ocorrência de cada registro (`M030 [0:13]`, `C040 [0;12]`, `C150 [0;13]`, `0000 [1:1]`…). "Obrigatórios ausentes" cobre o mínimo, mas não o máximo. |
| ➕3 | **Obrigatoriedade condicional.** | A obrigatoriedade de saída costuma ser condicional, ex. `M305: OC se M300.IND_RELACAO = 1 ou 3` e `M010: FC se 0010.FORMA_TRIB = 1–4`. Na base essas condições estão em texto livre (`tabela_registros.obrigatoriedade_saida.condicao`). Para o `validate_item` usar, precisam virar expressões no leiaute. |
| ➕4 | **M500 é derivado, como M990/9900.** | O manual diz que o M500 é "gerado pelo sistema a partir do saldo inicial e das movimentações". Ele agrega **todos os M305 de uma conta em todos os M300 do período**, mais o M410. Na simulação, a PB001 tem +150.000 D (M300 24) e −100.000 C (M300 120), o que dá 50.000 D. Deveria ser calculado pelo gerador ou por um passo de consolidação, não por um produtor. Com o SK em caminho, o agrupamento por conta exige varrer o prefixo `M030=A00#` inteiro ou criar um GSI por `COD_CTA_B|COD_TRIBUTO`. |
| ➕5 | **Saldo disponível da Parte B.** | `M305/M355.VL_CTA ≤ saldo disponível` (REGRA_SALDO_DISPONIVEL_PARTE_B) depende do M010, do E020 (saldos da ECF anterior, REGRA_SALDOS_M010_E020) e de outros M305 do período. Também é validação de conjunto e entre blocos. |
| ➕6 | **Caracteres reservados nas chaves.** | A recomendação reserva `#`, `=` e `\|`. Pelo leiaute, `COD_CTA_B` e `NUM_LCTO` são campos C livres (até 255/50 caracteres), definidos pelo contribuinte. Escapar esses caracteres é obrigatório. Além disso, `\|` é o delimitador do próprio SPED, então um `\|` dentro de um valor também quebra o TXT final. |
| ➕7 | **Leiaute como dado: a base já é o insumo.** | Para cada registro, `arvore_registros.json` + `registros/*.yml` já trazem nível, pai, filhos, irmãos, `ordem_no_arquivo`, campos-chave, campos (tipo/tamanho/decimal/valores válidos/obrigatório) e obrigatoriedade condicional. Dá para gerar o `v12.json` do `ecf-layout` com um script, em vez de digitá-lo. Faltaria acrescentar `pad`, separadores, `vigencia` e as chaves sintéticas de ➕/2.6. |

---

## 4. Ajustes sugeridos ao exemplo de leiaute da recomendação

```json
{
  "leiaute": "ECF-v12",
  "cod_ver": "0012",
  "vigencia": { "ano_calendario": 2025, "situacoes_especiais_ate": "2026-12-31" },
  "separadores": { "nivel": "#", "chave": "=", "composta": "|", "escape": "\\" },
  "registros": {
    "M001": { "nivel": 1, "pai": null,   "ordem": 1,   "chave": [] },
    "M010": { "nivel": 2, "pai": "M001", "ordem": 10,  "chave": ["COD_CTA_B", "COD_TRIBUTO"] },
    "M030": { "nivel": 2, "pai": "M001", "ordem": 30,  "chave": ["PER_APUR"], "ocorrencia": [0, 13] },
    "M300": { "nivel": 3, "pai": "M030", "ordem": 300, "chave": ["CODIGO"], "ordenar_por": "tabela_dinamica:M300A" },
    "M305": { "nivel": 4, "pai": "M300", "ordem": 305, "chave": ["COD_CTA_B"],
              "referencias": { "COD_CTA_B": "M010.COD_CTA_B[COD_TRIBUTO=I]" },
              "obrigatorio_se": "M300.IND_RELACAO in (1,3)" },
    "M310": { "nivel": 4, "pai": "M300", "ordem": 310, "chave": ["COD_CTA", "COD_CCUS"],
              "referencias": { "COD_CTA": "J050.COD_CTA", "COD_CCUS": "J100.COD_CCUS" } },
    "M312": { "nivel": 5, "pai": "M310", "ordem": 312, "chave": ["NUM_LCTO"] },
    "M315": { "nivel": 4, "pai": "M300", "ordem": 315, "chave": ["IND_PROC", "NUM_PROC"] },
    "M410": { "nivel": 3, "pai": "M030", "ordem": 410, "chave": ["COD_CTA_B", "COD_TRIBUTO", "#seq"], "chave_sintetica": true },
    "M500": { "nivel": 3, "pai": "M030", "ordem": 500, "chave": ["COD_CTA_B", "COD_TRIBUTO"], "calculado_pelo_gerador": true },
    "M990": { "nivel": 1, "pai": null,   "ordem": 990, "chave": [], "calculado_pelo_gerador": true }
  }
}
```

(M350–M365, M415 e M510 seguem o mesmo padrão. `ordenar_por: tabela_dinamica` depende da planilha de tabelas dinâmicas, que ainda não está na base.)

---

## 5. Conclusão

- A direção da recomendação é **confirmada pelo manual**: no SPED, a hierarquia vem só do nível e da ordem, e o caminho materializado elimina o acoplamento por hash. O gerador do Bloco M desta sessão funciona exatamente assim, sem ponteiros.
- **Corrigir antes de publicar o `ecf-layout`:**
  - versão 12 e vigência por `DT_FIN` + situação especial (2.1);
  - chave do M010/M500 com `COD_TRIBUTO` (2.2);
  - ordem entre blocos (2.3).
- **Acrescentar ao estado de validação:**
  - referências entre registros (➕1);
  - cardinalidade e obrigatoriedade condicional (➕2, ➕3);
  - sinais da REGRA_VALOR_DETALHADO com `J050.COD_NAT` (2.4);
  - saldo disponível da Parte B (➕5).
  Essas regras são **de conjunto e entre blocos**. Não cabem num `validate_item` item a item.
- **Tratar M500 como derivado** (➕4) e definir chaves sintéticas para os 12 registros sem chave (2.6).
