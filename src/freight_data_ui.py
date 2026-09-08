"""Self-contained dashboard extension; bundled into the portable executable."""

DATA_STYLE = """
.tabs{flex-wrap:wrap}button:disabled{opacity:.45;cursor:not-allowed}
#dataView{--ink:#18374a;--accent:#176975}#dataView h2{color:var(--ink)}
.data-head{display:flex;justify-content:space-between;gap:24px;align-items:center;border-top:4px solid var(--accent)}
.data-head h2{margin:0 0 7px;font-size:25px}.data-head p{margin:0;color:#64748b;font-size:14px}
.data-group{width:min(330px,100%);flex-shrink:0}.data-group label{font-size:13px;color:#64748b}
.data-group select{margin-top:5px}.data-nav{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}
.data-nav button{border:1px solid #dbe4eb;background:white;border-radius:8px;padding:10px 18px;font:inherit;cursor:pointer}
.data-nav button.selected{background:var(--ink);color:white;border-color:var(--ink)}
.data-filters{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr));gap:14px}
.data-filters label{font-size:13px;color:#506477;display:grid;gap:6px}.data-tools{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:16px}
#dataView .primary{background:var(--accent)}.data-summary{display:flex;gap:24px;align-items:baseline;flex-wrap:wrap}
.data-summary strong{font-size:29px;font-variant-numeric:tabular-nums;color:var(--ink)}
.data-summary span{color:#617386;font-size:13px}.data-pager{display:flex;gap:12px;align-items:center;justify-content:flex-end;margin-top:16px;flex-wrap:wrap}
.data-table{font-size:14px}.data-table th{font-size:12px;white-space:nowrap;color:#64748b;background:#f5f8fa}
.data-table td{font-variant-numeric:tabular-nums}.data-table tr:hover td{background:#f6fafb}.data-table input[type=checkbox]{width:17px;height:17px;accent-color:#176975}
.data-table .number{text-align:right;white-space:nowrap}.data-table details{min-width:140px;max-width:360px}.data-table summary{cursor:pointer;color:#176975}
.data-table details p{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0;max-height:220px;overflow:auto}
.data-empty{text-align:center;color:#718096;padding:48px 16px!important}.data-message{padding:12px 0;color:#176975;overflow-wrap:anywhere}
.data-message.bad{color:#b91c1c}.data-note{font-size:13px;color:#64748b;line-height:1.7}
.report-layout{display:grid;grid-template-columns:320px minmax(0,1fr);gap:18px}.report-list{max-height:660px;overflow:auto;padding-right:8px}
.report-file{display:block;width:100%;text-align:left;border:1px solid #e0e8ed;border-radius:8px;background:#fff;padding:12px;margin:8px 0;cursor:pointer;color:#1f3b50;overflow-wrap:anywhere;font:inherit;font-size:13px}
.report-file.active{border-color:#176975;background:#eef8f7}.report-file small{display:block;color:#718096;margin-top:5px}
.report-preview{min-width:0;border:1px solid #e0e8ed;border-radius:10px;padding:16px}.report-preview img{max-width:100%;height:auto;display:block;margin:auto}
.report-title{font-size:16px;overflow-wrap:anywhere;margin:0 0 12px}.report-toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.report-toolbar select{max-width:270px}.report-toolbar a{color:#176975}.report-table{max-height:610px;overflow:auto}
.report-table th,.report-table td{white-space:nowrap}.delete-warning{padding:12px;background:#fff6ee;border-left:3px solid #df8a4d;line-height:1.8}
@media(max-width:1000px){.data-filters{grid-template-columns:repeat(2,minmax(0,1fr))}.report-layout{grid-template-columns:1fr}.report-list{max-height:220px}}
@media(max-width:650px){.data-head{display:block}.data-group{margin-top:18px;width:100%}.data-filters{grid-template-columns:1fr 1fr}.data-pager{justify-content:center}.data-summary{gap:12px}}
"""

