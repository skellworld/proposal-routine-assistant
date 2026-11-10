// 공통 유틸
async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const res = await fetch(url, opt);
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  // 세션이 끝났거나 최초 설정이 남아 있으면 해당 화면으로 보낸다
  if (res.status === 401 && !url.startsWith("/api/auth/") && !url.startsWith("/api/setup")) location.href = "/login";
  if (res.status === 403 && data && /AI 연결 설정/.test(data.detail || "")) location.href = "/welcome";
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

// ---------------- AI 연결 여부에 따른 버튼 잠금 ----------------
// AI가 필요한 버튼·입력에는 data-ai 속성을 붙인다. AI 연결 안 함이면 꺼지고 본문 위에 안내가 붙는다.
function aiOn() {
  const f = document.getElementById("ai-flag");
  return !f || f.dataset.on === "1";
}

function applyAiGuard() {
  if (aiOn()) return;
  const els = document.querySelectorAll("#content [data-ai]");
  els.forEach(el => {
    el.disabled = true;
    if (el.type === "checkbox") el.checked = false;
    el.title = "AI가 연결되어 있지 않아 쓸 수 없습니다 — ⚙ 내 AI 연결에서 설정";
  });
  const content = document.getElementById("content");
  if (els.length && content && !content.querySelector(".ai-off-note")) {
    const note = document.createElement("div");
    note.className = "notice-done notice-err ai-off-note";
    note.style.marginBottom = "18px";
    note.innerHTML = `<span>🔌 AI가 연결되어 있지 않아 AI 분석 기능(분석 실행·추천·추출·구조화)은 쓸 수 없습니다. 파일 업로드·체크리스트·목차 편집·PPT 생성 등은 그대로 쓸 수 있습니다.</span>
      <a class="btn sm" href="/settings">⚙ AI 연결하기</a>`;
    const head = content.querySelector(".ws-head");
    head ? head.after(note) : content.prepend(note);
  }
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
    applyAiGuard();
  },

  _eligible(a, e) {
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return false;
    // 로그인·시작하기 화면처럼 사이드바/본문 틀이 없는 페이지에서는 일반 링크로 이동한다
    if (!document.getElementById("content") || !document.getElementById("sidebar")) return false;
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
document.addEventListener("DOMContentLoaded", () => {
  Router.runPageScripts();
  if (document.getElementById("sidebar")) Jobs.watch();  // 로그인·최초 설정 화면에서는 돌지 않는다
});

// ---------------- 백그라운드 분석 알림 ----------------
// 어느 페이지에 있든 진행 중인 RFP 분석이 끝나면 알리고 사이드바를 갱신한다.
const Jobs = {
  _known: new Map(),  // pid -> 워크스페이스 이름
  _timer: null,

  watch() {
    if (!this._timer) this._check();
  },

  async _check() {
    this._timer = null;
    let list;
    try { list = (await api("GET", "/api/jobs")).filter(j => j.kind === "analyze"); } catch (e) { list = null; }
    if (list) {
      const now = new Map(list.map(j => [j.pid, j.name]));
      for (const [pid, name] of this._known) {
        if (now.has(pid)) continue;
        // 해당 1단계 화면을 보고 있으면 그 화면이 결과를 직접 표시한다
        if (location.pathname !== `/workspaces/${pid}/step1`) {
          toast(`'${name}' RFP 분석이 끝났습니다 — 1단계에서 확인하세요`);
          Router.refreshSidebar();
        }
      }
      this._known = now;
    }
    if (!list || list.length) this._timer = setTimeout(() => this._check(), 5000);
  },
};

// ---------------- 워크스페이스 생성 대화상자 ----------------
const App = {
  async logout() {
    try { await api("POST", "/api/auth/logout"); } catch (e) {}
    location.href = "/login";
  },
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

// ---------------- 내 AI 연결 설정 폼 (최초 설정 화면·설정 페이지 공용) ----------------
// opts: { me, local, cli, saveLabel, onSaved(me) }
function AiForm(root, opts) {
  const me = opts.me;
  const P = {
    worker: { t: "내 PC의 Claude Code (Pro·Max 구독) — 추천", d: "설치 파일 하나만 실행하면 끝납니다. 내가 실행한 분석을 내 PC의 Claude 구독으로 처리하고, 결과는 팀 워크스페이스에 공유됩니다. 추가 비용이 없습니다." },
    anthropic: { t: "Claude (Anthropic)", d: "본인의 Anthropic API 키로 분석합니다. 사용한 만큼 본인 계정에 과금됩니다.",
                 link: "https://console.anthropic.com/settings/keys", ph: "sk-ant-...", pick: ids => ids.find(i => i === "claude-opus-5-5") || ids[0] },
    openai:    { t: "ChatGPT (OpenAI)", d: "본인의 OpenAI API 키로 분석합니다. 사용한 만큼 본인 계정에 과금됩니다.",
                 link: "https://platform.openai.com/api-keys", ph: "sk-...", pick: ids => ids.find(i => /^gpt-\d/.test(i) && !/mini|nano/.test(i)) || ids[0] },
  };
  if (opts.local) P.cli = { t: "서버 PC의 Pro 계정 (Claude Code)", d: "서버 PC에 로그인된 Claude Code 구독으로 분석합니다. 서버 PC에서 직접 접속할 때만 쓸 수 있습니다." };
  P.none = { t: "AI 연결 안 함", d: "AI 없이 씁니다. 분석 실행·전략 추천·목차 추출·템플릿 구조화 버튼은 꺼지고, 팀원이 만든 분석 결과 보기·파일 업로드·체크리스트·목차 편집·PPT 생성은 그대로 쓸 수 있습니다. 나중에 언제든 연결할 수 있습니다." };
  let provider = me.ai.provider && P[me.ai.provider] ? me.ai.provider : "worker";
  let models = [];

  root.innerHTML = `
    <div class="opt-list">${Object.entries(P).map(([k, p]) => `
      <label class="opt"><input type="radio" name="ai-provider" value="${k}">
        <div><div class="t">${p.t}</div><div class="muted">${p.d}</div></div></label>`).join("")}</div>
    <div class="ai-key form-grid" style="margin-top:16px">
      <label><span>API 키 <span class="ai-link"></span></span>
        <div class="key-row"><input type="password" class="ai-keyin" autocomplete="off">
          <button type="button" class="btn ai-check">키 확인</button></div></label>
      <label>모델<select class="ai-model"><option value="">먼저 [키 확인]을 눌러 주세요</option></select></label>
      <p class="muted" style="margin:0">Claude Pro·ChatGPT Plus 같은 채팅 구독과 API 결제는 별개입니다. API 콘솔에서 결제 수단(크레딧)을 등록해야 키가 동작합니다.
        키는 서버에 암호화해 저장하고, 본인이 실행한 분석에만 씁니다.</p>
    </div>
    <div class="ai-cli" style="margin-top:16px"></div>
    <div class="ai-worker" style="margin-top:16px">
      <div class="status"><span class="k">내 PC 실행기</span><span class="ai-wstat muted">확인 중...</span></div>
      <div style="margin:12px 0">
        <button type="button" class="btn primary ai-install">⬇ 설치 파일 받기 (Windows)</button>
        <span class="muted ai-tokinfo" style="margin-left:8px"></span>
      </div>
      <ol class="worker-steps">
        <li>내려받은 <b>proposal-worker-setup.cmd</b>를 더블클릭합니다. ("Windows의 PC 보호" 창이 뜨면 <b>추가 정보 → 실행</b>)</li>
        <li>Python·Claude Code가 없으면 자동으로 설치합니다. 처음 한 번만 브라우저에서 <b>Claude 계정 로그인</b>을 승인하세요.</li>
        <li>설치가 끝나면 이 화면이 <b>● 연결됨</b>으로 바뀌고 자동으로 연결 테스트를 합니다.</li>
      </ol>
      <p class="muted" style="margin:0">실행기는 창 없이 백그라운드에서 돌고, PC를 켤 때마다 자동으로 실행됩니다. 서버로 나가는 연결만 쓰므로 방화벽·포트 설정이 필요 없습니다.
        제거: <code>%LOCALAPPDATA%\\ProposalWorker\\uninstall.cmd</code></p>
      <details style="margin-top:8px"><summary class="muted">직접 설치 (macOS·Linux 등)</summary>
        <ol class="worker-steps">
          <li>Claude Code 설치 후 <code>claude auth login</code></li>
          <li><a href="/api/me/worker/script" download>⬇ proposal-worker.py</a> 내려받기 (Python 3.9+)</li>
          <li><button type="button" class="btn sm ai-token">🔑 토큰 발급</button> 후 표시되는 명령 실행
            <div class="ai-cmd"></div></li>
        </ol>
      </details>
    </div>
    <div style="display:flex;gap:8px;align-items:center;margin-top:16px">
      <button type="button" class="btn primary ai-save"></button>
      <span class="ai-res muted"></span>
    </div>`;
  const $ = s => root.querySelector(s);
  const keyIn = $(".ai-keyin"), sel = $(".ai-model"), res = $(".ai-res");

  function fillModels(ids, current) {
    models = ids;
    sel.innerHTML = ids.length ? ids.map(i => `<option>${esc(i)}</option>`).join("") : `<option value="">사용할 수 있는 모델이 없습니다</option>`;
    sel.value = ids.includes(current) ? current : (P[provider].pick(ids) || "");
  }

  function render() {
    root.querySelectorAll(".opt").forEach(o => {
      const i = o.querySelector("input"); i.checked = i.value === provider; o.classList.toggle("on", i.checked);
    });
    const api = provider === "anthropic" || provider === "openai";
    $(".ai-key").style.display = api ? "" : "none";
    $(".ai-cli").style.display = provider === "cli" ? "" : "none";
    $(".ai-worker").style.display = provider === "worker" ? "" : "none";
    $(".ai-save").textContent = provider === "none" ? "저장 (AI 없이 사용)" : (opts.saveLabel || "저장하고 연결 테스트");
    if (provider === "worker") {
      $(".ai-tokinfo").textContent = me.worker_token ? `발급됨 (${me.worker_issued_at}) · 다시 발급하면 이전 토큰은 끊깁니다` : "";
      checkWorker();
    } else if (api) {
      const saved = me.ai.provider === provider && me.ai.key_hint;
      keyIn.placeholder = saved ? `저장된 키 ${me.ai.key_hint} — 바꾸려면 새 키 입력` : P[provider].ph;
      $(".ai-link").innerHTML = `· <a href="${P[provider].link}" target="_blank" rel="noopener">키 발급 페이지 ↗</a>`;
      if (saved && me.ai.model) fillModels([me.ai.model], me.ai.model);
      else { models = []; sel.innerHTML = `<option value="">먼저 [키 확인]을 눌러 주세요</option>`; }
    } else if (provider === "cli") {
      const c = opts.cli || {};
      $(".ai-cli").innerHTML = `<div class="status"><span class="k">Claude Code</span><span>${c.installed ? '<span class="ok">설치됨</span>' : '<span class="bad">설치 안 됨</span>'}</span>
        <span class="k">로그인</span><span>${c.loggedIn ? `<span class="ok">로그인됨</span> ${esc(c.email || "")} ${esc((c.subscriptionType || "").toUpperCase())}` : '<span class="bad">로그인 필요</span>'}</span></div>
        ${c.loggedIn ? "" : `<p class="muted">서버 PC의 PowerShell에서 로그인하세요.</p><code class="cmd">&amp; "$env:USERPROFILE\\.local\\bin\\claude.exe" auth login</code>`}`;
    }
  }

  root.querySelectorAll(".opt input").forEach(i => i.onchange = () => { provider = i.value; res.textContent = ""; render(); });

  // 실행기 연결 상태: 이 화면이 열려 있고 '내 PC' 방식을 보고 있는 동안 5초마다 확인
  let wTimer = null;
  async function checkWorker() {
    clearTimeout(wTimer);
    if (!document.contains(root) || provider !== "worker") return;
    try {
      const w = await api("GET", "/api/me/worker");
      $(".ai-wstat").innerHTML = w.online ? '<span class="ok">● 연결됨</span> — 분석을 내 PC의 Claude Code로 처리할 수 있습니다'
        : (w.token ? '<span class="bad">● 꺼져 있음</span> — 설치 파일을 실행했는지, PC가 켜져 있는지 확인하세요' : '<span class="muted">설치 파일을 받아 실행해 주세요</span>');
      if (w.online && !w.token) me.worker_token = true;
      // 실행기가 처음 연결되면 저장·연결 테스트까지 자동으로 진행한다
      if (w.online && me.ai.provider !== "worker" && !autoSaved) { autoSaved = true; $(".ai-save").click(); }
    } catch (e) {}
    wTimer = setTimeout(checkWorker, 3000);
  }
  let autoSaved = false;
  $(".ai-install").onclick = () => {
    if (me.worker_token && !confirm("설치 파일을 새로 받으면 새 연결 토큰이 발급되어, 이전에 설치한 실행기는 끊깁니다(이 PC에서 새 설치 파일을 실행하면 됩니다). 계속할까요?")) return;
    download("/api/me/worker/installer");
    me.worker_token = true; me.worker_issued_at = "방금"; autoSaved = false;
    $(".ai-tokinfo").textContent = "내려받은 파일을 실행하세요 — 연결되면 자동으로 다음 단계로 넘어갑니다";
  };
  $(".ai-token").onclick = async () => {
    if (me.worker_token && !confirm("토큰을 다시 발급할까요? 이전 토큰으로 켜 둔 실행기는 연결이 끊깁니다.")) return;
    try {
      const r = await api("POST", "/api/me/worker/token");
      me.worker_token = true; me.worker_issued_at = "방금";
      $(".ai-tokinfo").textContent = "새 토큰은 이 화면에서 한 번만 보입니다";
      $(".ai-cmd").innerHTML = `<code class="cmd">python proposal-worker.py --server ${esc(location.origin)} --token ${esc(r.token)}</code>
        <span class="muted">처음 한 번만 이렇게 실행하면 설정이 저장되어, 다음부터는 <code>python proposal-worker.py</code> 만 실행하면 됩니다.</span>`;
    } catch (e) { toast(e.message, true); }
  };

  $(".ai-check").onclick = async () => {
    res.className = "ai-res muted"; res.textContent = "키 확인 중...";
    try {
      const r = await api("POST", "/api/me/ai/models", { provider, key: keyIn.value.trim() });
      fillModels(r.models, me.ai.provider === provider ? me.ai.model : "");
      res.className = "ai-res ok"; res.textContent = `✔ 키 확인됨 · 모델 ${r.models.length}개`;
    } catch (e) { res.className = "ai-res bad"; res.textContent = "✖ " + e.message; }
  };

  $(".ai-save").onclick = async () => {
    const usesKey = provider === "anthropic" || provider === "openai";
    const body = { provider, model: usesKey ? sel.value : "", key: usesKey ? keyIn.value.trim() : "" };
    if (usesKey && !body.key && !(me.ai.provider === provider && me.ai.key_hint)) return toast("API 키를 입력하세요", true);
    if (usesKey && !body.model) return toast("[키 확인]을 누르고 모델을 고르세요", true);
    if (provider === "worker" && !me.worker_token) return toast("먼저 [토큰 발급]을 누르고 내 PC에서 실행기를 켜세요", true);
    try {
      const saved = await api("PUT", "/api/me/ai", body);
      Object.assign(me, saved); keyIn.value = "";
      if (provider === "none") {  // 연결 테스트 없이 저장만
        res.className = "ai-res ok"; res.textContent = "✔ 저장됨 — AI 없이 사용합니다";
        render();
        return opts.onSaved && opts.onSaved(me);
      }
      res.className = "ai-res muted"; res.textContent = "저장됨 · 연결 테스트 중...";
      const t = await runLong("AI에 연결하는 중...", () => api("POST", "/api/me/ai/test"));
      res.className = "ai-res ok"; res.textContent = `✔ 연결 성공 (${t.seconds}초): "${t.reply}"`;
      render();
      opts.onSaved && opts.onSaved(me);
    } catch (e) { res.className = "ai-res bad"; res.textContent = "✖ " + e.message; }
  };
  render();
}
