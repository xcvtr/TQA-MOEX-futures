#!/usr/bin/env python3
"""Phase2 Dashboard — клик по графику = zoom. Порт 8086."""
import sys,os,json,http.server,socketserver
from urllib.parse import urlparse,parse_qs
sys.path.insert(0,'/home/user/projects/TQA-MOEX')
os.chdir('/home/user/projects/TQA-MOEX')
import numpy as np, clickhouse_connect
from config import CH_HOST,CH_PORT,CH_DB
PORT=8090
ch=clickhouse_connect.get_client(host=CH_HOST,port=CH_PORT,database=CH_DB)
with open('reports/triz_phase4/phase2_fullscan.json') as f: PHASE2=json.load(f)
PATTERNS={'v':lambda dv,dyb,dys,dfn,dtoi:dv>0 and dtoi>0 and dyb>0,'s':lambda dv,dyb,dys,dfn,dtoi:dv>0 and dyb>0 and dfn<0,'d':lambda dv,dyb,dys,dfn,dtoi:dv>0 and dtoi<0,'y':lambda dv,dyb,dys,dfn,dtoi:dv>0 and dyb<0 and dfn>0,'f':lambda dv,dyb,dys,dfn,dtoi:dv>0 and abs(dfn)>5}
_CACHE={}
def load_data(sym):
    if sym in _CACHE: return _CACHE[sym]
    rows=ch.query(f"""SELECT time,open,high,low,close FROM moex.prices_5m WHERE symbol='{sym}' AND time>='2024-01-01' AND time<='2026-05-01' ORDER BY time""").result_rows
    if not rows or len(rows)<1000: return None
    times=[str(r[0]) for r in rows];close=np.array([float(r[4]) for r in rows]);dates=[str(r[0])[:10] for r in rows]
    _CACHE[sym]={'N':len(rows),'times':times,'dates':dates,'close':[float(c) for c in close]}
    return _CACHE[sym]
