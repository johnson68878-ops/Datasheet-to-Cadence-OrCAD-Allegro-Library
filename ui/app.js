const $ = (id) => document.getElementById(id);
const buildButton = $('buildButton');
const outputButton = $('outputButton');
const allegroButton = $('allegroButton');
const resultBox = $('resultBox');
let apiReady = false;
let buildBusy = false;

function applyAllegroHealth(health={}){
  const found=Boolean(health.allegro_found);
  const version=health.allegro_version||'未配置';
  const appVersion=health.app_version||'未识别';
  $('startupAppVersion').textContent=`软件版本 v${appVersion}`;
  $('allegroVersionLabel').textContent=found?`ALLEGRO ${version}`:'ALLEGRO 未配置';
  $('missionAllegroVersion').textContent=found?version:'待选择';
  $('startupAllegroVersion').textContent=found?`Allegro ${version}`:'Allegro待选择';
  $('allegroStatus').classList.toggle('missing',!found);
  $('allegroSettingsButton').textContent=found?'更改Allegro':'选择Allegro';
  $('allegroSettingsButton').title=found?(health.allegro_path||'当前Allegro路径'):'选择Cadence安装目录中的allegro.exe';
}

window.setBuildProgress = ({percent=0,stage='处理中',detail='',status=''})=>{
  const value=Math.max(0,Math.min(100,Number(percent)||0));
  $('progressBar').style.width=`${value}%`;
  $('progressPercent').textContent=`${Math.round(value)}%`;
  $('progressStage').textContent=stage;
  $('progressDetail').textContent=detail;
  $('buildProgress').classList.remove('blocked','error');
  if(status)$('buildProgress').classList.add(status);
  if($('engineState'))$('engineState').textContent=stage;
  if($('engineDetail'))$('engineDetail').textContent=detail||'后台任务运行中，界面保持可响应。';
};

const samples = {
  NO_LEAD:{title:'QFN 32 · Exposed Pad',badge:'能力预览',type:'qfn',pins:32,metrics:[['Pitch','0.50 mm'],['端子','0.25 × 0.65'],['EP','3.40 × 3.40'],['Pin','32']]},
  GULLWING:{title:'LQFP 48 · Gullwing',badge:'能力预览',type:'qfp',pins:48,metrics:[['Pitch','0.50 mm'],['引脚','0.27 × 1.30'],['本体','7.00 × 7.00'],['Pin','48']]},
  ARRAY:{title:'WLCSP 110 Ball',badge:'能力预览',type:'array',rows:10,cols:11,metrics:[['Pitch','0.35 mm'],['Copper','Ø 0.20 mm'],['Mask','Ø 0.25 mm'],['Ball','110']]},
  PASSIVE:{title:'R / L / C · 0201',badge:'参考库预览',type:'passive',metrics:[['封装','0201'],['端子','0.25 × 0.30'],['间距','0.30 mm'],['焊盘','2']]},
  THROUGH_HOLE:{title:'THT Connector · 1×4',badge:'能力预览',type:'tht',metrics:[['Pitch','2.54 mm'],['钻孔','Ø 1.00 mm'],['焊盘','Ø 1.80 mm'],['Pin','4']]},
  CUSTOM:{title:'异形机械封装',badge:'人工标注',type:'custom',metrics:[['输入','PDF / DXF'],['槽孔','支持'],['机械层','支持'],['状态','需审核']]}
};
const capabilityHomeMarkup=$('previewStage').innerHTML;
const capabilityMetricsMarkup=$('metrics').innerHTML;

