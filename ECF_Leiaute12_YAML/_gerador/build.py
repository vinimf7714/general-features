import json, re, os, sys, shutil, unicodedata
import yaml

SRC = 'Manual_ECF_Leiaute_12_23_07_2026_AC_2025_SIT_ESP_2026.pdf'
OUT = sys.argv[1]
P = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pages.json')))

# ---------------------------------------------------------------- yaml setup
class Lit(str): pass
def lit_rep(d, s):
    return d.represent_scalar('tag:yaml.org,2002:str', s, style='|' if '\n' in s else None)
yaml.add_representer(Lit, lit_rep)
def str_rep(d, s):
    if '\n' in s:
        return d.represent_scalar('tag:yaml.org,2002:str', s, style='|')
    return d.represent_scalar('tag:yaml.org,2002:str', s)
yaml.add_representer(str, str_rep)

def dump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(obj, f, allow_unicode=True, sort_keys=False, width=4096, default_flow_style=False)

# ---------------------------------------------------------------- helpers
def clean(s):
    s = (s or '').replace(' ', ' ').replace('', '•').replace('‐', '-')
    s = re.sub(r'[ \t]+', ' ', s)
    s = '\n'.join(l.strip() for l in s.split('\n'))
    return s.strip()

def flat(s):
    return re.sub(r'\s*\n\s*', ' ', clean(s)).strip()

def unwrap(s):
    """Join wrapped lines inside a table cell, keeping list items / rule lines separate."""
    lines = [l for l in clean(s).split('\n') if l.strip()]
    if len(lines) < 2:
        return clean(s)
    mx = max(len(l) for l in lines)
    out = [lines[0]]
    for l in lines[1:]:
        p = out[-1]
        if BULLET.match(l) and not l[:1].islower():
            out.append(l); continue
        if l[:1].islower() or l[:1] in '(“"' or (len(p) >= 0.8 * mx and not re.search(r'[.:;]$', p)) or re.search(r'[,(]$|\b(e|ou|de|da|do|a|o|no|na|com|para|que|em)$', p):
            out[-1] = p + ' ' + l
        else:
            out.append(l)
    return '\n'.join(out)

def none_dash(s):
    s = flat(s)
    return None if s in ('', '-') else s

BULLET = re.compile(r'^([-•–*]|\d{1,3}\s*[-–.)]\s|[a-zA-Z]\)\s|[IVX]+\s*[-–]\s|REGRA_|\||Exemplo|Observa|Obs\.|Nota|Atenção|Se |Senão|Caso )')

def join_lines(lines):
    """Join PDF-wrapped lines into paragraphs; keeps list items / rule lines on their own line."""
    out = []
    prev = None
    for ln in lines:
        t, top, bottom = ln['text'], ln['top'], ln['bottom']
        if out and prev is not None:
            gap = top - prev['bottom']
            same_page = ln['page'] == prev['page']
            cont = (same_page and gap < 6) or (not same_page)
            last = out[-1]
            if cont and not BULLET.match(t) and not re.search(r'[:;]$', last) and not last.startswith('|') \
                    and (t[:1].islower() or t[:1] in '(“"' or (len(last.split('\n')[-1]) >= 140 and not re.search(r'[.!?]$', last))):
                out[-1] = last + ' ' + t
                prev = ln
                continue
        out.append(t)
        prev = ln
    return out

def md_table(rows):
    rows = [[flat(c).replace('|', '\\|') for c in r] for r in rows if any(flat(c) for c in r)]
    if not rows:
        return ''
    n = max(len(r) for r in rows)
    rows = [r + [''] * (n - len(r)) for r in rows]
    s = '| ' + ' | '.join(rows[0]) + ' |\n|' + '---|' * n + '\n'
    s += '\n'.join('| ' + ' | '.join(r) + ' |' for r in rows[1:])
    return s

def table_obj(rows):
    rows = [[clean(c) for c in r] for r in rows if any(clean(c) for c in r)]
    if not rows:
        return None
    hdr = [flat(c) for c in rows[0]]
    return {'cabecalho': hdr, 'linhas': [[flat(c) for c in r] for r in rows[1:]]}

