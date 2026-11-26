# Simulação do Bloco M (e-Lalur / e-Lacs): Parte A, Parte B e os N registros M305

Fonte: arquivos `registros/bloco_M/*.yml` desta base (Manual ECF Leiaute 12, jul/2026), em especial M010, M030, M300, M305, M310, M312, M315, M350, M355, M360, M410, M500 e M990.

## 1. Arquivos desta pasta

| Arquivo | O que é |
|---|---|
| `cenario_padrao.yml` | Cenário da empresa simulada: contas da Parte B, lançamentos da Parte A (IRPJ e CSLL) e lançamentos só da Parte B. Edite aqui. |
| `gerar_bloco_m.py` | Gerador. Calcula os valores, controla o razão da Parte B e valida cada linha contra os YAMLs do leiaute. |
| `bloco_M_simulado.txt` | Bloco M gerado (60 linhas, formato SPED `\|REG\|...\|`, CRLF). |
| `bloco_M_simulado_arvore.txt` | O mesmo arquivo, indentado pela hierarquia pai → filho. |
| `bloco_M_simulado_10_contas_extra*.txt` | Variante com `--n-contas-extra 10`: 13 registros M305 sob um único M300. |
| `relatorio_validacao*.txt` | Lucro real, compensação, razão da Parte B e resultado das validações. |

```
python gerar_bloco_m.py                       # cenário padrão
python gerar_bloco_m.py --n-contas-extra 25   # N contas extras penduradas no mesmo M300
python gerar_bloco_m.py --cenario outro.yml
```

## 2. Hierarquia do Bloco M

```
M001  Abertura do bloco                                  [1:1]
├── M010  Cadastro das contas da Parte B (chave COD_CTA_B + COD_TRIBUTO)   [0:N]
└── M030  Período de apuração (A00..A12, T01..T04)        [0:13]
    ├── M300  Lançamento da Parte A do e-Lalur (IRPJ)     [0:N]
    │   ├── M305  Conta da Parte B do e-Lalur             [0:N]  ← N contas por lançamento
    │   ├── M310  Conta contábil do lançamento            [0:N]
    │   │   └── M312  Nº do lançamento na ECD (I200)      [0:N]
    │   └── M315  Processo judicial/administrativo        [0:N]
    ├── M350  Lançamento da Parte A do e-Lacs (CSLL)      [0:N]
    │   ├── M355  Conta da Parte B do e-Lacs              [0:N]
    │   ├── M360  Conta contábil  └── M362                [0:N]
    │   └── M365  Processo                                [0:N]
    ├── M410  Lançamento só na Parte B (sem reflexo na A) [0:N]
    │   └── M415  Processo                                [0:N]
    ├── M500  Controle de saldos de cada conta da Parte B [0:N]
    └── M510  Controle de saldos por conta padrão RFB     [0:N]
M990  Encerramento (QTD_LIN conta de M001 até M990)      [1:1]
```

Pontos centrais:
- **Parte B é cadastrada uma vez (M010)** e movimentada dentro de cada período (M030).
- **M010 é por conta e por tributo.** A mesma conta `PB001` aparece duas vezes, com `COD_TRIBUTO = I` e com `C`, porque o IRPJ e a CSLL têm controles separados. Contas usadas no M305 precisam estar cadastradas com `I`; no M355, com `C` (REGRA_PARTE_B_PARTE_A).
- **M305 é filho do M300, não da conta.** É a "perna" do lançamento da Parte A que movimenta a Parte B.

## 3. Por que podem existir N registros M305

Um lançamento da Parte A (M300) corresponde a **uma linha da tabela dinâmica** (ex.: "Provisões não dedutíveis"). Várias contas da Parte B diferentes podem compor essa linha: provisão trabalhista, PECLD, garantias, e assim por diante. Cada conta vira um M305:

```
|M300|24|Provisões não dedutíveis|A|3|250000,00|CONSTITUICAO DE PROVISOES ...|
    |M305|PB001|150000,00|D|      ← contingências trabalhistas
    |M305|PB002|60000,00|D|       ← PECLD
    |M305|PB004|40000,00|D|       ← garantias (conta criada no período)
    |M310|3.01.02.001||150000,00|D|
        |M312|LC2025-0451|
        |M312|LC2025-0987|
    |M310|3.01.02.002||60000,00|D|
    |M310|3.01.02.003||40000,00|D|
```

