const $=s=>document.querySelector(s);let current=null;let channel='preview';
const LIMITS={grid:64,colors:16,yarns:256};
const WEAVE_NAMES={plain:'平纹',twill:'斜纹',basket:'方平',custom:'自定义组织'};
const fields=[['size','输出边长',32,1024,1],['repeat_x','经向纱数（自动取整共同周期）',1,64,1],['repeat_y','纬向纱数（自动取整共同周期）',1,64,1],['gap','纱线间隙',.02,.8,.01],['relief','织纹起伏',0,1,.01],['height_blur','高度平滑',0,12,.1],['height_low','高度低点',0,1,.01],['height_high','高度高点',0,1,.01],['invert','高度反转','checkbox'],['normal_strength','法线强度',0,20,.1],['normal_y','法线Y约定',['up','down']],['roughness','基础粗糙度',0,1,.01],['roughness_detail','粗糙细节',0,1,.01],['ao_radius','AO范围',1,32,.5],['ao_strength','AO强度',0,8,.1],['source_scale','源图缩放',.1,10,.1],['source_x','源图横移',-2,2,.01],['source_y','源图纵移',-2,2,.01],['source_turn','源图转角',-360,360,1],['light_azimuth','光源方位',0,360,1],['light_elevation','光源仰角',5,90,1]];
// 对话框内的临时组织状态，保存时才写回材质
let draft=null,draftTimer=null,applying=0,pendingApply=null;
async function api(path,options={}){const r=await fetch('/loom'+path,options);if(!r.ok){let e=await r.json();throw Error(e.error||r.status)}return r.json()}
function json(method,data){return {method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}}
function toast(t){$('#toast').textContent=t;$('#toast').style.display='block';setTimeout(()=>$('#toast').style.display='none',3500)}
async function safe(fn){try{await fn()}catch(e){toast(e.message);console.error(e)}}
function gcd(a,b){a=Math.abs(a);b=Math.abs(b);while(b){[a,b]=[b,a%b]}return a||1}
function lcm(a,b){return a/gcd(a,b)*b}
function periodsOf(st){
  if(st.weave==='custom')return [st.custom_grid[0].length,st.custom_grid.length];
  if(st.weave==='plain')return [2,2];
  return [4,4];
}
function effective(st){
  const [px,py]=periodsOf(st);
  const cx=lcm(px,st.warp_colors.length),cy=lcm(py,st.weft_colors.length);
  const rx=Math.min(LIMITS.yarns,Math.ceil(st.repeat_x/cx)*cx);
  const ry=Math.min(LIMITS.yarns,Math.ceil(st.repeat_y/cy)*cy);
  return {px,py,cx,cy,rx,ry};
}
async function listing(){const list=await api('/materials');$('#list').replaceChildren();for(const p of list){const b=document.createElement('button');b.textContent=p.name;b.className=current?.id===p.id?'active':'';b.onclick=()=>safe(()=>open(p.id));$('#list').append(b)}}
function mapurl(c,repeat=false){return `/loom/materials/${current.id}/map/${c}.png?r=${current.revision}${repeat?'&repeat=1':''}`}
function images(){if(!current)return;$('#picture').src=mapurl(channel,$('#repeat').checked);$('#revision').textContent=`${current.settings.size} × ${current.settings.size} / REV ${current.revision} / ${channel}`;$('#report').hidden=true;$('#thumbs').replaceChildren();for(const c of ['albedo','height','normal','roughness','ao','orm']){const b=document.createElement('button');const img=document.createElement('img');img.src=mapurl(c);b.append(img,document.createTextNode(c));b.onclick=()=>{$('#channel').value=c;channel=c;images()};$('#thumbs').append(b)}}
function form(){const root=$('#fields');root.replaceChildren();root.className='field-grid';
  // 组织与配色入口（不进通用 fields 循环）
  const weaveWrap=document.createElement('label');weaveWrap.style.gridColumn='1 / -1';
  weaveWrap.textContent='组织结构';
  const weaveRow=document.createElement('div');weaveRow.className='row weave-row';
  const sel=document.createElement('select');sel.name='weave';
  for(const v of ['plain','twill','basket','custom']){const o=document.createElement('option');o.value=v;o.textContent=WEAVE_NAMES[v];if(current.settings.weave===v)o.selected=true;sel.append(o)}
  const editBtn=document.createElement('button');editBtn.type='button';editBtn.textContent='▦ 网格编辑 / 配色循环';editBtn.className='quiet';
  editBtn.onclick=()=>openWeave();
  weaveRow.append(sel,editBtn);weaveWrap.append(weaveRow);root.append(weaveWrap);
  sel.onchange=()=>safe(async()=>{await pushSettings({weave:sel.value});refreshPeriodHint()});
  const hint=document.createElement('p');hint.id='periodHint';hint.className='period-hint';root.append(hint);
  for(const [key,label,a,b,step] of fields){const l=document.createElement('label');l.textContent=label;let e;if(Array.isArray(a)){e=document.createElement('select');for(const v of a){let o=document.createElement('option');o.value=v;o.textContent=v;e.append(o)}}else{e=document.createElement('input');e.type=typeof a==='string'?a:'number';if(typeof a==='number'){e.min=a;e.max=b;e.step=step}}e.name=key;if(a==='checkbox')e.checked=current.settings[key];else e.value=current.settings[key];l.append(e);root.append(l)}
  refreshPeriodHint();
}
function refreshPeriodHint(){
  const el=$('#periodHint');if(!el||!current)return;
  const m=effective(current.settings);
  const over=m.cx>LIMITS.yarns||m.cy>LIMITS.yarns;
  let txt=`组织循环 ${m.px}×${m.py}；共同周期 经${m.cx} / 纬${m.cy} 根；实际输出 ${m.rx}×${m.ry} 根纱`;
  if(m.rx!==current.settings.repeat_x||m.ry!==current.settings.repeat_y)txt+='（已向上取整，不截断末根纱）';
  el.textContent=txt;el.classList.toggle('warn',over);
  if(over)el.textContent+=`；超过单方向 ${LIMITS.yarns} 根上限，请缩小网格或配色循环`;
}
async function open(id){current=await api('/materials/'+id);$('#empty').hidden=true;$('#editor').hidden=false;$('#title').textContent=current.name;$('#subtitle').textContent='可编辑织物材质 / 线性数据通道 / sRGB底色';$('#name').value=current.name;$('#mode').value=current.mode;form();images();$('#snapshots').replaceChildren();for(const s of current.snapshots){const b=document.createElement('button');b.textContent='↶ '+s.label;b.onclick=()=>safe(async()=>{current=await api(`/materials/${id}/snapshots/${s.id}/restore`,json('POST',{revision:current.revision}));await open(id)});$('#snapshots').append(b)}await listing()}
// 串行化设置提交，避免实时预览与手动应用的修订号竞争；排队期间合并补丁防止丢字段
async function pushSettings(patch){
  if(applying){pendingApply=pendingApply?{...pendingApply,...patch}:patch;return}
  applying++;
  try{
    current=await api('/materials/'+current.id,json('PATCH',{revision:current.revision,settings:patch}));
  }finally{applying--}
  if(pendingApply){const next=pendingApply;pendingApply=null;await pushSettings(next)}
  else{form();images();refreshPeriodHint()}
}
function collectForm(){
  const s={};
  for(const [key,label,a] of fields){const el=$(`[name="${key}"]`);s[key]=a==='checkbox'?el.checked:typeof a==='number'?Number(el.value):el.value}
  s.weave=$('[name="weave"]').value;
  return s;
}
$('#new').onclick=()=>safe(async()=>{let p=await api('/materials',json('POST',{name:'新织物'}));await open(p.id)});
$('#demo').onclick=$('#start').onclick=()=>safe(async()=>{let p=await api('/demo',json('POST',{}));await open(p.id)});
$('#settings').onsubmit=e=>{e.preventDefault();safe(async()=>{
  current=await api('/materials/'+current.id,json('PATCH',{revision:current.revision,name:$('#name').value,mode:$('#mode').value,settings:collectForm()}));
  await open(current.id);
})};
$('#channel').onchange=e=>{channel=e.target.value;images()};$('#repeat').onchange=images;
$('#inspect').onclick=()=>safe(async()=>{if(!current)return;const r=await api(`/loom/materials/${current.id}/inspect/${channel}`);$('#report').textContent=JSON.stringify(r,null,2);$('#report').hidden=false});
$('#upload').onclick=()=>$('#sourcefile').click();$('#sourcefile').onchange=()=>safe(async()=>{let f=$('#sourcefile').files[0];if(!f||!current)return;let d=new FormData();d.append('file',f);d.append('revision',current.revision);current=await api(`/loom/materials/${current.id}/source`,{method:'POST',body:d});await open(current.id);$('#sourcefile').value=''});
$('#snapshot').onclick=()=>safe(async()=>{if(!current)return;current=await api(`/materials/${current.id}/snapshots`,json('POST',{revision:current.revision,label:$('#snaplabel').value||'材质版本'}));await open(current.id)});
$('#export').onclick=()=>{if(current)location.href=`/loom/materials/${current.id}/export.zip`};$('#archive').onclick=()=>{if(current)location.href=`/loom/materials/${current.id}/project.zip`};
$('#import').onclick=()=>$('#projectfile').click();$('#projectfile').onchange=()=>safe(async()=>{let f=$('#projectfile').files[0];if(!f)return;let d=new FormData();d.append('file',f);let p=await api('/import',{method:'POST',body:d});await open(p.id);$('#projectfile').value=''});