def render(els):
    """Render a list of elements (text lines + tables) to clean text, tables as markdown."""
    parts, buf = [], []
    for e in els:
        if e['k'] == 'text':
            buf.append(e)
        else:
            if buf:
                parts.append('\n'.join(join_lines(buf))); buf = []
            m = md_table(e['rows'])
            if m:
                parts.append(m)
    if buf:
        parts.append('\n'.join(join_lines(buf)))
    return clean('\n\n'.join(p for p in parts if p.strip())) or None

# ---------------------------------------------------------------- flatten document
def smart_join(cells):
    out = ''
    for c in [clean(x) for x in cells if clean(x)]:
        if out and re.search(r'[a-zà-úç]$', out) and re.match(r'^[a-zà-úç]', c):
            out += c
        else:
            out = (out + ' ' + c).strip()
    return out

def is_field_hdr0(r):
    h = [re.sub(r'\s+', ' ', (c or '')).strip().lower() for c in r]
    return 'campo' in h and any(x.startswith('descri') for x in h) and 'tipo' in h

ELS = []
for pg in P:
    exp = []
    for e in pg['elements']:
        if e['k'] == 'table':
            hi = next((i for i, r in enumerate(e['rows']) if is_field_hdr0(r)), 0)
            if hi > 0:
                for i, r in enumerate(e['rows'][:hi]):
                    t = smart_join(r)
                    if t:
                        exp.append({'k': 'text', 'top': e['top'] + i * 0.01, 'bottom': e['top'] + i * 0.01, 'x0': 40, 'text': t})
                exp.append({**e, 'rows': e['rows'][hi:], 'top': e['top'] + 0.5})
                continue
        exp.append(e)
    for e in exp:
        e = dict(e); e['page'] = pg['page']
        if e['k'] == 'text':
            e['text'] = clean(e['text'])
            if re.match(r'^Página \d+ de \d+$', e['text']) or e['text'].startswith('Manual de Orientação do Leiaute 12 da ECF') and 'Atualização' in e['text']:
                continue
        ELS.append(e)

def is_field_hdr(r):
    h = [flat(c).lower() for c in r]
    return 'campo' in h and any(x.startswith('descri') for x in h) and 'tipo' in h

def is_rule_hdr(r):
    h = [flat(c).lower() for c in r]
    return 'campo' in h and any('regras de valida' in x for x in h)

RE_REG_HEAD = re.compile(r'^Registro ([0-9A-Z]{4})\s*:\s*(.*)$')
RE_REG_BOX = re.compile(r'^REGISTRO ([0-9A-Z]{4})\s*:\s*(.*)$')
RE_BLOCO = re.compile(r'^Bloco ([0-9A-Z])\s*:\s*(.+)$')
RE_CAP = re.compile(r'^Capítulo (\d)\s*[–-]\s*(.+)$')

FIRST_BODY_PAGE = 10
LAYOUT_PAGE = 61

# ---------------------------------------------------------------- table 4.2 (register list / tree)
reg_rows = []
for e in ELS:
    if e['k'] != 'table' or not (44 <= e['page'] <= 58):
        continue
    for r in e['rows']:
        r = [clean(c) for c in r] + [''] * 6
        if flat(r[0]).lower() == 'registro':
            continue
        if re.match(r'^[0-9A-Z]{4}$', r[0]):
            reg_rows.append({'registro': r[0], 'nivel': int(r[1]), 'nome': flat(r[2]),
                             'obrigatoriedade_entrada': flat(r[3]), 'obrigatoriedade_saida': flat(r[4]),
                             'ocorrencia': flat(r[5])})
        elif reg_rows:  # continuation of previous row across page break
            last = reg_rows[-1]
            for k, i in (('nome', 2), ('obrigatoriedade_entrada', 3), ('obrigatoriedade_saida', 4), ('ocorrencia', 5)):
                if flat(r[i]):
                    last[k] = (last[k] + ' ' + flat(r[i])).strip()

# Errata: the manual's Tabela de Registros lists "9099 - Encerramento do Bloco 9"; the layout section (p. 618) defines it as 9990.
for r in reg_rows:
    if r['registro'] == '9099':
        r['registro'] = '9990'
        r['nota'] = 'Na Tabela de Registros (item 4.2) do manual consta "9099"; o leiaute do registro (página 618) e o padrão SPED definem "9990". Código normalizado para 9990.'