function svgFrame(content,showFrame=true){return `<svg class="actual-svg" viewBox="0 0 520 320" role="img" aria-label="封装二维预览">${showFrame?'<rect class="outline" x="44" y="28" width="432" height="250"/>':''}${content}<text x="48" y="300">TOP / COMPONENT VIEW</text></svg>`}
function arraySvg(m){let s='<rect class="body" x="150" y="55" width="220" height="200"/>';const rows=m.rows||8,cols=m.cols||8;for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){const x=170+c*(180/Math.max(cols-1,1)),y=72+r*(166/Math.max(rows-1,1));s+=`<circle class="pad ${r===0&&c===0?'pin-one':''}" cx="${x}" cy="${y}" r="4.4"/>`}s+='<text x="137" y="70">1</text><text x="230" y="270">4.05 mm</text><text x="390" y="160" transform="rotate(90 390 160)">3.95 mm</text>';return svgFrame(s)}
function qfnSvg(m){let s='<rect class="body" x="160" y="64" width="200" height="190"/><rect class="ep" x="220" y="120" width="80" height="78"/>';for(let i=0;i<8;i++){const p=80+i*21;s+=`<rect class="pad ${i===0?'pin-one':''}" x="145" y="${p}" width="24" height="9"/><rect class="pad" x="351" y="${p}" width="24" height="9"/><rect class="pad" x="${175+i*21}" y="49" width="9" height="24"/><rect class="pad" x="${175+i*21}" y="245" width="9" height="24"/>`}s+='<text x="131" y="82">1</text><text x="233" y="160">EP</text>';return svgFrame(s)}
function qfpSvg(){
  let s='<path class="body" d="M184 76L198 62H342V248H170V76Z"/><path class="silk" d="M155 92V48H200 M320 48H366V92 M366 218V265H320 M200 265H155V218"/><circle class="pin-marker" cx="192" cy="86" r="5"/><text class="pin-label" x="112" y="86">1</text>';
  for(let i=0;i<12;i++){
    const p=82+i*13.5,q=184+i*13.5;
    s+=`<rect class="pad ${i===0?'pin-one':''}" x="112" y="${p}" width="61" height="7" rx="1.5"><title>左侧引脚 ${i+1}</title></rect>`;
    s+=`<rect class="pad" x="339" y="${p}" width="61" height="7" rx="1.5"/>`;
    s+=`<rect class="pad" x="${q}" y="20" width="7" height="45" rx="1.5"/>`;
    s+=`<rect class="pad" x="${q}" y="245" width="7" height="45" rx="1.5"/>`;
  }
  s+='<path class="dimension" d="M112 302H400 M112 297V307 M400 297V307"/><text class="dim-label" x="232" y="315">LEAD SPAN</text><text x="207" y="158">LQFP · 48</text><text x="194" y="174">GULLWING LEADS</text>';
  return svgFrame(s)
}
function passiveSvg(){return svgFrame('<rect class="pad pin-one" x="145" y="120" width="82" height="80"/><rect class="body" x="207" y="109" width="106" height="102"/><rect class="pad" x="293" y="120" width="82" height="80"/><text x="240" y="232">0201</text>')}
function thtSvg(){let s='<rect class="body" x="105" y="105" width="310" height="110"/>';for(let i=0;i<4;i++){s+=`<circle class="pad ${i===0?'pin-one':''}" cx="${155+i*70}" cy="160" r="22"/><circle cx="${155+i*70}" cy="160" r="10" fill="#05080d"/>`}return svgFrame(s)}
function customSvg(){return svgFrame('<path class="body" d="M150 65H365V175H315V255H150Z"/><circle class="pad pin-one" cx="178" cy="94" r="9"/><rect class="ep" x="220" y="118" width="95" height="65"/><text x="220" y="205">需人工标注与尺寸证据</text>')}
function geometrySvg(g){
  const pads=g.pads||[],bw=g.body_width_mm||1,bl=g.body_length_mm||1;
  const spanX=Math.max(bw/2,...pads.map(p=>Math.abs(p.x_mm)+(p.length_mm||p.width_mm)/2));
  const spanY=Math.max(bl/2,...pads.map(p=>Math.abs(p.y_mm)+(p.length_mm||p.width_mm)/2));
  const scale=Math.min(180/Math.max(spanX,0.1),105/Math.max(spanY,0.1));
  const cx=260,cy=155;
  const gullwing=/QFP|SOIC|SOT/i.test(g.family||'');
  const bx=cx-bw*scale/2,by=cy-bl*scale/2,bws=bw*scale,bls=bl*scale,ch=Math.min(13,bws*.12,bls*.12);
  let s='';
  const hasManufacturerBody=g.assembly_outline_kind!=='ball_land_envelope_review';
  if(hasManufacturerBody){
    s+=gullwing?`<path class="body" d="M${bx} ${by+ch}L${bx+ch} ${by}H${bx+bws}V${by+bls}H${bx}Z"/><circle class="pin-marker" cx="${bx+ch}" cy="${by+ch}" r="4"/>`:`<rect class="body" x="${bx}" y="${by}" width="${bws}" height="${bls}"/>`;
  }
  pads.forEach(p=>{const x=cx+p.x_mm*scale,y=cy-p.y_mm*scale,pin=p.number==='1'||p.number==='A1'?' pin-one':'';if(p.shape==='CIRCLE'){s+=`<circle class="pad${pin}" cx="${x}" cy="${y}" r="${p.width_mm*scale/2}"><title>Pin ${p.number}</title></circle>`}else{const rotated=Math.abs(Number(p.rotation_deg)||0)%180===90,w=(rotated?p.length_mm:p.width_mm)*scale,h=(rotated?p.width_mm:p.length_mm)*scale;s+=`<rect class="pad${pin}" x="${x-w/2}" y="${y-h/2}" width="${w}" height="${h}" rx="1"><title>Pin ${p.number}</title></rect>`}});
  if(!hasManufacturerBody&&pads.length){
    const xs=pads.map(p=>Number(p.x_mm)),ys=pads.map(p=>Number(p.y_mm));
    const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
    const left=cx+minX*scale,right=cx+maxX*scale,top=cy-maxY*scale,bottom=cy-minY*scale,dimY=bottom+31,dimX=right+31;
    s+=`<path class="dimension" d="M${left} ${dimY}H${right}M${left} ${dimY-5}V${dimY+5}M${right} ${dimY-5}V${dimY+5}M${dimX} ${top}V${bottom}M${dimX-5} ${top}H${dimX+5}M${dimX-5} ${bottom}H${dimX+5}"/>`;
    s+=`<text class="dim-label" x="${(left+right)/2-18}" y="${dimY+14}">${(maxX-minX).toFixed(3).replace(/0+$/,'').replace(/\.$/,'')} mm</text><text class="dim-label" x="${dimX+7}" y="${(top+bottom)/2}">${(maxY-minY).toFixed(3).replace(/0+$/,'').replace(/\.$/,'')} mm</text>`;
  }
  s+=`<text x="48" y="300">${g.family} · ${pads.length} PADS · ${hasManufacturerBody?'DATASHEET BODY':'D/E未定义，仅显示铜盘阵列'}</text>`;
  return svgFrame(s,false)
}
function graphic(m){return m.geometry?geometrySvg(m.geometry):m.type==='array'?arraySvg(m):m.type==='qfn'?qfnSvg(m):m.type==='qfp'?qfpSvg():m.type==='passive'?passiveSvg():m.type==='tht'?thtSvg():customSvg()}
function renderModel(m){$('previewKicker').textContent=m.partNumber||'PARAMETRIC FOOTPRINT';$('previewTitle').textContent=m.title;$('previewBadge').textContent=m.badge||'二维预览';$('previewStage').innerHTML=graphic(m);$('metrics').innerHTML=(m.metrics||[]).slice(0,4).map(([k,v])=>`<div><small>${k}</small><b>${v}</b></div>`).join('');$('previewBack').hidden=false}
function renderCapabilityHome(){document.querySelectorAll('.cap-card').forEach(card=>card.classList.remove('active'));$('previewKicker').textContent='CAPABILITY MAP';$('previewTitle').textContent='可生成的PCB Footprint';$('previewBadge').textContent='等待输入';$('previewStage').innerHTML=capabilityHomeMarkup;$('metrics').innerHTML=capabilityMetricsMarkup;$('previewBack').hidden=true;bindCapabilityCards()}
function showResult(kind,title,message){resultBox.classList.remove('success','error');if(kind)resultBox.classList.add(kind);$('resultTitle').textContent=title;$('resultMessage').textContent=message}

