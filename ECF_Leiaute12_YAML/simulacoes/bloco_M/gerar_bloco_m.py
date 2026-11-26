"""Gera uma simulação do Bloco M (e-Lalur / e-Lacs) da ECF – Leiaute 12 – a partir de um cenário YAML.

Uso:
    python gerar_bloco_m.py [--cenario cenario_padrao.yml] [--n-contas-extra N] [--saida DIR]

O gerador:
  * monta M001, M010, M030, M300/M305/M310/M312/M315, M350/M355/M360/M362/M365, M410/M415, M500 e M990;
  * calcula M300.VALOR / M350.VALOR a partir dos filhos, aplicando a conversão de sinais da REGRA_VALOR_DETALHADO;
  * calcula a compensação de prejuízo / base negativa limitada a 30% e ao saldo da conta da Parte B;
  * controla o razão de cada conta da Parte B (saldo inicial -> movimentos -> M500) e checa saldo disponível;
  * valida cada linha contra o leiaute dos YAMLs em ../../registros/bloco_M (qtde de campos, tipo, tamanho, valores válidos).
"""
import argparse
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP

import yaml

AQUI = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.normpath(os.path.join(AQUI, '..', '..'))
D0 = Decimal('0.00')


def dec(v):
    return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def fmt(v):
    return f'{dec(v):.2f}'.replace('.', ',')


def linha(*campos):
    return '|' + '|'.join('' if c is None else str(c) for c in campos) + '|'


# ---------------------------------------------------------------- layout (base de conhecimento YAML)
def carregar_leiautes():
    lay = {}
    pasta = os.path.join(BASE, 'registros', 'bloco_M')
    for f in sorted(os.listdir(pasta)):
        if f.endswith('.yml'):
            d = yaml.safe_load(open(os.path.join(pasta, f), encoding='utf-8'))
            lay[d['registro']] = d
    return lay


def valores_validos(txt):
    """'[A; E; P; L]' -> {'A','E','P','L'}; referências como [M010.COD_CTA_B] -> None."""
    if not txt or not re.fullmatch(r'\[[^\]]+\]', txt.strip()):
        return None
    itens = [x.strip() for x in txt.strip()[1:-1].split(';')]
    if any('.' in x or '_' in x for x in itens):
        return None
    return set(itens)


def validar_linha(l, lay, erros):
    partes = l.split('|')[1:-1]
    reg = partes[0]
    d = lay.get(reg)
    if not d:
        erros.append(f'{reg}: registro sem leiaute'); return
    campos = d['campos']
    if len(partes) != len(campos):
        erros.append(f'{reg}: {len(partes)} campos na linha, leiaute tem {len(campos)} -> {l}'); return
    for c, v in zip(campos, partes):
        nome = f"{reg}.{c['campo']}"
        if v == '':
            if c['obrigatorio'] is True:
                erros.append(f'{nome}: obrigatório e vazio -> {l}')
            continue
        tipo, tam, decs = c['tipo'], c['tamanho'], c['decimal']
        if tipo in ('N', 'NS'):
            padrao = r'-?\d+(,\d+)?' if tipo == 'NS' else r'\d+(,\d+)?'
            if not re.fullmatch(padrao, v):
                erros.append(f'{nome}: valor "{v}" não é {tipo}')
            if isinstance(decs, int) and ',' in v and len(v.split(',')[1]) > decs:
                erros.append(f'{nome}: mais de {decs} decimais')
            if isinstance(tam, int) and decs in ('-', None) and len(v) != tam:
                erros.append(f'{nome}: tamanho {len(v)} != {tam} (exato)')
        elif isinstance(tam, int) and len(v) > tam:
            erros.append(f'{nome}: tamanho {len(v)} > {tam}')
        vv = valores_validos(c.get('valores_validos'))
        if vv and v not in vv:
            erros.append(f'{nome}: "{v}" fora de {sorted(vv)}')


# ---------------------------------------------------------------- regras de negócio
def sinal(tipo, ind, natureza=None):
    """Conversão de sinais da REGRA_VALOR_DETALHADO (M300/M350).
    natureza None -> conta da Parte B (M305/M355); 4 -> conta contábil de resultado; 1/2/3 -> patrimonial."""
    positivo = (ind == 'D')
    if natureza in (1, 2, 3):
        positivo = not positivo
    if tipo in ('E', 'P'):
        positivo = not positivo
    return 1 if positivo else -1