TABLE = {r['registro']: r for r in reg_rows}
order = [r['registro'] for r in reg_rows]
parent, children = {}, {}
def build_tree():
    parent.clear(); children.clear()
    stack = []
    for r in reg_rows:
        while stack and stack[-1]['nivel'] >= r['nivel']:
            stack.pop()
        parent[r['registro']] = stack[-1]['registro'] if stack else None
        stack.append(r)
    for c in order:
        children[c] = []
    for c in order:
        if parent[c]:
            children[parent[c]].append(c)
build_tree()

def split_obrig(s):
    m = re.match(r'^(O|F|OC|FC|N)\b\s*(.*)$', s or '')
    if not m:
        return {'codigo': s or None}
    d = {'codigo': m.group(1)}
    cond = m.group(2).strip()
    if cond:
        d['condicao'] = re.sub(r'^\((.*)\)$', r'\1', cond)
    return d

OBRIG = {'O': 'Obrigatório', 'F': 'Facultativo', 'OC': 'Obrigatório Condicional', 'FC': 'Facultativo Condicional', 'N': 'Não Deve Existir'}

def rel(code):
    p = parent.get(code)
    sib = [c for c in (children[p] if p else [x for x in order if parent[x] is None]) if c != code]
    return {'pai': p, 'filhos': children.get(code, []), 'irmaos': sib}

# ---------------------------------------------------------------- segment document
# indices of register starts (body), block starts, chapter starts
starts = []  # (idx, kind, code, title)
seen = set()
for i, e in enumerate(ELS):
    if e['page'] < FIRST_BODY_PAGE:
        continue
    if e['k'] == 'text':
        t = e['text']
        m = RE_CAP.match(t)
        if m and e['page'] < LAYOUT_PAGE:
            starts.append((i, 'cap', m.group(1), m.group(2))); continue
        if e['page'] >= LAYOUT_PAGE:
            m = RE_BLOCO.match(t)
            if m and e['x0'] < 60:
                starts.append((i, 'bloco', m.group(1), m.group(2))); continue
            m = RE_REG_HEAD.match(t)
            if m and m.group(1) not in seen:
                seen.add(m.group(1)); starts.append((i, 'reg', m.group(1), m.group(2))); continue
            m = RE_REG_BOX.match(t)
            if m and m.group(1) not in seen:  # register without its own heading line
                seen.add(m.group(1)); starts.append((i, 'reg', m.group(1), m.group(2).title())); continue
starts.append((len(ELS), 'end', None, None))

segments = []
for (i, kind, code, title), (j, *_) in zip(starts, starts[1:]):
    segments.append({'kind': kind, 'code': code, 'title': clean(title), 'els': ELS[i + 1:j], 'page': ELS[i]['page']})

def pages_of(seg):
    ps = [e['page'] for e in seg['els']] or [seg['page']]
    return [seg['page'], max(ps)]

# ---------------------------------------------------------------- register parsing
def parse_fields(tables):
    fields = []
    for rows in tables:
        hdr = None
        for r in rows:
            if is_field_hdr(r):
                hdr = [flat(c).lower() for c in r]; continue
            if hdr is None:
                hdr = ['nº', 'campo', 'descrição', 'tipo', 'tamanho', 'decimal', 'valores válidos', 'obrigatório'][-len(r):]
            d = {}
            for h, c in zip(hdr, r):
                d[h] = c
            num = flat(d.get('nº', ''))
            campo = flat(d.get('campo', '')).replace(' ', '')
            if not campo and not num and fields:  # continuation row
                f = fields[-1]
                extra = unwrap(d.get('descrição', ''))
                if extra:
                    f['descricao'] = (f['descricao'] + '\n' + extra).strip()
                vv = none_dash(d.get('valores válidos', ''))
                if vv:
                    f['valores_validos'] = (f.get('valores_validos') or '') + vv
                continue
            if not campo:
                continue
            f = {'numero': int(num) if num.isdigit() else (num or None), 'campo': campo,
                 'descricao': unwrap(d.get('descrição', '')), 'tipo': none_dash(d.get('tipo', '')),
                 'tamanho': flat(d.get('tamanho', '')) or None, 'decimal': flat(d.get('decimal', '')) or None,
                 'valores_validos': none_dash(d.get('valores válidos', ''))}
            ob = flat(d.get('obrigatório', ''))
            f['obrigatorio'] = {'sim': True, 's': True, 'não': False, 'nao': False, 'n': False}.get(ob.lower(), ob or None)
            for k in ('tamanho', 'decimal'):
                if f[k] and f[k].isdigit():
                    f[k] = int(f[k])
            fields.append(f)
    # derive numero when missing
    for k, f in enumerate(fields):
        if f['numero'] is None:
            f['numero'] = k + 1
    return fields