const blockingDialog=$('blockingDialog');
const startupDialog=$('startupDialog');
const variantDialog=$('variantDialog');
const schematicComponentDialog=$('schematicComponentDialog');
const productGate=$('productGate');
const routerSelection=$('routerSelection');
const orcadPreview=$('orcadPreview');
let dialogHasOutput=false;
let startupEntryPending=true;
function enterFootprintPlatform(){
  productGate.hidden=true;
  document.body.classList.remove('product-open');
  buildButton.focus();
}
function showOrcadPreview(){
  routerSelection.hidden=true;
  orcadPreview.hidden=false;
  $('backToRouter').focus();
}
function showProductRouter(){
  productGate.hidden=false;
  document.body.classList.add('product-open');
  orcadPreview.hidden=true;
  routerSelection.hidden=false;
  $('enterFootprint').focus();
}
$('enterFootprint').addEventListener('click',enterFootprintPlatform);
$('enterOrcad').addEventListener('click',showOrcadPreview);
$('backToRouter').addEventListener('click',showProductRouter);
$('backToProductRouter').addEventListener('click',showProductRouter);
const orcadBuildButton=$('orcadBuildButton');
const orcadContextButton=$('orcadContextButton');
const orcadOpenButton=$('orcadOpenButton');
const orcadOutputButton=$('orcadOutputButton');
let orcadContextPath='';
let orcadContextName='';
let orcadContextComponents=[];
let orcadSelectedComponent=null;
let orcadPendingDatasheetPath='';
let variantDialogMode='footprint';
orcadContextButton.addEventListener('click',async()=>{
  if(!window.pywebview?.api||buildBusy)return;
  const selected=await window.pywebview.api.choose_datasheet();
  if(selected?.cancelled)return;
  if(!selected?.ok){$('orcadBuildStatus').textContent=`HOLD：${selected?.message||'无法选择原理图PDF。'}`;return;}
  orcadContextPath=selected.path;
  orcadContextName=selected.name;
  orcadSelectedComponent=null;
  orcadContextButton.disabled=true;
  orcadContextButton.textContent=`正在辨识原理图：${selected.name}`;
  $('orcadBuildStatus').textContent='正在盘点原理图中的RefDes、料号与实体Pin，请稍候。';
  try{
    const started=await window.pywebview.api.start_orcad_schematic_inspection(selected.path);
    if(!started?.ok)throw new Error(started?.message||'无法启动原理图元件盘点。');
    const inventory=await pollOrcadBuild(started.job_id);
    if(!inventory?.ok)throw new Error(inventory?.message||'原理图元件辨识失败。');
    orcadContextComponents=inventory.components||[];
    showSchematicComponentDialog(inventory);
  }catch(error){
    orcadContextPath='';orcadContextName='';orcadContextComponents=[];
    orcadContextButton.textContent='选择原理图 PDF 并生成 OLB';
    $('orcadBuildStatus').textContent=`HOLD：${String(error)}`;
  }finally{orcadContextButton.disabled=false;}
});

