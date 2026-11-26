import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdfplumber,json,re
from tt import tables
PDF='/Users/viniciusevangelista/Documents/Manual_ECF_Leiaute_12_23_07_2026_AC_2025_SIT_ESP_2026.pdf'
pdf=pdfplumber.open(PDF)
LOW=r'[a-zà-úç]'
def is_texty(rows):
    rows=[[c for c in r] for r in rows if any(c.strip() for c in r)]
    if not rows: return True
    ncol=max(len(r) for r in rows)
    if ncol<=2: return True
    if max(sum(1 for c in r if c.strip()) for r in rows)<=1: return True
    pairs=splits=0
    for r in rows:
        cs=[c.strip() for c in r if c.strip()]
        for a,b in zip(cs,cs[1:]):
            pairs+=1
            if re.search(LOW+'$',a) and re.match('^'+LOW,b): splits+=1
    return pairs and splits/pairs>0.25
def lines_of(q):
    out=[]
    for ln in q.extract_text_lines(x_tolerance=1.5):
        t=ln['text'].strip()
        if not t: continue
        if t.startswith('Anexo ao Ato Declarat') or t.startswith('RFB/Subsecretaria') or re.match(r'^Atualização: \w+/\d{4}$',t) or t.startswith('Manual de Orientação do Leiaute 12 da ECF') and len(t)<60: continue
        out.append({'k':'text','top':ln['top'],'bottom':ln['bottom'],'x0':ln['x0'],'text':t})
    return out
pages=[]
for i,p in enumerate(pdf.pages):
    pn=i+1
    els=[]; real=[]
    for bb,t in tables(p):
        rows=[[(c or '').strip() for c in r] for r in t]
        # drop tables nested inside an already-kept real table
        if any(bb[0]>=r[0]-1 and bb[1]>=r[1]-1 and bb[2]<=r[2]+1 and bb[3]<=r[3]+1 for r in real): continue
        if is_texty(rows): continue
        rows=[r for r in rows if any(r)]
        els.append({'k':'table','top':bb[1],'bottom':bb[3],'rows':rows}); real.append(bb)
    def keep(o, real=real):
        if o.get('object_type') != 'char': return True
        cx=(o['x0']+o['x1'])/2; cy=(o['top']+o['bottom'])/2
        return not any(b[0]-0.5<=cx<=b[2]+0.5 and b[1]-0.5<=cy<=b[3]+0.5 for b in real)
    q=p.filter(keep)
    els+=lines_of(q)
    els.sort(key=lambda e:(e['top'],0 if e['k']=='table' else 1))
    pages.append({'page':pn,'elements':els})
    if pn%100==0: print(pn,file=sys.stderr)
json.dump(pages,open('pages.json','w'),ensure_ascii=False)