RE_RULE = re.compile(r'(?:(?<=^)|(?<=\n)|(?<=\s))(REGRA_[A-Z0-9_ÀÁÂÃÇÉÊÍÓÔÕÚ]+)\s*:?')

def split_rules(text):
    """Split 'REGRA_X: desc ... REGRA_Y: desc' into list."""
    text = clean(text)
    if not text:
        return []
    out = []
    pos = [m for m in re.finditer(r'(?m)^\s*(REGRA_[A-Z0-9_ÀÁÂÃÇÉÊÍÓÔÕÚ]+)\b\s*:?', text)]
    if not pos:
        return [{'regra': None, 'descricao': flat(text)}]
    pre = text[:pos[0].start()].strip()
    if pre:
        out.append({'regra': None, 'descricao': flat(pre)})
    for m, n in zip(pos, pos[1:] + [None]):
        body = text[m.end(): n.start() if n else len(text)]
        out.append({'regra': m.group(1), 'descricao': unwrap(body) or None})
    return out

def parse_field_rules(tables):
    res = []
    for rows in tables:
        hdr = None
        for r in rows:
            if is_rule_hdr(r):
                hdr = [flat(c).lower() for c in r]; continue
            if hdr is None:
                hdr = ['nº', 'campo', 'regras de validação do campo', 'tipo'][-len(r):]
            d = {}
            for h, c in zip(hdr, r):
                key = 'regras' if 'regras' in h else h
                d[key] = c
            campo = flat(d.get('campo', '')).replace(' ', '')
            num = flat(d.get('nº', ''))
            rules = split_rules(d.get('regras', ''))
            tipos = [t for t in clean(d.get('tipo', '')).split('\n') if t.strip()]
            named = [x for x in rules if x['regra']]
            if len(tipos) == len(named):
                for x, t in zip(named, tipos):
                    x['tipo'] = t
            elif len(set(tipos)) == 1 and named:
                for x in named:
                    x['tipo'] = tipos[0]
            elif tipos:
                for x, t in zip(named, tipos):
                    x['tipo'] = t
            if not campo and res:
                prev = res[-1]
                if rules and rules[0]['regra'] is None and prev['regras']:
                    p = prev['regras'][-1]
                    p['descricao'] = ((p['descricao'] or '') + '\n' + rules[0]['descricao']).strip()
                    rules = rules[1:]
                prev['regras'] += rules
                continue
            if not campo and not rules:
                continue
            if not campo:
                res.append({'numero': None, 'campo': None,
                            'nota': 'Continuação de regras de campo iniciadas na página anterior do manual (ver regras_validacao_campos_observacoes).',
                            'regras': rules})
                continue
            res.append({'numero': int(num) if num.isdigit() else (num or None), 'campo': campo or None, 'regras': rules})
    return res

def parse_example(els):
    lines = [e['text'] for e in els if e['k'] == 'text']
    ex = {'linhas_exemplo': [], 'explicacao': []}
    for l in lines:
        m = re.match(r'^(\|.*?\|)\s*:\s*(.*)$', l)
        if m and not re.match(r'^\|[0-9A-Z]{4}\|.*\|.*\|', m.group(1) + '|x|') or (m and l.count('|') == 2):
            ex['explicacao'].append({'valor': m.group(1), 'descricao': m.group(2)})
        elif l.startswith('|'):
            ex['linhas_exemplo'].append(l)
        else:
            ex.setdefault('observacoes', []).append(l)
    for t in [e for e in els if e['k'] == 'table']:
        ex.setdefault('tabelas', []).append(md_table(t['rows']))
    return ex

