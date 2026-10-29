// 공통 유틸
async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const res = await fetch(url, opt);
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.detail) || `오류 (${res.status})`);
  return data;
}

function busy(on, msg) {
  const el = document.getElementById("busy");
  el.querySelector(".msg").textContent = msg || "처리 중입니다...";
  el.classList.toggle("on", on);
}

function toast(msg, isErr) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.toggle("err", !!isErr);
  el.classList.add("on");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("on"), isErr ? 6000 : 2500);
}

// 오래 걸리는 작업 실행 (경과 시간 표시)
async function runLong(msg, fn) {
  const start = Date.now();
  busy(true, msg);
  const sub = document.querySelector("#busy .sub");
  const timer = setInterval(() => { sub.textContent = `경과 ${Math.round((Date.now() - start) / 1000)}초 · 문서 분량에 따라 수 분 걸릴 수 있습니다`; }, 1000);
  try { return await fn(); }
  catch (e) { toast(e.message, true); throw e; }
  finally { clearInterval(timer); sub.textContent = ""; busy(false); }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// 페이지 이동 없이 파일 내려받기
function download(url) {
  const a = document.createElement("a");
  a.href = url; a.download = "";
  document.body.appendChild(a); a.click(); a.remove();
}

async function downloadPost(url, body) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  if (!res.ok) { let d = {}; try { d = await res.json(); } catch (e) {} throw new Error(d.detail || `오류 (${res.status})`); }
  const cd = res.headers.get("content-disposition") || "";
  const m = cd.match(/filename\*=UTF-8''([^;]+)/) || cd.match(/filename="?([^";]+)"?/);
  const blobUrl = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = blobUrl; a.download = m ? decodeURIComponent(m[1]) : "download";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 5000);
}

// ---------------- 새로고침 없는 화면 전환 ----------------
// 서버가 렌더링한 페이지를 받아 #sidebar 와 #content 만 교체한다.
// 페이지 스크립트는 <script type="text/x-page"> 로 두고 교체 후 함수 스코프로 실행한다.
const Router = {
  _seq: 0,

  async go(url, { push = true, keepScroll = false } = {}) {
    const seq = ++this._seq;
    const bar = document.getElementById("navprogress");
    bar.classList.add("on");
    try {
      const res = await fetch(url, { headers: { "X-Requested-With": "router" } });
      const html = await res.text();
      if (seq !== this._seq) return; // 더 최근 이동이 있으면 무시
      const doc = new DOMParser().parseFromString(html, "text/html");
      const content = doc.getElementById("content");
      if (!content) { location.href = url; return; }
      if (push) history.pushState({}, "", res.redirected ? res.url : url);
      else if (res.redirected) history.replaceState({}, "", res.url);
      document.title = doc.title;
      document.getElementById("sidebar").innerHTML = doc.getElementById("sidebar").innerHTML;
      document.getElementById("content").innerHTML = content.innerHTML;
      if (!keepScroll) window.scrollTo(0, 0);
      this.runPageScripts();
      if (!res.ok) toast(`페이지를 불러오지 못했습니다 (${res.status})`, true);
    } catch (e) {
      toast("페이지를 불러오지 못했습니다: " + e.message, true);
    } finally {
      if (seq === this._seq) bar.classList.remove("on");
    }
  },

  // 현재 화면 상태는 두고 사이드바만 갱신 (진행률·D-day 등)
  async refreshSidebar() {
    try {
      const html = await (await fetch(location.pathname)).text();
      const side = new DOMParser().parseFromString(html, "text/html").getElementById("sidebar");
      if (side) document.getElementById("sidebar").innerHTML = side.innerHTML;
    } catch (e) {}
  },

  reload() { return this.go(location.pathname + location.search, { push: false, keepScroll: true }); },

  runPageScripts() {
    document.querySelectorAll('#content script[type="text/x-page"]').forEach(old => {
      const s = document.createElement("script");
      s.textContent = `(() => {\n${old.textContent}\n})();`;
      old.replaceWith(s);
    });
  },

  _eligible(a, e) {
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return false;
    if (a.target || a.hasAttribute("download") || a.dataset.noRouter !== undefined) return false;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) return false;
    if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/static/")) return false;
    if (url.pathname === location.pathname && url.hash) return false;
    return true;
  },
};

document.addEventListener("click", e => {
  const a = e.target.closest("a[href]");
  if (!Router._eligible(a, e)) return;
  e.preventDefault();
  Router.go(a.href);
});
window.addEventListener("popstate", () => Router.go(location.href, { push: false }));
document.addEventListener("DOMContentLoaded", () => Router.runPageScripts());