### Regras que governam os N M305

| # | Regra | Fonte | Consequência prática |
|---|---|---|---|
| 1 | Ocorrência `0:N` sob o M300 | M305 (leiaute) | Não há limite de contas por lançamento. |
| 2 | Chave `COD_CTA_B` | M305.campos_chave | **Uma conta não pode se repetir sob o mesmo M300.** Se houver dois eventos da mesma conta, some-os numa única linha. O gerador avisa. |
| 3 | Só existe quando `M300.IND_RELACAO` = 1 ou 3 | tabela 4.2 + REGRA_RELACAO_INEXISTENTE | Relação 1 ou 3 exige pelo menos um M305. Relação 2 ou 4 não pode ter M305. |
| 4 | `COD_CTA_B` deve existir no M010, com tributo I | M305 campo 2, REGRA_PARTE_B_PARTE_A | Cadastre antes no M010. |
| 5 | Conta padrão (`M010.COD_PB_RFB`) compatível com o código do M300 | REGRA_PARTE_B_PARTE_A (tabela PARTEB_PARTEA) | Nem toda conta pode ser usada em qualquer linha da Parte A. |
| 6 | `VL_CTA` ≤ saldo disponível da conta no período | M305 campo 3 (obs.), M355 REGRA_SALDO_DISPONIVEL_PARTE_B | Nos usos de saldo (exclusão de saldo D, adição de saldo C, compensação), o valor não pode passar do saldo. O gerador checa isso. |
| 7 | Soma dos filhos com sinal = `M300.VALOR` | M300 REGRA_VALOR_DETALHADO | Ver seção 4. |
| 8 | A mesma conta pode aparecer sob **vários** M300 | (decorre da chave) | O M500 consolida todos os M305 da conta no período em `VL_LCTO_PARTE_A`. |
| 9 | M305 pertence a um período (M030) | hierarquia | No trimestral ou com balancetes de suspensão, há M305 em cada M030 e o saldo vai passando de um período para o seguinte (M500). |

### Exemplo da regra 8 na simulação: PB001 (IRPJ)

| Origem | Movimento |
|---|---|
| M010 saldo inicial | 300.000,00 D |
| M305 sob M300 24 (adição: constituição) | +150.000,00 D |
| M305 sob M300 120 (exclusão: reversão/baixa) | −100.000,00 C |
| **M500.VL_LCTO_PARTE_A** (líquido dos M305) | **50.000,00 D** |
| M410 de PB002 com contrapartida PB001 | +10.000,00 D (`VL_LCTO_PARTEB`) |
| **M500.SD_FIM_LAL** | **360.000,00 D** → vai para o E020 da próxima ECF |

## 4. Sinais: como os N filhos somam no VALOR do M300

Este é o ponto que mais gera erro. Pela REGRA_VALOR_DETALHADO (M300):

| Tipo do M300 | M305 `D` | M305 `C` | M310 resultado (nat. 4) `D` / `C` | M310 patrimonial (nat. 1–3) `D` / `C` |
|---|---|---|---|---|
| A (adição) ou L (lucro) | + | − | + / − | − / + |
| E (exclusão) ou P (compensação) | − | + | − / + | + / − |

Leitura contábil da Parte B (tabela do próprio M300):

| Lançamento | Movimento na Parte B | Efeito |
|---|---|---|
| Adição | **Débito** (D) | constitui saldo D para excluir depois, **ou** consome saldo C |
| Exclusão | **Crédito** (C) | consome saldo D, **ou** constitui saldo C para adicionar depois |
| Compensação de prejuízo | **Crédito** (C) | consome o saldo D de prejuízos fiscais |

Consequências:
- Os N M305 de um mesmo M300 **podem ter indicadores mistos**. Exemplo: numa adição, uma conta com D 30.000 e outra com C 5.000 dão VALOR = 25.000. Funciona na aritmética, mas raramente faz sentido contábil. A simulação usa apenas indicadores coerentes.
- Com `IND_RELACAO = 3`, o VALOR pode ser igual à soma dos M305, **ou** à soma dos M310, **ou** à soma dos dois. Na simulação as duas somas dão 250.000, então VALOR = 250.000.

