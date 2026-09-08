"""Collection controls visible on every view; explicit daily output in Data."""

STYLE = """
.collection-bar{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:18px 24px;margin:0 0 20px;background:#e8f1f2;border:1px solid #bdd2d5;border-radius:12px}
.collection-bar strong{color:#154955}.collection-copy{display:block;font-size:13px;color:#48646c;margin-top:6px}.collection-actions{display:flex;gap:10px;flex-shrink:0}
.collection-bar[data-state="paused"],.collection-bar[data-state="pausing"]{background:#fff6df;border-color:#dbc791}.collection-bar button:disabled{opacity:.45;cursor:not-allowed}
.daily-card{border-top:3px solid #b19350}.daily-kicker{font-size:11px;letter-spacing:2px;color:#927439;font-weight:700;margin:0 0 6px}.daily-controls{display:grid;grid-template-columns:180px 1fr 1fr auto;gap:16px;align-items:end}.daily-controls label{font-size:13px;color:#48646c}.daily-controls select,.daily-controls input{margin-top:6px}.daily-note{font-size:13px;color:#667781;line-height:1.7}.daily-result{margin-top:14px;padding:16px;background:#f2f7f7;border-radius:8px}.daily-result.stale{background:#fff4dc}.daily-downloads{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px}.daily-result summary{cursor:pointer}.daily-result img{max-width:100%;height:auto;margin-top:14px}.daily-links{display:flex;gap:16px;flex-wrap:wrap}
@media(max-width:760px){.collection-bar{align-items:flex-start;flex-direction:column}.collection-actions{width:100%}.collection-actions button{flex:1}.daily-controls{grid-template-columns:1fr 1fr}.daily-controls button{grid-column:1/-1}.daily-controls label:first-child{grid-column:1/-1}}
"""

CONTROL_HTML = """
<section id="collectionBar" class="collection-bar" aria-label="采集控制">
 <div><strong id="collectionLabel">正在读取采集状态…</strong><span id="collectionDetail" class="collection-copy">控制所有已配置QQ群；不退出NapCat，不影响已存数据。</span><span id="collectionFeedback" class="bad" role="alert"></span></div>
 <div class="collection-actions"><button id="collectionStop" class="danger" disabled>停止采集</button><button id="collectionStart" class="primary" disabled>开始采集</button></div>
</section>
<dialog id="collectionDialog" aria-labelledby="collectionDialogTitle"><div class="dialog-head"><h3 id="collectionDialogTitle">停止所有群的采集？</h3></div><div class="dialog-body"><p>已接收的队列会处理完成，之后不再采集新消息。</p><p class="delete-warning">停止期间的消息不会在重新开始后补采。程序重启后仍保持停止，需手动点击“开始采集”。</p><p id="collectionError" role="alert" class="bad"></p></div><div class="dialog-actions"><button id="collectionCancel" class="secondary">取消</button><button id="collectionConfirm" class="danger">确认停止采集</button></div></dialog>
"""

DAILY_HTML = """
<section id="dailyCard" class="card daily-card" aria-label="每日出图">
 <p class="daily-kicker">DAILY REPORT / 每日出图</p><div class="section-title"><h2>确认数据，再生成当日图表</h2></div>
 <p class="daily-note">使用上方选中的QQ群。按报价生效日期统计，图片与配套Excel来自同一份快照；生成不会停止采集，也不会结束年度。</p>
 <div class="daily-controls"><label>统计日期<input id="dailyDay" type="date"></label><label>始发地<select id="dailyOrigin"><option value="">全部始发地</option></select></label><label>目的城市<select id="dailyDestination"><option value="">全部目的城市</option></select></label><button id="dailyGenerate" class="primary" disabled>生成当日图表</button></div>
 <p id="dailyMessage" class="daily-note" role="status" aria-live="polite"></p><div id="dailyResults"></div>
</section>
"""

