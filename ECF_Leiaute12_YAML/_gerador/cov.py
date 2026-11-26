import json,glob,re,yaml,sys,collections
S=sys.argv[1]
P=json.load(open(S+'/pages.json'))
norm=lambda s: re.sub(r'[^\w]','',s.lower().replace('\\|','|'))
vals=[]
def walk(o):
    if isinstance(o,dict):
        for v in o.values(): walk(v)
    elif isinstance(o,list):
        for v in o: walk(v)
    elif o is not None: vals.append(str(o))
for f in sorted(glob.glob(S+'/out/**/*.yml',recursive=True)):
    walk(yaml.safe_load(open(f)))
blob=norm(''.join(vals))
SKIP=re.compile(r'^(Capítulo \d|Bloco [0-9A-Z]:|Registro [0-9A-Z]{4}:|REGISTRO [0-9A-Z]{4}:|Regras de Validação d|Nível Hierárquico|Campo\(s\) chave|I+ ?[–-] ?Regras de Valida|Exemplos? de Preenchimento|Nº Campo|III ?[–-] ?Tabelas Din)')
miss=[];tot=0
for pg in P[9:]:
  for e in pg['elements']:
    items=[e['text']] if e['k']=='text' else [c for r in e['rows'] for c in r]
    for t in items:
      for l in t.split('\n'):
        n=norm(l)
        if len(n)<6 or SKIP.match(l.strip()): continue
        tot+=1
        if n not in blob: miss.append((pg['page'],l))
print(tot,len(miss))
c=collections.Counter(p for p,_ in miss)
print(c.most_common(20))
for m in miss[:int(sys.argv[2]) if len(sys.argv)>2 else 40]: print(m)