function renderSchematicComponents(filter=''){
  const needle=filter.trim().toUpperCase();
  const visible=orcadContextComponents.filter(item=>!needle||`${item.refdes} ${item.part_name}`.toUpperCase().includes(needle));
  const allCard=!needle&&orcadContextComponents.length>1?`<button class="component-card component-card-all" type="button" data-component-refdes="*"><span class="component-ref">ALL</span><span class="component-data"><strong>全部生成到同一个 OLB</strong><small>依原理图中的RefDes、Pin名称与A/B/C/D Part标记直接建库，不读取Datasheet</small></span><span class="component-count"><b>${orcadContextComponents.length}</b>PART</span></button>`:'';
  $('componentGrid').innerHTML=allCard+visible.map(item=>`<button class="component-card" type="button" data-component-refdes="${escapeHtml(item.refdes)}"><span class="component-ref">${escapeHtml(item.refdes)}</span><span class="component-data"><strong>${escapeHtml(item.part_name||'料号待确认')}</strong><small>SCHEMATIC PAGE ${(item.pages||[]).join(' / ')||'—'} · 实体Pin已盘点</small></span><span class="component-count"><b>${Number(item.pin_count)||0}</b>PIN</span></button>`).join('');
  $('componentEmpty').hidden=visible.length>0;
}
function showSchematicComponentDialog(result={}){
  $('componentDialogSummary').textContent=`${result.source_name||'当前原理图'}已识别${Number(result.component_count)||0}个候选。可选择单一零件，或全部生成到同一个OLB；不需要Datasheet。`;
  $('componentSearch').value='';
  renderSchematicComponents();
  schematicComponentDialog.hidden=false;document.body.classList.add('component-dialog-open');
  $('componentSearch').focus();
}
function closeSchematicComponentDialog(){schematicComponentDialog.hidden=true;document.body.classList.remove('component-dialog-open');orcadContextButton.focus()}
$('componentSearch').addEventListener('input',event=>renderSchematicComponents(event.target.value));
$('componentGrid').addEventListener('click',async event=>{
  const card=event.target.closest?.('[data-component-refdes]');
  if(!card)return;
  const target=card.dataset.componentRefdes;
  const selected=target==='*'?null:orcadContextComponents.find(item=>item.refdes===target);
  if(target!=='*'&&!selected)return;
  orcadSelectedComponent=selected;
  orcadContextButton.textContent=target==='*'?'原理图目标：全部零件':`原理图目标：${selected.refdes} · ${selected.part_name}`;
  closeSchematicComponentDialog();
  buildBusy=true;orcadBuildButton.disabled=true;orcadContextButton.disabled=true;orcadOpenButton.disabled=true;orcadOutputButton.disabled=true;
  $('orcadBuildStatus').textContent=target==='*'?`正在把${orcadContextComponents.length}个候选写入同一个OLB。`:`正在从原理图直接建立 ${selected.refdes} / ${selected.part_name}；不读取Datasheet。`;
  try{
    const started=await window.pywebview.api.start_orcad_schematic_build(orcadContextPath,target);
    if(!started?.ok)throw new Error(started?.message||'无法启动原理图直接建库。');
    const result=await pollOrcadBuild(started.job_id);
    await handleOrcadResult(result);
  }catch(error){
    const message=String(error);
    $('orcadBuildStatus').textContent=`HOLD：${message}`;
    showOrcadBlockingDialog(message,'SCHEMATIC_PDF_TO_OLB_BLOCKED');
  }finally{buildBusy=false;orcadBuildButton.disabled=false;orcadContextButton.disabled=false;}
});
$('componentCancel').addEventListener('click',()=>{
  orcadContextPath='';orcadContextName='';orcadContextComponents=[];orcadSelectedComponent=null;
  orcadContextButton.textContent='选择原理图 PDF 并生成 OLB';
  $('orcadBuildStatus').textContent='已取消原理图载入；可改用Datasheet模式。';
  closeSchematicComponentDialog();
});
async function pollOrcadBuild(jobId){
  while(true){
    const state=await window.pywebview.api.job_status(jobId);
    if(!state?.ok)throw new Error(state?.message||'无法读取OrCAD任务状态。');
    const progress=state.progress||{};
    $('orcadBuildStatus').textContent=`${progress.stage||'处理中'} · ${progress.detail||''} · ${Number(progress.percent)||0}%`;
    if(state.done)return state.result||{ok:false,message:'OrCAD任务没有返回结果。'};
    await new Promise(resolve=>setTimeout(resolve,350));
  }
}
orcadBuildButton.addEventListener('click',async()=>{
  if(!window.pywebview?.api||buildBusy)return;
  buildBusy=true;orcadBuildButton.disabled=true;orcadOpenButton.disabled=true;orcadOutputButton.disabled=true;
  try{
    const selected=await window.pywebview.api.choose_datasheet();
    if(selected?.cancelled){$('orcadBuildStatus').textContent='已取消选择，尚未建立OLB。';return;}
    if(!selected?.ok)throw new Error(selected?.message||'无法选择Datasheet。');
    $('orcadBuildStatus').textContent=`已选择 ${selected.name}，正在建立Pin证据。`;
    orcadPendingDatasheetPath=selected.path;
    const started=await window.pywebview.api.start_orcad_build(selected.path,'','','');
    if(!started?.ok)throw new Error(started?.message||'无法启动OrCAD建库任务。');
    const result=await pollOrcadBuild(started.job_id);
    if(result?.needs_orcad_variant_selection){
      variantDialogMode='orcad';showVariantDialog(result);return;
    }
    await handleOrcadResult(result);
  }catch(error){
    const message=String(error);
    $('orcadBuildStatus').textContent=`HOLD：${message}`;
    showOrcadBlockingDialog(message,'DATASHEET_TO_OLB_BLOCKED');
  }finally{buildBusy=false;orcadBuildButton.disabled=false;}
});
async function handleOrcadResult(result){
  if(!result?.ok)throw new Error(result?.message||'OrCAD建库失败。');
  const counts=result.section_counts||{};
  const partSummary=Object.entries(counts).map(([part,count])=>`${part}${count}`).join('/');
  const contextSummary=result.context_matched_pins?` · 上下文匹配${result.context_matched_pins} Pin`:'';
  const architectureSummary=(result.architecture_evidence_pages||[]).length?` · 架构证据P${result.architecture_evidence_pages.join('/')}`:'';
  $('orcadBuildStatus').textContent=`Capture API回读通过：${result.part_name} · ${result.package_variant||''} · ${result.pin_count} Pin · ${partSummary}${contextSummary}${architectureSummary}；正在打开OrCAD Capture。`;
  orcadOpenButton.disabled=false;orcadOutputButton.disabled=false;
  const opened=await window.pywebview.api.open_in_orcad();
  $('orcadBuildStatus').textContent=opened?.ok
    ?`完成：${result.part_name} · ${result.pin_count} Pin；已自动用OrCAD Capture打开OLB。`
    :`OLB及API回读已完成，但Capture界面开启失败：${opened?.message||'请按下方按钮重试。'}`;
}
function showOrcadBlockingDialog(message,code='ORCAD_OLB_BLOCKED'){
  showBlockingDialog({
    dialog_title:'OrCAD OLB 未生成',
    message:`OrCAD零件库未生成：${message}`,
    manual_review:false,
    blocking_items:[{code,message,action:'检查PDF是否为可搜索的EDA输出、Pin Table/RefDes/Pin标记是否完整，以及OrCAD Capture 17.2/17.4是否可用。'}],
  });
}
orcadOpenButton.addEventListener('click',async()=>{
  const result=await window.pywebview?.api?.open_in_orcad();
  if(!result?.ok)$('orcadBuildStatus').textContent=`HOLD：${result?.message||'无法打开OrCAD Capture。'}`;
});
orcadOutputButton.addEventListener('click',async()=>{await window.pywebview?.api?.open_output_folder();});
function closeStartupDialog(){
  startupDialog.hidden=true;
  document.body.classList.remove('startup-open');
  if(startupEntryPending){startupEntryPending=false;showProductRouter();return;}
  if(!productGate.hidden){(orcadPreview.hidden?$('enterFootprint'):$('backToRouter')).focus();}
  else{buildButton.focus();}
}
function openStartupDialog(){startupDialog.hidden=false;document.body.classList.add('startup-open');$('enterPlatform').focus()}
$('enterPlatform').addEventListener('click',closeStartupDialog);
$('aboutButton').addEventListener('click',openStartupDialog);
function closeBlockingDialog(){blockingDialog.hidden=true;document.body.classList.remove('dialog-open');buildButton.focus()}
function closeVariantDialog(){variantDialog.hidden=true;document.body.classList.remove('variant-dialog-open');(variantDialogMode==='orcad'?orcadBuildButton:buildButton).focus()}
function showVariantDialog(result={}){
  const variants=result.variants||[];
  const isOrcad=variantDialogMode==='orcad';
  $('variantTitle').textContent=isOrcad?'选择原理图零件的封装 Pin 定义':'选择实际订购封装';
  $('variantSummary').textContent=`${result.source_name||'当前Datasheet'}识别到${variants.length}个封装候选。请依据完整订购料号选择，确认前不会调用${isOrcad?'OrCAD Capture':'Allegro'}。`;
  $('variantGrid').innerHTML=variants.map((item,index)=>`<button class="variant-card" type="button" data-variant-index="${Number(item.index??index)}" data-variant-id="${escapeHtml(item.id||'')}"><span class="variant-chip">${isOrcad?'PIN MAP':'PACKAGE'}</span><strong>${escapeHtml(item.label)}</strong><em>${escapeHtml(item.package_code||'未标注')}</em><small>${item.pin_count?`${Number(item.pin_count)} 个实体 Pin · `:(item.observed_pin_count?`Pin表观测 ${Number(item.observed_pin_count)} · `:'')}证据页：${escapeHtml((item.evidence_pages||[]).join('、')||'待复核')}${isOrcad?'':' <span class="risk">· '+Number(item.risk_count||0)+'项待审核</span>'}</small><b class="variant-enter">选择此封装</b></button>`).join('');
  variantDialog.hidden=false;document.body.classList.add('variant-dialog-open');
  variantDialog.querySelector('.variant-card')?.focus();
}
function showBlockingDialog(result={}){
  const items=(result.blocking_items?.length?result.blocking_items:[{code:'PIPELINE_STOP',message:result.message||'当前任务未能生成Footprint。',action:'打开工程输出，核对原始Datasheet封装图和证据报告。'}]);
  $('dialogTitle').textContent=result.dialog_title||(result.generated_with_risk?'Footprint已生成 - 风险待确认':(result.manual_review?'需要工程师确认':'封装生成失败'));
  $('dialogSummary').textContent=(result.message||(result.generated_with_risk?'已生成工程审核暂存封装，请依据证据与Actual差异逐项复核。':'当前Datasheet证据不足，未调用Allegro。'))+(result.allegro_reason?` ${result.allegro_reason}`:'');
  $('dialogSafety').innerHTML=result.generated_with_risk?'<b>安全状态：</b>已生成至暂存目录，尚未写入正式Library':'<b>安全状态：</b>没有生成或写入正式Library';
  $('dialogMatrix').innerHTML=items.map(item=>`<article><code>${escapeHtml(item.code||'UNRESOLVED')}</code><div><b>${escapeHtml(item.message||'证据不足')}</b><p>${escapeHtml(item.action||item.required_action||'请核对原始Datasheet。')}</p></div></article>`).join('');
  dialogHasOutput=Boolean(result.output_dir);$('dialogOutput').disabled=!dialogHasOutput;
  blockingDialog.hidden=false;document.body.classList.add('dialog-open');$('dialogAcknowledge').focus();
}
// 仅供本地视觉回归使用；正式程序不会附加该查询参数。
if(new URLSearchParams(window.location.search).get('ui_review')==='blocking'){
  showBlockingDialog({
    message:'检测到2项阻断问题。系统未写入Allegro Library。',
    blocking_items:[
      {code:'BODY_SIZE_UNRESOLVED',message:'封装本体尺寸D/E缺少可追溯证据',action:'请在原始规格书中确认Package Outline表格或图纸。'},
      {code:'NO_RECOMMENDED_LAND_PATTERN',message:'未找到原厂Recommended Land Pattern',action:'请选择经批准的IPC-7351或公司Padstack规则后重新生成。'}
    ]
  });
}
function escapeHtml(value){return String(value).replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]))}
$('dialogClose').addEventListener('click',closeBlockingDialog);
$('dialogAcknowledge').addEventListener('click',closeBlockingDialog);
$('dialogOutput').addEventListener('click',async()=>{if(dialogHasOutput&&window.pywebview?.api)await window.pywebview.api.open_output_folder()});
blockingDialog.addEventListener('click',event=>{if(event.target?.hasAttribute('data-dialog-close'))closeBlockingDialog()});
document.addEventListener('keydown',event=>{if(event.key!=='Escape')return;if(!schematicComponentDialog.hidden){event.preventDefault();$('componentCancel').click();}else if(!variantDialog.hidden)closeVariantDialog();else if(!blockingDialog.hidden)closeBlockingDialog();else if(!startupDialog.hidden){event.preventDefault();$('enterPlatform').focus();}else if(!productGate.hidden&&!orcadPreview.hidden)showProductRouter()});

