let lastPanelFocus=null;

function setPanelOpen(open, restoreFocus){
  const panel=document.getElementById('panel');
  const btn=document.getElementById('btnCompare');
  if(open){
    if(panel.classList.contains('open')) return;
    lastPanelFocus=document.activeElement;
    panel.classList.add('open');
    if(btn) btn.setAttribute('aria-expanded','true');
    const closeBtn=document.getElementById('btnClosePanel');
    if(closeBtn) closeBtn.focus();
  }else{
    if(!panel.classList.contains('open')) return;
    panel.classList.remove('open');
    if(btn) btn.setAttribute('aria-expanded','false');
    if(restoreFocus){
      const target=(lastPanelFocus && document.contains(lastPanelFocus))?lastPanelFocus:btn;
      if(target) target.focus();
    }
  }
}

function openCompare(){
  if(selected.size<2){ alert('請先揀至少 2 個型號再撳「開始比較」'); return; }
  buildPanel();
  setPanelOpen(true);
}

function selectedModels(){
  return ALL.filter(m=>selected.has(m.brand+'|'+m.model));
}

function buildPanel(){
  const list=selectedModels();
  const body=document.getElementById('panelBody');
  document.getElementById('panelTitle').textContent=`📋 型號對比（${list.length} 個）`;
  const rows=[['<b>屬性</b>', ...list.map(m=>`<b>${esc(m.brand)}<br>${esc(m.model)}<br><button class="rm" onclick="removeSel('${esc(m.brand+'|'+m.model).replace(/'/g,'&#39;')}')">✕ 移除</button></b>`)]];
  for(const [key,label] of FIELDS){
    // 搵最佳值（最少 2 個先高亮）
    let best=null;
    if(list.length>=2){
      if(key==='price'){
        const vals=list.map(m=>{const m1=String(m.price||'').match(/\$([\d,]+)/); return m1?parseInt(m1[1].replace(/,/g,'')):NaN;});
        const ok=vals.filter(v=>!isNaN(v));
        if(ok.length===list.length) best=Math.min(...ok);
      }else if(key==='kwh'){
        const vals=list.map(m=>parseFloat(m.kwh));
        if(vals.every(v=>!isNaN(v))) best=Math.min(...vals);
      }else if(key==='cspf'){
        const vals=list.map(m=>parseFloat(m.cspf));
        if(vals.every(v=>!isNaN(v))) best=Math.max(...vals);
      }
    }
    rows.push([`<td>${label}</td>`, ...list.map(m=>{
      const v=m[key];
      const txt=(v===null||v===undefined||v==='')?'待查':String(v);
      let cls='';
      if(best!==null && !isNaN(best)){
        if(key==='price'){
          const m1=String(m.price||'').match(/\$([\d,]+)/);
          if(m1 && parseInt(m1[1].replace(/,/g,''))===best) cls=' class="best"';
        }else{
          const pv=parseFloat(m[key]);
          if(pv===best) cls=' class="best"';
        }
      }
      const inner = key==='price' && txt==='待查'
        ? `<a href="${gsearch(m)}" target="_blank" rel="noopener">🔍 搜價</a>`
        : (key==='price'
            ? `${esc(txt)} <a href="${gsearch(m)}" target="_blank" rel="noopener">🔍</a>`
            : esc(txt));
      return `<td${cls}>${inner}</td>`;
    })]);
  }
  body.innerHTML=`<table><thead><tr>${rows[0].map(h=>`<th>${h}</th>`).join('')}</tr></thead>
    <tbody>${rows.slice(1).map(r=>`<tr>${r.join('')}</tr>`).join('')}</tbody></table>`;
}

function removeSel(id){
  selected.delete(id);
  const panel=document.getElementById('panel');
  if(selected.size<2) panel.classList.remove('open');
  updateUI();
}

function copyCompare(){
  const list=selectedModels();
  if(list.length<2){ alert('請先揀至少 2 個型號'); return; }
  const lines=['屬性\t'+list.map(m=>m.brand+' '+m.model).join('\t')];
  for(const [k,l] of FIELDS){
    lines.push(l+'\t'+list.map(m=>{const v=m[k]; return (v===null||v==='')?'待查':String(v);}).join('\t'));
  }
  navigator.clipboard.writeText(lines.join('\n')).then(()=>{
    alert('✅ 已複製對比結果！可以直接貼去 Excel / WhatsApp / 記事簿');
  }).catch(()=>{
    prompt('請手動複製以下內容：', lines.join('\n'));
  });
}

