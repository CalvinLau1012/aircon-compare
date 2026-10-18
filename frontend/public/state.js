
const MODELS = __MODELS_JSON__;
const EMSD_EXTRA = __EMSD_JSON__;
const ALL = MODELS.concat(EMSD_EXTRA);
const FIELDS = __FIELDS_JSON__;
let selected = new Set();
let shown = 60;

function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
// Google 搜價：完整品牌+型號+價錢，用戶喺自己瀏覽器搜最新價
function gsearch(m){return 'https://www.google.com/search?q='+encodeURIComponent(m.brand+' '+m.model+' 價錢');}
function resetShown(){shown = 60;}