RE_SUB = re.compile(r'^(I{1,3}|IV|V|VI{0,3})\s*[–-]\s*(.+?)\s*:?\s*$')

def put(reg, key, val, titulo):
    """Store a parsed sub-section; if the manual repeats the same sub-section, keep the repetition apart."""
    if key not in reg:
        reg[key] = val
    else:
        reg.setdefault('secoes_repetidas_no_manual', []).append(
            {'chave': key, 'titulo_secao': titulo,
             'nota': 'O manual repete esta seção para o mesmo registro (possivelmente com redação diferente); ambas as versões foram preservadas.',
             'conteudo': val})

def parse_register(seg):
    code = seg['code']
    els = seg['els']
    reg = {'registro': code, 'nome': seg['title'], 'bloco': code[0]}
    if code in TABLE:
        t = TABLE[code]
        reg['nome_tabela_registros'] = t['nome']
    reg['paginas_manual'] = pages_of(seg)
    # header box
    hi = next((k for k, e in enumerate(els) if e['k'] == 'text' and RE_REG_BOX.match(e['text']) and RE_REG_BOX.match(e['text']).group(1) == code), None)
    if hi is None and RE_REG_BOX.match(seg['title'].upper()):
        hi = -1
    fi = next((k for k, e in enumerate(els) if e['k'] == 'table' and e['rows'] and is_field_hdr(e['rows'][0])), None)
    desc_end = hi if hi is not None and hi >= 0 else (fi if fi is not None else len(els))
    reg['descricao'] = render(els[:desc_end])
    if hi is not None and hi >= 0:
        reg['titulo_leiaute'] = clean(RE_REG_BOX.match(els[hi]['text']).group(0))
    hdr_end = fi if fi is not None else len(els)
    hdr_lines = [e['text'] for e in els[(hi + 1 if hi is not None else desc_end):hdr_end] if e['k'] == 'text']
    hdr_text = ' '.join(hdr_lines)
    m = re.search(r'Regras de Validação do Registro\s*:?(.*?)(?=Nível Hierárquico|$)', hdr_text)
    reg['regras_validacao_registro'] = [x for x in re.findall(r'REGRA_[A-Z0-9_ÀÁÂÃÇÉÊÍÓÔÕÚ]+', m.group(1))] if m else []
    m = re.search(r'Nível Hierárquico\s*[–-]\s*(\d+)', hdr_text)
    reg['nivel_hierarquico'] = int(m.group(1)) if m else (TABLE.get(code, {}).get('nivel'))
    m = re.search(r'Ocorrência\s*[–-]\s*(.+?)(?=Campo\(s\) chave|$)', hdr_text)
    reg['ocorrencia'] = m.group(1).strip() if m else None
    m = re.search(r'Campo\(s\) chave\s*:\s*(.*)$', hdr_text)
    reg['campos_chave'] = (m.group(1).strip() or None) if m else None
    if reg['campos_chave'] in ('-', '[não há]', 'Não há'):
        reg['campos_chave'] = None
    other_hdr = [l for l in hdr_lines if not re.match(r'^(Regras de Validação do Registro|Nível Hierárquico|Campo\(s\) chave|REGRA_)', l)]
    if other_hdr:
        reg['observacoes_leiaute'] = other_hdr
    if code in TABLE:
        t = TABLE[code]
        reg['tabela_registros'] = {
            'nivel': t['nivel'],
            'obrigatoriedade_entrada': split_obrig(t['obrigatoriedade_entrada']),
            'obrigatoriedade_saida': split_obrig(t['obrigatoriedade_saida']),
            'ocorrencia': t['ocorrencia'],
        }
        reg['hierarquia'] = rel(code)
    # fields tables: from fi, consecutive tables of field type (allow interleaved short text)
    ftables, k, notes = [], fi, []
    if fi is not None:
        while k < len(els):
            e = els[k]
            if e['k'] == 'table' and e['rows'] and (is_field_hdr(e['rows'][0]) or (not is_rule_hdr(e['rows'][0]) and len(e['rows'][0]) in (7, 8) and not ftables == [] and re.match(r'^\d*$', flat(e['rows'][0][0])))):
                ftables.append(e['rows']); k += 1; continue
            if e['k'] == 'text':
                # stop at sub-section headings / examples
                if RE_SUB.match(e['text']) or e['text'].startswith('Exemplo') or e['text'].startswith('Regras de Valida') :
                    break
                # look-ahead: more field tables coming?
                nxt = next((x for x in els[k:k + 15] if x['k'] == 'table'), None)
                if nxt and is_field_hdr(nxt['rows'][0]):
                    notes.append(els[k]); k += 1; continue
            break
    reg['campos'] = parse_fields(ftables)
    if notes:
        reg['notas_entre_campos'] = render(notes)
    rest = els[k:] if fi is not None else els[hdr_end:]
    # split rest into sub-sections
    subs, cur = [], {'titulo': None, 'els': []}
    for e in rest:
        if e['k'] == 'text':
            m = RE_SUB.match(e['text'])
            if m and len(e['text']) < 120:
                subs.append(cur); cur = {'titulo': clean(e['text']).rstrip(':').strip(), 'els': []}; continue
            if re.match(r'^Exemplos? de [Pp]reenchimento', e['text']):
                subs.append(cur); cur = {'titulo': 'Exemplo de Preenchimento', 'els': [], 'ex': True, 'head': e['text']}; continue
        cur['els'].append(e)
    subs.append(cur)
    outros = []
    for s in subs:
        if not s['els'] and not s.get('ex'):
            continue
        tit = s['titulo'] or ''
        tl = tit.lower()
        if s.get('ex'):
            ex = parse_example(s['els'])
            h = re.sub(r'^Exemplos? de [Pp]reenchimento\s*:?\s*', '', s['head']).strip()
            if h:
                ex['contexto'] = h
            reg.setdefault('exemplos_preenchimento', []).append(ex)
        elif 'validação do registro' in tl:
            put(reg, 'regras_validacao_registro_detalhe', split_rules(render(s['els']) or ''), tit)
        elif 'validação de campo' in tl or 'validação dos campos' in tl:
            tabs = [e['rows'] for e in s['els'] if e['k'] == 'table']
            put(reg, 'regras_validacao_campos', parse_field_rules(tabs), tit)
            txt = render([e for e in s['els'] if e['k'] == 'text'])
            if txt:
                put(reg, 'regras_validacao_campos_observacoes', txt, tit)
        elif 'tabela' in tl and 'din' in tl:
            extra = re.sub(r'^(I{1,3}|IV|V)\s*[–-]\s*Tabelas? Dinâmicas?\s*:?\s*', '', tit).strip()
            txt = render(s['els']) or ''
            put(reg, 'tabelas_dinamicas', ((extra + '\n') if extra else '') + txt, tit)
        else:
            txt = render(s['els'])
            if txt:
                outros.append({'titulo': tit or None, 'conteudo': txt} if tit else {'conteudo': txt})
    if outros:
        reg['informacoes_adicionais'] = outros
    # tables found in description kept also in structured form
    dt = [table_obj(e['rows']) for e in els[:desc_end] if e['k'] == 'table']
    dt = [t for t in dt if t]
    return reg

