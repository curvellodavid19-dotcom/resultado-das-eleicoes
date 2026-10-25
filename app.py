"""Brasil Votos 2026 — independent, source-labelled dashboard. Never fabricates totals."""
import asyncio, json, os, time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response

ROOT=Path(__file__).resolve().parent
app=FastAPI(title='Brasil Votos 2026 - FINAL', docs_url='/docs', openapi_url='/openapi.json')
UFS='AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split()
BASE='https://resultados.tse.jus.br/oficial'
PRES='6258'; GOV='6260'; FIRST_PRES='6257'; FIRST_GOV='6259'
try: TZ=ZoneInfo('America/Sao_Paulo')
except Exception: TZ=timezone(timedelta(hours=-3))
START=datetime(2026,10,25,17,tzinfo=TZ)
CACHE={}; LOCKS={}; HISTORY=deque(maxlen=180); ROUND_HISTORY={1:deque(maxlen=180),2:HISTORY}; NATIONAL_CACHE={}; NATIONAL_LOCKS={}
INTERVAL=max(20,int(os.getenv('CACHE_SECONDS','30')))

def num(v):
    try: return int(str(v).replace('.','').replace(',','')) if v is not None else 0
    except (ValueError,TypeError):return 0

def source_url(uf,election,office):
    return f'{BASE}/ele2026/{election}/dados/{uf.lower()}/{uf.lower()}-c{office:04d}-e{int(election):06d}-u.json'

def parse(raw,uf,office):
    if not isinstance(raw,dict):raise ValueError('Arquivo nao e objeto JSON')
    cargos=raw.get('carg') or []
    cargo=next((c for c in cargos if num(c.get('cd'))==office),None)
    if cargo is None and raw.get('cand') is not None and num(raw.get('cdcar'))==office: cargo=raw
    if cargo is None: raise ValueError('Cargo nao localizado no arquivo EA20')
    tally=defaultdict(int);names={};party={};sqcands={}
    for agr in cargo.get('agr',[]):
        for par in agr.get('par',[]) or [agr]:
            for c in par.get('cand',[]):
                n=str(c.get('n') or c.get('sqcand') or '').strip()
                if not n:continue
                tally[n]+=num(c.get('vap'));sqcands[n]=str(c.get('sqcand') or c.get('sqCand') or '')
                names[n]=str(c.get('nm') or c.get('nmu') or names.get(n) or 'Candidatura '+n)
                party[n]=str(c.get('sgp') or c.get('sg') or par.get('sgp') or par.get('sg') or party.get(n) or '')
    if not tally and cargo.get('cand'):
        for c in cargo['cand']:
            n=str(c.get('n') or c.get('sqcand') or '').strip()
            if n: tally[n]+=num(c.get('vap'));sqcands[n]=str(c.get('sqcand') or c.get('sqCand') or '');names[n]=str(c.get('nmu') or c.get('nm') or n);party[n]=str(c.get('sgp') or '')
    if not tally:raise ValueError('Candidaturas nao encontradas; formato EA20 pode ter mudado')
    total=sum(tally.values())
    candidates=sorted(({'numero':n,'sqcand':sqcands.get(n,''),'nome':names[n],'partido':party[n],'votos':v,'percentual':round(100*v/total,2) if total else 0} for n,v in tally.items()),key=lambda c:-c['votos'])
    st=num(cargo.get('st',raw.get('st'))); s=num(cargo.get('s',raw.get('s')))
    if s and st>s:raise ValueError('Numero de secoes inconsistente')
    return {'uf':uf,'candidatos':candidates,'lider':candidates[0] if total else None,'validos':total,'secoes_totalizadas':st,'secoes_previstas':s,'percentual_secoes':round(st*100/s,2) if s else None,'gerado_em':f"{raw.get('dg','')} {raw.get('hg','')}".strip(),'totalizacao_final':str(raw.get('and','')).lower()=='f','fonte_url':source_url(uf,PRES if office==1 else GOV,office)}

async def fetch_one(client,uf,election,office):
    url=source_url(uf,election,office)
    try:
        resp=await client.get(url);resp.raise_for_status()
        return parse(resp.json(),uf,office),None
    except Exception as exc:
        return None,{'uf':uf,'erro':f'{type(exc).__name__}: {str(exc)[:110]}','url':url}

async def collect(election,office,ufs):
    key=f'{election}-{office}'
    lock=LOCKS.setdefault(key,asyncio.Lock())
    async with lock:
        item=CACHE.get(key)
        if item and time.monotonic()-item['at']<INTERVAL:return item['data']
        async with httpx.AsyncClient(timeout=13,follow_redirects=True,limits=httpx.Limits(max_connections=8),headers={'User-Agent':'BrasilVotos2026/4.0 independent election information'}) as client:
            values=await asyncio.gather(*(fetch_one(client,uf,election,office) for uf in ufs))
        states=[a for a,b in values if a]; errors=[b for a,b in values if b]
        result={'estados':states,'erros':errors,'ufs_recebidas':len(states),'ufs_esperadas':len(ufs),'consultado_em':datetime.now(TZ).isoformat(),'eleicao':election,'cargo':office,'fonte':BASE,'status':'parcial' if states else 'indisponivel'}
        if not states and item and item['data']['estados']:
            result={**item['data'],'status':'desatualizado','erros':errors,'ultima_tentativa':datetime.now(TZ).isoformat()}
        CACHE[key]={'at':time.monotonic(),'data':result}
        return result

