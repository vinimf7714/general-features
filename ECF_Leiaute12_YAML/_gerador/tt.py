import pdfplumber,sys
pdf=pdfplumber.open('/Users/viniciusevangelista/Documents/Manual_ECF_Leiaute_12_23_07_2026_AC_2025_SIT_ESP_2026.pdf')
def tables(p):
    out=[]
    for t in p.find_tables():
        x0,t0,x1,b0=t.bbox
        c=p.crop((max(0,x0-1),max(0,t0-1),min(p.width,x1+1),min(p.height,b0+1)))
        H=[];V=[]
        for r in c.rects+c.lines:
            w=r['x1']-r['x0']; h=r['bottom']-r['top']
            if h<=1.6 and w>2: H.append(round((r['top']+r['bottom'])/2,1))
            elif w<=1.6 and h>2: V.append(round((r['x0']+r['x1'])/2,1))
        if len(set(H))<2 or len(set(V))<2: out.append((t.bbox,t.extract()));continue
        ts=c.extract_tables({'vertical_strategy':'explicit','horizontal_strategy':'explicit','explicit_vertical_lines':sorted(set(V)),'explicit_horizontal_lines':sorted(set(H)),'snap_tolerance':2.5,'join_tolerance':2.5,'intersection_tolerance':3})
        for tt in ts: out.append((t.bbox,tt))
    return out
if __name__=='__main__':
  for pn in map(int,sys.argv[1:]):
    p=pdf.pages[pn-1]
    print('=====',pn)
    for bb,t in tables(p):
        print('--T',[round(x) for x in bb])
        for r in t: print(r)