# ---------------------------------------------------------------- general chapters
def sections_numbered(els):
    """Split chapter elements by numbered headings like 1.2. / 2.10.1"""
    secs, cur = [], {'titulo': None, 'els': []}
    for e in els:
        if e['k'] == 'text' and re.match(r'^(Seção )?\d+\.\d+(\.\d+)*\.?\s+\S', e['text']) and len(e['text']) < 160 and e['x0'] < 60:
            secs.append(cur); cur = {'titulo': e['text'], 'els': []}; continue
        cur['els'].append(e)
    secs.append(cur)
    out = []
    for s in secs:
        c = render(s['els'])
        if not s['titulo'] and not c:
            continue
        pg = [e['page'] for e in s['els']]
        d = {'secao': s['titulo']}
        if pg:
            d['paginas_manual'] = [min(pg), max(pg)]
        d['conteudo'] = c
        out.append(d)
    return out

# ---------------------------------------------------------------- write
if os.path.exists(OUT):
    shutil.rmtree(OUT)
os.makedirs(OUT)

meta = {
    'fonte': SRC,
    'documento': 'Manual de Orientação do Leiaute 12 da Escrituração Contábil Fiscal (ECF)',
    'ato': 'Anexo ao Ato Declaratório Executivo Cofis nº 02/2026',
    'atualizacao': 'julho/2026',
    'ano_calendario': '2025 e situações especiais de 2026',
    'leiaute': 12,
    'orgao': 'RFB/Subsecretaria de Fiscalização/Coordenação Geral de Fiscalização/Div. de Captação de Dados',
    'total_paginas': len(P),
}