## 5. O que a simulação demonstra

**IRPJ (M300):**

| Código* | Tipo | Relação | Filhos | Demonstra |
|---|---|---|---|---|
| 1 | L | 4 | — | lucro líquido antes do IRPJ |
| 2 | A | 2 | 1 M310 | adição só com conta contábil (CSLL) |
| 24 | A | 3 | **3 M305** + 3 M310 + 2 M312 | N contas da Parte B num lançamento, constituindo saldo D |
| 80 | A | 1 | 1 M305 D | consumo de saldo C (realização da depreciação acelerada) |
| 120 | E | 3 | **2 M305** C + 1 M310 + 1 M315 | consumo de saldos D, com processo judicial |
| 140 | E | 1 | 1 M305 C | constituição de saldo C |
| 173 | P | 1 | 1 M305 C | compensação calculada: min(30% × 1.205.000; saldo 400.000) = 361.500 |

**CSLL (M350):** mesma lógica, com M355/M360/M362. A base negativa (PB901, 250.000) é toda compensada porque fica abaixo do limite de 30%.

**M410:** transferência de 10.000 de PB002 para PB001 (CR + contrapartida), sem reflexo na Parte A.

**Resultado:** lucro real de 843.500,00 e base da CSLL de 970.000,00. Esses valores seguem para o Bloco N (N500/N650).

\* Códigos: só o **173** (compensação) vem do manual (itens 1.8/1.9). Os demais são **ilustrativos** e devem ser trocados pelos da planilha `Tabelas_Dinamicas_ECF_Leiaute_12_AC2025_SIT_ESP_2026.xlsx` (M300A, M350A, PARTEB_PADRAO), que não faz parte do PDF. O mesmo vale para os `COD_PB_RFB`.

## 6. Pontos de atenção encontrados no manual

1. **Descrição do M305/M355 vs. tabela de sinais.** O texto diz "se prejuízo, debita conta da parte B". A tabela de sinais do M300 indica **C** para compensação de prejuízo positiva, e a REGRA_VALOR_DETALHADO também trata P como E. O gerador segue a tabela e a regra.
2. **Indicadores do M500.** A descrição de `IND_VL_LCTO_PARTE_A` / `IND_VL_LCTO_PARTEB` inverte D/C em relação aos saldos. Já o exemplo do manual (`1000 C − 500 D − 100 D = 400 C`) usa o sentido contábil natural. O gerador segue o exemplo. Confirme no PGE.
3. **Exemplos do M500/M510 sem `COD_TRIBUTO`.** O leiaute tem `COD_TRIBUTO` como campo 3, mas o exemplo do manual o omite. O gerador segue o leiaute (11 campos no M500).
4. **M410 com contrapartida.** Assumi que uma linha com `COD_CTA_B_CTP` movimenta as duas contas: crédito numa, débito na outra. Confirme no PGE se ele espera uma segunda linha.
5. **M500 e M510 são gerados pelo PGE** a partir do M010 e dos movimentos. Na importação, divergências podem ser recalculadas. O M510 foi omitido porque depende das contas padrão reais.

## 7. Dependências com outros blocos (não geradas aqui)

Para um arquivo ECF completo, o Bloco M precisa de:
- **0000/0010**: `FORMA_TRIB` 1–4 (lucro real) e `FORMA_APUR`, que definem quais M030 existem.
- **J050/J100**: contas contábeis e centros de custo usados no M310/M360.
- **K155/K355 e ECD (I200)**: saldos e números de lançamento que tornam o M312/M362 obrigatório (quando `M310.VL_CTA` é só parte do saldo da conta).
- **E020**: saldos finais da Parte B da ECF anterior. Devem bater com `M010.VL_SALDO_INI` (REGRA_SALDOS_M010_E020).
- **N500/N650**: recebem o lucro real e a base da CSLL apurados no M300/M350.
- **9900**: quantidade de linhas por tipo de registro, incluindo os do Bloco M.
