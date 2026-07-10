const $ = (id) => document.getElementById(id);
let currentJob = null;
let pollTimer = null;
let lastData = { raw_data: "", script: "", thumbnail_copy: "", thumbnail_concepts: [], thumbnail_images: [], infographic_concepts: [], infographic_slides: [], voice_items: [], full_package: {} };
const deptOrder = ["planning","research","writing","review","design","video","shipping"];
const activeLines = {
  research: ["lineResearchWrite"], writing: ["linePlanWrite","lineResearchWrite"], review: ["lineWriteReview"], design: ["lineReviewDesign"], video: ["lineReviewDesign"], shipping: ["lineDesignShip","lineReviewShip"]
};
const deptDefaultTalk = {
  planning: "오늘 영상 방향 잡는 중",
  research: "공시랑 수급 확인할게요",
  writing: "말로 읽히게 다듬는 중",
  review: "숫자랑 장중 표현 볼게요",
  design: "클릭될 그림으로 바꾸는 중",
  video: "일레븐랩스 음성 준비",
  shipping: "한 폴더로 포장합니다"
};
function log(msg){ const el=$("log"); const t=new Date().toLocaleTimeString(); el.innerHTML = `<div>[${t}] ${escapeHtml(msg)}</div>` + el.innerHTML; }
function escapeHtml(s){return String(s||"").replace(/[&<>"]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[m]));}
function shortTask(msg, fallback){
  const text=String(msg||fallback||'대기중').replace(/^[0-9]+\/[0-9]+\s*/,'');
  return text.length>22 ? text.slice(0,22)+'…' : text;
}
function setDept(active, done=[], message=''){
  document.querySelectorAll('.office-room').forEach(room=>{
    const id=room.dataset.dept; room.classList.toggle('active', id===active); room.classList.toggle('done', done.includes(id));
    room.querySelector('span').textContent = id===active ? '작업중' : (done.includes(id) ? '완료' : '대기');
    const bubble=room.querySelector('.bubble'); if(bubble) bubble.textContent = id===active ? shortTask(message, deptDefaultTalk[id]) : deptDefaultTalk[id];
    const chip=room.querySelector('.task-chip'); if(chip) chip.textContent = id===active ? '지금 작업중' : (done.includes(id) ? '완료됨' : '대기중');
  });
  document.querySelectorAll('.flow-lines path').forEach(p=>p.classList.remove('active'));
  (activeLines[active]||[]).forEach(id=>{ const el=$(id); if(el) el.classList.add('active'); });
}
function doneBefore(active){ const i=deptOrder.indexOf(active); return i>0 ? deptOrder.slice(0,i) : []; }
function payload(engine){ return { stock_name: $('stockName').value.trim(), stock_code: $('stockCode').value.trim(), format_name: $('formatName').value, custom_topic: $('customTopic').value, output_dir: $('outputDir').value, engine: engine || 'chain', raw_data: lastData.raw_data, script: lastData.script, thumbnail_copy: lastData.thumbnail_copy, concepts: selectedConcepts(), infographic_concepts: selectedInfoConcepts(), infographic_color_theme: $('infoTheme')?.value || 'dark_lineart_city', infographic_layout_concept: $('infoLayout')?.value || 'photo_fullbleed', infographic_photo_accent: $('infoPhotoAccent')?.checked ?? true, infographic_custom_color: $('infoCustomColor')?.value || '', image_parallel_workers: Number($('infoWorkers')?.value || 2) }; }
async function api(path, body){ const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}); const j=await r.json(); if(!j.ok) throw new Error(j.error||'요청 실패'); return j; }
async function startJob(path, body){
  const res=await api(path, body); currentJob=res.job_id; document.body.classList.add('is-running'); $('globalStatus').textContent='작업 시작'; $('jobTitle').textContent='작업 #' + currentJob; $('progressBar').style.width='3%'; log('제작 의뢰 접수: '+path); document.querySelectorAll('button').forEach(b=>b.classList.add('busy')); poll();
}
async function poll(){
  if(!currentJob) return;
  clearTimeout(pollTimer);
  try{
    const r=await fetch('/api/job/'+currentJob); const j=await r.json(); if(!j.ok) throw new Error(j.error||'작업 조회 실패');
    const job=j.job; const pct=job.progress||0; $('jobProgress').textContent=pct+'%'; $('progressBar').style.width=pct+'%'; $('globalStatus').textContent=job.message||job.status; $('jobTitle').textContent=job.title+' · '+job.status; setDept(job.department||'planning', doneBefore(job.department), job.message||'');
    if(job.message) log(job.message);
    if(job.status==='done') { handleResult(job); cleanupButtons(); setDept('shipping', deptOrder.filter(d=>d!=='video'), '출고 완료'); log('작업 완료'); return; }
    if(job.status==='error') { cleanupButtons(); setDept('review', []); log('오류: '+job.error); alert(job.error||'오류'); return; }
    pollTimer=setTimeout(poll,1200);
  } catch(e){ cleanupButtons(); log('조회 오류: '+e.message); alert(e.message); }
}
function cleanupButtons(){ document.querySelectorAll('button').forEach(b=>b.classList.remove('busy')); document.body.classList.remove('is-running'); }
function handleResult(job){
  const r=job.result||{};
  if(r.raw_data){ lastData.raw_data=r.raw_data; $('rawOut').value=r.raw_data; }
  if(r.script){ lastData.script=r.script; $('scriptOut').value=r.script; }
  if(r.thumbnail_copy){ lastData.thumbnail_copy=r.thumbnail_copy; $('thumbOut').value=r.thumbnail_copy; }
  if(Array.isArray(r.concepts)){ lastData.thumbnail_concepts=r.concepts; renderConcepts(r.concepts); }
  if(Array.isArray(r.items)){ lastData.thumbnail_images=r.items; renderGallery(r.items); $('thumbOut').value = JSON.stringify(r,null,2); }
  if(Array.isArray(r.infographic_concepts)){ lastData.infographic_concepts=r.infographic_concepts; renderInfoConcepts(r.infographic_concepts); $('thumbOut').value = JSON.stringify(r.infographic_concepts,null,2); }
  if(Array.isArray(r.infographic_items)){ lastData.infographic_slides=r.infographic_items; renderInfoGallery(r.infographic_items); $('thumbOut').value = JSON.stringify(r,null,2); }
  if(Array.isArray(r.voice_items)){ lastData.voice_items=r.voice_items; }
  if(r.package){ lastData.full_package=r.package; }
  if(Array.isArray(r.results)){ $('thumbOut').value = JSON.stringify(r,null,2); }
  if(r.summary){ renderPackageSummary(r.summary); }
  if(r.path){ log('저장 파일: '+r.path); }
}

function renderPackageSummary(summary){
  const root=$('packageGrid');
  if(!root) return;
  const rawChars=Number(summary.raw_chars||0).toLocaleString();
  const scriptChars=Number(summary.script_chars||0).toLocaleString();
  const thumbCount=Number(summary.thumbnail_image_count ?? summary.thumbnail_concept_count ?? 0);
  const voiceCount=Number(summary.voice_count ?? summary.infographic_concept_count ?? 0);
  const thumbLabel = summary.thumbnail_image_count !== undefined ? '썸네일 이미지' : 'CTR 컨셉 후보';
  const voiceLabel = summary.voice_count !== undefined ? 'mp3 음성 파일' : '인포 장면 후보';
  $('packageStatus').textContent = `${escapeHtml(summary.stock_name||'종목')} 준비 완료`;
  root.innerHTML = `
    <div class="package-card"><b>자료</b><strong>${rawChars}</strong><small>수집 데이터 글자</small></div>
    <div class="package-card"><b>대본</b><strong>${scriptChars}</strong><small>완성 대본 글자</small></div>
    <div class="package-card"><b>썸네일</b><strong>${thumbCount}개</strong><small>${thumbLabel}</small></div>
    <div class="package-card"><b>음성/인포</b><strong>${voiceCount}개</strong><small>${voiceLabel}</small></div>
  `;
  $('packageFolder').textContent = summary.package_dir ? `출고 폴더: ${summary.package_dir}` : '';
  if(Array.isArray(summary.next_steps)){
    summary.next_steps.forEach(step=>log('다음 단계: '+step));
  }
}

function renderConcepts(concepts){
  const root=$('conceptStrip');
  if(!root) return;
  if(!concepts.length){ root.innerHTML='<div class="empty-card">컨셉 후보가 없습니다.</div>'; return; }
  root.innerHTML=concepts.map((c,idx)=>`
    <div class="concept-card ${c.selected?'selected':''}" data-idx="${idx}">
      <span class="score">${c.ctr_score||80}</span>
      <b>${escapeHtml(c.badge||'긴급속보')}</b>
      <strong>${escapeHtml(c.main||'메인문구')}</strong>
      <em>${escapeHtml(c.sub||'서브문구')}</em>
      <small>${escapeHtml(c.style||'프리미엄')} · 후보 ${idx+1}</small>
    </div>
  `).join('');
  root.querySelectorAll('.concept-card').forEach(card=>{
    card.onclick=()=>{
      const idx=Number(card.dataset.idx);
      lastData.thumbnail_concepts[idx].selected=!lastData.thumbnail_concepts[idx].selected;
      renderConcepts(lastData.thumbnail_concepts);
    };
  });
}

function selectedConcepts(){
  return (lastData.thumbnail_concepts||[]).filter(c=>c.selected);
}

function renderGallery(items){
  const root=$('thumbGallery');
  if(!root) return;
  if(!items.length){ root.innerHTML='<div class="empty-card">이미지 결과가 없습니다.</div>'; return; }
  root.innerHTML=items.map((item,idx)=>{
    const url=item.url||'';
    return `<div class="thumb-card">
      ${url?`<img src="${escapeHtml(url)}" alt="thumbnail ${idx+1}" />`:'<div class="empty-card">이미지 없음</div>'}
      <a href="${escapeHtml(url)}" target="_blank">시안 ${idx+1} 열기 · ${escapeHtml(item.style||'')}</a>
    </div>`;
  }).join('');
}

function renderInfoConcepts(concepts){
  const root=$('infoConceptStrip');
  if(!root) return;
  if(!concepts.length){ root.innerHTML='<div class="empty-card">인포그래픽 후보가 없습니다.</div>'; return; }
  root.innerHTML=concepts.map((c,idx)=>`
    <div class="concept-card ${c.selected?'selected':''}" data-idx="${idx}">
      <span class="score">S${c.scene_no||idx+1}</span>
      <b>${escapeHtml(c.style||'프리미엄 다크')}</b>
      <strong>${escapeHtml(c.title||'슬라이드 제목')}</strong>
      <em>${escapeHtml(c.main||'핵심 문장')}</em>
      <span class="layout">${escapeHtml(c.layout||'카드형 레이아웃')}</span>
      <small>대본 장면 ${idx+1}</small>
    </div>
  `).join('');
  root.querySelectorAll('.concept-card').forEach(card=>{
    card.onclick=()=>{
      const idx=Number(card.dataset.idx);
      lastData.infographic_concepts[idx].selected=!lastData.infographic_concepts[idx].selected;
      renderInfoConcepts(lastData.infographic_concepts);
    };
  });
}

function selectedInfoConcepts(){
  return (lastData.infographic_concepts||[]).filter(c=>c.selected);
}

function renderInfoGallery(items){
  const root=$('infoGallery');
  if(!root) return;
  if(!items.length){ root.innerHTML='<div class="empty-card">인포그래픽 이미지 결과가 없습니다.</div>'; return; }
  root.innerHTML=items.map((item,idx)=>{
    const url=item.url||'';
    return `<div class="thumb-card">
      ${url?`<img src="${escapeHtml(url)}" alt="infographic ${idx+1}" />`:'<div class="empty-card">이미지 없음</div>'}
      <a href="${escapeHtml(url)}" target="_blank">인포 ${idx+1} 열기 · ${escapeHtml(item.style||'')}</a>
    </div>`;
  }).join('');
}
async function loadConfig(){
  const r=await fetch('/api/config'); const j=await r.json(); if(!j.ok) throw new Error(j.error||'설정 로드 실패');
  const preset=$('preset'); preset.innerHTML=''; Object.entries(j.presets).forEach(([name,code])=>{ const o=document.createElement('option'); o.value=name; o.textContent=name; o.dataset.code=code; preset.appendChild(o); });
  const fmt=$('formatName'); fmt.innerHTML=''; j.formats.forEach(f=>{ const o=document.createElement('option'); o.value=f; o.textContent=f; fmt.appendChild(o); });
  const theme=$('infoTheme'); if(theme){ theme.innerHTML=''; (j.infographic_themes||[]).forEach(t=>{ const o=document.createElement('option'); o.value=t.key; o.textContent=t.label; theme.appendChild(o); }); theme.value='dark_lineart_city'; }
  const layout=$('infoLayout'); if(layout){ layout.innerHTML=''; (j.infographic_layouts||[]).forEach(l=>{ const o=document.createElement('option'); o.value=l.key; o.textContent=l.label; layout.appendChild(o); }); layout.value='photo_fullbleed'; }
  $('outputDir').value=j.output_dir||'';
  preset.addEventListener('change',()=>{ const opt=preset.selectedOptions[0]; $('stockName').value=opt.value; $('stockCode').value=opt.dataset.code||''; });
  log('AI 제작사 로드 완료');
}
$('collectBtn').onclick=()=>startJob('/api/collect', payload());
$('autoPilotBtn').onclick=()=>startJob('/api/full-package', payload('chain'));
$('fullPackageBtn').onclick=()=>startJob('/api/full-package', payload('chain'));
$('oneClickBtn').onclick=()=>startJob('/api/one-click', payload('chain'));
$('scriptBtn').onclick=()=>startJob('/api/script', payload('chain'));
$('fastScriptBtn').onclick=()=>startJob('/api/script', payload('fast_openai'));
$('thumbCopyBtn').onclick=()=>startJob('/api/thumbnail-copy', payload());
$('thumbConceptBtn').onclick=()=>startJob('/api/thumbnail-concepts', {...payload(), count:8});
$('thumbImgBtn').onclick=()=>startJob('/api/thumbnail-images', {...payload(), count:Math.max(1, selectedConcepts().length || 3)});
$('infoConceptBtn').onclick=()=>startJob('/api/infographic-concepts', {...payload(), count:6});
$('infoImgBtn').onclick=()=>startJob('/api/infographic-slides', {...payload(), count:Math.max(1, selectedInfoConcepts().length || 4)});
$('openOutputBtn').onclick=()=>api('/api/open-output',{}).then(r=>log('폴더 열기: '+r.path)).catch(e=>alert(e.message));
$('selectAllConcepts').onclick=()=>{ lastData.thumbnail_concepts=(lastData.thumbnail_concepts||[]).map(c=>({...c,selected:true})); renderConcepts(lastData.thumbnail_concepts); };
$('clearConcepts').onclick=()=>{ lastData.thumbnail_concepts=(lastData.thumbnail_concepts||[]).map(c=>({...c,selected:false})); renderConcepts(lastData.thumbnail_concepts); };
$('selectAllInfo').onclick=()=>{ lastData.infographic_concepts=(lastData.infographic_concepts||[]).map(c=>({...c,selected:true})); renderInfoConcepts(lastData.infographic_concepts); };
$('clearInfo').onclick=()=>{ lastData.infographic_concepts=(lastData.infographic_concepts||[]).map(c=>({...c,selected:false})); renderInfoConcepts(lastData.infographic_concepts); };
loadConfig().catch(e=>alert(e.message));
setDept(null,[]);