chap_names = {}
blocks = {}
registers = {}
cur_block = None
for seg in segments:
    if seg['kind'] == 'cap':
        n = seg['code']
        # chapter 4 general part ends where layout (block 0) starts: segment already ends at first bloco heading
        doc = {'_meta': meta, 'capitulo': int(n), 'titulo': seg['title'], 'paginas_manual': pages_of(seg)}
        if n == '4':
            doc['nota'] = 'A Tabela de Registros (4.2) também está estruturada em arvore_registros.yaml / arvore_registros.json.'
        doc['secoes'] = sections_numbered(seg['els'])
        fn = f"capitulo_{n}_" + re.sub(r'[^a-z0-9]+', '_', unicodedata.normalize('NFKD', seg['title'].lower()).encode('ascii', 'ignore').decode()).strip('_') + '.yml'
        chap_names[n] = fn
        dump(doc, os.path.join(OUT, 'geral', fn))
    elif seg['kind'] == 'bloco':
        cur_block = seg['code']
        blocks[cur_block] = {'bloco': cur_block, 'nome': seg['title'], 'paginas_manual': pages_of(seg),
                             'orientacoes_gerais': render(seg['els'])}
    elif seg['kind'] == 'reg':
        r = parse_register(seg)
        registers[r['registro']] = r

# Level divergences: the layout section of each register is more specific than the summary table 4.2
DIVERG = {}
for code, r in registers.items():
    t = TABLE.get(code)
    if t and r.get('nivel_hierarquico') is not None and r['nivel_hierarquico'] != t['nivel']:
        DIVERG[code] = (t['nivel'], r['nivel_hierarquico'])
        t['nota'] = (f'Divergência no manual: a Tabela de Registros (item 4.2) indica nível {t["nivel"]}, '
                     f'mas o leiaute do registro indica nível {r["nivel_hierarquico"]}. A árvore usa o nível do leiaute.')
        t['nivel_tabela_4_2'] = t['nivel']
        t['nivel'] = r['nivel_hierarquico']
if DIVERG:
    build_tree()
    for code, r in registers.items():
        if code in TABLE:
            r['hierarquia'] = rel(code)
            r['tabela_registros']['nivel'] = TABLE[code].get('nivel_tabela_4_2', TABLE[code]['nivel'])
print('level divergences:', DIVERG)

# texts between a block's last register and next block heading belong to that last register (e.g. long
# appendices); those are already kept inside the register's informacoes_adicionais.

LEGENDA_CAMPOS = {
    'tipo': 'N = numérico; NS = numérico com sinal; C = alfanumérico',
    'tamanho': 'Algarismo = tamanho exato (N/NS) ou exato no caso geral (C); "-" em N/NS = sem máximo; "-" em C = máximo de 255 caracteres; "65536" = até 65.536 caracteres',
    'decimal': 'Algarismo = quantidade máxima de casas decimais; "-" = sem casas decimais',
    'obrigatorio': 'true = Sim; false = Não',
    'formato_linha': 'Cada linha do arquivo: |REG|CAMPO2|CAMPO3|...| (campos delimitados por pipe, valores decimais com vírgula)',
}

reg_files = {}
for code, r in registers.items():
    doc = {'_meta': {'fonte': SRC, 'leiaute': 12, 'atualizacao': 'julho/2026', 'legenda_campos': LEGENDA_CAMPOS}}
    doc.update(r)
    if TABLE.get(code, {}).get('nota'):
        doc['nota_errata'] = TABLE[code]['nota']
    fn = os.path.join('registros', f'bloco_{code[0]}', f'{code}.yml')
    reg_files[code] = fn
    dump(doc, os.path.join(OUT, fn))