HTML=r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Ph2</title><style>
body{background:#0d1117;color:#c9d1d9;font-family:system-ui,sans-serif;padding:12px;margin:0}
.sel{padding:4px 10px;background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;font-size:12px}
.cfg{display:flex;gap:3px;margin:6px 0;flex-wrap:wrap}
.cb{padding:2px 7px;border:1px solid #30363d;border-radius:3px;cursor:pointer;font-size:9px;background:#161b22;color:#c9d1d9}
.cb.a{background:#1f6feb;border-color:#1f6feb;color:#fff}
.st{display:inline-block;background:#161b22;padding:2px 8px;border-radius:4px;font-size:10px;margin-left:6px}
.st .l{color:#8b949e;font-size:8px}
#c{width:100%;height:280px;background:#161b22;border-radius:5px;margin:4px 0;cursor:crosshair}
#cz{width:100%;height:160px;background:#161b22;border-radius:5px;margin:4px 0}
.r{color:#8b949e;font-size:10px;margin:2px 0}
.zi{background:#161b22;border-radius:5px;padding:8px;margin:6px 0;display:none;font-size:11px;line-height:1.5}
.zi .up{color:#3fb950}.zi .dn{color:#f85149}.zi .hl{color:#58a6ff}
table{width:100%;border-collapse:collapse;font-size:10px}
th{background:#21262d;padding:2px 5px;text-align:left;color:#8b949e;font-weight:500;font-size:9px}
td{padding:2px 5px;border-bottom:1px solid #21262d;font-size:10px;cursor:pointer}
tr:hover{background:#1c2128}
.w{color:#3fb950}.l{color:#f85149}.oo{background:rgba(56,139,253,0.04)}
.sl{background:rgba(31,111,235,0.25)!important}
</style></head><body>
<h3 style="font-size:14px;margin:0">Phase2 — кликни график/строку</h3>
<div><select id=ts class=sel onchange=loadT()><option value=VB>VB</option><option value=Si>Si</option><option value=SR>SR</option></select>
<span class=st><span class=l>WFA</span><b id=s1>-</b></span>
<span class=st><span class=l>OOS</span><b id=s2>-</b></span>
<span class=st><span class=l>Ret</span><b id=s3>-</b></span>
</div>
<div class=cfg id=bar></div>
<div class=r id=rng></div>
<canvas id=c></canvas>
<canvas id=cz></canvas>
<div class=zi id=zi></div>
<div><span id=tc style="color:#8b949e;font-size:10px"></span></div>
<table><thead><tr><th>#<th>Entry<th>Exit<th>B<th>Dir<th>E<th>X<th>PnL<th>P</thead><tbody id=tb></tbody></table>
<script>
var D={},T=[],sel=null,cfg=null,cx=null,cr=null
function $(id){return document.getElementById(id)}
async function loadT(){
  sel=null;zi.style.display='none';cx=null
  const r=await fetch('/d?t='+ts.value);D=await r.json()
  bar.innerHTML=(D.c||[]).map((c,i)=>'<div class="cb'+(i==0?' a':'')+'" onclick="loadS('+i+')">'+c.pn+' '+c.dr+' h='+c.h+' am='+c.am+' <b>'+(c.os?.c||'-').toFixed(1)+'c</b></div>').join('')
  loadS(0)
}
async function loadS(i){
  sel=null;zi.style.display='none';cx=null
  document.querySelectorAll('.cb').forEach((b,j)=>b.className='cb'+(j==i?' a':''))
  cfg=D.c[i];const r=await fetch('/t?t='+D.tk+'&p='+cfg.p+'&d='+cfg.dr+'&h='+cfg.h+'&a='+cfg.am)
  T=r.ok?await r.json():[]
  const o=cfg.os||{};s1.textContent=D.w||'-';s2.textContent=o.n||'0';s3.textContent=(o.r>0?'+':'')+(o.r||0).toFixed(1)+'%'
  draw(Math.max(0,D.p.length-2000),1000);render()
}
function render(){
  tb.innerHTML=T.map((x,i)=>{
    const c=x.p>0?'w':'l',os=x.e>='2025-01-01'?'oo ':'',sl=sel&&sel.e==x.e?' sl':''
    return '<tr class='+os+c+sl+' di='+i+'><td>'+(i+1)+'<td>'+x.e.slice(5,16)+'<td>'+x.x.slice(5,16)+'<td>'+(x.xi-x.ei)+'<td>'+(x.p>0?'▲':'▼')+'<td>'+x.ep.toFixed(0)+'<td>'+x.xp.toFixed(0)+'<td>'+(x.p>0?'+':'')+x.p.toFixed(0)+'<td>'+(x.e>='2025-01-01'?'O':'I')
  }).join('')
  tc.textContent=T.length+' trades'
}
function draw(zc,zr){
  cx=zc;cr=zr
  const cv=c,cv2=cz
  cv.width=(cv.parentElement.clientWidth-8)*2;cv.height=560
  cv2.width=(cv2.parentElement.clientWidth-8)*2;cv2.height=320
  var N=D.p.length,st=0,en=N-1
  if(zc!==undefined&&zr){st=Math.max(0,zc-zr);en=Math.min(N-1,zc+zr)}
  // Main chart
  ;(function(){
    var ctx=cv.getContext('2d'),W=cv.width,H=cv.height,pl=44,pr=10,pt=10,pb=30,cw=W-pl-pr,ch=H-pt-pb
    var px=D.p,sl=px.slice(st,en+1),mn=Math.min(...sl),mx=Math.max(...sl),rg=mx-mn||1
    var xi=i=>pl+(i-st)/(en-st)*cw,yi=p=>pt+(1-(p-mn)/rg)*ch
    ctx.clearRect(0,0,W,H)
    ctx.strokeStyle='#30363d';ctx.lineWidth=.5
    for(var i=0;i<5;i++){var yy=pt+i*ch/4;ctx.beginPath();ctx.moveTo(pl,yy);ctx.lineTo(W-pr,yy);ctx.stroke();ctx.fillStyle='#8b949e';ctx.font='9px monospace';ctx.fillText((mn+rg*(1-i/4)).toFixed(0),2,yy+3)}
    ctx.strokeStyle='#58a6ff';ctx.lineWidth=1.5;ctx.beginPath()
    for(var i=st;i<=en;i++){i==st?ctx.moveTo(xi(i),yi(px[i])):ctx.lineTo(xi(i),yi(px[i]))}
    ctx.stroke()
    var sp=en-st,ts=sp<=40?5:sp<=100?10:sp<=500?50:Math.floor(sp/8)
    for(var i=Math.ceil(st/ts)*ts;i<=en;i+=ts){ctx.fillStyle='#8b949e';ctx.font='8px monospace';ctx.fillText(zc!==undefined?D.t[i].slice(5,16):D.d[i].slice(5),xi(i),pt+ch+12)}
    rng.textContent=(zc!==undefined?D.t[st].slice(0,16):D.t[0].slice(0,10))+' .. '+(zc!==undefined?D.t[en].slice(0,16):D.t[N-1].slice(0,10))+'  '+(en-st+1)+' bars'
    if(zc===undefined){var ox=0;for(var i=0;i<N;i++){if(D.d[i]>='2025-01-01'){ox=xi(i);break}}
      ctx.strokeStyle='rgba(248,81,73,0.25)';ctx.lineWidth=1;ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(ox,pt);ctx.lineTo(ox,pt+ch);ctx.stroke();ctx.setLineDash([])
      ctx.fillStyle='rgba(248,81,73,0.4)';ctx.font='9px monospace';ctx.fillText('OOS →',ox+4,pt+12)}
    // trades overlay
    T.filter(x=>x.ei>=st&&x.ei<=en).forEach(function(x){
      var cl=x.p>0?'#3fb950':'#f85149',al=x.e>='2025-01-01'?.85:.3
      ctx.globalAlpha=al;ctx.strokeStyle=cl;ctx.lineWidth=x.p>0?1.2:.8
      ctx.beginPath();ctx.moveTo(xi(x.ei),yi(x.ep));ctx.lineTo(xi(x.xi),yi(x.xp));ctx.stroke()
      ctx.fillStyle=cl;ctx.beginPath();ctx.arc(xi(x.xi),yi(x.xp),2,0,2*Math.PI);ctx.fill();ctx.globalAlpha=1})
    if(sel&&zc!==undefined){ctx.strokeStyle='#f0c000';ctx.lineWidth=2.5;ctx.beginPath();ctx.moveTo(xi(sel.ei),yi(sel.ep));ctx.lineTo(xi(sel.xi),yi(sel.xp));ctx.stroke()
      ctx.fillStyle='#f0c000';ctx.beginPath();ctx.arc(xi(sel.xi),yi(sel.xp),5,0,2*Math.PI);ctx.fill()}
  })()
  // zoom chart
  if(!sel)return
  ;(function(){
    var ctx=cv2.getContext('2d'),W=cv2.width,H=cv2.height,pl=44,pr=10,pt=10,pb=30,cw=W-pl-pr,ch=H-pt-pb
    var px=D.p,N=px.length,mg=Math.max(10,Math.floor((sel.xi-sel.ei)*1.5))+5
    var st2=Math.max(0,sel.ei-mg),en2=Math.min(N-1,sel.xi+mg)
    var sl2=px.slice(st2,en2+1),mn2=Math.min(...sl2),mx2=Math.max(...sl2),rg2=mx2-mn2||1
    var xi=i=>pl+(i-st2)/(en2-st2)*cw,yi=p=>pt+(1-(p-mn2)/rg2)*ch
    ctx.clearRect(0,0,W,H)
    ctx.strokeStyle='#30363d';ctx.lineWidth=.5
    for(var i=0;i<5;i++){var yy=pt+i*ch/4;ctx.beginPath();ctx.moveTo(pl,yy);ctx.lineTo(W-pr,yy);ctx.stroke();ctx.fillStyle='#8b949e';ctx.font='9px monospace';ctx.fillText((mn2+rg2*(1-i/4)).toFixed(0),2,yy+3)}
    ctx.strokeStyle='#58a6ff';ctx.lineWidth=2;ctx.beginPath()
    for(var i=st2;i<=en2;i++){i==st2?ctx.moveTo(xi(i),yi(px[i])):ctx.lineTo(xi(i),yi(px[i]))}
    ctx.stroke()
    var sp2=en2-st2,ts2=Math.max(1,Math.floor(sp2/8))
    for(var i=Math.ceil(st2/ts2)*ts2;i<=en2;i+=ts2){ctx.fillStyle='#8b949e';ctx.font='9px monospace';ctx.fillText(D.t[i].slice(5,16),xi(i),pt+ch+12)}
    ctx.strokeStyle='rgba(88,166,255,0.5)';ctx.lineWidth=1;ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(xi(sel.ei),pt);ctx.lineTo(xi(sel.ei),pt+ch);ctx.stroke();ctx.setLineDash([])
    ctx.fillStyle='#58a6ff';ctx.font='10px sans-serif';ctx.fillText('ENTRY '+D.t[sel.ei].slice(5,16),xi(sel.ei)+4,pt+12)
    ctx.strokeStyle='rgba(248,81,73,0.5)';ctx.lineWidth=1;ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(xi(sel.xi),pt);ctx.lineTo(xi(sel.xi),pt+ch);ctx.stroke();ctx.setLineDash([])
    ctx.fillStyle='#f85149';ctx.font='10px sans-serif';ctx.fillText('EXIT '+D.t[sel.xi].slice(5,16),xi(sel.xi)+4,pt+12)
    var cl=sel.p>0?'#3fb950':'#f85149'
    ctx.strokeStyle=cl;ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(xi(sel.ei),yi(sel.ep));ctx.lineTo(xi(sel.xi),yi(sel.xp));ctx.stroke()
    ctx.fillStyle=cl;ctx.beginPath();ctx.arc(xi(sel.xi),yi(sel.xp),6,0,2*Math.PI);ctx.fill()})()
}
// Events
tb.addEventListener('click',function(e){var tr=e.target.closest('tr');if(!tr)return;var i=tr.getAttribute('di');if(i!==null)zoom(parseInt(i))})
c.addEventListener('dblclick',function(){sel=null;zi.style.display='none';cx=null;draw(Math.max(0,D.p.length-2000),1000);var rows=document.querySelectorAll('#tb tr');for(var i=0;i<rows.length;i++)rows[i].className=rows[i].className.replace(' sl','')})
c.addEventListener('click',function(e){
  var rect=c.getBoundingClientRect(),mx=e.clientX-rect.left
  var W=c.width,pl=44,pr=10,cw=W-pl-pr
  var N=D.p.length,st=0,en=N-1
  if(cx!==undefined&&cx!==null&&cr!==undefined){st=Math.max(0,cx-cr);en=Math.min(N-1,cx+cr)}
  var bi=Math.round(st+(mx/cw)*(en-st))
  if(bi<0||bi>=N)return
  var best=null,bd=Infinity
  for(var i=0;i<T.length;i++){var d=Math.abs(T[i].ei-bi);if(d<bd){bd=d;best=i}}
  if(best!==null&&bd<Math.max(20,(en-st)/20))zoom(best)
})
function zoom(i){
  sel=T[i];zi.style.display='block'
  document.querySelectorAll('tr.sl').forEach(function(r){r.className=r.className.replace(' sl','')})
  var rows=document.querySelectorAll('#tb tr');if(rows[i])rows[i].className+=' sl'
  draw(sel.ei,Math.max(50,(sel.xi-sel.ei)*3))
  zi.innerHTML='<div>'+cfg.pn+' '+cfg.dr+' h='+cfg.h+' am='+cfg.am+' | Entry: <b>'+sel.e+'</b> @ <b>'+sel.ep.toFixed(0)+
    '</b> | Exit: <b>'+sel.x+'</b> @ <b>'+sel.xp.toFixed(0)+'</b> | Bars: <b>'+(sel.xi-sel.ei)+'</b> ('+((sel.xi-sel.ei)*5)+'min) | PnL: <b class='+(sel.p>0?'up':'dn')+'>'+(sel.p>0?'+':'')+sel.p.toFixed(0)+'</b></div>'
}
loadT()
</script></body></html>"""
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        qs=parse_qs(urlparse(self.path).query)
        if self.path.startswith('/d'):
            t=qs.get('t',['VB'])[0];d=load_data(t);r=PHASE2['results'].get(t,{})
            if d:self._json({'tk':t,'p':[float(x) for x in d['close']],'d':d['dates'],'t':d['times'],'w':r.get('wfa_passed',0),'c':[{'pn':x['pattern_name'],'dr':x['direction'],'h':x['hold'],'am':x['atr_mult'],'p':x['pattern'],'os':{'c':x['oos']['calmar'],'r':x['oos']['ret'],'n':x['oos']['n']}} for x in r.get('top_configs',[])]})
            else:self._json({'error':'no data'},404)
        elif self.path.startswith('/t'):
            t=qs.get('t',['VB'])[0];p=qs.get('p',['v'])[0];d_=qs.get('d',['L'])[0];h=int(qs.get('h',[5])[0]);a=int(qs.get('a',[2])[0])
            # run_bt simplified — just signal detection, no chandelier (fast), return trades as [{ei,xi,e,x,ep,xp,p}]
            rows=ch.query(f"""SELECT time,open,high,low,close,volume,yur_buy,yur_sell,fiz_buy,fiz_sell,total_oi FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time WHERE p.symbol='{t}' AND p.time>='2024-01-01' AND p.time<='2026-05-01' ORDER BY p.time""").result_rows
            if not rows:self._json([],404);return
            N=len(rows);times=[str(r[0]) for r in rows]
            opn=np.array([float(r[1]) for r in rows]);high=np.array([float(r[2]) for r in rows]);low=np.array([float(r[3]) for r in rows])
            close=np.array([float(r[4]) for r in rows]);vol=np.array([float(r[5]) for r in rows])
            yb=np.array([float(r[6]) for r in rows]);ys=np.array([float(r[7]) for r in rows]);fb=np.array([float(r[8]) for r in rows]);fs=np.array([float(r[9]) for r in rows]);toi=np.where(np.array([float(r[10]) for r in rows])<=0,1,1)
            tr=np.zeros(N);tr[1:]=np.maximum(high[1:]-low[1:],np.maximum(abs(high[1:]-close[:-1]),abs(low[1:]-close[:-1])))
            atr=np.full(N,np.nan)
            for i in range(14,N):atr[i]=np.mean(tr[i-13:i+1])
            v_m=np.mean(vol)+1;yb_m=np.mean(yb)+1;ys_m=np.mean(ys)+1;toi_m=np.mean(toi)+1
            dv=np.diff(vol)/v_m;dyb=np.diff(yb)/yb_m;dys=np.diff(ys)/ys_m;dtoi=np.diff(toi)/toi_m;fiz_net=(fb-fs)/toi*100;dfn=np.diff(fiz_net)
            end_idx=N-max(h,2)-1;vm=np.full(N,np.nan);vc=np.cumsum(vol)
            for i in range(60,N):vm[i]=(vc[i]-vc[i-60])/60
            trades=[]
            for i in range(64,end_idx):
                if i>=len(dv):break
                ep=float(opn[i+1])
                if not PATTERNS[p](dv[i],dyb[i],dys[i],dfn[i],dtoi[i]):continue
                if np.isnan(vm[i]) or vol[i]<vm[i]*1.2:continue
                xi=min(i+1+h,N-1)
                if d_=='L':
                    sp=ep*(1-min(max(atr[i]/ep*a,0.005),0.05)) if not np.isnan(atr[i]) else ep*0.95
                    r_h=ep;exit_idx=xi;xp=float(close[xi])
                    for j in range(i+1,xi+1):
                        bh=float(high[j])
                        if bh>r_h:r_h=bh
                        if not np.isnan(atr[j]):sp=max(sp,r_h*(1-min(max(atr[j]/r_h*a,0.005),0.05)))
                        if float(low[j])<=sp:xp=sp;exit_idx=j;break
                    pnl=xp-ep
                else:
                    sp=ep*(1+min(max(atr[i]/ep*a,0.005),0.05)) if not np.isnan(atr[i]) else ep*1.05
                    r_l=ep;exit_idx=xi;xp=float(close[xi])
                    for j in range(i+1,xi+1):
                        bl=float(low[j])
                        if bl<r_l:r_l=bl
                        if not np.isnan(atr[j]):sp=min(sp,r_l*(1+min(max(atr[j]/r_l*a,0.005),0.05)))
                        if float(high[j])>=sp:xp=sp;exit_idx=j;break
                    pnl=ep-xp
                trades.append({'ei':i+1,'xi':exit_idx,'e':str(times[i+1])[:16],'x':str(times[exit_idx])[:16],'ep':round(ep,2),'xp':round(xp,2),'p':round(pnl,2)})
            self._json(trades)
        else:
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(HTML.encode())
    def _json(self,data,c=200):
        self.send_response(c);self.send_header('Content-Type','application/json');self.send_header('Access-Control-Allow-Origin','*');self.end_headers();self.wfile.write(json.dumps(data,default=str).encode())
if __name__=='__main__':
    s=socketserver.ThreadingTCPServer(('0.0.0.0',PORT),H);s.allow_reuse_address=True;s.daemon_threads=True
    print(f'http://localhost:{PORT}');print('Кликни на график или строку = zoom')
    try:s.serve_forever()
    except KeyboardInterrupt:s.shutdown()