SCRIPT = r"""
<script>
const DC={collection:null,controlBusy:false,reportBusy:false,request:0};
function dailyParams(){return {group_id:DM.group,day:dq('#dailyDay').value,origin:dq('#dailyOrigin').value,destination:dq('#dailyDestination').value}}
async function collectionRefresh(){try{const x=await dataApi('collection-status');DC.collection=x;dq('#collectionBar').dataset.state=x.state;dq('#collectionLabel').textContent=({collecting:'采集开关已开启 · 全部群',pausing:'正在停止 · 等待已接收消息处理完成',paused:'已停止采集 · 全部群',unavailable:'请先配置QQ群'})[x.state]||x.state;dq('#collectionDetail').textContent=x.state==='pausing'?`剩余 ${x.pending} 条已接收消息；新消息已停止接收。`:x.enabled?'实际收消息还需NapCat在线；停止后可继续查询、删除和出图。':'不会采集新消息；点击“开始采集”恢复，不补采暂停期间消息。';dq('#collectionStop').disabled=DC.controlBusy||!x.enabled;dq('#collectionStart').disabled=DC.controlBusy||x.enabled||x.state==='unavailable'}catch(e){dq('#collectionLabel').textContent='采集状态读取失败：'+e.message;dq('#collectionStop').disabled=true;dq('#collectionStart').disabled=true}}
async function collectionChange(enabled){if(!DC.collection||DC.controlBusy)return;DC.controlBusy=true;dq('#collectionError').textContent='';dq('#collectionFeedback').textContent='';dq('#collectionConfirm').disabled=true;dq('#collectionCancel').disabled=true;try{await dataApi('collection-change',{enabled,version:DC.collection.version,confirm:true});dq('#collectionDialog').close()}catch(e){if(enabled)dq('#collectionFeedback').textContent=e.message;else dq('#collectionError').textContent=e.message}finally{DC.controlBusy=false;dq('#collectionConfirm').disabled=false;dq('#collectionCancel').disabled=false;await collectionRefresh()}}
async function dailyInit(){try{const x=await dataApi('options');if(!dq('#dailyDay').value)dq('#dailyDay').value=x.today;dq('#dailyDay').max=x.today;for(const [id,key,title] of [['dailyOrigin','origins','全部始发地'],['dailyDestination','destinations','全部目的城市']]){const select=dq('#'+id),old=select.value;select.innerHTML=`<option value="">${title}</option>`+x[key].map(v=>`<option value="${dh(v)}">${dh(v)}</option>`).join('');if(x[key].includes(old))select.value=old}await dailyRefresh()}catch(e){dq('#dailyMessage').textContent=e.message}}
function dailyUrl(group,path){return '/api/data/file?'+new URLSearchParams({group_id:group,path})}
async function dailyRefresh(){if(!DM.group||!dq('#dailyDay').value)return;const seq=++DC.request,params=dailyParams();try{const x=await dataApi('daily-status?'+new URLSearchParams(params));if(seq!==DC.request||params.group_id!==DM.group)return;const running=x.job.state==='running';dq('#dailyGenerate').disabled=running||DC.reportBusy;let message=running?'正在后台生成，请稍候…':`当前筛选范围有 ${x.current_quote_count} 条参与统计的报价。`;if(x.job.error)message+=' '+x.job.error;if(x.errors.length)message+=' '+x.errors.join(' ');dq('#dailyMessage').textContent=message;dq('#dailyResults').innerHTML=x.reports.slice(0,3).map((r,i)=>{const obsolete=r.stale||r.missing;return `<article class="daily-result ${obsolete?'stale':''}"><strong>${obsolete?(r.missing?'输出文件不完整，请重新生成':'数据或规则已变化，需要重新生成'):'与当前数据一致'}${i?' · 历史版本':''}</strong><p class="daily-note">${dh(r.selection.day)} · ${dh(r.group_name)} · ${r.quote_count} 条报价 · 生成于 ${dh(r.generated_at.replace('T',' '))}</p><div class="daily-downloads">${r.files.map(name=>`<a href="${dh(dailyUrl(params.group_id,r.base_path+'/'+name)+'&download=1')}">${dh(name)}</a>`).join('')}</div>${i===0?`<details><summary>预览本次图片</summary>${r.files.filter(n=>n.endsWith('.png')).map(name=>`<img loading="lazy" alt="${dh(r.selection.day+' '+name)}" src="${dh(dailyUrl(params.group_id,r.base_path+'/'+name))}">`).join('')}</details>`:''}</article>`}).join('')||'<p class="daily-note">这个日期和线路尚未生成图表。</p>'}catch(e){if(seq===DC.request){dq('#dailyMessage').textContent=e.message;dq('#dailyGenerate').disabled=DC.reportBusy}}}
async function dailyGenerate(){if(DC.reportBusy)return;DC.reportBusy=true;dq('#dailyGenerate').disabled=true;let accepted=false;try{await dataApi('daily-generate',dailyParams());accepted=true}catch(e){dq('#dailyMessage').textContent=e.message}finally{DC.reportBusy=false;if(accepted)await dailyRefresh();else dq('#dailyGenerate').disabled=false}}
dq('#collectionStop').onclick=()=>{dq('#collectionError').textContent='';dq('#collectionDialog').showModal()};dq('#collectionCancel').onclick=()=>dq('#collectionDialog').close();dq('#collectionConfirm').onclick=()=>collectionChange(false);dq('#collectionStart').onclick=()=>collectionChange(true);dq('#collectionDialog').addEventListener('cancel',e=>{if(DC.controlBusy)e.preventDefault()});
dq('#dailyGenerate').onclick=dailyGenerate;for(const id of ['dailyDay','dailyOrigin','dailyDestination'])dq('#'+id).addEventListener('change',dailyRefresh);dq('#dataGroup').addEventListener('change',dailyRefresh);dq('[data-view="data"]').addEventListener('click',dailyInit);
collectionRefresh();dailyInit();setInterval(collectionRefresh,3000);setInterval(()=>{if(!dq('#dataView').classList.contains('hidden'))dailyRefresh()},5000);
</script>
"""


def extend_daily_dashboard(html: str) -> str:
    html = html.replace('</style>', STYLE + '</style>', 1)
    html = html.replace('<main id="statusView"', CONTROL_HTML + '<main id="statusView"', 1)
    html = html.replace('<nav class="data-nav"', DAILY_HTML + '<nav class="data-nav"', 1)
    return html.replace('</body>', SCRIPT + '</body>', 1)