function bindCapabilityCards(){document.querySelectorAll('.cap-card').forEach(card=>card.addEventListener('click',()=>{document.querySelectorAll('.cap-card').forEach(x=>x.classList.remove('active'));card.classList.add('active');renderModel(samples[card.dataset.family])},{once:true}))}
bindCapabilityCards();
$('previewBack').addEventListener('click',renderCapabilityHome);

let apiInitializing=false;
async function initializeApi(){
  if(apiReady||apiInitializing||!window.pywebview?.api)return;
  apiInitializing=true;
  try{
    const health=await window.pywebview.api.health_check();
    apiReady=Boolean(health?.ok);
    applyAllegroHealth(health);
    $('modeLabel').textContent=health?.build_mode==='developer'?'开发测试版':'用户交付版';
    buildButton.disabled=!apiReady;
    $('fileName').textContent=apiReady?'选择元件Datasheet':'本机引擎启动失败';
    window.setBuildProgress({percent:0,stage:apiReady?'等待任务':'初始化失败',detail:health?.message||'本机解析器已就绪。',status:apiReady?'':'error'});
  }catch(error){
    buildButton.disabled=true;
    window.setBuildProgress({percent:100,stage:'初始化失败',detail:String(error),status:'error'});
  }finally{
    apiInitializing=false;
  }
}
document.addEventListener('pywebviewready',initializeApi);
window.addEventListener('DOMContentLoaded',initializeApi);
const apiProbe=setInterval(()=>{initializeApi();if(apiReady)clearInterval(apiProbe)},150);
setTimeout(()=>{if(!apiReady){buildButton.disabled=true;window.setBuildProgress({percent:100,stage:'初始化超时',detail:'未连接到本机解析器，请重新启动程序。',status:'error'})}clearInterval(apiProbe)},15000);