@app.get('/api/diagnostico')
async def diagnostico():
    return {'versao':'FINAL 2026','servidor':'online','porta':8014,'rotas':['/api/status','/api/resultados','/api/governadores','/api/eleitos','/api/diagnostico'],'nota':'TSE consultado apenas após 17h; antes disso pode haver arquivos ausentes. Verifique erros em /api/resultados.','urls_exemplo':{'presidente_BR':source_url('BR',PRES,1),'exterior_ZZ':source_url('ZZ',PRES,1),'governador_RJ':source_url('RJ',GOV,3)}}

@app.get('/api/status')
async def status():
    now=datetime.now(TZ)
    return {'servidor':'online','horario_brasilia':now.isoformat(),'divulgacao_iniciada':now>=START,'codigos':{'presidente':PRES,'governador':GOV},'fonte':BASE}

def summary(candidates):
    total=sum(c['votos'] for c in candidates)
    return [{**c,'percentual':round(100*c['votos']/total,2) if total else 0} for c in candidates]

def sum_regions(regions):
    tally=defaultdict(int); names={}; parties={};sqcands={}
    for state in regions:
        for c in state['candidatos']:
            tally[c['numero']]+=c['votos']; names[c['numero']]=c['nome']; parties[c['numero']]=c['partido'];sqcands[c['numero']]=c.get('sqcand','')
    return summary(sorted(({'numero':n,'sqcand':sqcands.get(n,''),'nome':names[n],'partido':parties[n],'votos':v} for n,v in tally.items()),key=lambda c:-c['votos']))

async def fetch_national_cached(election):
    """Limit BR/ZZ TSE requests across visitors to one pair per cache interval."""
    lock=NATIONAL_LOCKS.setdefault(election,asyncio.Lock())
    async with lock:
        cached=NATIONAL_CACHE.get(election)
        if cached and time.monotonic()-cached['at']<INTERVAL:
            return cached['value']
        async with httpx.AsyncClient(timeout=13,follow_redirects=True,headers={'User-Agent':'Mozilla/5.0 BrasilVotos2026'}) as client:
            br,br_error=await fetch_one(client,'BR',election,1)
            zz,zz_error=await fetch_one(client,'ZZ',election,1)
        value=(br,br_error,zz,zz_error)
        NATIONAL_CACHE[election]={'at':time.monotonic(),'value':value}
        return value

@app.get('/api/resultados')
async def results(turno: int = 2):
    if turno not in (1,2): return {"status":"indisponivel","mensagem":"Turno invalido","estados":[]}
    election = FIRST_PRES if turno == 1 else PRES
    inicio = datetime(2026,10,4 if turno == 1 else 25,17,tzinfo=TZ)
    if datetime.now(TZ)<inicio:
        return {'status':'aguardando','mensagem':'Divulgacao oficial prevista a partir das 17h de Brasilia.','estados':[],'historico':list(ROUND_HISTORY[turno]),'exterior_status':'aguardando'}
    data=await collect(election,1,UFS)
    # O arquivo BR oficial ja inclui exterior. NUNCA somar BR + ZZ (duplicaria exterior).
    br,br_error,zz,zz_error=await fetch_national_cached(election)
    partial=sum_regions(data['estados']+([zz] if zz else []))
    candidates=br['candidatos'] if br else partial
    sec=br['secoes_totalizadas'] if br else sum(x['secoes_totalizadas'] for x in data['estados']+([zz] if zz else []))
    prev=br['secoes_previstas'] if br else sum(x['secoes_previstas'] for x in data['estados']+([zz] if zz else []))
    total=sum(x['votos'] for x in candidates)
    snap={'hora':datetime.now(TZ).strftime('%H:%M:%S'),'votos':total,'ufs':data['ufs_recebidas']}
    history=ROUND_HISTORY[turno]
    if total and (not history or history[-1]['votos']!=total or history[-1]['ufs']!=snap['ufs']):history.append(snap)
    return {**data,'status':'oficial' if br and total else ('parcial' if total else 'indisponivel'),'candidatos':candidates,'votos_validos':total,'secoes_totalizadas':sec,'secoes_previstas':prev,'percentual_secoes':round(100*sec/prev,2) if prev else None,'historico':list(ROUND_HISTORY[turno]),'exterior':zz,'exterior_status':'disponivel' if zz else 'indisponivel','exterior_erro':zz_error,'brasil_erro':br_error,'abrangencia_completa':bool(br),'total_nacional_oficial':bool(br),'totalizacao_final':bool(br and br['totalizacao_final']),'aviso':'Total oficial BR (inclui exterior); ZZ exibido separadamente, sem somar duas vezes.' if br else 'Soma parcial de UFs e exterior quando disponiveis. Nao equivale ao total oficial BR.'}