/* ---------- 组织网格编辑器 + 配色循环 ---------- */
function openWeave(){
  draft=JSON.parse(JSON.stringify(current.settings));
  renderWeaveDialog();
  $('#weaveDialog').showModal();
}
function renderWeaveDialog(){
  const body=$('#weaveBody');body.replaceChildren();
  const wrap=document.createElement('div');wrap.className='weave-editor';

  // 组织选择
  const orgRow=document.createElement('div');orgRow.className='weave-org-row';
  orgRow.append(Object.assign(document.createElement('span'),{textContent:'组织结构'}));
  const sel=document.createElement('select');
  for(const v of ['plain','twill','basket','custom']){const o=document.createElement('option');o.value=v;o.textContent=WEAVE_NAMES[v];if(draft.weave===v)o.selected=true;sel.append(o)}
  sel.onchange=()=>{draft.weave=sel.value;renderWeaveDialog()};
  orgRow.append(sel);wrap.append(orgRow);

  if(draft.weave==='custom')wrap.append(gridSection());
  wrap.append(paletteSection('warp_colors','经纱配色循环（沿经向依次重复）'));
  wrap.append(paletteSection('weft_colors','纬纱配色循环（沿纬向依次重复）'));
  wrap.append(periodSection());

  const foot=document.createElement('div');foot.className='weave-foot';
  const live=document.createElement('label');live.className='live-label';
  live.innerHTML='<input type="checkbox" id="livePreview" checked> 实时预览（自动保存并刷新）';
  const cancel=document.createElement('button');cancel.type='button';cancel.textContent='取消';
  cancel.onclick=()=>{$('#weaveDialog').close();clearTimeout(draftTimer)};
  const save=document.createElement('button');save.type='button';save.className='primary';save.textContent='保存到材质';
  save.onclick=()=>safe(saveDraft);
  foot.append(live,cancel,save);wrap.append(foot);
  body.append(wrap);
  scheduleLive();
}
function gridSection(){
  const sec=document.createElement('div');sec.className='grid-section';
  const cap=document.createElement('p');cap.className='grid-cap';
  cap.innerHTML='格内 <b>深色＝经纱在上(1)</b>，浅色＝纬纱在上(0)；行自上而下对应纬纱，列自左而右对应经纱。点击或拖动绘制。';
  sec.append(cap);

  const dimRow=document.createElement('div');dimRow.className='row dim-row';
  dimRow.append(dimInput('经向格数（列）','cols'),dimInput('纬向格数（行）','rows'));
  sec.append(dimRow);

  const tools=document.createElement('div');tools.className='row grid-tools';
  for(const [t,fn] of [['全部经在上',()=>fillGrid(1)],['全部纬在上',()=>fillGrid(0)],['反相',invertGrid],['导入黑白组织图',()=>$('#draftfile').click()]]){
    const b=document.createElement('button');b.type='button';b.textContent=t;b.onclick=fn;tools.append(b);
  }
  sec.append(tools);

  const canvas=document.createElement('canvas');canvas.id='draftCanvas';canvas.className='draft-canvas';
  sec.append(canvas);
  drawGrid(canvas);
  bindPaint(canvas);
  return sec;
}
function dimInput(label,key){
  const l=document.createElement('label');l.textContent=label;
  const e=document.createElement('input');e.type='number';e.min=2;e.max=LIMITS.grid;e.value=key==='cols'?draft.custom_grid[0].length:draft.custom_grid.length;
  e.onchange=()=>safe(()=>resizeGrid(Number(e.value),key));
  l.append(e);return l;
}
function cellSize(){
  const cols=draft.custom_grid[0].length,rows=draft.custom_grid.length;
  return Math.max(10,Math.min(34,Math.floor(520/Math.max(cols,rows))));
}
function drawGrid(canvasEl){
  const canvas=canvasEl||$('#draftCanvas');if(!canvas)return;
  const g=draft.custom_grid,rows=g.length,cols=g[0].length,cs=cellSize();
  canvas.width=cols*cs;canvas.height=rows*cs;
  const ctx=canvas.getContext('2d');
  for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){
    ctx.fillStyle=g[r][c]?'#26322b':'#f3efe2';
    ctx.fillRect(c*cs,r*cs,cs,cs);
  }
  ctx.strokeStyle='#b9b2a0';ctx.lineWidth=1;
  for(let c=0;c<=cols;c++){ctx.beginPath();ctx.moveTo(c*cs+.5,0);ctx.lineTo(c*cs+.5,rows*cs);ctx.stroke()}
  for(let r=0;r<=rows;r++){ctx.beginPath();ctx.moveTo(0,r*cs+.5);ctx.lineTo(cols*cs,r*cs+.5);ctx.stroke()}
}
function bindPaint(canvas){
  let painting=false,paintValue=null;
  const pick=ev=>{const rect=canvas.getBoundingClientRect();
    const c=Math.floor((ev.clientX-rect.left)/rect.width*canvas.width/cellSize());
    const r=Math.floor((ev.clientY-rect.top)/rect.height*canvas.height/cellSize());
    if(r<0||c<0||r>=draft.custom_grid.length||c>=draft.custom_grid[0].length)return null;
    return [r,c];};
  canvas.onpointerdown=ev=>{ev.preventDefault();const p=pick(ev);if(!p)return;
    painting=true;paintValue=draft.custom_grid[p[0]][p[1]]?0:1;
    draft.custom_grid[p[0]][p[1]]=paintValue;drawGrid();scheduleLive();
    canvas.setPointerCapture(ev.pointerId);};
  canvas.onpointermove=ev=>{if(!painting)return;const p=pick(ev);if(!p)return;
    if(draft.custom_grid[p[0]][p[1]]!==paintValue){draft.custom_grid[p[0]][p[1]]=paintValue;drawGrid();scheduleLive()}};
  canvas.onpointerup=()=>{painting=false};
}
function fillGrid(v){draft.custom_grid=draft.custom_grid.map(row=>row.map(()=>v));drawGrid();scheduleLive()}
function invertGrid(){draft.custom_grid=draft.custom_grid.map(row=>row.map(v=>v?0:1));drawGrid();scheduleLive()}
function resizeGrid(value,key){
  if(!Number.isInteger(value)||value<2||value>LIMITS.grid)throw Error(`网格每方向为2至${LIMITS.grid}格`);
  const old=draft.custom_grid,oldRows=old.length,oldCols=old[0].length;
  const rows=key==='rows'?value:oldRows,cols=key==='cols'?value:oldCols;
  const next=[];
  for(let r=0;r<rows;r++){const row=[];for(let c=0;c<cols;c++)row.push(old[r%oldRows][c%oldCols]);next.push(row)}
  draft.custom_grid=next;renderWeaveDialog();scheduleLive();
}
$('#draftfile').onchange=()=>safe(async()=>{
  const f=$('#draftfile').files[0];if(!f)return;
  const bmp=await loadImage(f);
  const rows=draft.custom_grid.length,cols=draft.custom_grid[0].length;
  const cv=document.createElement('canvas');cv.width=cols;cv.height=rows;
  const ctx=cv.getContext('2d');ctx.imageSmoothingEnabled=false;
  ctx.drawImage(bmp,0,0,cols,rows);
  const data=ctx.getImageData(0,0,cols,rows).data;
  for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){
    const i=(r*cols+c)*4;
    // 感知亮度；深色（黑）=经纱在上
    const lum=data[i]*.2126+data[i+1]*.7152+data[i+2]*.0722;
    draft.custom_grid[r][c]=lum<128?1:0;
  }
  $('#draftfile').value='';renderWeaveDialog();toast('已按当前网格尺寸导入，深色格＝经纱在上；方向相反可用「反相」');
});
function loadImage(file){
  return new Promise((resolve,reject)=>{const img=new Image();
    img.onload=()=>resolve(img);img.onerror=()=>reject(Error('无法读取组织图片'));
    img.src=URL.createObjectURL(file);});
}
function paletteSection(key,title){
  const sec=document.createElement('div');sec.className='palette-section';
  const head=document.createElement('div');head.className='palette-head';
  head.append(Object.assign(document.createElement('span'),{textContent:title}));
  const add=document.createElement('button');add.type='button';add.textContent='＋ 加色';
  add.disabled=draft[key].length>=LIMITS.colors;
  add.onclick=()=>{if(draft[key].length<LIMITS.colors){draft[key].push('#8a8a8a');renderWeaveDialog();scheduleLive()}};
  head.append(add);sec.append(head);
  draft[key].forEach((hex,idx)=>{
    const row=document.createElement('div');row.className='palette-row';
    const idxLabel=document.createElement('span');idxLabel.className='palette-idx';idxLabel.textContent=String(idx+1).padStart(2,'0');
    const picker=document.createElement('input');picker.type='color';picker.value=hex;
    picker.oninput=()=>{draft[key][idx]=picker.value;scheduleLive()};
    const up=document.createElement('button');up.type='button';up.textContent='↑';up.disabled=idx===0;
    up.onclick=()=>{[draft[key][idx-1],draft[key][idx]]=[draft[key][idx],draft[key][idx-1]];renderWeaveDialog();scheduleLive()};
    const down=document.createElement('button');down.type='button';down.textContent='↓';down.disabled=idx===draft[key].length-1;
    down.onclick=()=>{[draft[key][idx+1],draft[key][idx]]=[draft[key][idx],draft[key][idx+1]];renderWeaveDialog();scheduleLive()};
    const del=document.createElement('button');del.type='button';del.textContent='删';del.disabled=draft[key].length<=1;
    del.onclick=()=>{draft[key].splice(idx,1);renderWeaveDialog();scheduleLive()};
    row.append(idxLabel,picker,up,down,del);sec.append(row);
  });
  return sec;
}
function periodSection(){
  const sec=document.createElement('div');sec.className='period-section';
  const m=effective(draft);
  const over=m.cx>LIMITS.yarns||m.cy>LIMITS.yarns;
  const lines=[
    `组织循环：经 ${m.px} 根 / 纬 ${m.py} 根`,
    `配色循环：经 ${draft.warp_colors.length} 色 / 纬 ${draft.weft_colors.length} 色`,
    `共同周期：经 ${m.cx} 根 / 纬 ${m.cy} 根（LCM）`,
    `本张贴图实际平铺：经 ${m.rx} 根 / 纬 ${m.ry} 根纱，按完整周期连续重复`,
  ];
  for(const t of lines){const p=document.createElement('p');p.textContent=t;sec.append(p)}
  if(over){const w=document.createElement('p');w.className='period-warn';
    w.textContent=`共同周期超过单方向 ${LIMITS.yarns} 根上限，无法生成；请缩小网格（≤${LIMITS.grid}）或配色（≤${LIMITS.colors}色）。`;sec.append(w)}
  else if(m.rx!==draft.repeat_x||m.ry!==draft.repeat_y){
    const w=document.createElement('p');w.className='period-note';
    w.textContent=`请求 ${draft.repeat_x}×${draft.repeat_y} 根，已自动向上取整为 ${m.rx}×${m.ry}，末根纱不会被截断。`;sec.append(w)}
  return sec;
}
function scheduleLive(){
  const m=effective(draft);
  if(m.cx>LIMITS.yarns||m.cy>LIMITS.yarns)return;
  clearTimeout(draftTimer);
  draftTimer=setTimeout(()=>safe(async()=>{
    if(!$('#livePreview')?.checked||!current)return;
    await saveDraft(true);
  }),450);
}
async function saveDraft(live=false){
  const patch={weave:draft.weave,warp_colors:draft.warp_colors,weft_colors:draft.weft_colors};
  if(draft.weave==='custom')patch.custom_grid=draft.custom_grid;
  await pushSettings(patch);
  draft=JSON.parse(JSON.stringify(current.settings));
  if(!live){$('#weaveDialog').close();toast('组织与配色已保存进工程')}
}
$('#weaveClose').onclick=()=>{clearTimeout(draftTimer);$('#weaveDialog').close()};
safe(listing);