function clearAll(){selected.clear(); updateUI();}
function selectType(ty){
  const arr=sortModels(ALL.filter(matches)).filter(m=>m.type===ty);
  selected=new Set(arr.map(m=>m.brand+'|'+m.model));
  updateUI();
}
function closePanel(restoreFocus){ setPanelOpen(false, restoreFocus !== false); }
// Escape 關閉對比面板（基本鍵盤可用性）
document.addEventListener('keydown',(e)=>{
  if(e.key!=='Escape') return;
  const panel=document.getElementById('panel');
  if(panel && panel.classList.contains('open')){ e.preventDefault(); closePanel(true); }
});

// 將 markdown 表格包裝成可橫向捲動容器（手機友好）
document.addEventListener('DOMContentLoaded', ()=>{
  // 重置會殘留於 reload 嘅 checkbox（瀏覽器會保留 checked 狀態）
  const fso = document.getElementById('fSelOnly');
  if(fso) fso.checked = false;
  // 保留每個歷史節點，只為更新日誌新增日期卡片。
  const journalHeading=[...document.querySelectorAll('.md-content h2')]
    .find(h=>h.textContent.trim()==='📅 更新日誌');
  if(journalHeading){
    const journal=document.createElement('section');
    journal.className='changelog';
    journalHeading.parentNode.insertBefore(journal,journalHeading);
    let node=journalHeading;
    while(node){
      const next=node.nextSibling;
      if(node!==journalHeading && node.nodeType===1 && /^(H1|H2)$/.test(node.tagName)) break;
      journal.appendChild(node);
      node=next;
    }
    let entry=null;
    [...journal.childNodes].forEach(child=>{
      if(child.nodeType===1 && child.tagName==='H3'){
        entry=document.createElement('article');
        entry.className='changelog-entry';
        journal.insertBefore(entry,child);
      }
      if(entry) entry.appendChild(child);
    });
  }
  document.querySelectorAll('.md-content table').forEach(t=>{
    if(!t.closest('.table-scroll')){
      const w=document.createElement('div');
      w.className='table-scroll';
      t.parentNode.insertBefore(w,t);
      w.appendChild(t);
    }
  });
  // GitHub 風格 slug（同 md 目錄錨點一致）：移除非文字/emoji，空格→-
  function slugify(t){
    return t.toLowerCase()
      .replace(/[^\p{Script=Han}A-Za-z0-9\s-]/gu, '')
      .replace(/\s+/g, '-');
  }
  // 導覽用 id（頂部導覽列）
  const navMap={'統合對比總表':'table','官方驗證結果':'verify','能源標籤分析':'energy',
    '排名':'rank','最終推薦':'recommend','價格驗證':'price','品牌官網核實':'official','論壇討論精華':'forum','資料來源':'source',
    '定頻 vs 變頻':'compare-base'};
  // 為所有章節標題加 slug id（md 目錄超鏈接用）+ 導覽 id（雙錨點並存）
  const used = new Set();
  document.querySelectorAll('.md-content h1,.md-content h2,.md-content h3').forEach(h=>{
    const t=h.textContent.trim();
    let slug = slugify(t);
    let base = slug, k = 1;
    while(used.has(slug)){ slug = base + '-' + (k++); }
    used.add(slug);
    // 搵導覽 id
    let navId = null;
    for(const [kw,id] of Object.entries(navMap)){
      if(t.includes(kw)){ navId = id; }
    }
    if(navId && navId !== slug){
      // 標題用導覽 id，前面加 slug 錨點 span（md 目錄可跳）
      h.id = navId;
      const sp = document.createElement('span');
      sp.id = slug;
      sp.style.cssText = 'display:block; position:relative; top:-70px; height:0;';
      h.parentNode.insertBefore(sp, h);
    } else {
      h.id = slug;
    }
    h.style.scrollMarginTop = '70px';
  });
  // 修正 md 目錄連結：手寫 anchor 同實際 slug 有出入時自動匹配最接近 id
  function findCloseId(hashId){
    const key = hashId.replace(/-/g, '').toLowerCase();
    const allIds = [...document.querySelectorAll('[id]')].map(e=>e.id).filter(Boolean);
    const exact = allIds.find(id => id.replace(/-/g,'').toLowerCase() === key);
    if(exact) return exact;
    // 反向前綴：實際 id 以連結 key 開頭（連結文字係標題簡寫）
    const pre = allIds.filter(id => id.replace(/-/g,'').toLowerCase().startsWith(key));
    if(pre.length) return pre[0];
    return null;
  }
  document.querySelectorAll('.md-content a[href^="#"]').forEach(a=>{
    const id=(a.getAttribute('href')||'').slice(1);
    if(id && !document.getElementById(id)){
      const fixed = findCloseId(id);
      if(fixed) a.setAttribute('href', '#' + fixed);
    }
  });
  // Scrollspy：滾動時高亮導覽列當前章節
  const navLinks=[...document.querySelectorAll('.topnav a[href^="#"]')];
  let spyCur=null;
  function scrollSpy(){
    const y=window.scrollY + 110;
    let cur=null;
    for(const a of navLinks){
      const el=document.getElementById((a.getAttribute('href')||'').slice(1));
      if(!el) continue;
      const top=el.getBoundingClientRect().top + window.scrollY;
      if(top <= y) cur=a;
    }
    navLinks.forEach(a=>{
      a.style.color='';
      a.classList.toggle('active', a===cur);
    });
    // 只橫向捲動導覽列本身，唔好牽連頁面上下位置
    if(cur && cur!==spyCur){
      const navWrap=document.querySelector('.topnav .wrap');
      if(navWrap && navWrap.scrollWidth > navWrap.clientWidth){
        const wr=navWrap.getBoundingClientRect();
        const cr=cur.getBoundingClientRect();
        navWrap.scrollTo({left: navWrap.scrollLeft + (cr.left + cr.width/2) - (wr.left + wr.width/2), behavior:'smooth'});
      }
    }
    spyCur=cur;
  }
  let spyTicking=false;
  window.addEventListener('scroll',()=>{
    if(!spyTicking){ spyTicking=true; requestAnimationFrame(()=>{scrollSpy(); spyTicking=false;}); }
  },{passive:true});
  scrollSpy();
  // 自製平滑捲動（rAF 動畫，所有環境可靠）
  function smoothScrollTo(y){
    const start = window.scrollY;
    const diff = y - start;
    if(Math.abs(diff) < 2) return;
    const dur = Math.min(600, Math.max(250, Math.abs(diff) * 0.3));
    const t0 = performance.now();
    function step(t){
      const p = Math.min(1, (t - t0) / dur);
      const e = p < .5 ? 2*p*p : 1 - Math.pow(-2*p+2, 2)/2;
      window.scrollTo({top: start + diff * e, behavior:'instant'});
      if(p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
    // 保險：若 rAF 被節流（如背景分頁）而冇進展，直接跳轉
    setTimeout(()=>{
      if(Math.abs(window.scrollY - start) < 2){
        window.scrollTo({top: y, behavior:'instant'});
      }
    }, 400);
  }
  // 錨點連結平滑定位（md 內容內嘅 # 連結統一處理，確保任何環境都可跳轉）
  document.addEventListener('click', (e)=>{
    const a = e.target.closest('.md-content a[href^="#"]');
    if(!a) return;
    const id = (a.getAttribute('href')||'').slice(1);
    let el = document.getElementById(id);
    if(!el) return;
    e.preventDefault();
    // 若係零高度錨點 span，改為捲動到後面實際標題
    if(el.offsetHeight === 0 && el.nextElementSibling) el = el.nextElementSibling;
    const y = el.getBoundingClientRect().top + window.scrollY - 70;
    smoothScrollTo(Math.max(0, y));
    try{ history.replaceState(null, '', '#'+id); }catch(_){}
  });
  // 頂部導覽列：同樣用自製平滑捲動
  document.querySelectorAll('.topnav a').forEach(a=>{
    a.addEventListener('click', (e)=>{
      const href = a.getAttribute('href')||'';
      if(!href.startsWith('#')) return;
      const el = document.getElementById(href.slice(1));
      if(!el) return;
      e.preventDefault();
      const y = el.getBoundingClientRect().top + window.scrollY - 58;
      smoothScrollTo(Math.max(0, y));
    });
  });
});