// ---------------- 워크스페이스 생성 대화상자 ----------------
const App = {
  newWorkspace() {
    const dlg = document.getElementById("ws-dialog");
    dlg.querySelector("form").reset();
    dlg.showModal();
    dlg.querySelector("[name=name]").focus();
  },
  async submitWorkspace(form) {
    const data = Object.fromEntries(new FormData(form));
    if (!data.name.trim()) return toast("워크스페이스 이름을 입력하세요", true);
    try {
      const p = await api("POST", "/api/projects", data);
      document.getElementById("ws-dialog").close();
      toast("워크스페이스를 만들었습니다");
      Router.go(`/workspaces/${p.id}/step1`);
    } catch (e) { toast(e.message, true); }
  },
};

// 드롭 영역 연결: 클릭하면 파일 선택, 여러 파일 드래그앤드롭 허용
function bindDrop(drop, input, onFiles) {
  drop.onclick = () => input.click();
  input.onchange = () => { onFiles([...input.files]); input.value = ""; };
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); onFiles([...e.dataTransfer.files]); };
}

// 파일명으로 분류: 공고문 여부 + 연도(가장 최근 연도 = 현재, 그보다 이전 = 과거)
function classifyFiles(files) {
  const yearOf = name => {
    const ys = [...name.matchAll(/(20\d{2})|(?:^|[^\d])(\d{2})년/g)].map(m => +(m[1] || "20" + m[2]));
    return ys.length ? Math.max(...ys) : null;
  };
  const years = files.map(f => yearOf(f.name)).filter(Boolean);
  const latest = years.length ? Math.max(...years) : null;
  return files.map(f => {
    const n = f.name;
    const notice = /공고/.test(n) && !/제안요청|과업지시|과업내용/.test(n);
    const y = yearOf(n);
    const past = /전년|작년|과거|기존|이전/.test(n) || (y && latest && y < latest);
    const kind = notice ? (past ? "past_notice" : "notice") : (past ? "past_rfp" : "current_rfp");
    return { file: f, kind };
  });
}

// 업로드 영역: <div class="uploader" data-pid data-kind data-label data-files>
// 같은 화면의 업로드 영역끼리 파일 분류를 서로 옮길 수 있다.
function initUploaders(onChange) {
  const boxes = [...document.querySelectorAll("#content .uploader")];
  if (!boxes.length) return null;
  const pid = boxes[0].dataset.pid;
  const kinds = boxes.map(b => [b.dataset.kind, b.dataset.label || b.dataset.kind]);
  const renderers = {};

  const renderAll = files => {
    Object.entries(renderers).forEach(([k, r]) => r(files[k] || []));
    onChange && onChange(files);
  };

  boxes.forEach(box => {
    const kind = box.dataset.kind;
    const list = box.querySelector(".filelist");
    renderers[kind] = names => {
      const count = box.querySelector(".count");
      if (count) count.textContent = names.length ? `${names.length}개` : "";
      list.innerHTML = names.map(n => `<li>
        <span>📄 ${esc(n)}</span>
        <span class="file-ops">
          ${kinds.length > 1 ? `<select title="분류 변경" data-n="${esc(n)}">${kinds.map(([k, l]) => `<option value="${k}" ${k === kind ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>` : ""}
          <button title="삭제" data-n="${esc(n)}">✕</button>
        </span></li>`).join("");
      list.querySelectorAll("button").forEach(b => b.onclick = async () => {
        if (!confirm(`${b.dataset.n} 파일을 삭제할까요?`)) return;
        try { renderAll(await api("DELETE", `/api/projects/${pid}/files/${kind}/${encodeURIComponent(b.dataset.n)}`)); }
        catch (e) { toast(e.message, true); }
      });
      list.querySelectorAll("select").forEach(s => s.onchange = async () => {
        try { renderAll(await api("POST", `/api/projects/${pid}/files/${kind}/${encodeURIComponent(s.dataset.n)}/move`, { to: s.value })); toast("분류를 바꿨습니다"); }
        catch (e) { toast(e.message, true); s.value = kind; }
      });
    };
    bindDrop(box.querySelector(".drop"), box.querySelector("input[type=file]"), files => uploadMany({ [kind]: files }));
    renderers[kind](JSON.parse(box.dataset.files || "[]"));
  });

  // { kind: [File, ...] } 를 분류별로 업로드
  async function uploadMany(byKind) {
    const entries = Object.entries(byKind).filter(([, fs]) => fs.length);
    if (!entries.length) return;
    busy(true, "업로드 중...");
    try {
      let files;
      for (const [kind, fs] of entries) {
        const fd = new FormData();
        fs.forEach(f => fd.append("files", f));
        files = await api("POST", `/api/projects/${pid}/files/${kind}`, fd);
      }
      renderAll(files);
      toast(`${entries.reduce((n, [, fs]) => n + fs.length, 0)}개 파일 업로드 완료`);
    } catch (e) { toast(e.message, true); }
    finally { busy(false); }
  }
  return { uploadMany };
}