class Razao:
    """Razão das contas da Parte B por (conta, tributo). Valores com sinal: D = +, C = -."""

    def __init__(self):
        self.ini, self.mov_a, self.mov_b = {}, {}, {}

    def abrir(self, k, valor, ind):
        self.ini[k] = dec(valor) * (1 if ind == 'D' else -1)
        self.mov_a[k] = D0
        self.mov_b[k] = D0

    def saldo(self, k):
        return self.ini[k] + self.mov_a[k] + self.mov_b[k]

    def lancar(self, k, valor, ind, parte, avisos, contexto):
        if k not in self.ini:
            raise SystemExit(f'Conta da Parte B {k} não cadastrada no M010 ({contexto})')
        v = dec(valor) * (1 if ind in ('D', 'DB', 'PF', 'BC') else -1)
        antes = self.saldo(k)
        if antes != 0 and (antes > 0) != (v > 0) and abs(v) > abs(antes):
            avisos.append(f'{contexto}: utilização de {fmt(abs(v))} maior que o saldo disponível '
                          f'{fmt(abs(antes))} da conta {k[0]}/{k[1]} (VL_CTA deve ser <= saldo disponível)')
        (self.mov_a if parte == 'A' else self.mov_b)[k] += v


def dc(v):
    v = dec(v)
    return fmt(abs(v)), ('D' if v > 0 else 'C' if v < 0 else 'D')


