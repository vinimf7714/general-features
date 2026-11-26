import yaml,glob,os,sys,json
OUT=sys.argv[1]
def str_rep(d, s):
    return d.represent_scalar('tag:yaml.org,2002:str', s, style='|' if '\n' in s else None)
yaml.add_representer(str, str_rep)
tree=json.load(open(os.path.join(OUT,'arvore_registros.json')))
blocos=[]
for b in tree['ordem_blocos']:
    d=yaml.safe_load(open(os.path.join(OUT,'blocos',f'bloco_{b}.yml')))
    blocos.append({'bloco':b,'nome':d.get('nome') or d.get('tabela_blocos',{}).get('nome'),'arquivo':f'blocos/bloco_{b}.yml',
                   'registros':[r['registro'] for r in d['registros']]})
idx={
 '_meta':tree['_meta'] | {'descricao':'Base de conhecimento estruturada (YAML) extraída do Manual de Orientação do Leiaute 12 da ECF, para consulta por agentes de IA.'},
 'estrutura':{
   'arvore_registros.yaml / arvore_registros.json':'Árvore hierárquica completa (pai → filhos) + índice plano por registro com pai, filhos, irmãos, nível, obrigatoriedade, ocorrência e ordem no arquivo.',
   'registros/bloco_<B>/<REG>.yml':'Um arquivo por registro (ex.: registros/bloco_M/M300.yml): descrição, nível, ocorrência, campos-chave, hierarquia, campos (nº, nome, descrição, tipo, tamanho, decimal, valores válidos, obrigatório), regras de validação do registro e dos campos, tabelas dinâmicas e exemplos de preenchimento.',
   'blocos/bloco_<B>.yml':'Um arquivo por bloco: orientações gerais do bloco (texto introdutório do manual) e lista dos registros.',
   'geral/capitulo_<N>_*.yml':'Capítulos 1 a 4 (informações gerais, dados técnicos do arquivo, regras de validação, blocos e registros), divididos por seção.',
   '_gerador/':'Scripts Python usados para gerar esta base a partir do PDF (permite regenerar para novas versões do manual).',
 },
 'esquema_registro':{
   'registro':'Código do registro (4 caracteres)','nome':'Nome do registro (título da seção no leiaute)','bloco':'Bloco ao qual pertence',
   'paginas_manual':'[página inicial, página final] no PDF','descricao':'Texto explicativo antes do leiaute (tabelas em markdown)',
   'regras_validacao_registro':'Nomes das regras aplicadas ao registro','nivel_hierarquico':'Nível conforme o leiaute do registro',
   'ocorrencia':'Ocorrência conforme o leiaute do registro','campos_chave':'Campos que formam a chave do registro',
   'tabela_registros':'Dados da Tabela de Registros (item 4.2): nível, obrigatoriedade de entrada/saída (código + condição), ocorrência',
   'hierarquia':'pai, filhos e irmãos (registros com o mesmo pai)','campos':'Lista ordenada de campos do leiaute',
   'regras_validacao_registro_detalhe':'Descrição de cada regra de validação do registro',
   'regras_validacao_campos':'Regras por campo: [{numero, campo, regras: [{regra, descricao, tipo (Erro/Aviso)}]}]',
   'tabelas_dinamicas':'Referências às tabelas dinâmicas do registro','exemplos_preenchimento':'Linhas de exemplo e explicação de cada valor',
   'informacoes_adicionais':'Outras seções do registro no manual','secoes_repetidas_no_manual':'Quando o manual repete uma seção, a repetição é preservada aqui',
 },
 'observacoes_de_qualidade':[
   'Textos extraídos automaticamente do PDF e normalizados (quebras de linha do PDF unidas, cabeçalhos/rodapés removidos). Em caso de dúvida, confira a página indicada em paginas_manual.',
   'Errata: a Tabela de Registros (4.2) do manual lista "9099 - Encerramento do Bloco 9"; foi normalizado para 9990, conforme o leiaute do registro.',
   'Divergência: X451 e Y650 aparecem com nível 2 na Tabela 4.2, mas nível 3 no leiaute do registro; a árvore usa o nível 3 (X451 filho de X450; Y650 filho de Y640).',
   'Valores de "tamanho" e "decimal" foram mantidos como no manual ("-" tem significado próprio — ver _meta.legenda_campos em cada registro).',
   'O conteúdo das tabelas dinâmicas (planilha Tabelas_Dinamicas_ECF_Leiaute_12_AC2025_SIT_ESP_2026.xlsx) não faz parte do PDF e não está incluído.',
 ],
 'blocos':blocos,
}
with open(os.path.join(OUT,'indice.yml'),'w',encoding='utf-8') as f:
    yaml.dump(idx,f,allow_unicode=True,sort_keys=False,width=4096)