# block files
BLOCK_ORDER = ['0', 'C', 'E', 'J', 'K', 'L', 'M', 'N', 'P', 'Q', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', '9']
# block descriptions from table 4.1
desc41 = {}
for e in ELS:
    if e['k'] == 'table' and e['page'] in (41, 42):
        for r in e['rows']:
            if len(r) >= 3 and re.match(r'^[0-9A-Z]$', flat(r[0])):
                desc41[flat(r[0])] = {'nome': flat(r[1]), 'descricao': clean(r[2])}
for b in BLOCK_ORDER:
    regs = [c for c in order if c[0] == b] + [c for c in registers if c[0] == b and c not in TABLE]
    d = {'_meta': {'fonte': SRC, 'leiaute': 12, 'atualizacao': 'julho/2026'}}
    d.update(blocks.get(b, {'bloco': b}))
    if b in desc41:
        d['tabela_blocos'] = desc41[b]
    d['registros'] = [{'registro': c, 'nome': (TABLE.get(c) or registers.get(c, {})).get('nome') or registers.get(c, {}).get('nome'),
                       'nivel': (TABLE.get(c) or {}).get('nivel', registers.get(c, {}).get('nivel_hierarquico')),
                       'arquivo': f'../registros/bloco_{b}/{c}.yml' if c in registers else None} for c in regs]
    dump(d, os.path.join(OUT, 'blocos', f'bloco_{b}.yml'))

# tree
def node(c):
    t = TABLE[c]
    n = {'registro': c, 'nome': t['nome'], 'nivel': t['nivel'],
         'obrigatoriedade_entrada': t['obrigatoriedade_entrada'], 'obrigatoriedade_saida': t['obrigatoriedade_saida'],
         'ocorrencia': t['ocorrencia']}
    if t.get('nota'):
        n['nota'] = t['nota']
    if children[c]:
        n['filhos'] = [node(x) for x in children[c]]
    return n

roots = [c for c in order if parent[c] is None]
flat_idx = {}
for c in order:
    t = TABLE[c]
    rr = rel(c)
    flat_idx[c] = {'nome': t['nome'], 'bloco': c[0], 'nivel': t['nivel'],
                   'obrigatoriedade_entrada': split_obrig(t['obrigatoriedade_entrada']),
                   'obrigatoriedade_saida': split_obrig(t['obrigatoriedade_saida']),
                   'ocorrencia': t['ocorrencia'], **rr,
                   'ordem_no_arquivo': order.index(c) + 1,
                   'arquivo_detalhe': reg_files.get(c)}
    if t.get('nota'):
        flat_idx[c]['nota'] = t['nota']
tree = {
    '_meta': {**meta, 'descricao': 'Árvore hierárquica de registros da ECF conforme a Tabela de Registros (item 4.2 do manual). '
              'O pai de cada registro é o registro imediatamente anterior com nível hierárquico menor. '
              'A ordem dos registros é a ordem obrigatória de apresentação no arquivo.'},
    'legenda': {'obrigatoriedade': OBRIG,
                'obrigatoriedade_entrada': 'Preenchimento obrigatório no momento da importação do arquivo da ECF para o programa da ECF.',
                'obrigatoriedade_saida': 'Obrigatório no momento da geração do arquivo da ECF para transmissão.',
                'ocorrencia': {'[0]': 'não há ocorrência', '[1:1]': '1 ocorrência', '[0:12]': 'de zero a 12 ocorrências',
                               '[1:N]': 'de uma até n ocorrências', '[0:N]': 'de zero até n ocorrências'}},
    'ordem_blocos': BLOCK_ORDER,
    'arvore': [node(c) for c in roots],
    'indice': flat_idx,
}
dump(tree, os.path.join(OUT, 'arvore_registros.yaml'))
with open(os.path.join(OUT, 'arvore_registros.json'), 'w', encoding='utf-8') as f:
    json.dump(tree, f, ensure_ascii=False, indent=2)

# report
missing_body = [c for c in order if c not in registers]
extra_body = [c for c in registers if c not in TABLE]
print('registers parsed', len(registers), 'table', len(order))
print('in table but no layout section:', missing_body)
print('layout section not in table:', extra_body)
nof = [c for c, r in registers.items() if not r['campos']]
print('no fields:', nof)
json.dump({'reg_files': reg_files, 'chapters': chap_names}, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'build_info.json'), 'w'))