@app.get('/api/governadores')
async def governors():
    if datetime.now(TZ)<START:return {'status':'aguardando','estados':[]}
    return await collect(GOV,3,UFS)

def ea10_url(election,office):
    return f'{BASE}/ele2026/{election}/dados/br/br-c{office:04d}-e{int(election):06d}-e.json'

async def read_ea10(client,election,office):
    url=ea10_url(election,office)
    try:
        r=await client.get(url);r.raise_for_status();raw=r.json()
        if not isinstance(raw,dict) or not isinstance(raw.get('abr'),list) or num(raw.get('cdcar'))!=office:
            raise ValueError('Formato EA10 invalido')
        result=[]
        for abr in raw['abr']:
            if abr.get('esae')=='s':continue
            for c in abr.get('cand',[]):
                result.append({'cargo':{3:'Governador',5:'Senador',6:'Deputado federal'}.get(office,str(office)),'uf':str(abr.get('cdabr','')).upper(),'nome':str(c.get('nmu') or c.get('nm') or ''),'numero':str(c.get('n') or ''),'partido':str(c.get('sgp') or ''),'votos':num(c.get('vap')),'eleicao':election,'fonte_url':url,'totalizacao_abrangencia':abr.get('dt')})
        return result,None
    except Exception as exc:return [],{'url':url,'erro':f'{type(exc).__name__}: {str(exc)[:110]}'}

@app.get('/api/eleitos')
async def elected():
    if datetime.now(TZ)<START:return {'status':'aguardando','eleitos':[],'mensagem':'Aguardando inicio da divulgacao oficial.'}
    async with httpx.AsyncClient(timeout=13,follow_redirects=True,headers={'User-Agent':'Mozilla/5.0 BrasilVotos2026'}) as client:
        first=await asyncio.gather(read_ea10(client,FIRST_GOV,5),read_ea10(client,FIRST_GOV,6))
        gov2,err2=await read_ea10(client,GOV,3)
        br,br_err=await fetch_one(client,'BR',PRES,1)
        gov_data=await collect(GOV,3,UFS)
    elected=[]; errors=[e for _,e in first if e]
    if err2:errors.append(err2)
    if br_err:errors.append(br_err)
    # EA10 para senador/deputado federal lista candidatos eleitos oficialmente.
    for rows,err in first:elected.extend(rows)
    # Governador 2o turno: EA10 pode listar DOIS candidatos; selecionar apenas
    # o lider quando a totalizacao final EA20 da UF confirmar o resultado.
    for state in gov_data['estados']:
        if not state['totalizacao_final'] or not state['lider'] or not state['validos']:continue
        matched=next((c for c in gov2 if c['uf']==state['uf'] and c['numero']==state['lider']['numero']),None)
        if matched:elected.append(matched)
    # Presidente: TSE informa que NAO existe EA10 presidencial.
    # Usar EA20 BR somente quando marcado como totalizacao final.
    if br and br['totalizacao_final'] and br['lider'] and br['validos']:
        c=br['lider'];elected.append({'cargo':'Presidente','uf':'BR','nome':c['nome'],'numero':c['numero'],'partido':c['partido'],'votos':c['votos'],'eleicao':PRES,'fonte_url':source_url('BR',PRES,1)})
    return {'status':'disponivel' if elected else 'aguardando_confirmacao','eleitos':elected,'erros':errors,'fonte':BASE,'mensagem':'Somente confirmacoes suportadas por arquivos oficiais. Presidente via EA20 BR final; Governador 2o turno via EA10 + EA20 final; Senador e Deputado Federal via EA10 1o turno. Outros cargos ainda nao integrados.'}


@app.get('/api/foto-presidencial/{turno}/{sqcand}', include_in_schema=False)
async def foto_presidencial(turno: int, sqcand: str):
    # Somente imagens oficiais TSE; nao aceita URLs arbitrarias.
    if turno not in (1,2) or not sqcand.isdecimal() or len(sqcand)>18:
        return Response(status_code=404)
    election=FIRST_PRES if turno==1 else PRES
    url=f'{BASE}/ele2026/{election}/fotos/br/{sqcand}.jpeg'
    try:
        async with httpx.AsyncClient(timeout=9,follow_redirects=True) as client:
            resp=await client.get(url)
        if resp.status_code==200 and resp.headers.get('content-type','').lower().startswith('image/') and len(resp.content)<2000000:
            return Response(content=resp.content,media_type='image/jpeg',headers={'Cache-Control':'public, max-age=86400'})
    except httpx.HTTPError:
        pass
    return Response(status_code=404)

@app.get('/')
async def index():return FileResponse(ROOT/'index.html')
@app.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    return FileResponse(
        ROOT / "sitemap.xml",
        media_type="application/xml"
    )
@app.get("/robots.txt", include_in_schema=False)
def robots():
    return FileResponse(
        ROOT / "robots.txt",
        media_type="text/plain"
    )