$('allegroSettingsButton').addEventListener('click',async()=>{
  if(!window.pywebview?.api)return;
  const button=$('allegroSettingsButton');button.disabled=true;
  try{
    window.setBuildProgress({percent:0,stage:'正在打开Allegro选择窗口',detail:'请选择Cadence安装目录 tools\\bin 内的 allegro.exe。'});
    const result=await window.pywebview.api.choose_allegro_executable();
    if(result?.cancelled){window.setBuildProgress({percent:0,stage:'已取消选择Allegro',detail:'当前配置未变更。'});return;}
    if(!result?.ok)throw new Error(result?.message||'Allegro路径设置失败。');
    applyAllegroHealth(result);
    window.setBuildProgress({percent:0,stage:'Allegro已配置',detail:result.message});
    showResult('success','工作站配置已保存',`${result.message} 路径：${result.allegro_path}`);
  }catch(error){
    showResult('error','Allegro设置失败',String(error));
  }finally{button.disabled=false}
});

function applyBuildResult(result){
  if(result?.needs_variant_selection){
    variantDialogMode='footprint';
    $('score').textContent='SELECT';
    $('extractState').textContent='检测到多个封装Variant，等待用户确认';
    showResult('','请选择封装',result.message||'选择实际订购封装后继续生成。');
    showVariantDialog(result);
    return;
  }
  if(result?.preview_model)renderModel(result.preview_model);
  if(result?.source_name)$('fileName').textContent=result.source_name;
  if(result?.ok){
    $('fileName').textContent=`${result.part_number} · ${result.package_type}`;
    const verified=result.generated_kind==='allegro_verified_staging';
    const unverified=result.generated_kind==='allegro_unverified_staging';
    $('score').textContent=verified&&!result.review_hold?'PASS':'HOLD';
    $('extractState').textContent=verified
      ? (result.review_hold?'Allegro原生封装已生成并回读，但仍有工程风险待审核':'Allegro原生封装已生成，实际数据库回读已通过')
      : (unverified?'仅完成PDF解析、预览与SKILL暂存；Allegro原生封装尚未生成':'仅生成Allegro SKILL草稿，等待工程确认');
    const title=verified?'已生成并验证Allegro暂存封装':(unverified?'Allegro原生封装未完成':'已生成Allegro SKILL草稿');
    showResult(verified?'success':'error',title,`${result.message} 输出：${result.output_dir}`);
    outputButton.disabled=false;allegroButton.disabled=!result.allegro_ready;
    if(result.generated_with_risk)showBlockingDialog(result);
  }else{
    const reason=result?.allegro_reason?` Allegro未调用：${result.allegro_reason}`:'';
    $('score').textContent=result?.manual_review?'HOLD':'STOP';
    $('extractState').textContent=result?.manual_review?'需要工程师标注或确认':'生成已停止';
    showResult(result?.manual_review?'':'error',result?.manual_review?'需要人工标注':'生成已停止',(result?.message||'后台任务未返回结果。')+reason);
    if(result?.output_dir)outputButton.disabled=false;
    allegroButton.disabled=true;
    showBlockingDialog(result||{});
  }
}