DATA_HTML = """
<main id="dataView" class="hidden">
 <section class="card data-head"><div><h2>数据与报表</h2><p>历史报价、日均统计与输出文件，在这里集中查看。</p></div>
 <div class="data-group"><label for="dataGroup">当前QQ群</label><select id="dataGroup"><option value="">正在读取群列表…</option></select></div></section>
 <nav class="data-nav" aria-label="数据类型"><button data-panel="records" class="selected">报价明细</button><button data-panel="statistics">日均统计</button><button data-panel="files">Excel / CSV / 图表</button></nav>
 <div id="dataMessage" class="data-message" role="status" aria-live="polite"></div>
 <section id="dataFilterCard" class="card"><form id="dataFilterForm">
 <div class="data-filters">
 <label>数据类别<select id="dataKind"><option value="all">全部报价（含历史与待生效）</option><option value="active">正式识别（含历史）</option><option value="pending">待生效报价</option><option value="rejected">不合格消息</option></select></label>
 <label>开始日期<input id="dataStart" type="date"></label><label>结束日期<input id="dataEnd" type="date"></label>
 <label>关键词（原文 / 发布人 / 原因）<input id="dataKeyword" maxlength="200" placeholder="输入关键词"></label>
 <label>始发地<input id="dataOrigin" list="dataOrigins" placeholder="全部始发地"><datalist id="dataOrigins"></datalist></label>
 <label>目的城市<input id="dataDestination" list="dataDestinations" placeholder="全部目的城市"><datalist id="dataDestinations"></datalist></label>
 <label>货物小类<input id="dataCargo" list="dataCargoOptions" placeholder="全部货物"><datalist id="dataCargoOptions"></datalist></label>
 <label>每页条数<select id="dataPageSize"><option>25</option><option>50</option><option>100</option></select></label>
 <label>平均报价下限<input id="dataMinPrice" type="number" step="any" placeholder="不限"></label><label>平均报价上限<input id="dataMaxPrice" type="number" step="any" placeholder="不限"></label>
 <label id="dataLevelLabel" class="hidden">日均统计层级<select id="dataLevel"><option value="category">货物大类</option><option value="subcategory">货物小类</option></select></label>
 </div><div class="data-tools"><button class="primary" id="dataSearch" type="submit">查询数据</button><button class="secondary" id="dataReset" type="button">重置条件</button><span id="dataFilterNote" class="data-note">报价按生效日期筛选；不合格消息按消息日期筛选。</span></div>
 </form></section>
 <section id="dataResultsCard" class="card"><div class="section-title"><div class="data-summary"><strong id="dataTotal">—</strong><span id="dataTotalLabel">条符合条件的报价</span><span id="dataStatsNote"></span></div><button class="danger" id="dataDeleteSelected" disabled>删除已选（0）</button></div>
 <div id="dataResults" class="table-wrap"><p class="data-empty">选择群后查询数据</p></div>
 <div class="data-pager"><button id="dataPrevious" class="secondary" disabled>上一页</button><span id="dataPageInfo">第 1 页</span><button id="dataNext" class="secondary" disabled>下一页</button></div>
 </section>
 <section id="dataFilesCard" class="card hidden"><div class="section-title"><h2>输出报表</h2><button id="dataRefreshFiles" class="secondary">刷新报表列表</button></div>
 <p class="data-note">Excel、CSV与图表继续保存在原群目录。选择文件即可预览；Excel支持切换工作表。周 / 月 / 春节年度统计及历史归档也在这里。</p>
 <div class="report-layout"><aside><input id="dataFileSearch" aria-label="查找报表" placeholder="查找文件、线路或年度"><div id="dataFileList" class="report-list"></div></aside>
 <div class="report-preview"><h3 id="dataReportTitle" class="report-title">选择左侧报表</h3><div id="dataReportToolbar" class="report-toolbar"></div><div id="dataReportBody" class="report-table"><p class="data-empty">查看表格内容，或预览走势图</p></div><div id="dataReportPager" class="data-pager"></div></div></div></section>
</main>
<dialog id="dataDeleteDialog" aria-labelledby="dataDeleteTitle"><div class="dialog-head"><h3 id="dataDeleteTitle">确认删除报价</h3><button id="dataCancelDeleteTop" class="secondary">关闭</button></div><div class="dialog-body"><p id="dataDeleteDescription"></p><div class="delete-warning">确认后，所选记录将退出统计，系统会重新生成该群的报表。删除内容保留在本机删除记录中。</div><p id="dataDeleteError" class="bad" role="alert"></p></div><div class="dialog-actions"><button id="dataCancelDelete" class="secondary">取消</button><button id="dataConfirmDelete" class="danger">确认删除</button></div></dialog>
"""

