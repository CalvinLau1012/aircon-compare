// ===== 部署資訊（治理文檔 §7.2.7：只讀取已部署 metadata.json） =====
// Last Deploy：metadata.json.deployTime（UTC）→ HKT 顯示
// Last Update：metadata.json.datasetDate；載入失敗顯示「暫不可用」，唔回退硬編舊值
(function loadDeployInfo(){
  const el = document.getElementById('deployInfo');
  const foot = document.getElementById('footStatus');
  const ver = document.getElementById('verInfo');
  fetch('metadata.json', {cache:'no-store'})
    .then(r => { if(!r.ok) throw new Error('http '+r.status); return r.json(); })
    .then(m => {
      if (ver && m.version) ver.textContent = 'v' + m.version;
      const ds = m.datasetDate;
      const dt = m.deployTime;
      const sb = (m.sourceStatus && m.sourceStatus.biggo) || {};
      const hkt = (v) => {
        const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z$/.exec(String(v || ''));
        if (!m) return '';
        const y=+m[1], mo=+m[2], d=+m[3], hh=+m[4], mi=+m[5], ss=+m[6];
        if (y < 1 || mo < 1 || mo > 12 || d < 1 || d > 31 || hh > 23 || mi > 59 || ss > 59) return '';
        const raw = new Date(Date.UTC(y, mo-1, d, hh, mi, ss));
        if (raw.getUTCFullYear() !== y || raw.getUTCMonth() !== mo-1 || raw.getUTCDate() !== d
            || raw.getUTCHours() !== hh || raw.getUTCMinutes() !== mi || raw.getUTCSeconds() !== ss) return '';
        const shifted = new Date(raw.getTime() + 8*3600*1000);
        if (!isFinite(shifted.getTime()) || shifted.getUTCFullYear() > 9999) return '';
        return shifted.toISOString().slice(0,16).replace('T',' ');
      };
      const acq = hkt(sb.lastAcquiredAt), app = hkt(sb.lastAppliedAt);
      let biggoText = '🛒 BigGo 更新時間未確認（分批快照）';
      if (sb.status === 'ok' && acq) {
        biggoText = '🛒 BigGo 最近成功更新 ' + acq + ' HKT（分批快照）' + (app ? ('；已套用 ' + app + ' HKT') : '');
      } else if (sb.status === 'ok' && app) {
        biggoText = '🛒 BigGo 取得時間未確認；已套用 ' + app + ' HKT（分批快照，非全部同時查價）';
      } else if (sb.status === 'degraded' || sb.status === 'blocked' || sb.status === 'pending') {
        const head = sb.status === 'degraded' ? '⚠️ BigGo 價格來源降級'
          : (sb.status === 'blocked' ? '⛔ BigGo 價格來源受阻' : '⏳ BigGo 本輪待更新');
        biggoText = head + (acq ? ('；先前快照取得 ' + acq + ' HKT') : (app ? ('；已套用 ' + app + ' HKT（取得時間未確認）') : ''))
          + ' · 價格更新未完成，沿用先前已驗證快照';
      } else if (acq || app) {
        biggoText = '🛒 BigGo 更新時間 ' + (acq || app) + ' HKT（分批快照）';
      }
      let line = (ds ? ('📅 EMSD 資料日期 ' + ds) : '📅 EMSD 資料日期暫不可用');
      if (m.version) line += ' · v' + m.version;
      const deployHkt = hkt(dt);
      if (deployHkt) {
        const pub = (m.sourceStatus && m.sourceStatus.publish) || {};
        const label = (m.sourceStatus && !pub.deployedAt) ? '最後封裝' : '最後部署';
        line += ' · 📦 ' + label + ' ' + deployHkt + ' HKT';
      } else {
        line += ' · 🔄 每日偵測新機 · 有機先更新';
      }
      if (el) el.textContent = line + '\n' + biggoText;
      if (foot) foot.textContent = '🔄 每日 00:30 偵測新機（EMSD 官方資料庫；GitHub Actions 排程屬預定觸發；高負載可延遲或丟棄，非準時保證）· 有新機先分批核實更新 · '
        + (ds ? ('資料日期 ' + ds) : '資料日期暫不可用') + ' · ' + biggoText
        + ' · 部署時間為封包時間（HKT 顯示）· 價錢為快照僅供參考';
    })
    .catch(() => {
      if (el) el.textContent = '📅 資料日期暫不可用 · 部署資訊暫不可用';
      if (ver) ver.textContent = 'v?';
    });
})();