# ---------------------------------------------------------------- geração
def gerar(cen, n_extra):
    linhas, arvore, avisos = [], [], []
    razao = Razao()
    contas_ctb = cen.get('contas_contabeis', {})
    per = cen['periodo']

    # contas extras (N contas) penduradas no mesmo lançamento -> N registros M305/M355
    contas_b = list(cen['contas_parte_b'])
    if n_extra:
        mod = cen['contas_extras_modelo']
        for n in range(1, n_extra + 1):
            cod = f"{mod['prefixo']}{n:02d}"
            valor = float(dec(mod['valor_base']) * n)
            contas_b.append({'codigo': cod, 'descricao': f"{mod['descricao']} {n:02d}", 'dt_ap': mod['dt_ap'],
                             'cod_pb_rfb': mod['cod_pb_rfb'], 'saldos_iniciais': {t: [0, 'D'] for t in mod['cod_pb_rfb']}})
            for trib in ('I', 'C'):
                for lanc in cen['parte_a'].get(trib, []):
                    if lanc['id'] == mod['lancamento_alvo']:
                        lanc.setdefault('contas_parte_b', []).append({'conta': cod, 'valor': valor, 'ind': 'D'})
                        cc = next((c for c in lanc.get('contas_contabeis', []) if c['cod_cta'] == mod['conta_contabil']), None)
                        if cc:
                            cc['valor'] = float(dec(cc['valor']) + dec(valor))

    def emitir(l, nivel):
        linhas.append(l)
        arvore.append('    ' * nivel + l)

    emitir(linha('M001', '0'), 0)

    # M010 – uma linha por (conta, tributo)
    for cb in contas_b:
        for trib in ('I', 'C'):
            if trib not in cb['saldos_iniciais']:
                continue
            v, ind = cb['saldos_iniciais'][trib]
            if cb['dt_ap'][4:] == per['dt_fin'][4:] and cb['dt_ap'] >= per['dt_ini'] and dec(v) != 0:
                avisos.append(f"M010 {cb['codigo']}/{trib}: conta criada no período deve ter saldo inicial zero (REGRA_DT_AP_ZERO)")
            razao.abrir((cb['codigo'], trib), v, ind)
            emitir(linha('M010', cb['codigo'], cb['descricao'], cb['dt_ap'], cb['cod_pb_rfb'][trib], cb.get('dt_lim'),
                         trib, fmt(v), ind, cb.get('cnpj_sit_esp')), 1)

    emitir(linha('M030', per['dt_ini'], per['dt_fin'], per['per_apur']), 1)

    resumo = {}
    for trib, (rA, rB, rC, rCP, rProc) in (('I', ('M300', 'M305', 'M310', 'M312', 'M315')),
                                          ('C', ('M350', 'M355', 'M360', 'M362', 'M365'))):
        base = D0
        lucro_antes_comp = None
        for lanc in cen['parte_a'].get(trib, []):
            tipo, rel = lanc['tipo'], lanc['relacao']
            filhos_b = list(lanc.get('contas_parte_b', []))
            filhos_c = list(lanc.get('contas_contabeis', []))
            if 'compensacao' in lanc:
                comp = lanc['compensacao']
                k = (comp['conta'], trib)
                lucro_antes_comp = base
                limite = (base * dec(comp['limite_pct']) / 100).quantize(Decimal('0.01'))
                saldo_pf = razao.saldo(k)
                disp = saldo_pf if saldo_pf > 0 else D0
                valor_comp = max(D0, min(limite, disp))
                filhos_b = [{'conta': comp['conta'], 'valor': float(valor_comp), 'ind': 'C'}] if valor_comp > 0 else []
                resumo[f'{trib}_compensacao'] = (valor_comp, limite, disp)

            # REGRA: chave do M305/M355 = COD_CTA_B -> uma linha por conta sob o mesmo M300
            vistos = set()
            for fb in filhos_b:
                if fb['conta'] in vistos:
                    avisos.append(f"{rA} {lanc['codigo']}: conta {fb['conta']} repetida em {rB} (chave COD_CTA_B) – agregue os valores")
                vistos.add(fb['conta'])

            soma_b = sum((dec(f['valor']) * sinal(tipo, f['ind']) for f in filhos_b), D0)
            soma_c = sum((dec(f['valor']) * sinal(tipo, f['ind'], contas_ctb[f['cod_cta']]['natureza']) for f in filhos_c), D0)
            if 'valor' in lanc:
                valor = dec(lanc['valor'])
            elif rel == 1:
                valor = soma_b
            elif rel == 2:
                valor = soma_c
            elif rel == 3:
                valor = soma_b if soma_b == soma_c else soma_b + soma_c
            else:
                valor = D0
            if rel == 3 and valor not in (soma_b, soma_c, soma_b + soma_c):
                avisos.append(f'{rA} {lanc["codigo"]}: VALOR não fecha com filhos (REGRA_VALOR_DETALHADO)')
            # REGRA_RELACAO_INEXISTENTE
            precisa_b, precisa_c = rel in (1, 3), rel in (2, 3)
            if bool(filhos_b) != precisa_b or bool(filhos_c) != precisa_c:
                if not ('compensacao' in lanc and not filhos_b):
                    avisos.append(f'{rA} {lanc["codigo"]}: IND_RELACAO={rel} incompatível com os filhos (REGRA_RELACAO_INEXISTENTE)')
            if 'compensacao' in lanc and not filhos_b:
                continue  # nada a compensar

            emitir(linha(rA, lanc['codigo'], lanc['descricao'], tipo, rel, fmt(valor), lanc.get('historico')), 2)
            for fb in filhos_b:
                razao.lancar((fb['conta'], trib), fb['valor'], fb['ind'], 'A', avisos, f"{rB} sob {rA} {lanc['codigo']}")
                emitir(linha(rB, fb['conta'], fmt(fb['valor']), fb['ind']), 3)
            for fc in filhos_c:
                emitir(linha(rC, fc['cod_cta'], fc.get('cod_ccus'), fmt(fc['valor']), fc['ind']), 3)
                for num in fc.get('lancamentos', []):
                    emitir(linha(rCP, num), 4)
            for pr in lanc.get('processos', []):
                emitir(linha(rProc, pr['ind_proc'], pr['num_proc']), 3)

            if tipo in ('L', 'A'):
                base += valor
            else:
                base -= valor
        resumo[f'{trib}_base'] = base
        resumo[f'{trib}_antes_comp'] = lucro_antes_comp

    # M410 – Parte B sem reflexo na Parte A
    for lb in cen.get('lancamentos_parte_b', []):
        k = (lb['conta'], lb['tributo'])
        razao.lancar(k, lb['valor'], lb['ind'], 'B', avisos, f"M410 {lb['conta']}")
        if lb.get('contrapartida'):
            kc = (lb['contrapartida'], lb['tributo'])
            if kc not in razao.ini:
                avisos.append(f"M410: contrapartida {kc} inexistente no M010 para o mesmo tributo (REGRA_MESMO_TRIBUTO)")
            else:
                inv = {'CR': 'DB', 'DB': 'CR'}[lb['ind']]
                razao.lancar(kc, lb['valor'], inv, 'B', avisos, f"M410 contrapartida {lb['contrapartida']}")
        emitir(linha('M410', lb['conta'], lb['tributo'], fmt(lb['valor']), lb['ind'], lb.get('contrapartida'),
                     lb['historico'], lb.get('realizacao_diferida', 'N')), 2)
        for pr in lb.get('processos', []):
            emitir(linha('M415', pr['ind_proc'], pr['num_proc']), 3)

    # M500 – controle de saldos (um por conta/tributo do M010)
    m500 = []
    for k in razao.ini:
        si, sa, sb, sf = razao.ini[k], razao.mov_a[k], razao.mov_b[k], razao.saldo(k)
        v_si, i_si = dc(si)
        v_a, i_a = dc(sa)
        v_b, i_b = dc(sb)
        v_f, i_f = dc(sf)
        emitir(linha('M500', k[0], k[1], v_si, i_si, v_a, i_a, v_b, i_b, v_f, i_f), 2)
        m500.append((k, si, sa, sb, sf))

    qtd = len(linhas) + 1
    emitir(linha('M990', qtd), 0)
    return linhas, arvore, avisos, resumo, m500


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cenario', default=os.path.join(AQUI, 'cenario_padrao.yml'))
    ap.add_argument('--n-contas-extra', type=int, default=0)
    ap.add_argument('--saida', default=AQUI)
    ap.add_argument('--sufixo', default='')
    a = ap.parse_args()

    cen = yaml.safe_load(open(a.cenario, encoding='utf-8'))
    lay = carregar_leiautes()
    linhas, arvore, avisos, resumo, m500 = gerar(cen, a.n_contas_extra)

    erros = []
    for l in linhas:
        validar_linha(l, lay, erros)

    suf = a.sufixo or (f'_{a.n_contas_extra}_contas_extra' if a.n_contas_extra else '')
    with open(os.path.join(a.saida, f'bloco_M_simulado{suf}.txt'), 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\n'.join(linhas) + '\n')
    with open(os.path.join(a.saida, f'bloco_M_simulado{suf}_arvore.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(arvore) + '\n')

    rel = [f'Linhas geradas: {len(linhas)} (M990.QTD_LIN = {len(linhas)})']
    from collections import Counter
    c = Counter(l.split('|')[1] for l in linhas)
    rel.append('Registros: ' + ', '.join(f'{k}={v}' for k, v in sorted(c.items())))
    for trib, nome in (('I', 'IRPJ – Lucro real'), ('C', 'CSLL – Base de cálculo')):
        if f'{trib}_base' in resumo:
            rel.append(f'{nome} antes da compensação: {fmt(resumo[trib + "_antes_comp"] or 0)}')
            if f'{trib}_compensacao' in resumo:
                v, lim, disp = resumo[f'{trib}_compensacao']
                rel.append(f'  compensação: {fmt(v)} (limite 30% = {fmt(lim)}; saldo disponível na Parte B = {fmt(disp)})')
            rel.append(f'{nome} após compensação (vai para N500/N650): {fmt(resumo[trib + "_base"])}')
    rel.append('')
    rel.append('Razão da Parte B (saldo inicial + mov. com reflexo na Parte A + mov. sem reflexo = saldo final):')
    for (k, si, sa, sb, sf) in m500:
        rel.append(f'  {k[0]:<6} {k[1]}  ini {"/".join(dc(si)):>14}  parteA {"/".join(dc(sa)):>14}  parteB {"/".join(dc(sb)):>12}  fim {"/".join(dc(sf)):>14}')
    rel.append('')
    rel.append(f'Validação de leiaute (YAML): {"OK" if not erros else str(len(erros)) + " erro(s)"}')
    rel += ['  - ' + e for e in erros]
    rel.append(f'Validação de regras de negócio: {"OK" if not avisos else str(len(avisos)) + " aviso(s)"}')
    rel += ['  - ' + e for e in avisos]
    texto = '\n'.join(rel)
    open(os.path.join(a.saida, f'relatorio_validacao{suf}.txt'), 'w', encoding='utf-8').write(texto + '\n')
    print(texto)
    return 1 if erros else 0


if __name__ == '__main__':
    sys.exit(main())