DATA_SCRIPT = r"""
<script>
const DM={panel:'records',page:1,pages:1,items:[],selected:new Set(),group:'',files:[],file:'',filePage:1,sheet:'',request:0,preview:null,ready:false,busy:false,fileRequest:0};
const dq=s=>document.querySelector(s);
const dh=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dn=v=>v==null||v===''?'—':Number.isFinite(Number(v))?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:3,maximumFractionDigits:3}):dh(v);
function dataMessage(text,bad=false){dq('#dataMessage').textContent=text;dq('#dataMessage').classList.toggle('bad',bad)}
async function dataApi(path,body){const r=await fetch('/api/data/'+path,{cache:'no-store',...(body?{method:'POST',headers:{'Content-Type':'application/json','X-Freight-CSRF':CSRF_TOKEN},body:JSON.stringify(body)}:{})});CSRF_TOKEN=r.headers.get('X-Freight-CSRF-Token')||CSRF_TOKEN;const x=await r.json();if(!r.ok)throw Error(x.error||'请求失败');return x}
function dataParams(){return new URLSearchParams({group_id:DM.group,kind:dq('#dataKind').value,start:dq('#dataStart').value,end:dq('#dataEnd').value,q:dq('#dataKeyword').value.trim(),origin:dq('#dataOrigin').value.trim(),destination:dq('#dataDestination').value.trim(),cargo:dq('#dataCargo').value.trim(),min_price:dq('#dataMinPrice').value,max_price:dq('#dataMaxPrice').value,page_size:dq('#dataPageSize').value,page:String(DM.page),level:dq('#dataLevel').value})}
function dataClearSelection(){DM.selected.clear();dq('#dataDeleteSelected').disabled=true;dq('#dataDeleteSelected').textContent='删除已选（0）'}
function dataSelectionChanged(){dq('#dataDeleteSelected').disabled=!DM.selected.size||DM.busy;dq('#dataDeleteSelected').textContent=`删除已选（${DM.selected.size}）`;const all=dq('#dataSelectAll');if(all){all.checked=DM.items.length>0&&DM.selected.size===DM.items.length;all.indeterminate=DM.selected.size>0&&DM.selected.size<DM.items.length}}
function dataReportState(s){if(s?.pending)return s.error?'数据已更新，报表同步遇到问题：'+s.error+'。关闭占用中的Excel后会重试。':'数据已更新，当前Excel / CSV正在重新生成；每日日报是固定快照，变更后请重新出图。';return (s?.updated_at?'最近表格更新：'+s.updated_at.replace('T',' '):'')+(s?.manual?' 当前为手动年度；charts / 小类统计中的旧模式图片不随当前年度更新。':'')}
async function dataInit(){try{const x=await dataApi('options');const previous=DM.group;dq('#dataGroup').innerHTML=x.groups.map(g=>`<option value="${dh(g.id)}">${dh(g.name)} · ${dh(g.id)}</option>`).join('')||'<option value="">请先添加QQ群</option>';DM.group=x.groups.some(g=>g.id===previous)?previous:(x.groups[0]?.id||'');dq('#dataGroup').value=DM.group;for(const [id,key] of [['dataOrigins','origins'],['dataDestinations','destinations'],['dataCargoOptions','cargo']])dq('#'+id).innerHTML=x[key].map(v=>`<option value="${dh(v)}"></option>`).join('');DM.ready=true;if(DM.group)await dataLoad();else dataMessage('请在“群与线路管理”中先添加QQ群。')}catch(e){dataMessage(e.message,true)}}
async function dataLoad(){if(!DM.group)return;const seq=++DM.request;dataClearSelection();DM.busy=true;dq('#dataSearch').disabled=true;dq('#dataPrevious').disabled=true;dq('#dataNext').disabled=true;dataMessage('正在读取…');try{if(DM.panel==='files'){await dataLoadFiles();return}const x=await dataApi((DM.panel==='statistics'?'statistics?':'records?')+dataParams());if(seq!==DM.request)return;DM.items=x.items;DM.page=x.page;DM.pages=x.pages;dq('#dataTotal').textContent=x.total.toLocaleString();dq('#dataTotalLabel').textContent=DM.panel==='statistics'?'条日均统计':'条符合条件的记录';dq('#dataStatsNote').textContent=DM.panel==='statistics'?`参与统计 ${x.quote_count} 条报价`:'';dataRender(x);dq('#dataPageInfo').textContent=`第 ${DM.page} / ${DM.pages} 页`;dq('#dataPrevious').disabled=DM.page<=1;dq('#dataNext').disabled=DM.page>=DM.pages;dataMessage(x.note?x.note+` 当前价格阈值：${x.price_threshold}。`:dataReportState(x.report_state))}catch(e){if(seq===DM.request){dq('#dataResults').innerHTML='<p class="data-empty">未能加载数据，请调整条件或重试。</p>';DM.items=[];dq('#dataTotal').textContent='—';dataMessage(e.message,true)}}finally{if(seq===DM.request){DM.busy=false;dq('#dataSearch').disabled=false;dataSelectionChanged()}}}
function dataRender(x){let headers,rows;if(DM.panel==='statistics'){headers=['日期','始发地','目的城市','货物','平均运价','最低运价','最高运价','报价数量'];rows=x.items.map(r=>'<tr>'+headers.map((k,i)=>`<td class="${i>=4?'number':''}">${i>=4&&i<=6?dn(r[k]):dh(r[k])}</td>`).join('')+'</tr>')}else{headers=['<input id="dataSelectAll" type="checkbox" aria-label="选择本页全部记录">','生效 / 消息日期','分类','线路','货物','报价','平均报价','发布人','原文 / 原因','操作'];rows=x.items.map((r,index)=>`<tr><td><input class="data-row-check" type="checkbox" data-index="${index}" aria-label="选择第${index+1}条记录"></td><td>${dh(r.day)}</td><td><span class="badge ${r.type==='rejected'?'rejected':r.kind==='待生效'?'pending':'active'}">${dh(r.kind)}</span>${r.eligible===false?'<small class="bad"> 不参与统计</small>':''}</td><td>${r.origin?dh(r.origin)+' → '+dh(r.destination):'—'}</td><td>${dh(r.cargo||'—')}<small class="muted"> ${dh(r.category||'')}</small></td><td class="number">${dh(r.price||'—')}</td><td class="number">${dn(r.average)}</td><td>${dh(r.sender||'—')}</td><td><details><summary>查看详情</summary><p>${dh(r.detail||'—')}</p>${r.reason?`<p class="bad">${dh(r.reason)}</p>`:''}<p class="muted">记录时间：${dh(r.created_at)}</p></details></td><td><button class="danger data-row-delete" data-index="${index}">删除</button></td></tr>`)}dq('#dataResults').innerHTML=`<table class="data-table"><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')||`<tr><td colspan="${headers.length}" class="data-empty">没有符合条件的数据</td></tr>`}</tbody></table>`;document.querySelectorAll('.data-row-check').forEach(el=>el.onchange=()=>{const id=DM.items[Number(el.dataset.index)].id;el.checked?DM.selected.add(id):DM.selected.delete(id);dataSelectionChanged()});const all=dq('#dataSelectAll');if(all)all.onchange=()=>{document.querySelectorAll('.data-row-check').forEach(el=>{el.checked=all.checked;const id=DM.items[Number(el.dataset.index)].id;all.checked?DM.selected.add(id):DM.selected.delete(id)});dataSelectionChanged()};document.querySelectorAll('.data-row-delete').forEach(el=>el.onclick=()=>dataPreviewDelete([DM.items[Number(el.dataset.index)].id]))}
async function dataPreviewDelete(ids){if(DM.busy||!ids.length)return;try{DM.preview=await dataApi('delete-preview',{group_id:DM.group,type:dq('#dataKind').value==='rejected'?'rejected':'freight',ids});dq('#dataDeleteTitle').textContent=DM.preview.type==='rejected'?'确认删除不合格消息':'确认删除报价';dq('#dataDeleteDescription').textContent=`将从“${DM.preview.group_name}”删除 ${DM.preview.count} 条已选记录。`;dq('#dataDeleteError').textContent='';dq('#dataConfirmDelete').disabled=false;dq('#dataDeleteDialog').showModal()}catch(e){dataMessage(e.message,true)}}
function dataCloseDelete(){dq('#dataDeleteDialog').close();DM.preview=null}
async function dataConfirmDelete(){if(!DM.preview)return;dq('#dataConfirmDelete').disabled=true;dq('#dataCancelDelete').disabled=true;dq('#dataCancelDeleteTop').disabled=true;try{const x=await dataApi('delete',{token:DM.preview.token,confirm:true});dataCloseDelete();await dataLoad();dataMessage(x.message)}catch(e){dq('#dataDeleteError').textContent=e.message}finally{dq('#dataConfirmDelete').disabled=false;dq('#dataCancelDelete').disabled=false;dq('#dataCancelDeleteTop').disabled=false}}
function dataPanel(panel){DM.panel=panel;DM.page=1;DM.request++;dataClearSelection();document.querySelectorAll('[data-panel]').forEach(b=>b.classList.toggle('selected',b.dataset.panel===panel));dq('#dataFilterCard').classList.toggle('hidden',panel==='files');dq('#dataResultsCard').classList.toggle('hidden',panel==='files');dq('#dataFilesCard').classList.toggle('hidden',panel!=='files');dq('#dataDeleteSelected').classList.toggle('hidden',panel!=='records');dq('#dataLevelLabel').classList.toggle('hidden',panel!=='statistics');if(DM.ready)dataLoad()}
async function dataLoadFiles(){const group=DM.group;const x=await dataApi('files?'+new URLSearchParams({group_id:group}));if(group!==DM.group||DM.panel!=='files')return;DM.files=x.items;dataRenderFiles();dataMessage(dataReportState(x.report_state)+(x.truncated?' 仅展示前3000个文件。':''));if(DM.file&&DM.files.some(f=>f.path===DM.file))await dataPreviewFile(DM.file,DM.filePage);else{DM.file='';dq('#dataReportTitle').textContent='选择左侧报表';dq('#dataReportToolbar').innerHTML='';dq('#dataReportBody').innerHTML='<p class="data-empty">选择文件查看表格或走势图</p>';dq('#dataReportPager').innerHTML=''}}
function dataRenderFiles(){const q=dq('#dataFileSearch').value.toLowerCase();dq('#dataFileList').innerHTML=DM.files.map((f,i)=>({f,i})).filter(({f})=>f.path.toLowerCase().includes(q)).map(({f,i})=>`<button class="report-file ${f.path===DM.file?'active':''}" data-file-index="${i}">${dh(f.path)}<small>${dh(f.type.toUpperCase())} · ${(f.size/1024).toFixed(1)} KB · ${dh(f.modified.replace('T',' '))}</small></button>`).join('')||'<p class="data-empty">暂无匹配报表</p>';document.querySelectorAll('[data-file-index]').forEach(b=>b.onclick=()=>{DM.sheet='';dataPreviewFile(DM.files[Number(b.dataset.fileIndex)].path,1)})}
async function dataPreviewFile(path,page=1){const seq=++DM.fileRequest;const group=DM.group;DM.file=path;DM.filePage=page;dataRenderFiles();dq('#dataReportTitle').textContent=path;dq('#dataReportBody').innerHTML='<p class="data-empty">正在加载报表…</p>';dq('#dataReportPager').innerHTML='';const params=new URLSearchParams({group_id:group,path,page:String(page),sheet:DM.sheet});const url='/api/data/file?'+new URLSearchParams({group_id:group,path});try{if(path.toLowerCase().endsWith('.png')){dq('#dataReportToolbar').innerHTML=`<a href="${dh(url+'&download=1')}">下载图片</a>`;dq('#dataReportBody').innerHTML=`<img src="${dh(url+'&v='+Date.now())}" alt="${dh(path)}">`;return}const x=await dataApi('file-preview?'+params);if(seq!==DM.fileRequest||group!==DM.group)return;DM.sheet=x.sheet;dq('#dataReportToolbar').innerHTML=`<a href="${dh(url+'&download=1')}">下载原文件</a>${x.sheets.length?`<label>工作表 <select id="dataSheet">${x.sheets.map(s=>`<option ${s===x.sheet?'selected':''} value="${dh(s)}">${dh(s)}</option>`).join('')}</select></label>`:''}<span class="data-note">${x.total} 行</span>`;if(dq('#dataSheet'))dq('#dataSheet').onchange=()=>{DM.sheet=dq('#dataSheet').value;dataPreviewFile(DM.file,1)};dq('#dataReportBody').innerHTML=`<table class="data-table"><thead><tr>${x.headers.map(h=>`<th>${dh(h)}</th>`).join('')}</tr></thead><tbody>${x.items.map(r=>'<tr>'+r.map(v=>`<td>${typeof v==='number'&&!Number.isInteger(v)?dn(v):dh(v)}</td>`).join('')+'</tr>').join('')||'<tr><td class="data-empty">暂无数据</td></tr>'}</tbody></table>`;dq('#dataReportPager').innerHTML=`<button class="secondary" id="dataFilePrevious" ${page<=1?'disabled':''}>上一页</button><span>${page} / ${x.pages} 页</span><button class="secondary" id="dataFileNext" ${page>=x.pages?'disabled':''}>下一页</button>`;dq('#dataFilePrevious').onclick=()=>dataPreviewFile(DM.file,page-1);dq('#dataFileNext').onclick=()=>dataPreviewFile(DM.file,page+1)}catch(e){if(seq===DM.fileRequest)dq('#dataReportBody').innerHTML=`<p class="bad">${dh(e.message)}</p>`}}
dq('[data-view="data"]').addEventListener('click',()=>dataInit());
dq('#dataGroup').onchange=()=>{DM.group=dq('#dataGroup').value;DM.page=1;DM.file='';DM.sheet='';DM.fileRequest++;dataCloseDelete();dataLoad()};
document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>dataPanel(b.dataset.panel));
dq('#dataFilterForm').onsubmit=e=>{e.preventDefault();DM.page=1;dataLoad()};
dq('#dataReset').onclick=()=>{dq('#dataFilterForm').reset();dataKindChanged();DM.page=1;dataLoad()};
function dataKindChanged(){const rejected=dq('#dataKind').value==='rejected';for(const id of ['dataOrigin','dataDestination','dataCargo','dataMinPrice','dataMaxPrice'])dq('#'+id).disabled=rejected;dataClearSelection();DM.items=[];DM.request++;DM.busy=false;dq('#dataSearch').disabled=false;dq('#dataResults').innerHTML='<p class="data-empty">类别已更改，请点击“查询数据”。</p>';dq('#dataPrevious').disabled=true;dq('#dataNext').disabled=true}
dq('#dataKind').onchange=dataKindChanged;
dq('#dataPrevious').onclick=()=>{if(DM.page>1){DM.page--;dataLoad()}};dq('#dataNext').onclick=()=>{if(DM.page<DM.pages){DM.page++;dataLoad()}};
dq('#dataDeleteSelected').onclick=()=>dataPreviewDelete([...DM.selected]);dq('#dataConfirmDelete').onclick=dataConfirmDelete;
dq('#dataCancelDelete').onclick=dataCloseDelete;dq('#dataCancelDeleteTop').onclick=dataCloseDelete;
dq('#dataDeleteDialog').addEventListener('cancel',e=>{e.preventDefault();if(!dq('#dataConfirmDelete').disabled)dataCloseDelete()});
dq('#dataRefreshFiles').onclick=()=>dataLoad();dq('#dataFileSearch').oninput=dataRenderFiles;
if(new URLSearchParams(location.search).get('view')==='data')dq('[data-view="data"]').click();
</script>
"""


def extend_dashboard(html: str) -> str:
    html = html.replace('</style>', DATA_STYLE + '</style>', 1)
    html = html.replace('<button class="tab" data-view="config">', '<button class="tab" data-view="data">数据与报表</button><button class="tab" data-view="config">', 1)
    html = html.replace('<main id="configView"', DATA_HTML + '<main id="configView"', 1)
    html = html.replace("const config=b.dataset.view==='config';", "const data=b.dataset.view==='data';$('#dataView').classList.toggle('hidden',!data);const config=b.dataset.view==='config';", 1)
    html = html.replace("$('#statusView').classList.toggle('hidden',config);", "$('#statusView').classList.toggle('hidden',config||data);", 1)
    html = html.replace("async function refreshAll(){await", "async function refreshAll(){if(!$('#dataView').classList.contains('hidden'))return;await", 1)
    return html.replace('</body>', DATA_SCRIPT + '</body>', 1)