variantDialog.addEventListener('click',async event=>{
  const card=event.target.closest?.('[data-variant-index]');
  if(!card||buildBusy||!window.pywebview?.api)return;
  const index=Number(card.dataset.variantIndex);
  closeVariantDialog();buildBusy=true;buildButton.disabled=true;orcadBuildButton.disabled=true;allegroButton.disabled=true;
  window.setBuildProgress({percent:4,stage:'已选择封装Variant',detail:card.querySelector('strong')?.textContent||`候选${index+1}`});
  try{
    if(variantDialogMode==='orcad'){
      $('orcadBuildStatus').textContent=`已选择 ${card.querySelector('strong')?.textContent||card.dataset.variantId}，正在按该Pin定义建立OLB。`;
      const started=await window.pywebview.api.start_orcad_build(orcadPendingDatasheetPath,orcadContextPath,card.dataset.variantId||'',orcadSelectedComponent?.refdes||'');
      if(!started?.ok)throw new Error(started?.message||'无法继续OrCAD建库任务。');
      const result=await pollOrcadBuild(started.job_id);
      await handleOrcadResult(result);
      return;
    }
    const started=await window.pywebview.api.start_variant_build(index);
    if(!started?.ok)throw new Error(started?.message||'无法继续封装生成。');
    await pollBuild(started.job_id);
  }catch(error){
    window.setBuildProgress({percent:100,stage:'处理失败',detail:String(error),status:'error'});
    showResult('error','生成失败',String(error));showBlockingDialog({message:String(error)});
  }finally{
    buildBusy=false;buildButton.disabled=!apiReady;orcadBuildButton.disabled=false;
  }
});
$('variantCancel').addEventListener('click',()=>{const isOrcad=variantDialogMode==='orcad';closeVariantDialog();if(isOrcad){$('orcadBuildStatus').textContent='已取消封装Pin定义选择，未调用OrCAD Capture。';orcadBuildButton.disabled=false;}else{window.setBuildProgress({percent:0,stage:'等待任务',detail:'已取消封装Variant选择，未调用Allegro。'});showResult('','已取消','请重新选择Datasheet或确认完整订购料号。')}});

const delay=(ms)=>new Promise(resolve=>setTimeout(resolve,ms));
async function pollBuild(jobId){
  let failures=0;
  while(buildBusy){
    try{
      const state=await window.pywebview.api.job_status(jobId);
      if(!state?.ok)throw new Error(state?.message||'无法读取后台任务状态。');
      failures=0;
      if(state.progress)window.setBuildProgress(state.progress);
      if(state.done){applyBuildResult(state.result);return}
    }catch(error){
      failures+=1;
      if(failures>=4)throw error;
    }
    await delay(300);
  }
}

buildButton.addEventListener('click',async()=>{
  if(!apiReady||!window.pywebview?.api){showResult('error','本机引擎尚未就绪','请等待“选择元件Datasheet”按钮启用后再操作。');return}
  if(buildBusy)return;
  buildBusy=true;buildButton.setAttribute('aria-busy','true');buildButton.disabled=true;allegroButton.disabled=true;
  window.setBuildProgress({percent:1,stage:'选择Datasheet',detail:'正在打开Windows文件选择窗口。'});
  showResult('','等待选择','请选择一个PDF格式的元件Datasheet。');
  try{
    const selection=await window.pywebview.api.choose_datasheet();
    if(selection?.cancelled){window.setBuildProgress({percent:0,stage:'等待任务',detail:'未选择文件。'});showResult('','已取消','未选择Datasheet。');return}
    if(!selection?.ok)throw new Error(selection?.message||'文件选择失败。');
    $('fileName').textContent=selection.name;
    window.setBuildProgress({percent:2,stage:'提交后台任务',detail:`已选择：${selection.name}`});
    showResult('','正在后台处理','可以观察实时阶段和进度；界面不会因PDF解析或Allegro等待而锁死。');
    const started=await window.pywebview.api.start_build(selection.path);
    if(!started?.ok)throw new Error(started?.message||'无法启动后台任务。');
    await pollBuild(started.job_id);
  }catch(error){
    window.setBuildProgress({percent:100,stage:'处理失败',detail:String(error),status:'error'});
    showResult('error','生成失败',String(error));
    showBlockingDialog({message:String(error),manual_review:false});
  }finally{
    buildBusy=false;buildButton.removeAttribute('aria-busy');buildButton.disabled=!apiReady;
    if($('fileName').textContent.includes('正在'))$('fileName').textContent='选择元件Datasheet';
  }
});
outputButton.addEventListener('click',async()=>{if(window.pywebview?.api)await window.pywebview.api.open_output_folder()});
allegroButton.addEventListener('click',async()=>{if(!window.pywebview?.api)return;allegroButton.disabled=true;window.setBuildProgress({percent:95,stage:'正在启动Allegro',detail:'正在载入暂存DRA或SKILL脚本。'});const result=await window.pywebview.api.open_in_allegro();window.setBuildProgress({percent:100,stage:result.ok?'Allegro已启动':'Allegro启动失败',detail:result.message,status:result.ok?'':'error'});if(!result.ok)showResult('error','Allegro启动失败',result.message);allegroButton.disabled=!result.ok});
