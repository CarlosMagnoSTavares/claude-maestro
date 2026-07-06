/* Claude Maestro — SPA sobre SB Admin 2 (Bootstrap 4). PT-BR, sem build. */
"use strict";

const $id = (s) => document.getElementById(s);
const main = $id("main");
let pollTimer = null;

/* ---------------- utilidades ---------------- */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Registra o poll da tela SEMPRE limpando o anterior (evita timers acumulados,
// que causavam telas piscando e travamento após alguns minutos de uso).
function poll(fn, ms) {
  clearInterval(pollTimer);
  pollTimer = setInterval(fn, ms);
}

// True se o usuário ainda está na view indicada (polls não devem redesenhar outra tela)
function onView(name) {
  return (location.hash.slice(1) || "painel").split("/")[0] === name;
}

async function api(path, opts = {}) {
  if (opts.json !== undefined) {
    opts.method = opts.method || "POST";
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = typeof data.detail === "string" ? data.detail
      : data.detail ? JSON.stringify(data.detail)
      : `Erro ${r.status} na API (${path.split("?")[0]})`;
    throw new Error(msg);
  }
  return data;
}

function toast(msg, isErr = false) {
  const el = document.createElement("div");
  el.className = "m-toast" + (isErr ? " err" : "");
  el.textContent = msg;
  $id("toasts").appendChild(el);
  setTimeout(() => el.remove(), 5200);
}

const STATUS = {
  nova: ["Nova", "secondary"],
  executando: ["Executando", "primary"],
  aguardando: ["Aguardando", "warning"],
  aguardando_aprovacao: ["Aguardando aprovação", "warning"],
  aguardando_limite: ["Aguardando limite (heartbeat)", "info"],
  pausada: ["Pausada pelo gestor", "warning"],
  concluida: ["Concluída", "success"],
  falhou: ["Falhou", "danger"],
  cancelada: ["Cancelada", "secondary"],
  pendente: ["Pendente", "light"],
  bloqueada: ["Bloqueada", "danger"],
  preparando: ["Preparando", "primary"],
  aprovada: ["Aprovada", "success"],
  rejeitada: ["Rejeitada", "danger"],
};

function badge(status) {
  const [label, color] = STATUS[status] || [status, "secondary"];
  const spin = (status === "executando" || status === "preparando")
    ? '<i class="fas fa-circle-notch fa-spin mr-1"></i>' : "";
  return `<span class="badge badge-${color}">${spin}${esc(label)}</span>`;
}

const AUTONOMY = {
  0: ["Somente sugestão", "secondary"],
  1: ["Aprovação obrigatória", "warning"],
  3: ["Autônomo", "danger"],
  4: ["Só se vive uma vez", "dark"],
};
function autonomyBadge(level) {
  const [label, color] = AUTONOMY[level] || AUTONOMY[1];
  return `<span class="badge badge-${color}">${label}</span>`;
}

const MODELS = ["haiku", "sonnet", "opus"];
// versão concreta de cada alias (o CLI resolve o alias para a versão mais recente)
const MODEL_SHORT = { haiku: "Haiku 4.5", sonnet: "Sonnet 5", opus: "Opus 4.8" };
const modelShort = m => MODEL_SHORT[m] || m;
const EFFORTS = ["low", "medium", "high"];

function fmtTs(ts) { return (ts || "").replace("T", " ").slice(5, 16); }
function usd(v) { return "US$ " + (+(v || 0)).toFixed(2); }

function statCard(color, icon, label, value) {
  return `
  <div class="col-xl-3 col-md-6 mb-4">
    <div class="card border-left-${color} shadow h-100 py-2">
      <div class="card-body">
        <div class="row no-gutters align-items-center">
          <div class="col mr-2">
            <div class="text-xs font-weight-bold text-${color} text-uppercase mb-1">${label}</div>
            <div class="h5 mb-0 font-weight-bold text-gray-800">${value}</div>
          </div>
          <div class="col-auto"><i class="fas ${icon} fa-2x text-gray-300"></i></div>
        </div>
      </div>
    </div>
  </div>`;
}

function cardOpen(title, extra = "") {
  return `<div class="card shadow mb-4">
    <div class="card-header py-3 d-flex flex-row align-items-center justify-content-between">
      <h6 class="m-0 font-weight-bold text-primary">${title}</h6>${extra}
    </div>
    <div class="card-body">`;
}
const cardClose = "</div></div>";

/* ---------------- jornada guiada ---------------- */

let journey = { step: 0, complete: true };  // atualizado pelo servidor

async function fetchJourney() {
  try {
    journey = await api("/api/journey");
  } catch { /* servidor reiniciando */ }
  applyJourneyUI();
  return journey;
}

function applyJourneyUI() {
  document.body.classList.toggle("journey-on", !journey.complete);
  $id("navComecar").classList.toggle("d-none", journey.complete);
}

/* ---------------- workspace (empresa ativa) ---------------- */

let companies = [];
let workspaceId = Number(localStorage.getItem("maestro_ws") || 0) || null;
let wsSnapshot = "";

function wsCompany() { return companies.find(c => c.id === workspaceId) || null; }

// sufixo de querystring para filtrar as APIs pelo workspace ativo
function wsQS(sep = "?") { return workspaceId ? `${sep}company_id=${workspaceId}` : ""; }

function wsLabel() {
  const c = wsCompany();
  return c ? c.name : "";
}

async function loadWorkspaces() {
  try { companies = await api("/api/companies"); } catch { return; }
  if (!companies.length) workspaceId = null;
  else if (!companies.some(c => c.id === workspaceId)) workspaceId = companies[0].id;
  renderWorkspaceSwitcher();
}

function renderWorkspaceSwitcher() {
  const box = $id("wsSwitcher");
  if (!box) return;
  const snap = JSON.stringify([companies.map(c => [c.id, c.name]), workspaceId]);
  if (snap === wsSnapshot) return;          // não fecha o dropdown à toa
  wsSnapshot = snap;
  if (!companies.length) { box.innerHTML = ""; return; }
  const cur = wsCompany();
  box.innerHTML = `
    <a class="nav-link dropdown-toggle" href="#" id="wsDropdown" role="button"
       data-toggle="dropdown" aria-haspopup="true" aria-expanded="false" title="Workspace ativo">
      <i class="fas fa-building mr-1"></i>
      <span class="font-weight-bold small">${esc(cur ? cur.name : "Escolher empresa")}</span>
      <i class="fas fa-caret-down ml-1 small"></i>
    </a>
    <div class="dropdown-menu dropdown-menu-right shadow animated--grow-in" aria-labelledby="wsDropdown">
      <h6 class="dropdown-header">Workspace — empresa ativa</h6>
      ${companies.map(c => `
        <a class="dropdown-item ${c.id === workspaceId ? "active" : ""}" href="#" data-ws="${c.id}">
          <i class="fas fa-building mr-2 ${c.id === workspaceId ? "" : "text-gray-400"}"></i>${esc(c.name)}</a>`).join("")}
      <div class="dropdown-divider"></div>
      <a class="dropdown-item small" href="#empresas"><i class="fas fa-cog mr-2 text-gray-400"></i>Gerenciar empresas</a>
    </div>`;
  box.querySelectorAll("[data-ws]").forEach(el => el.onclick = (e) => {
    e.preventDefault();
    setWorkspace(Number(el.dataset.ws));
  });
}

function setWorkspace(id) {
  if (id === workspaceId) return;
  workspaceId = id;
  localStorage.setItem("maestro_ws", String(id));
  renderWorkspaceSwitcher();
  toast("Workspace: " + wsLabel());
  navigate();       // redesenha a tela atual já filtrada pela nova empresa
  refreshTopbar();
}

// linha-padrão que as telas usam para deixar claro o workspace em uso
function wsContextHtml() {
  const c = wsCompany();
  if (!c) return "";
  return `<div class="ws-context mb-3">
    <i class="fas fa-building mr-2"></i>Você está no workspace
    <strong class="mx-1">${esc(c.name)}</strong> — tudo nesta tela pertence a esta empresa.
    <span class="text-muted small ml-1">(troque no topo direito)</span>
  </div>`;
}

/* ---------------- roteador ---------------- */

const routes = {
  comecar: renderComecar,
  painel: renderPainel, empresas: renderEmpresas, projetos: renderProjetos,
  missoes: renderMissoes, aprovacoes: renderAprovacoes, agentes: renderAgentes,
  agenda: renderAgenda, porter: renderPorter, ajustes: renderAjustes,
  whatsapp: renderWhatsapp,
};
const TITLES = {
  comecar: "Bem-vindo", painel: "Painel", empresas: "Empresas", projetos: "Projetos",
  missoes: "Missões", aprovacoes: "Aprovações", agentes: "Equipe de Agentes",
  agenda: "Agenda", porter: "Exportar / Importar", ajustes: "Ajustes",
  whatsapp: "WhatsApp",
};

function navigate() {
  clearInterval(pollTimer);
  const hash = location.hash.slice(1) || "painel";
  const [view, arg] = hash.split("/");
  document.querySelectorAll("#accordionSidebar .nav-item[data-view]").forEach(li =>
    li.classList.toggle("active", li.dataset.view === view ||
      (view === "missao" && li.dataset.view === "missoes")));
  $id("pageTitle").textContent = view === "missao" ? "Missão" : (TITLES[view] || "Painel");
  if (view === "missao" && arg) return renderMissao(Number(arg));
  if (view === "comecar") return renderComecar(Number(arg) || 0);
  (routes[view] || renderPainel)();
}
window.addEventListener("hashchange", navigate);

/* ---------------- topbar (status + sino + badge aprovações) ---------------- */

async function refreshTopbar() {
  try {
    const [st, dash, notifs] = await Promise.all([
      api("/api/status"), api("/api/dashboard"), api("/api/notifications"),
    ]);
    fetchJourney();
    loadWorkspaces();
    const b = $id("statusBadge");
    if (st.ready) {
      b.className = "badge badge-success";
      b.innerHTML = '<i class="fas fa-check mr-1"></i>Claude pronto';
    } else {
      b.className = "badge badge-danger cursor";
      b.innerHTML = '<i class="fas fa-exclamation-triangle mr-1"></i>Configurar Claude';
      b.onclick = () => { location.hash = "#painel"; };
    }
    const nav = $id("navApprovalBadge");
    nav.classList.toggle("d-none", !dash.waiting_approval);
    nav.textContent = dash.waiting_approval || "";

    // botão único Pausar/Retomar empresa (visível quando há trabalho ativo ou pausado)
    const pb = $id("btnPauseCompany");
    if (pb) {
      const paused = dash.paused || 0;
      pb.classList.toggle("d-none", !dash.running && !paused);
      if (paused) {
        pb.className = "btn btn-sm btn-warning";
        pb.innerHTML = `<i class="fas fa-play mr-1"></i>Retomar empresa (${paused})`;
        pb.onclick = async () => {
          pb.disabled = true;
          try { await api("/api/company/resume" + wsQS(), { method: "POST" }); toast("Empresa retomada — a equipe voltou ao trabalho."); }
          catch (e) { toast(e.message, true); }
          pb.disabled = false; refreshTopbar();
        };
      } else {
        pb.className = "btn btn-sm btn-outline-warning";
        pb.innerHTML = '<i class="fas fa-pause mr-1"></i>Pausar empresa';
        pb.onclick = async () => {
          if (!confirm("Pausar todas as execuções da empresa? A memória (MEMORY.md) fica salva e você pode retomar depois sem perder nada.")) return;
          pb.disabled = true;
          try { await api("/api/company/pause" + wsQS(), { method: "POST" }); toast("Pausando… efetiva ao fim da micro-tarefa atual."); }
          catch (e) { toast(e.message, true); }
          pb.disabled = false; refreshTopbar();
        };
      }
    }

    const unread = notifs.filter(n => !n.read);
    const bell = $id("bellBadge");
    bell.classList.toggle("d-none", !unread.length);
    bell.textContent = unread.length > 9 ? "9+" : unread.length;
    $id("bellItems").innerHTML = notifs.slice(0, 6).map(n => `
      <a class="dropdown-item d-flex align-items-center" href="#aprovacoes">
        <div class="mr-3"><div class="icon-circle ${n.read ? "bg-secondary" : "bg-primary"}">
          <i class="fas fa-bell text-white"></i></div></div>
        <div><div class="small text-gray-500">${fmtTs(n.ts)}</div>
          <span class="${n.read ? "" : "font-weight-bold"}">${esc(n.title)}</span>
          <div class="small text-gray-600">${esc((n.message || "").slice(0, 80))}</div></div>
      </a>`).join("") ||
      '<span class="dropdown-item small text-gray-500">Sem avisos ainda.</span>';
  } catch { /* servidor reiniciando */ }
}
$id("bellClear").addEventListener("click", async (e) => {
  e.preventDefault();
  await api("/api/notifications/read", { method: "POST" });
  refreshTopbar();
});
setInterval(refreshTopbar, 5000);

/* ---------------- Começar (jornada guiada de primeiro uso) ---------------- */

const JOURNEY_STEPS = [
  [1, "Preparar o sistema"],
  [2, "Sua empresa"],
  [3, "Seu projeto"],
  [4, "Primeira missão"],
];

function journeyStepsHtml(current, realStep) {
  return `<div class="journey-steps">${JOURNEY_STEPS.map(([n, label]) => {
    const cls = n === current ? "now" : n < realStep ? "done" : "";
    const icon = n < realStep && n !== current ? '<i class="fas fa-check"></i>' : n;
    const inner = `<span class="num">${icon}</span>${label}`;
    // passos já concluídos são clicáveis: dá para voltar e editar
    if (n < realStep && n !== current) {
      return `<a class="journey-step ${cls}" href="#comecar/${n}"
        title="Voltar e editar este passo">${inner}<i class="fas fa-pen small ml-1"></i></a>`;
    }
    return `<span class="journey-step ${cls}">${inner}</span>`;
  }).join("")}</div>`;
}

async function renderComecar(backStep = 0) {
  clearInterval(pollTimer);  // re-render interno: não acumular polls
  await fetchJourney();
  if (journey.complete) { location.hash = "#painel"; return; }
  const realStep = journey.step;
  // usuário clicou num passo já concluído: modo edição (pode voltar até fechar os 4 itens)
  const editing = backStep > 0 && backStep < realStep;
  const step = editing ? backStep : realStep;

  let company = null, project = null;
  if (step >= 2) {
    const cs = await api("/api/companies").catch(() => []);
    company = cs[0] || null;
  }
  if (step >= 3) {
    const ps = await api("/api/projects").catch(() => []);
    project = ps[0] || null;
  }

  const backBtn = editing
    ? `<a class="btn btn-link btn-block text-muted" href="#comecar">
         <i class="fas fa-arrow-left mr-1"></i>Voltar para onde eu estava (sem salvar)</a>`
    : "";

  let body = "";
  if (step === 1) {
    const st = await api("/api/status");
    const check = (ok, txt) =>
      `<div class="check-line"><i class="fas fa-${ok ? "check-circle text-success" : "circle text-gray-300"} mr-2"></i>${txt}</div>`;
    body = `
      <h4 class="mb-3">Vamos preparar o sistema</h4>
      <p class="text-muted">O Maestro instala e configura <strong>tudo sozinho</strong> — você só faz o
        login na sua conta do Claude quando ele pedir. Preparação única.</p>
      ${check(st.claude_installed, "Claude Code" + (st.claude_version ? ` <span class="text-muted small">(${esc(st.claude_version)})</span>` : ""))}
      ${check(st.bypass_accepted, "Modo automático")}
      ${check(st.node_ok, 'Ferramentas de habilidades <span class="text-muted small">(opcional)</span>')}
      ${check(st.logged_in, "Login na sua conta do Claude")}
      <div class="mt-4">
        <button class="btn btn-primary btn-lg btn-block" id="wzAuto">
          <i class="fas fa-magic mr-2"></i>Preparar tudo automaticamente</button>
        <div id="wzProgress" class="small text-muted mt-3"></div>
      </div>
      ${backBtn}`;
  } else if (step === 2) {
    const edit = editing && company;
    body = `
      <h4 class="mb-3">${edit ? "Editar a sua empresa" : "Conte sobre a sua empresa"}</h4>
      <p class="text-muted">Isso vira o contexto que a sua equipe de agentes usa em todas as missões.
        Escreva do seu jeito, sem termos técnicos.</p>
      <div class="form-group">
        <label class="font-weight-bold">Qual o nome da sua empresa?</label>
        <input class="form-control form-control-lg" id="wzCName" placeholder="Ex.: Padaria do João"
          value="${edit ? esc(company.name) : ""}">
      </div>
      <div class="form-group">
        <label class="font-weight-bold">O que ela faz e o que você quer melhorar?</label>
        <textarea class="form-control" id="wzCDesc" rows="4"
          placeholder="Ex.: Somos uma padaria de bairro. Quero vender pela internet e organizar as encomendas.">${edit ? esc(company.objectives || "") : ""}</textarea>
      </div>
      <button class="btn btn-primary btn-lg btn-block" id="wzCSave">
        ${edit ? '<i class="fas fa-save mr-2"></i>Salvar alterações' : 'Continuar <i class="fas fa-arrow-right ml-2"></i>'}</button>
      ${backBtn}`;
  } else if (step === 3) {
    const edit = editing && project;
    body = `
      <h4 class="mb-3">${edit ? "Editar o seu projeto" : "Crie o seu primeiro projeto"}</h4>
      ${company ? `<div class="ws-context mb-3"><i class="fas fa-building mr-2"></i>
        Este projeto pertence à empresa <strong>${esc(company.name)}</strong>.</div>` : ""}
      <p class="text-muted">Um projeto é uma <strong>pasta no seu computador</strong> onde a equipe vai
        trabalhar e salvar o que produzir.</p>
      <div class="form-group">
        <label class="font-weight-bold">Nome do projeto</label>
        <input class="form-control form-control-lg" id="wzPName" placeholder="Ex.: Site da padaria"
          value="${edit ? esc(project.name) : ""}">
      </div>
      <div class="form-group">
        <label class="font-weight-bold">Pasta no computador</label>
        <div class="input-group">
          <input class="form-control" id="wzPFolder" placeholder="Use o botão ao lado" readonly
            value="${edit ? esc(project.folder) : ""}">
          <div class="input-group-append">
            <button class="btn btn-primary" id="wzPPick"><i class="fas fa-folder-open mr-1"></i>Escolher pasta</button>
          </div>
        </div>
        <small class="text-muted">Dica: crie uma pasta nova e vazia, por exemplo na Área de Trabalho.</small>
      </div>
      <button class="btn btn-primary btn-lg btn-block" id="wzPSave">
        ${edit ? '<i class="fas fa-save mr-2"></i>Salvar alterações' : 'Continuar <i class="fas fa-arrow-right ml-2"></i>'}</button>
      ${editing ? backBtn : `<a class="btn btn-link btn-block text-muted" href="#comecar/2">
        <i class="fas fa-arrow-left mr-1"></i>Voltar e editar a empresa</a>`}`;
  } else if (step === 4) {
    body = `
      <h4 class="mb-3">Qual é a sua primeira missão?</h4>
      ${company || project ? `<div class="ws-context mb-3"><i class="fas fa-building mr-2"></i>
        Empresa <strong>${esc(company ? company.name : "")}</strong>
        ${project ? ` &middot; projeto <strong>${esc(project.name)}</strong>` : ""}</div>` : ""}
      <p class="text-muted">Descreva com suas palavras o que você quer. O Maestro monta a equipe,
        faz o plano e <strong>mostra para você aprovar antes de executar</strong> — nada roda sem a sua permissão.</p>
      <div class="form-group">
        <label class="font-weight-bold">Dê um nome para a missão</label>
        <input class="form-control form-control-lg" id="wzTTitle" placeholder="Ex.: Criar o site da padaria">
      </div>
      <div class="form-group">
        <label class="font-weight-bold">O que deve ser feito?</label>
        <textarea class="form-control" id="wzTMission" rows="4"
          placeholder="Ex.: Quero um site simples com o cardápio, fotos, horário de funcionamento e botão do WhatsApp."></textarea>
      </div>
      <button class="btn btn-primary btn-lg btn-block" id="wzTGo">
        <i class="fas fa-play mr-2"></i>Criar missão e começar</button>
      <button class="btn btn-link btn-block text-muted" id="wzTLater">Criar sem executar agora</button>
      <a class="btn btn-link btn-block text-muted" href="#comecar/3">
        <i class="fas fa-arrow-left mr-1"></i>Voltar e editar o projeto</a>`;
  }

  main.innerHTML = `
    ${journeyStepsHtml(step, realStep)}
    <div class="card shadow journey-card"><div class="card-body p-4">${body}</div></div>
    <p class="text-center small journey-foot mt-3">Passo ${step} de 4 —
      clique num passo concluído (acima) para voltar e editar.</p>`;

  // passo 1 — preparação totalmente automática (narrada)
  const auto = $id("wzAuto");
  if (auto) auto.onclick = async () => {
    auto.disabled = true;
    auto.innerHTML = '<i class="fas fa-circle-notch fa-spin mr-2"></i>Preparando…';
    const prog = $id("wzProgress");
    const say = (msg, cls = "") => {
      prog.innerHTML += `<div class="check-line ${cls}">${msg}</div>`;
    };
    try {
      let st = await api("/api/status");
      if (!st.claude_installed) {
        say('<i class="fas fa-circle-notch fa-spin mr-2"></i>Baixando e instalando o Claude Code (pode levar alguns minutos)…');
        const r = await api("/api/bootstrap/install", { method: "POST" });
        say(r.ok ? '<i class="fas fa-check text-success mr-2"></i>Claude Code instalado.'
                 : '<i class="fas fa-times text-danger mr-2"></i>Falha ao instalar o Claude Code. Verifique a internet e tente de novo.');
        if (!r.ok) throw new Error("instalação");
      }
      st = await api("/api/status");
      if (!st.bypass_accepted) {
        const r = await api("/api/bootstrap/bypass", { method: "POST" });
        say(r.ok !== false ? '<i class="fas fa-check text-success mr-2"></i>Modo automático habilitado.'
                           : '<i class="fas fa-exclamation-triangle text-warning mr-2"></i>Modo automático fica para depois.');
      }
      if (!st.node_ok) {
        say('<i class="fas fa-circle-notch fa-spin mr-2"></i>Instalando ferramentas de habilidades (opcional)…');
        const r = await api("/api/bootstrap/node", { method: "POST" }).catch(() => ({ ok: false }));
        say(r.ok ? '<i class="fas fa-check text-success mr-2"></i>Ferramentas de habilidades prontas.'
                 : '<i class="fas fa-exclamation-triangle text-warning mr-2"></i>Ferramentas opcionais ficaram para depois — o Maestro funciona sem elas.');
      }
      st = await api("/api/status");
      if (!st.logged_in) {
        await api("/api/bootstrap/login", { method: "POST" });
        say('<i class="fas fa-sign-in-alt text-primary mr-2"></i><strong>Última etapa:</strong> abriu uma janela preta — siga as instruções dela para entrar na sua conta. Esta página avança sozinha quando terminar.');
      } else {
        renderComecar(backStep);
      }
    } catch {
      auto.disabled = false;
      auto.innerHTML = '<i class="fas fa-redo mr-2"></i>Tentar novamente';
    }
  };
  if (step === 1 && !editing) poll(async () => {
    if (!onView("comecar")) return;
    const j = await fetchJourney();
    if (j.step !== 1) renderComecar();
  }, 4000);

  // passo 2 — empresa (criar ou editar)
  const cSave = $id("wzCSave");
  if (cSave) cSave.onclick = async () => {
    const name = $id("wzCName").value.trim();
    if (!name) return toast("Escreva o nome da empresa.", true);
    try {
      if (editing && company) {
        await api("/api/companies/" + company.id, { method: "PUT", json: {
          name, objectives: $id("wzCDesc").value,
          impediments: company.impediments || "", problems: company.problems || "" } });
        toast("Empresa atualizada.");
        location.hash = "#comecar";
      } else {
        await api("/api/companies", { json: { name, objectives: $id("wzCDesc").value } });
        toast("Empresa salva.");
      }
      loadWorkspaces();
      renderComecar();
    } catch (e) { toast(e.message, true); }
  };

  // passo 3 — projeto (criar ou editar)
  const pPick = $id("wzPPick");
  if (pPick) pPick.onclick = async () => {
    pPick.disabled = true;
    try { const r = await api("/api/pick-folder", { method: "POST" }); if (r.folder) $id("wzPFolder").value = r.folder; }
    finally { pPick.disabled = false; }
  };
  const pSave = $id("wzPSave");
  if (pSave) pSave.onclick = async () => {
    const name = $id("wzPName").value.trim();
    const folder = $id("wzPFolder").value.trim();
    if (!name) return toast("Dê um nome ao projeto.", true);
    if (!folder) return toast("Escolha a pasta do projeto.", true);
    try {
      if (editing && project) {
        await api("/api/projects/" + project.id, { method: "PUT", json: {
          name, folder, description: project.description || "" } });
        toast("Projeto atualizado.");
        location.hash = "#comecar";
      } else {
        await api("/api/projects", { json: { company_id: company.id, name, folder } });
        toast("Projeto criado.");
      }
      renderComecar();
    } catch (e) { toast(e.message, true); }
  };

  // passo 4 — primeira missão
  async function createFirstTask(run) {
    const title = $id("wzTTitle").value.trim();
    const mission = $id("wzTMission").value.trim();
    if (!title || !mission) return toast("Preencha o nome e a descrição da missão.", true);
    try {
      const projects = await api("/api/projects");
      const r = await api("/api/tasks", { json: {
        project_id: projects[0].id, title, mission, autonomy: 1 } });
      await fetchJourney();
      if (run) {
        await api(`/api/tasks/${r.id}/start`, { method: "POST" });
        toast("Missão iniciada. Em instantes o plano aparece para você aprovar.");
        location.hash = "#missao/" + r.id;
      } else {
        toast("Missão salva. Execute quando quiser em Missões.");
        location.hash = "#painel";
      }
    } catch (e) { toast(e.message, true); }
  }
  const tGo = $id("wzTGo");
  if (tGo) tGo.onclick = () => createFirstTask(true);
  const tLater = $id("wzTLater");
  if (tLater) tLater.onclick = () => createFirstTask(false);
}

/* ---------------- Painel ---------------- */

async function renderPainel() {
  const [st, dash, tasks, events] = await Promise.all([
    api("/api/status"), api("/api/dashboard" + wsQS()),
    api("/api/tasks" + wsQS()), api("/api/events"),
  ]);
  const running = tasks.filter(t => ["executando", "aguardando"].includes(t.status));
  const check = (ok, txt) =>
    `<div class="mb-1"><i class="fas fa-${ok ? "check-circle text-success" : "times-circle text-danger"} mr-2"></i>${txt}</div>`;

  const heroJourney = !journey.complete ? `
    <div class="card shadow mb-4 journey-hero">
      <div class="card-body d-sm-flex align-items-center justify-content-between">
        <div>
          <h5 class="font-weight-bold mb-1">Continue de onde parou</h5>
          <div class="small text-muted mb-2">Sua jornada de primeiro uso ainda não terminou:</div>
          ${JOURNEY_STEPS.map(([n, label]) => `
            <div class="check-line small">
              <i class="fas fa-${n < journey.step ? "check-circle text-success" : "circle text-gray-300"} mr-2"></i>${label}
            </div>`).join("")}
        </div>
        <a class="btn btn-primary btn-lg mt-3 mt-sm-0" href="#comecar">
          <i class="fas fa-flag-checkered mr-2"></i>Continuar jornada</a>
      </div>
    </div>` : "";

  main.innerHTML = `
    ${heroJourney}
    ${wsContextHtml()}
    <div class="row">
      ${statCard("primary", "fa-rocket", "Missões", dash.tasks)}
      ${statCard("info", "fa-play", "Em execução", dash.running)}
      ${statCard("warning", "fa-check-double", "Aguardando decisão", dash.waiting_approval)}
      ${statCard("success", "fa-dollar-sign", "Custo acumulado", usd(dash.total_cost_usd))}
    </div>

    ${dash.waiting_limit.length ? `
    <div class="alert alert-info shadow-sm">
      <i class="fas fa-heartbeat mr-2"></i>
      <strong>Heartbeat ativo:</strong> ${dash.waiting_limit.length} execução(ões) pausada(s) por limite
      de uso do Claude. Retomada automática:
      ${dash.waiting_limit.map(w =>
        `<a href="#missao/${w.task_id}" class="alert-link">missão ${w.task_id} às ${fmtTs(w.resume_at)}</a>`
      ).join(", ")}.
    </div>` : ""}

    <div class="row">
      <div class="col-lg-6">
        ${cardOpen("Estado do sistema")}
          ${check(st.claude_installed, `Claude Code instalado <span class="text-muted small">${esc(st.claude_version)}</span>`)}
          ${check(st.logged_in, "Login no Claude efetuado")}
          ${check(st.bypass_accepted, "Modo automático (bypass) habilitado")}
          ${st.ready ? "" : `
          <div class="mt-3">
            ${st.claude_installed ? "" : `<button class="btn btn-primary btn-sm mr-2" id="btnInstall"><i class="fas fa-download mr-1"></i>Instalar Claude Code</button>`}
            <button class="btn btn-secondary btn-sm mr-2" id="btnBypass"><i class="fas fa-unlock mr-1"></i>Habilitar modo automático</button>
            <button class="btn btn-secondary btn-sm" id="btnLogin"><i class="fas fa-sign-in-alt mr-1"></i>Fazer login</button>
          </div>
          <p class="small text-muted mt-2 mb-0">Configuração única. Depois do login, tudo roda sozinho.</p>`}
        ${cardClose}

        ${cardOpen("Missões em andamento",
          `<a class="btn btn-primary btn-sm" href="#missoes"><i class="fas fa-plus mr-1"></i>Nova missão</a>`)}
          ${running.length ? `<div class="table-responsive"><table class="table table-sm mb-0">
            <tbody>${running.map(t => `
              <tr>
                <td><a href="#missao/${t.id}">${esc(t.title)}</a>
                  <div class="small text-muted">${esc(t.company_name)} &middot; ${esc(t.project_name)}</div></td>
                <td class="text-right">${badge(t.status)}</td>
              </tr>`).join("")}</tbody></table></div>`
            : `<p class="text-muted mb-0">Nenhuma missão executando agora.</p>`}
        ${cardClose}
      </div>

      <div class="col-lg-6">
        ${cardOpen("Atividade recente")}
          <div class="feed">${events.map(e =>
            `<div class="item-row ${esc(e.level)}"><time>${fmtTs(e.ts)}</time>${esc(e.message)}</div>`).join("")
            || '<p class="text-muted mb-0">Sem atividade ainda.</p>'}</div>
        ${cardClose}
      </div>
    </div>`;

  const bind = (id, fn, okMsg) => {
    const b = $id(id); if (!b) return;
    b.onclick = async () => {
      b.disabled = true; b.textContent = "Aguarde…";
      try {
        const r = await fn();
        toast(r.ok === false ? (r.output || r.error || "Falhou") : okMsg, r.ok === false);
      } catch (e) { toast(e.message, true); }
      navigate();
    };
  };
  bind("btnInstall", () => api("/api/bootstrap/install", { method: "POST" }), "Claude Code instalado.");
  bind("btnBypass", () => api("/api/bootstrap/bypass", { method: "POST" }), "Modo automático habilitado.");
  bind("btnLogin", () => api("/api/bootstrap/login", { method: "POST" }), "Janela de login aberta.");
  poll(() => { if (onView("painel")) renderPainel(); }, 7000);
}

/* ---------------- Empresas ---------------- */

async function renderEmpresas() {
  const list = await api("/api/companies");
  main.innerHTML = `
    <p class="text-muted small mb-3"><i class="fas fa-info-circle mr-1"></i>
      Cada empresa é um <strong>workspace separado</strong>: projetos, missões e agentes de uma
      empresa não se misturam com os das outras. A empresa ativa aparece no topo direito.</p>
    <div class="row"><div class="col-lg-5">
      ${cardOpen("Cadastrar empresa")}
        <div class="form-group"><label>Nome da empresa</label>
          <input class="form-control" id="cName" placeholder="Ex.: Padaria do João"></div>
        <div class="form-group"><label>Objetivos</label>
          <textarea class="form-control" id="cObj" rows="2" placeholder="O que você quer alcançar"></textarea></div>
        <div class="form-group"><label>Impedimentos</label>
          <textarea class="form-control" id="cImp" rows="2" placeholder="O que atrapalha hoje"></textarea></div>
        <div class="form-group"><label>Problemas a resolver</label>
          <textarea class="form-control" id="cProb" rows="2"></textarea></div>
        <button class="btn btn-primary" id="cSave"><i class="fas fa-save mr-1"></i>Salvar empresa</button>
      ${cardClose}
    </div><div class="col-lg-7">
      ${cardOpen("Empresas cadastradas")}
        ${list.length ? `<div class="table-responsive"><table class="table table-sm">
          <thead><tr><th>Nome</th><th>Objetivos</th><th class="text-right">Ações</th></tr></thead>
          <tbody>${list.map(c => `
            <tr><td class="font-weight-bold">${esc(c.name)}
                ${c.id === workspaceId ? '<span class="badge badge-primary ml-1">workspace ativo</span>' : ""}</td>
              <td class="small text-muted">${esc((c.objectives || "").slice(0, 90))}</td>
              <td class="text-right text-nowrap">
                ${c.id !== workspaceId ? `<button class="btn btn-outline-primary btn-sm" data-ws="${c.id}"
                  title="Tornar esta empresa o workspace ativo"><i class="fas fa-exchange-alt"></i></button>` : ""}
                <button class="btn btn-outline-danger btn-sm" data-del="${c.id}">
                  <i class="fas fa-trash"></i></button></td></tr>`).join("")}</tbody></table></div>`
          : `<p class="text-muted mb-0">Nenhuma empresa ainda. Cadastre ao lado.</p>`}
      ${cardClose}
    </div></div>`;

  $id("cSave").onclick = async () => {
    try {
      await api("/api/companies", { json: {
        name: $id("cName").value, objectives: $id("cObj").value,
        impediments: $id("cImp").value, problems: $id("cProb").value } });
      toast("Empresa salva.");
      await loadWorkspaces();
      renderEmpresas();
    } catch (e) { toast(e.message, true); }
  };
  main.querySelectorAll("[data-ws]").forEach(b => b.onclick = () =>
    setWorkspace(Number(b.dataset.ws)));
  main.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    if (!confirm("Excluir esta empresa e tudo ligado a ela?")) return;
    await api("/api/companies/" + b.dataset.del, { method: "DELETE" });
    await loadWorkspaces();
    renderEmpresas();
  });
}

/* ---------------- Projetos ---------------- */

function journeyCta(msg) {
  return `<div class="card shadow"><div class="card-body text-center py-5">
    <i class="fas fa-flag-checkered fa-2x mb-3 text-gray-300"></i>
    <p class="text-muted">${msg}</p>
    <a class="btn btn-primary btn-lg" href="#comecar"><i class="fas fa-arrow-right mr-2"></i>Continuar jornada</a>
  </div></div>`;
}

async function renderProjetos() {
  await loadWorkspaces();
  const projects = await api("/api/projects" + wsQS());
  if (!companies.length) {
    main.innerHTML = journeyCta("Antes de criar um projeto, conte sobre a sua empresa — leva um minuto.");
    return;
  }
  main.innerHTML = `
    ${wsContextHtml()}
    <div class="row"><div class="col-lg-5">
      ${cardOpen("Criar projeto")}
        ${companies.length ? `
        <div class="form-group"><label>Empresa (workspace)</label>
          <select class="form-control" id="pCompany">${companies.map(c =>
            `<option value="${c.id}" ${c.id === workspaceId ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
          <small class="text-muted">O projeto pertence a esta empresa — missões dele nunca se
            misturam com as de outras empresas.</small></div>
        <div class="form-group"><label>Nome do projeto</label>
          <input class="form-control" id="pName" placeholder="Ex.: Site institucional"></div>
        <div class="form-group"><label>Pasta no computador</label>
          <div class="input-group">
            <input class="form-control" id="pFolder" placeholder="Use o botão ao lado">
            <div class="input-group-append">
              <button class="btn btn-outline-primary" id="pPick"><i class="fas fa-folder-open mr-1"></i>Escolher</button>
            </div></div></div>
        <div class="form-group"><label>Descrição (opcional)</label>
          <textarea class="form-control" id="pDesc" rows="2"></textarea></div>
        <button class="btn btn-primary" id="pSave"><i class="fas fa-save mr-1"></i>Criar projeto</button>`
        : `<p class="text-muted mb-0">Cadastre primeiro uma <a href="#empresas">empresa</a>.</p>`}
      ${cardClose}
    </div><div class="col-lg-7">
      ${cardOpen("Projetos de " + esc(wsLabel() || "todas as empresas"))}
        ${projects.length ? `<div class="table-responsive"><table class="table table-sm">
          <thead><tr><th>Projeto</th><th>Pasta</th><th></th></tr></thead>
          <tbody>${projects.map(p => `
            <tr><td><span class="font-weight-bold">${esc(p.name)}</span>
                <div class="small text-muted">${esc(p.company_name)}</div></td>
              <td><code class="path">${esc(p.folder)}</code></td>
              <td class="text-right"><button class="btn btn-outline-danger btn-sm" data-del="${p.id}">
                <i class="fas fa-trash"></i></button></td></tr>`).join("")}</tbody></table></div>`
          : `<p class="text-muted mb-0">Nenhum projeto ainda.</p>`}
      ${cardClose}
    </div></div>`;

  const pick = $id("pPick");
  if (pick) pick.onclick = async () => {
    pick.disabled = true;
    try { const r = await api("/api/pick-folder", { method: "POST" }); if (r.folder) $id("pFolder").value = r.folder; }
    finally { pick.disabled = false; }
  };
  const save = $id("pSave");
  if (save) save.onclick = async () => {
    try {
      const cid = Number($id("pCompany").value);
      await api("/api/projects", { json: {
        company_id: cid, name: $id("pName").value,
        folder: $id("pFolder").value, description: $id("pDesc").value } });
      toast("Projeto criado.");
      if (cid !== workspaceId) setWorkspace(cid);  // segue o usuário p/ o workspace certo
      else renderProjetos();
    } catch (e) { toast(e.message, true); }
  };
  main.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    if (!confirm("Excluir este projeto? (a pasta no computador não é apagada)")) return;
    await api("/api/projects/" + b.dataset.del, { method: "DELETE" });
    renderProjetos();
  });
}

/* ---------------- Missões ---------------- */

let riskPayloadPending = null;  // payload aguardando aceite do termo

async function renderMissoes() {
  await loadWorkspaces();
  const [tasks, projects, templates, suggestions] = await Promise.all([
    api("/api/tasks" + wsQS()), api("/api/projects" + wsQS()),
    api("/api/mission-templates").catch(() => []),
    api("/api/suggestions" + wsQS()).catch(() => [])]);
  const pendSugg = suggestions.filter(s => s.status === "pendente");
  if (!projects.length && !companies.length) {
    main.innerHTML = journeyCta("Para criar uma missão você precisa de uma empresa e um projeto. A jornada guia você.");
    return;
  }
  main.innerHTML = `
    ${wsContextHtml()}
    ${pendSugg.length ? `
    <div class="card border-left-info shadow mb-4">
      <div class="card-body">
        <h6 class="font-weight-bold text-info mb-1"><i class="fas fa-lightbulb mr-2"></i>
          A equipe foi além — ${pendSugg.length} sugestão(ões) de próxima missão</h6>
        <p class="small text-muted mb-3">Depois de concluir missões, os agentes analisam os
          objetivos da empresa e propõem os próximos passos. Você decide.</p>
        ${pendSugg.map(s => `
        <div class="border rounded p-2 mb-2">
          <div class="d-sm-flex justify-content-between align-items-start">
            <div class="mr-2">
              <strong>${esc(s.title)}</strong>
              <div class="small text-muted">${esc(s.reason || "")}
                ${s.origin_title ? `<span class="ml-1">· veio de: ${esc(s.origin_title)}</span>` : ""}</div>
              <div class="small mt-1">${esc(s.mission.slice(0, 220))}${s.mission.length > 220 ? "…" : ""}</div>
            </div>
            <div class="text-nowrap mt-2 mt-sm-0">
              <button class="btn btn-success btn-sm" data-sugg-go="${s.id}"
                title="Cria a missão e já executa"><i class="fas fa-play mr-1"></i>Aceitar e executar</button>
              <button class="btn btn-outline-primary btn-sm" data-sugg-ok="${s.id}"
                title="Cria a missão para executar depois"><i class="fas fa-save"></i></button>
              <button class="btn btn-outline-secondary btn-sm" data-sugg-no="${s.id}"
                title="Descartar sugestão"><i class="fas fa-times"></i></button>
            </div>
          </div>
        </div>`).join("")}
      </div>
    </div>` : ""}
    <div class="row"><div class="col-lg-5">
      ${cardOpen("Nova missão")}
        ${projects.length ? `
        <div class="form-group"><label>Projeto</label>
          <select class="form-control" id="tProject">${projects.map(p =>
            `<option value="${p.id}">${esc(p.company_name)} — ${esc(p.name)}</option>`).join("")}</select>
          <small class="text-muted">Só aparecem projetos da empresa ativa (${esc(wsLabel())}).</small></div>
        ${templates.length ? `
        <div class="form-group">
          <label><i class="fas fa-magic mr-1 text-primary"></i>Modelos prontos
            <small class="text-muted font-weight-normal">— clique para preencher e ajuste como quiser</small></label>
          <div id="tplGrid">${templates.map((t, i) => `
            <button type="button" class="btn btn-outline-primary btn-sm mb-1 mr-1 text-left"
              data-tpl="${i}" title="${esc(t.mission.slice(0, 220))}…">
              <i class="fas ${esc(t.icon)} mr-1"></i>${esc(t.title)}</button>`).join("")}
          </div></div>` : ""}
        <div class="form-group"><label>Nome da missão</label>
          <input class="form-control" id="tTitle" placeholder="Ex.: Criar o site institucional"></div>
        <div class="form-group"><label>Descreva o que deve ser feito</label>
          <textarea class="form-control" id="tMission" rows="4"
            placeholder="Com suas palavras: o que você quer que a equipe de agentes produza?"></textarea></div>
        <div class="form-group"><label>Nível de autonomia</label>
          <select class="form-control" id="tAutonomy">
            <option value="1" selected>Aprovação obrigatória — você aprova o plano antes de executar (padrão seguro)</option>
            <option value="0">Somente sugestão — só planeja, não executa nada</option>
            <option value="3">Autônomo — executa sem aprovação (exige termo de risco)</option>
            <option value="4">Só se vive uma vez — auto-aprova TUDO, nunca para para perguntar (YOLO total)</option>
          </select></div>
        <button class="btn btn-primary" id="tSave"><i class="fas fa-save mr-1"></i>Salvar missão</button>`
        : `<p class="text-muted mb-0">Crie primeiro um <a href="#projetos">projeto</a> nesta empresa.</p>`}
      ${cardClose}
    </div><div class="col-lg-7">
      ${cardOpen("Missões de " + esc(wsLabel() || "todas as empresas"))}
        ${tasks.length ? `<div class="table-responsive"><table class="table table-sm">
          <thead><tr><th>Missão</th><th>Autonomia</th><th>Estado</th><th class="text-right">Ações</th></tr></thead>
          <tbody>${tasks.map(t => `
            <tr>
              <td><a href="#missao/${t.id}" class="font-weight-bold">${esc(t.title)}</a>
                <div class="small text-muted">${esc(t.company_name)} &middot; ${esc(t.project_name)}
                  ${t.last_cost ? "&middot; " + usd(t.last_cost) : ""}</div></td>
              <td>${autonomyBadge(t.autonomy)}</td>
              <td>${badge(t.status)}</td>
              <td class="text-right text-nowrap">
                ${["executando", "aguardando"].includes(t.status)
                  ? `<a class="btn btn-outline-primary btn-sm" href="#missao/${t.id}">Acompanhar</a>`
                  : `<button class="btn btn-primary btn-sm" data-start="${t.id}"><i class="fas fa-play"></i></button>
                     <a class="btn btn-outline-secondary btn-sm" href="#missao/${t.id}"><i class="fas fa-eye"></i></a>
                     <button class="btn btn-outline-danger btn-sm" data-del="${t.id}"><i class="fas fa-trash"></i></button>`}
              </td></tr>`).join("")}</tbody></table></div>`
          : `<p class="text-muted mb-0">Nenhuma missão ainda. Crie a primeira ao lado.</p>`}
      ${cardClose}
    </div></div>`;

  main.querySelectorAll("[data-sugg-go],[data-sugg-ok],[data-sugg-no]").forEach(b => b.onclick = async () => {
    const go = b.dataset.suggGo, ok = b.dataset.suggOk, no = b.dataset.suggNo;
    b.disabled = true;
    try {
      if (no) { await api(`/api/suggestions/${no}/dismiss`, { method: "POST" }); toast("Sugestão descartada."); }
      else {
        const r = await api(`/api/suggestions/${go || ok}/accept`, { json: { start: !!go } });
        toast(go ? "Missão criada e em execução!" : "Missão criada — execute quando quiser.");
        if (go) { location.hash = "#missao/" + r.task_id; return; }
      }
      renderMissoes();
    } catch (e) { toast(e.message, true); b.disabled = false; }
  });
  main.querySelectorAll("[data-tpl]").forEach(b => b.onclick = () => {
    const t = templates[Number(b.dataset.tpl)];
    $id("tTitle").value = t.title;
    $id("tMission").value = t.mission;
    main.querySelectorAll("[data-tpl]").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    $id("tMission").focus();
  });
  const save = $id("tSave");
  if (save) save.onclick = async () => {
    const payload = {
      project_id: Number($id("tProject").value),
      title: $id("tTitle").value, mission: $id("tMission").value,
      autonomy: Number($id("tAutonomy").value),
    };
    if (payload.autonomy >= 2) { openRiskModal(payload); return; }
    await submitTask(payload);
  };
  main.querySelectorAll("[data-start]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      await api(`/api/tasks/${b.dataset.start}/start`, { method: "POST" });
      toast("Missão iniciada.");
      location.hash = "#missao/" + b.dataset.start;
    } catch (e) { toast(e.message, true); b.disabled = false; }
  });
  main.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    if (!confirm("Excluir esta missão?")) return;
    try { await api("/api/tasks/" + b.dataset.del, { method: "DELETE" }); renderMissoes(); }
    catch (e) { toast(e.message, true); }
  });
}

async function submitTask(payload) {
  try {
    await api("/api/tasks", { json: payload });
    toast("Missão salva. Clique em executar quando quiser começar.");
    if ((location.hash.slice(1) || "").startsWith("missoes")) renderMissoes();
  } catch (e) { toast(e.message, true); }
}

async function openRiskModal(payload) {
  riskPayloadPending = payload;
  const term = await api("/api/governance/term");
  $id("riskTermText").textContent = term.text;
  $id("riskHash").textContent = `Versão ${term.version} — hash ${term.hash}`;
  // aviso extra do modo "Só se vive uma vez" (nível 4)
  $id("riskYolo").classList.toggle("d-none", Number(payload.autonomy) < 4);
  $id("riskAccept").checked = false;
  $id("riskConfirm").disabled = true;
  window.jQuery("#riskModal").modal("show");
}
$id("riskAccept").addEventListener("change", e => {
  $id("riskConfirm").disabled = !e.target.checked;
});
$id("riskConfirm").addEventListener("click", async () => {
  if (!riskPayloadPending) return;
  const payload = { ...riskPayloadPending, accept_risk: true };
  riskPayloadPending = null;
  window.jQuery("#riskModal").modal("hide");
  await submitTask(payload);
});

/* ---------------- reprovação com motivo / cancelar missão ---------------- */

let rejectCtx = null;   // {apId, after}

function openRejectModal(apId, after) {
  rejectCtx = { apId, after };
  $id("rejectReason").value = "";
  window.jQuery("#rejectModal").modal("show");
}
$id("rejectConfirm").addEventListener("click", async () => {
  if (!rejectCtx) return;
  const reason = $id("rejectReason").value.trim();
  if (!reason) return toast("Escreva o motivo — a IA usa esse feedback para melhorar o plano.", true);
  const { apId, after } = rejectCtx; rejectCtx = null;
  window.jQuery("#rejectModal").modal("hide");
  try {
    await api(`/api/approvals/${apId}/resolve`, { json: { approve: false, reason } });
    toast("Reprovado. O Maestro está replanejando com o seu feedback…");
  } catch (e) { toast(e.message, true); }
  if (after) after();
});
$id("rejectCancelTask").addEventListener("click", async () => {
  if (!rejectCtx) return;
  if (!confirm("Cancelar a missão inteira? Nada mais será executado.")) return;
  const { apId, after } = rejectCtx; rejectCtx = null;
  const reason = $id("rejectReason").value.trim();
  window.jQuery("#rejectModal").modal("hide");
  try {
    await api(`/api/approvals/${apId}/resolve`, { json: {
      approve: false, cancel: true, reason: reason || "Missão cancelada pelo gestor." } });
    toast("Missão cancelada.");
  } catch (e) { toast(e.message, true); }
  if (after) after();
});

/* ---------------- Detalhe da missão ---------------- */

const PHASES = [
  ["contratando", "Contratando equipe"],
  ["habilidades", "Preparando habilidades"],
  ["planejando", "Planejando"],
  ["executando", "Executando"],
  ["concluida", "Concluída"],
];

let missaoSnap = "";
let debugOpen = false;

async function renderMissao(id) {
  missaoSnap = "";       // primeira pintura sempre desenha
  debugOpen = false;
  async function draw() {
    if (!onView("missao")) return;
    let d, calls = [], deliv = null;
    try {
      d = await api("/api/tasks/" + id);
      if (d.latest_run) {
        calls = await api(`/api/runs/${d.latest_run.id}/llm-calls`);
        deliv = await api(`/api/runs/${d.latest_run.id}/deliverables`).catch(() => null);
      }
    } catch { return; }
    // só mexe no DOM quando os dados mudam (evita tela piscando)
    const snap = JSON.stringify([d, calls, deliv, debugOpen]);
    if (snap === missaoSnap && main.querySelector(`#missaoWrap[data-id="${id}"]`)) return;
    missaoSnap = snap;
    const { task, latest_run: run, microtasks, events, approvals, agents } = d;
    const pendingAp = (approvals || []).find(a => a.status === "pendente");
    const isRunning = run && ["executando", "preparando"].includes(run.status);
    const isWaiting = run && (run.status.startsWith("aguardando") || run.status === "pausada");
    const phaseIdx = run ? PHASES.findIndex(p => p[0] === (run.status === "concluida" ? "concluida" : run.phase)) : -1;

    main.innerHTML = `
      <span id="missaoWrap" data-id="${id}" class="d-none"></span>
      <div class="d-sm-flex align-items-center justify-content-between mb-3">
        <div>
          <h1 class="h4 mb-0 text-gray-800">${esc(task.title)} ${autonomyBadge(task.autonomy)}</h1>
          <div class="small text-muted">${esc(task.company_name)} &middot; ${esc(task.project_name)}
            &middot; <code class="path">${esc(task.folder)}</code></div>
        </div>
        <a class="btn btn-outline-secondary btn-sm" href="#missoes"><i class="fas fa-arrow-left mr-1"></i>Voltar</a>
      </div>

      ${run && run.status === "aguardando_limite" ? `
      <div class="alert alert-info shadow-sm">
        <i class="fas fa-heartbeat mr-2"></i><strong>Heartbeat ativo:</strong>
        limite de uso do Claude atingido. Retomada automática às <strong>${fmtTs(run.resume_at)}</strong>.
        <button class="btn btn-info btn-sm ml-3" id="btnResume">Tentar agora</button>
      </div>` : ""}

      ${run && run.status === "pausada" ? `
      <div class="alert alert-warning shadow-sm">
        <i class="fas fa-pause-circle mr-2"></i><strong>Pausada pelo gestor:</strong>
        a memória da missão foi salva em <code>MEMORY.md</code> — retome quando quiser e a
        equipe continua de onde parou.
        <button class="btn btn-warning btn-sm ml-3" id="btnResume"><i class="fas fa-play mr-1"></i>Retomar agora</button>
      </div>` : ""}

      ${pendingAp ? `
      <div class="card border-left-warning shadow mb-4">
        <div class="card-body">
          <h6 class="font-weight-bold text-warning mb-2"><i class="fas fa-gavel mr-2"></i>Decisão necessária
            (${pendingAp.kind === "plan" ? "aprovar plano" : "liberar budget"})</h6>
          ${approvalPayloadHtml(pendingAp)}
          <div class="mt-3">
            <button class="btn btn-success btn-sm mr-2" data-ap="${pendingAp.id}" data-ok="1">
              <i class="fas fa-check mr-1"></i>Aprovar</button>
            <button class="btn btn-outline-danger btn-sm" data-ap="${pendingAp.id}" data-ok="0"
              title="Reprovar com motivo (replaneja) ou cancelar a missão inteira">
              <i class="fas fa-times mr-1"></i>Reprovar / Cancelar</button>
          </div>
        </div>
      </div>` : ""}

      ${cardOpen("Execução", `
        <div>
          ${isRunning ? `<button class="btn btn-outline-danger btn-sm" id="btnCancel"><i class="fas fa-stop mr-1"></i>Cancelar</button>`
            : isWaiting ? `<button class="btn btn-outline-danger btn-sm" id="btnCancelWait"><i class="fas fa-stop mr-1"></i>Cancelar</button>`
            : `<button class="btn btn-primary btn-sm" id="btnStart"><i class="fas fa-play mr-1"></i>Executar${run ? " novamente" : ""}</button>`}
        </div>`)}
        <div class="mb-2">${run ? badge(run.status) : badge(task.status)}
          ${run ? `<span class="small text-muted ml-2">custo desta execução: ${usd(run.total_cost_usd)}</span>` : ""}</div>
        ${agents && agents.length ? `<div class="mb-2">
          <span class="small text-muted mr-1">Equipe:</span>
          ${agents.map(a => `<span class="badge badge-light border mr-1 cursor" data-agent="${a.id}"
            title="Clique para ver a ficha completa do agente">
            <i class="fas fa-user-cog mr-1"></i>${esc(a.name)}${a.model ? ` · ${esc(modelShort(a.model))}` : ""}${a.effort ? `/${esc(a.effort)}` : ""}</span>`).join("")}
        </div>` : ""}
        ${run ? `<div>${PHASES.map(([key, label], i) => {
            let cls = "";
            if (run.status === "concluida" || i < phaseIdx) cls = "done";
            else if (i === phaseIdx) cls = isRunning ? "now" : (isWaiting ? "hold" : "");
            return `<span class="phase-pill ${cls}">${i + 1}. ${label}</span>`;
          }).join("")}</div>` : ""}
        ${run && run.error ? `<p class="small text-danger mt-2 mb-0"><i class="fas fa-exclamation-circle mr-1"></i>${esc(run.error)}</p>` : ""}
        ${run && run.summary ? `<p class="small mt-2 mb-0">${esc(run.summary)}</p>` : ""}
        <p class="small text-muted mt-3 mb-0"><strong>Missão:</strong> ${esc(task.mission).slice(0, 500)}</p>
      ${cardClose}

      ${deliv && deliv.files && deliv.files.length ? `
      ${cardOpen(`📦 Entregas desta execução (${deliv.files.length} arquivo${deliv.files.length === 1 ? "" : "s"})`, `
        <button class="btn btn-primary btn-sm" id="btnOpenFolder">
          <i class="fas fa-folder-open mr-1"></i>Abrir pasta no computador</button>`)}
        <p class="small text-muted mb-2">Tudo que a equipe produziu ou alterou na pasta do projeto.
          Clique no nome para baixar/abrir o arquivo.</p>
        <div class="table-responsive"><table class="table table-sm mb-0">
          <thead><tr><th>Arquivo</th><th>Tamanho</th><th>Modificado</th></tr></thead>
          <tbody>${deliv.files.slice(0, 50).map(f => `
            <tr><td><a href="/api/runs/${run.id}/deliverables/file?path=${encodeURIComponent(f.path)}"
                  target="_blank"><i class="fas fa-file-alt mr-1"></i>${esc(f.path)}</a></td>
              <td class="small">${f.size >= 1048576 ? (f.size / 1048576).toFixed(1) + " MB"
                : f.size >= 1024 ? (f.size / 1024).toFixed(0) + " KB" : f.size + " B"}</td>
              <td class="small">${fmtTs(f.modified)}</td></tr>`).join("")}</tbody></table></div>
        ${deliv.files.length > 50 ? `<p class="small text-muted mt-2 mb-0">Mostrando 50 de ${deliv.files.length} — veja o restante na pasta.</p>` : ""}
      ${cardClose}` : ""}

      ${microtasks.length ? `
      ${cardOpen("Micro-tarefas do plano")}
        <div class="table-responsive"><table class="table table-sm">
          <thead><tr><th>#</th><th>Descrição</th><th>Agente</th><th>Execução</th><th>Estado</th><th>Custo</th></tr></thead>
          <tbody>${microtasks.map(m => `
            <tr>
              <td>${m.ord}</td>
              <td class="small">${esc(m.description.slice(0, 140))}
                ${m.output && m.status !== "pendente" ? `<div class="text-muted">${esc(m.output.slice(0, 160))}</div>` : ""}</td>
              <td class="small">${esc(m.agent_name)}</td>
              <td class="small">${m.parallel ? "paralela" : "isolada"}${JSON.parse(m.depends_on || "[]").length
                ? " &middot; dep: #" + JSON.parse(m.depends_on).join(", #") : ""}</td>
              <td>${badge(m.status)}</td>
              <td class="small">${m.cost_usd ? usd(m.cost_usd) : "—"}</td>
            </tr>`).join("")}</tbody></table></div>
      ${cardClose}` : ""}

      ${(d.messages || []).length ? `
      ${cardOpen("Conversa da equipe (agentes falando entre si)")}
        <div class="feed">${d.messages.map(msg => `
          <div class="item-row ${msg.kind === "backlog" ? "warn" : ""}"><time>${fmtTs(msg.ts)}</time>
            <i class="fas fa-${msg.kind === "backlog" ? "tasks" : "comment"} mr-1"></i>
            <strong>${esc(msg.from_agent)}</strong> <i class="fas fa-long-arrow-alt-right mx-1"></i>
            <strong>${esc(msg.to_agent)}</strong>${msg.kind === "backlog"
              ? ' <span class="badge badge-warning">backlog</span>' : ""}: ${esc(msg.content)}</div>`).join("")}
        </div>
      ${cardClose}` : ""}

      ${events.length ? `
      ${cardOpen("Diário de bordo")}
        <div class="feed">${events.map(e =>
          `<div class="item-row ${esc(e.level)}"><time>${fmtTs(e.ts)}</time>${esc(e.message)}</div>`).join("")}</div>
      ${cardClose}` : ""}

      ${run ? `
      ${cardOpen(`Debug — o que trafegou com o Claude (${calls.length} chamada${calls.length === 1 ? "" : "s"})`,
        `<button class="btn btn-outline-secondary btn-sm" id="dbgToggle">
           <i class="fas fa-bug mr-1"></i>${debugOpen ? "Ocultar" : "Mostrar"}</button>`)}
        <div id="dbgBody" class="${debugOpen ? "" : "d-none"}">
          <p class="small text-muted">Cada linha é uma chamada real ao Claude Code. Clique em
            <strong>RAW</strong> para ver o prompt exato, o raciocínio (thinking), a resposta e a
            saída bruta — transparência total do que aconteceu.</p>
          ${calls.length ? `<div class="table-responsive"><table class="table table-sm">
            <thead><tr><th>#</th><th>Quando</th><th>Etapa</th><th>Agente</th><th>Modelo</th>
              <th>Esforço</th><th>OK</th><th>Custo</th><th>Duração</th><th></th></tr></thead>
            <tbody>${calls.map(c => `
              <tr class="${c.ok ? "" : "table-danger"}">
                <td>${c.id}</td><td class="small">${fmtTs(c.ts)}</td><td class="small">${esc(c.kind)}</td>
                <td class="small">${esc(c.agent_name || "—")}</td>
                <td class="small">${esc(c.model ? modelShort(c.model) : "—")}</td><td class="small">${esc(c.effort || "—")}</td>
                <td>${c.ok ? '<i class="fas fa-check text-success"></i>' : '<i class="fas fa-times text-danger"></i>'}</td>
                <td class="small">${usd(c.cost_usd)}</td>
                <td class="small">${((c.duration_ms || 0) / 1000).toFixed(1)}s</td>
                <td class="text-right"><button class="btn btn-outline-primary btn-sm" data-call="${c.id}">
                  <i class="fas fa-code mr-1"></i>RAW</button></td>
              </tr>
              ${!c.ok && c.error ? `<tr class="table-danger"><td></td>
                <td colspan="9" class="small text-danger">${esc(c.error)}</td></tr>` : ""}`).join("")}
            </tbody></table></div>`
            : `<p class="small text-muted mb-0">Nenhuma chamada registrada nesta execução ainda.</p>`}
        </div>
      ${cardClose}` : ""}`;

    const bStart = $id("btnStart");
    if (bStart) bStart.onclick = async () => {
      bStart.disabled = true;
      try { await api(`/api/tasks/${id}/start`, { method: "POST" }); toast("Missão iniciada."); draw(); }
      catch (e) { toast(e.message, true); bStart.disabled = false; }
    };
    const bResume = $id("btnResume");
    if (bResume) bResume.onclick = async () => {
      try { await api(`/api/runs/${run.id}/resume`, { method: "POST" }); toast("Retomando…"); draw(); }
      catch (e) { toast(e.message, true); }
    };
    ["btnCancel", "btnCancelWait"].forEach(bid => {
      const b = $id(bid);
      if (b) b.onclick = async () => {
        await api(`/api/runs/${run.id}/cancel`, { method: "POST" });
        toast("Cancelamento solicitado."); draw();
      };
    });
    main.querySelectorAll("[data-ap]").forEach(b => b.onclick = async () => {
      const ok = b.dataset.ok === "1";
      if (!ok) return openRejectModal(b.dataset.ap, () => { missaoSnap = ""; draw(); });
      try {
        await api(`/api/approvals/${b.dataset.ap}/resolve`, { json: {
          approve: true, agents_config: collectTeamConfig(b.dataset.ap) } });
        toast("Aprovado. Executando…");
        missaoSnap = "";
        draw();
      } catch (e) { toast(e.message, true); }
    });
    const bFolder = $id("btnOpenFolder");
    if (bFolder) bFolder.onclick = async () => {
      try { await api(`/api/tasks/${id}/open-folder`, { method: "POST" }); }
      catch (e) { toast(e.message, true); }
    };
    const dbg = $id("dbgToggle");
    if (dbg) dbg.onclick = () => {
      debugOpen = !debugOpen;
      $id("dbgBody").classList.toggle("d-none", !debugOpen);
      dbg.innerHTML = `<i class="fas fa-bug mr-1"></i>${debugOpen ? "Ocultar" : "Mostrar"}`;
    };
    main.querySelectorAll("[data-call]").forEach(b =>
      b.onclick = () => openLlmModal(Number(b.dataset.call)));
    main.querySelectorAll("[data-agent]").forEach(b =>
      b.onclick = () => openAgentModal(Number(b.dataset.agent)));
  }
  await draw();
  poll(draw, 3000);
}

/* ---------------- modal: chamada RAW ao Claude (debug) ---------------- */

async function openLlmModal(id) {
  let c;
  try { c = await api("/api/llm-calls/" + id); } catch (e) { return toast(e.message, true); }
  $id("llmMeta").innerHTML = `
    <span class="badge badge-primary mr-1">${esc(c.kind || "chamada")}</span>
    ${c.agent_name ? `<span class="badge badge-light border mr-1">agente: ${esc(c.agent_name)}</span>` : ""}
    ${c.model ? `<span class="badge badge-light border mr-1">modelo: ${esc(modelShort(c.model))}</span>` : ""}
    ${c.effort ? `<span class="badge badge-light border mr-1">esforço: ${esc(c.effort)}</span>` : ""}
    <span class="badge badge-${c.ok ? "success" : "danger"} mr-1">${c.ok ? "ok" : "erro"}</span>
    <span class="badge badge-light border mr-1">${usd(c.cost_usd)}</span>
    <span class="badge badge-light border mr-1">${((c.duration_ms || 0) / 1000).toFixed(1)}s</span>
    <span class="text-muted small">${fmtTs(c.ts)}</span>`;
  const secs = [
    ["Prompt enviado (RAW)", c.prompt, true],
    ["System prompt do agente", c.system_prompt, true],
    ["Thinking — raciocínio do modelo", c.thinking, true],
    ["Resultado final", c.result, true],
    ["Erro", c.error, true],
    ["Argumentos do CLI", c.args, false],
    ["Saída bruta completa (stream-json)", c.stdout, false],
    ["stderr", c.stderr, false],
  ];
  $id("llmSections").innerHTML = secs.filter(([, v]) => v).map(([t, v, open], i) => `
    <div class="mb-2">
      <div class="small font-weight-bold cursor text-primary" data-toggle="collapse"
           data-target="#llmSec${i}"><i class="fas fa-caret-right mr-1"></i>${t}
        <span class="text-muted font-weight-normal">(${String(v).length.toLocaleString("pt-BR")} caracteres)</span></div>
      <pre class="raw-pre collapse ${open ? "show" : ""}" id="llmSec${i}">${esc(v)}</pre>
    </div>`).join("") || '<p class="text-muted small mb-0">Sem conteúdo registrado.</p>';
  window.jQuery("#llmModal").modal("show");
}

/* ---------------- modal: ficha do agente ---------------- */

async function openAgentModal(id) {
  let a;
  try { a = await api("/api/agents/" + id); } catch (e) { return toast(e.message, true); }
  $id("agentModalName").textContent = a.name;
  $id("agentModalBody").innerHTML = `
    <div class="mb-2">
      ${a.role ? `<span class="badge badge-primary mr-1">${esc(a.role)}</span>` : ""}
      ${a.model ? `<span class="badge badge-info mr-1">modelo: ${esc(modelShort(a.model))}</span>` : ""}
      ${a.effort ? `<span class="badge badge-info mr-1">esforço: ${esc(a.effort)}</span>` : ""}
      ${a.company_name ? `<span class="badge badge-light border mr-1"><i class="fas fa-building mr-1"></i>${esc(a.company_name)}</span>` : ""}
    </div>
    ${a.description ? `<p class="small">${esc(a.description)}</p>` : ""}
    ${a.skills.length ? `<h6 class="small font-weight-bold mb-1">Skills instaladas</h6>
      <div class="mb-3">${a.skills.map(s =>
        `<span class="badge badge-light border mr-1" title="${esc(s.owner_repo)}">${esc(s.skill)}</span>`).join("")}</div>` : ""}
    <h6 class="small font-weight-bold mb-1">Prompt de sistema (persona completa)</h6>
    <pre class="raw-pre show">${esc(a.system_prompt || "(vazio)")}</pre>
    <h6 class="small font-weight-bold mb-1 mt-3">Missões em que atuou</h6>
    ${a.missions && a.missions.length ? `<ul class="small mb-0 pl-3">${a.missions.map(m => `
      <li class="mb-1"><a href="#missao/${m.task_id}" data-dismiss="modal">${esc(m.title)}</a>
        <span class="text-muted">— ${fmtTs(m.started_at)}</span> ${badge(m.status)}</li>`).join("")}</ul>`
      : `<p class="small text-muted mb-0">Ainda não participou de nenhuma missão.</p>`}
    <div class="small text-muted mt-3">Criado em ${fmtTs(a.created_at)} ·
      arquivo <code class="path">.claude/agents/${esc(a.name)}.md</code> no projeto</div>`;
  window.jQuery("#agentModal").modal("show");
}

function approvalPayloadHtml(ap) {
  try {
    const data = JSON.parse(ap.payload || "null");
    // formato novo: {microtasks:[...], team:[...]}; formato antigo: [...]
    const mts = Array.isArray(data) ? data : (data && data.microtasks);
    if (ap.kind === "plan" && Array.isArray(mts)) {
      let html = `<ol class="small mb-0">${mts.map(m =>
        `<li><strong>${esc(m.agente)}:</strong> ${esc(m.descricao)}</li>`).join("")}</ol>`;
      const team = data && !Array.isArray(data) ? data.team : null;
      if (Array.isArray(team) && team.length) {
        html += ap.status === "pendente"
          ? teamEditorHtml(ap.id, team)
          : `<div class="mt-2">${team.map(t =>
              `<span class="badge badge-light border mr-1">${esc(t.name)}${t.model ? ` · ${esc(modelShort(t.model))}` : ""}${t.effort ? `/${esc(t.effort)}` : ""}</span>`).join("")}</div>`;
      }
      return html;
    }
    if (ap.kind === "budget" && data) {
      return `<p class="small mb-0">Custo atual <strong>${usd(data.custo_atual)}</strong> atingiu o teto
        <strong>${usd(data.teto_usd)}</strong>. Aprovar libera a continuação desta execução.</p>`;
    }
  } catch { /* payload livre */ }
  return `<p class="small mb-0">${esc(ap.payload || "")}</p>`;
}

// editor de equipe dentro da aprovação: o gestor pode REBAIXAR modelo/esforço
// de cada agente antes de aprovar (proibir combinações caras)
function teamEditorHtml(apId, team) {
  return `<div class="team-editor mt-3" data-team="${apId}">
    <div class="small font-weight-bold mb-1"><i class="fas fa-users mr-1"></i>
      Equipe proposta — ajuste modelo e esforço antes de aprovar:</div>
    ${team.map(t => `
      <div class="d-flex align-items-center mb-1 team-row flex-wrap" data-name="${esc(t.name)}">
        <span class="small mr-2 team-name">${esc(t.name)}
          <span class="text-muted">${esc(t.role || "")}</span>
          ${t.reports_to ? `<span class="text-muted">· reporta a ${esc(t.reports_to)}</span>` : ""}
          ${(t.skills || []).length ? `<span class="d-block">${t.skills.map(s =>
            `<span class="badge badge-light border mr-1" title="Skill do skills.sh que será usada por este agente">
              <i class="fas fa-bolt text-warning mr-1"></i>${esc(s)}</span>`).join("")}</span>` : ""}
        </span>
        <select class="form-control form-control-sm mr-1 t-model" style="width:auto" title="Modelo">
          ${MODELS.map(m => `<option value="${m}" ${(t.model || "sonnet") === m ? "selected" : ""}>${esc(modelShort(m))}</option>`).join("")}
        </select>
        <select class="form-control form-control-sm t-effort" style="width:auto" title="Esforço">
          ${EFFORTS.map(e2 => `<option value="${e2}" ${(t.effort || "medium") === e2 ? "selected" : ""}>${esc(e2)}</option>`).join("")}
        </select>
      </div>`).join("")}
    <div class="small text-muted">Haiku 4.5 &lt; Sonnet 5 &lt; Opus 4.8 (custo) · low &lt; medium &lt; high (raciocínio).
      Os tetos definidos em Ajustes continuam valendo.
      <strong>Aprovar o plano aprova também a contratação dos agentes e as skills listadas.</strong></div>
  </div>`;
}

function collectTeamConfig(apId) {
  const box = main.querySelector(`[data-team="${apId}"]`);
  if (!box) return undefined;
  return [...box.querySelectorAll(".team-row")].map(r => ({
    name: r.dataset.name,
    model: r.querySelector(".t-model").value,
    effort: r.querySelector(".t-effort").value,
  }));
}

/* ---------------- Aprovações ---------------- */

let aprovacoesSnap = "";

async function renderAprovacoes() {
  const approvals = await api("/api/approvals" + wsQS());
  // redesenha SÓ quando os dados mudam — corrige o histórico "piscando"
  const snap = JSON.stringify(approvals);
  const alreadyDrawn = !!main.querySelector("#aprovacoesWrap");
  if (snap === aprovacoesSnap && alreadyDrawn) {
    poll(() => { if (onView("aprovacoes")) renderAprovacoes(); }, 8000);
    return;
  }
  aprovacoesSnap = snap;
  const pending = approvals.filter(a => a.status === "pendente");
  const resolved = approvals.filter(a => a.status !== "pendente");
  main.innerHTML = `
    <span id="aprovacoesWrap" class="d-none"></span>
    ${wsContextHtml()}
    ${pending.length ? pending.map(a => `
      <div class="card border-left-warning shadow mb-3">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-start">
            <div class="flex-grow-1">
              <h6 class="font-weight-bold mb-1">
                <a href="#missao/${a.task_id}">${esc(a.task_title)}</a>
                <span class="badge badge-warning ml-2">${a.kind === "plan" ? "Plano" : "Budget"}</span>
                ${a.company_name ? `<span class="badge badge-light border ml-1">
                  <i class="fas fa-building mr-1"></i>${esc(a.company_name)}</span>` : ""}
              </h6>
              <div class="small text-muted mb-2">${fmtTs(a.created_at)}</div>
              ${approvalPayloadHtml(a)}
            </div>
            <div class="text-nowrap ml-3">
              <button class="btn btn-success btn-sm mr-1" data-ap="${a.id}" data-ok="1">
                <i class="fas fa-check mr-1"></i>Aprovar</button>
              <button class="btn btn-outline-danger btn-sm" data-ap="${a.id}" data-ok="0"
                title="Reprovar com motivo (replaneja) ou cancelar a missão inteira">
                <i class="fas fa-times mr-1"></i>Reprovar / Cancelar</button>
            </div>
          </div>
        </div>
      </div>`).join("")
      : `<div class="card shadow mb-4"><div class="card-body text-center text-muted py-5">
          <i class="fas fa-check-double fa-2x mb-3 text-gray-300"></i>
          <p class="mb-0">Nenhuma decisão pendente. Tudo em dia.</p></div></div>`}

    ${resolved.length ? `
    ${cardOpen("Histórico de decisões")}
      <div class="table-responsive"><table class="table table-sm">
        <thead><tr><th>Missão</th><th>Empresa</th><th>Tipo</th><th>Estado</th><th>Criada</th><th>Resolvida</th></tr></thead>
        <tbody>${resolved.map(a => `
          <tr><td><a href="#missao/${a.task_id}">${esc(a.task_title)}</a></td>
            <td class="small text-muted">${esc(a.company_name || "")}</td>
            <td>${a.kind === "plan" ? "Plano" : "Budget"}</td>
            <td>${badge(a.status)}</td>
            <td class="small">${fmtTs(a.created_at)}</td>
            <td class="small">${fmtTs(a.resolved_at)}</td></tr>`).join("")}</tbody></table></div>
    ${cardClose}` : ""}`;

  main.querySelectorAll("[data-ap]").forEach(b => b.onclick = async () => {
    const ok = b.dataset.ok === "1";
    if (!ok) return openRejectModal(b.dataset.ap,
      () => { aprovacoesSnap = ""; renderAprovacoes(); refreshTopbar(); });
    try {
      await api(`/api/approvals/${b.dataset.ap}/resolve`, { json: {
        approve: true, agents_config: collectTeamConfig(b.dataset.ap) } });
      toast("Aprovado. Executando…");
      aprovacoesSnap = "";
      renderAprovacoes(); refreshTopbar();
    } catch (e) { toast(e.message, true); }
  });
  poll(() => { if (onView("aprovacoes")) renderAprovacoes(); }, 8000);
}

/* ---------------- Equipe ---------------- */

let orgSnap = "";

function orgNodeHtml(a) {
  const working = a.working;
  const status = working
    ? `<div class="org-status working"><span class="org-dot"></span>
         Trabalhando na <a href="#missao/${working.task_id}">#${working.ord} — ${esc(working.task_title)}</a>
         <div class="org-task">${esc(working.description)}</div></div>`
    : a.queue
      ? `<div class="org-status queued"><i class="fas fa-inbox mr-1"></i>${a.queue} tarefa(s) na fila</div>`
      : `<div class="org-status idle"><i class="fas fa-moon mr-1"></i>Disponível</div>`;
  return `
    <div class="org-node ${working ? "busy" : ""}" data-open="${a.id}">
      <div class="d-flex justify-content-between align-items-start">
        <div class="org-name"><i class="fas fa-user-cog mr-1"></i>${esc(a.name)}</div>
        <button class="btn btn-link btn-sm text-danger p-0" data-del="${a.id}" title="Dispensar agente">
          <i class="fas fa-trash"></i></button>
      </div>
      <div class="org-role">${esc(a.role || "")}</div>
      <div class="org-badges">
        ${a.model ? `<span class="badge badge-info mr-1">${esc(modelShort(a.model))}</span>` : ""}
        ${a.skills.map(s => `<span class="badge badge-light border mr-1">
          <i class="fas fa-bolt text-warning mr-1"></i>${esc(s.skill)}</span>`).join("")}
      </div>
      ${status}
    </div>`;
}

function orgTreeHtml(agents, parent) {
  const kids = agents.filter(a => (a.reports_to || "") === parent);
  if (!kids.length) return "";
  return `<ul class="org-level">${kids.map(a => `
    <li>${orgNodeHtml(a)}${orgTreeHtml(agents, a.name)}</li>`).join("")}</ul>`;
}

async function renderAgentes() {
  let d;
  try { d = await api("/api/team/org" + wsQS()); } catch { return; }
  const agents = d.agents;
  // não redesenha se nada mudou (evita piscar durante o poll)
  const snap = JSON.stringify(d);
  if (snap === orgSnap && main.querySelector("#orgWrap")) {
    poll(() => { if (onView("agentes")) renderAgentes(); }, 4000);
    return;
  }
  orgSnap = snap;
  const names = new Set(agents.map(a => a.name));
  // quem reporta a alguém fora da lista sobe para o Maestro (raiz)
  const norm = agents.map(a => ({ ...a,
    reports_to: names.has(a.reports_to) ? a.reports_to : "" }));
  const workingN = norm.filter(a => a.working).length;

  main.innerHTML = `
    <span id="orgWrap" class="d-none"></span>
    ${wsContextHtml()}
    <p class="text-muted">Organograma da equipe de <strong>${esc(wsLabel() || "suas empresas")}</strong> —
      hierarquia definida pelo Maestro. ${workingN
        ? `<span class="badge badge-success"><i class="fas fa-circle-notch fa-spin mr-1"></i>${workingN} agente(s) trabalhando agora</span>`
        : '<span class="badge badge-secondary">ninguém trabalhando agora</span>'}
      ${d.paused ? `<span class="badge badge-warning ml-1"><i class="fas fa-pause mr-1"></i>${d.paused} execução(ões) pausada(s)</span>` : ""}
      Clique num agente para ver a ficha completa.</p>
    ${agents.length ? `
    <div class="card shadow mb-4"><div class="card-body org-chart">
      <div class="org-root">
        <div class="org-node maestro">
          <div class="org-name"><i class="fas fa-wave-square mr-1"></i>Maestro</div>
          <div class="org-role">Orquestrador — contrata, planeja e distribui o trabalho</div>
        </div>
        ${orgTreeHtml(norm, "")}
      </div>
    </div></div>

    ${cardOpen("Fluxo de trabalho ao vivo — conversa e backlog entre agentes")}
      ${(d.messages || []).length ? `<div class="feed">${d.messages.map(msg => `
        <div class="item-row ${msg.kind === "backlog" ? "warn" : ""}"><time>${fmtTs(msg.ts)}</time>
          <i class="fas fa-${msg.kind === "backlog" ? "tasks" : "comment"} mr-1"></i>
          <strong>${esc(msg.from_agent)}</strong> <i class="fas fa-long-arrow-alt-right mx-1"></i>
          <strong>${esc(msg.to_agent)}</strong>${msg.kind === "backlog"
            ? ' <span class="badge badge-warning">backlog</span>' : ""}: ${esc((msg.content || "").slice(0, 200))}</div>`).join("")}
      </div>` : '<p class="text-muted small mb-0">Sem conversas nas execuções ativas. Quando um agente mandar mensagem ou criar backlog para um colega, aparece aqui.</p>'}
    ${cardClose}`
      : `<div class="card shadow"><div class="card-body text-center text-muted py-5">
          <i class="fas fa-users-cog fa-2x mb-3 text-gray-300"></i>
          <p class="mb-0">Nenhum agente ainda. Execute a primeira missão e o Maestro contratará a equipe.</p></div></div>`}`;

  main.querySelectorAll("[data-open]").forEach(card => card.onclick = (e) => {
    if (e.target.closest("[data-del]") || e.target.closest("a")) return;
    openAgentModal(Number(card.dataset.open));
  });
  main.querySelectorAll("[data-del]").forEach(b => b.onclick = async (e) => {
    e.stopPropagation();
    if (!confirm("Dispensar este agente?")) return;
    await api("/api/agents/" + b.dataset.del, { method: "DELETE" });
    orgSnap = "";
    renderAgentes();
  });
  poll(() => { if (onView("agentes")) renderAgentes(); }, 4000);
}

/* ---------------- Agenda ---------------- */

const DAY_NAMES = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];

async function renderAgenda() {
  const [schedules, tasks] = await Promise.all([
    api("/api/schedules" + wsQS()), api("/api/tasks" + wsQS())]);
  main.innerHTML = `
    ${wsContextHtml()}
    <div class="row"><div class="col-lg-5">
      ${cardOpen("Novo agendamento")}
        ${tasks.length ? `
        <div class="form-group"><label>Missão</label>
          <select class="form-control" id="sTask">${tasks.map(t =>
            `<option value="${t.id}">${esc(t.title)}</option>`).join("")}</select></div>
        <div class="form-group"><label>Horário</label>
          <input type="time" class="form-control" id="sTime" value="09:00"></div>
        <div class="form-group"><label>Dias da semana</label><div>
          ${DAY_NAMES.map((d, i) => `
          <div class="custom-control custom-checkbox custom-control-inline">
            <input type="checkbox" class="custom-control-input sDay" id="d${i}" data-i="${i}" checked>
            <label class="custom-control-label" for="d${i}">${d}</label>
          </div>`).join("")}</div></div>
        <button class="btn btn-primary" id="sSave"><i class="fas fa-save mr-1"></i>Agendar</button>`
        : `<p class="text-muted mb-0">Crie primeiro uma <a href="#missoes">missão</a>.</p>`}
      ${cardClose}
    </div><div class="col-lg-7">
      ${cardOpen("Agendamentos")}
        ${schedules.length ? `<div class="table-responsive"><table class="table table-sm">
          <thead><tr><th>Missão</th><th>Quando</th><th>Estado</th><th class="text-right">Ações</th></tr></thead>
          <tbody>${schedules.map(s => `
            <tr>
              <td class="font-weight-bold">${esc(s.task_title)}</td>
              <td class="small">${esc(s.time_hhmm)} &middot; ${[...(s.days || "1111111")].map((v, i) =>
                v === "1" ? DAY_NAMES[i] : null).filter(Boolean).join(", ")}
                ${s.last_run_date ? `<div class="text-muted">última: ${esc(s.last_run_date)}</div>` : ""}</td>
              <td><span class="badge badge-${s.enabled ? "success" : "secondary"}">${s.enabled ? "Ativo" : "Pausado"}</span></td>
              <td class="text-right text-nowrap">
                <button class="btn btn-outline-secondary btn-sm" data-tgl="${s.id}" title="${s.enabled ? "Pausar" : "Ativar"}">
                  <i class="fas fa-${s.enabled ? "pause" : "play"}"></i></button>
                <button class="btn btn-outline-primary btn-sm" data-win="${s.id}"
                  title="Registrar no Agendador do Windows (roda com o app fechado)"><i class="fab fa-windows"></i></button>
                <button class="btn btn-outline-danger btn-sm" data-del="${s.id}"><i class="fas fa-trash"></i></button>
              </td></tr>`).join("")}</tbody></table></div>`
          : `<p class="text-muted mb-0">Nenhum agendamento.</p>`}
      ${cardClose}
    </div></div>`;

  const save = $id("sSave");
  if (save) save.onclick = async () => {
    const days = [...main.querySelectorAll(".sDay")].map(c => c.checked ? "1" : "0").join("");
    try {
      await api("/api/schedules", { json: {
        task_id: Number($id("sTask").value), time_hhmm: $id("sTime").value, days } });
      toast("Agendado."); renderAgenda();
    } catch (e) { toast(e.message, true); }
  };
  main.querySelectorAll("[data-tgl]").forEach(b => b.onclick = async () => {
    await api(`/api/schedules/${b.dataset.tgl}/toggle`, { method: "POST" }); renderAgenda();
  });
  main.querySelectorAll("[data-win]").forEach(b => b.onclick = async () => {
    const r = await api(`/api/schedules/${b.dataset.win}/windows`, { method: "POST" });
    toast(r.ok ? "Registrado no Agendador do Windows." : "Falhou: " + r.output, !r.ok);
  });
  main.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    await api("/api/schedules/" + b.dataset.del, { method: "DELETE" }); renderAgenda();
  });
}

/* ---------------- WhatsApp ---------------- */

let waSnap = "";

async function renderWhatsapp() {
  let st;
  try { st = await api("/api/whatsapp/status"); } catch { return; }
  const snap = JSON.stringify(st);
  if (snap === waSnap && main.querySelector("#waWrap")) {
    poll(() => { if (onView("whatsapp")) renderWhatsapp(); }, 3000);
    return;
  }
  waSnap = snap;

  const S = {
    desligado: ["Desligado", "secondary"],
    preparando: ["Preparando…", "info"],
    instalando: ["Instalando componentes (primeira vez, pode levar minutos)…", "info"],
    conectando: ["Conectando… o QR aparece em alguns segundos", "info"],
    aguardando_qr: ["Aguardando você escanear o QR code", "warning"],
    conectado: ["Conectado", "success"],
    erro: ["Erro", "danger"],
  };
  const [label, color] = S[st.status] || [st.status, "secondary"];

  main.innerHTML = `
    <span id="waWrap" class="d-none"></span>
    <div class="row"><div class="col-lg-6">
      ${cardOpen("Conexão do WhatsApp")}
        <p class="small text-muted">Conecte um número de WhatsApp e opere o Maestro inteiro
          pela conversa: criar empresas, projetos e missões, aprovar planos, pausar a empresa e
          receber avisos — tudo por menus numerados, sem IA.</p>
        <div class="mb-3">
          <span class="badge badge-${color}">${label}</span>
          ${st.me ? `<span class="badge badge-light border ml-1"><i class="fab fa-whatsapp mr-1"></i>+${esc(st.me)}</span>` : ""}
          ${st.error ? `<div class="small text-danger mt-2">${esc(st.error)}</div>` : ""}
        </div>
        ${st.status === "aguardando_qr" ? `
          <div class="text-center mb-3">
            <img src="/api/whatsapp/qr.png?t=${Date.now()}" alt="QR code" class="border rounded" style="max-width:260px">
            <p class="small text-muted mt-2 mb-0">No celular: WhatsApp → Configurações →
              <strong>Aparelhos conectados</strong> → Conectar aparelho → escaneie.</p>
          </div>` : ""}
        ${!st.running ? `
          <button class="btn btn-success" id="waStart"><i class="fab fa-whatsapp mr-1"></i>Conectar WhatsApp</button>`
        : `
          <button class="btn btn-outline-secondary btn-sm mr-2" id="waStop">
            <i class="fas fa-power-off mr-1"></i>Desligar (mantém a conta)</button>
          <button class="btn btn-outline-danger btn-sm" id="waLogout">
            <i class="fas fa-sign-out-alt mr-1"></i>Desconectar conta (novo QR)</button>`}
      ${cardClose}

      ${cardOpen("Código de autorização")}
        <p class="small text-muted">Qualquer pessoa que enviar este código de 6 dígitos para o
          número conectado passa a operar o Maestro. Compartilhe só com quem você confia.</p>
        <div class="d-flex align-items-center">
          <span class="h3 mb-0 mr-3 font-weight-bold text-primary" style="letter-spacing:.4rem">${esc(st.secret)}</span>
          <button class="btn btn-outline-danger btn-sm" id="waRotate" title="Gerar novo código (o antigo para de valer)">
            <i class="fas fa-sync mr-1"></i>Regenerar</button>
        </div>
      ${cardClose}
    </div><div class="col-lg-6">
      ${cardOpen("Operadores autorizados")}
        ${st.operators.length ? `<div class="table-responsive"><table class="table table-sm">
          <thead><tr><th>Número</th><th>Autorizado em</th><th class="text-right"></th></tr></thead>
          <tbody>${st.operators.map(o => `
            <tr><td class="font-weight-bold">+${esc(o.phone)}</td>
              <td class="small">${fmtTs(o.created_at)}</td>
              <td class="text-right"><button class="btn btn-outline-danger btn-sm" data-rev="${esc(o.phone)}"
                title="Revogar acesso"><i class="fas fa-user-slash"></i></button></td></tr>`).join("")}
          </tbody></table></div>`
          : `<p class="text-muted small mb-0">Ninguém autorizado ainda. Peça para a pessoa mandar
              o código de 6 dígitos para o número conectado.</p>`}
      ${cardClose}

      ${cardOpen("Notificações enviadas no WhatsApp")}
        ${[["wa_notify_approval", "Aprovação pendente (responder 1/2/3 na conversa)"],
           ["wa_notify_result", "Missão concluída ou falhou"],
           ["wa_notify_team", "Conversa e backlog entre agentes"]].map(([k, lbl]) => `
          <div class="custom-control custom-switch mb-2">
            <input type="checkbox" class="custom-control-input" id="sw_${k}" data-notif="${k}"
              ${st.settings[k] ? "checked" : ""}>
            <label class="custom-control-label" for="sw_${k}">${lbl}</label>
          </div>`).join("")}
      ${cardClose}
    </div></div>`;

  const bStart = $id("waStart");
  if (bStart) bStart.onclick = async () => {
    bStart.disabled = true;
    try { await api("/api/whatsapp/start", { method: "POST" }); toast("Iniciando WhatsApp…"); }
    catch (e) { toast(e.message, true); }
    waSnap = ""; renderWhatsapp();
  };
  const bStop = $id("waStop");
  if (bStop) bStop.onclick = async () => {
    await api("/api/whatsapp/stop", { json: { logout: false } });
    waSnap = ""; renderWhatsapp();
  };
  const bLogout = $id("waLogout");
  if (bLogout) bLogout.onclick = async () => {
    if (!confirm("Desconectar a conta do WhatsApp? Será preciso escanear um novo QR code.")) return;
    await api("/api/whatsapp/stop", { json: { logout: true } });
    waSnap = ""; renderWhatsapp();
  };
  $id("waRotate").onclick = async () => {
    if (!confirm("Gerar novo código? O antigo deixa de autorizar novas pessoas (quem já está autorizado continua).")) return;
    await api("/api/whatsapp/secret/rotate", { method: "POST" });
    waSnap = ""; renderWhatsapp();
  };
  main.querySelectorAll("[data-rev]").forEach(b => b.onclick = async () => {
    if (!confirm("Revogar o acesso deste número?")) return;
    await api("/api/whatsapp/operators/" + b.dataset.rev, { method: "DELETE" });
    waSnap = ""; renderWhatsapp();
  });
  main.querySelectorAll("[data-notif]").forEach(sw => sw.onchange = async () => {
    await api("/api/settings", { json: { [sw.dataset.notif]: String(sw.checked) } });
    toast("Preferência salva.");
  });
  poll(() => { if (onView("whatsapp")) renderWhatsapp(); }, 3000);
}

/* ---------------- Exportar / Importar ---------------- */

async function renderPorter() {
  main.innerHTML = `
    <div class="row"><div class="col-lg-6">
      ${cardOpen("Exportar (levar para outro computador)")}
        <p class="small text-muted">Gera um pacote .zip com empresas, projetos, missões, agentes,
          skills, histórico e arquivos gerados. Agentes e skills usam o formato nativo do Claude Code.</p>
        <div class="custom-control custom-checkbox mb-3">
          <input type="checkbox" class="custom-control-input" id="expArt" checked>
          <label class="custom-control-label" for="expArt">Incluir os arquivos dos projetos (recomendado)</label>
        </div>
        <button class="btn btn-primary" id="btnExp"><i class="fas fa-file-export mr-1"></i>Gerar pacote</button>
        <div id="expResult" class="mt-3"></div>
      ${cardClose}
    </div><div class="col-lg-6">
      ${cardOpen("Importar (trazer de outro computador)")}
        <p class="small text-muted">Escolha um pacote .zip gerado pelo Maestro.</p>
        <div class="form-group"><input type="file" id="impFile" accept=".zip"></div>
        <button class="btn btn-primary" id="btnImp"><i class="fas fa-file-import mr-1"></i>Importar pacote</button>
      ${cardClose}
    </div></div>`;

  $id("btnExp").onclick = async () => {
    const b = $id("btnExp"); b.disabled = true;
    try {
      const r = await api("/api/export", { json: { include_artifacts: $id("expArt").checked } });
      $id("expResult").innerHTML =
        `<a class="btn btn-success" href="/api/export/download/${encodeURIComponent(r.file)}" download>
           <i class="fas fa-download mr-1"></i>Baixar ${esc(r.file)} (${r.size_mb} MB)</a>`;
      toast("Pacote pronto.");
    } catch (e) { toast(e.message, true); }
    b.disabled = false;
  };
  $id("btnImp").onclick = async () => {
    const f = $id("impFile").files[0];
    if (!f) return toast("Escolha um arquivo .zip primeiro.", true);
    const fd = new FormData(); fd.append("file", f);
    const b = $id("btnImp"); b.disabled = true;
    try {
      const r = await api("/api/import", { method: "POST", body: fd });
      toast("Importado: " + Object.entries(r).filter(([, v]) => v).map(([k, v]) => `${v} ${k}`).join(", "));
    } catch (e) { toast(e.message, true); }
    b.disabled = false;
  };
}

/* ---------------- Ajustes ---------------- */

// rótulos amigáveis: valor guardado é o alias (haiku/sonnet/opus); exibe a versão concreta
const OPT_LABELS = {
  haiku: "Haiku 4.5 — rápido e econômico",
  sonnet: "Sonnet 5 — equilíbrio (padrão)",
  opus: "Opus 4.8 — máxima capacidade",
  low: "Baixo — mecânico",
  medium: "Médio — padrão",
  high: "Alto — problemas difíceis",
  "0": "0 — Somente sugestão (só planeja)",
  "1": "1 — Aprovação obrigatória (padrão seguro)",
  "3": "3 — Autônomo (exige termo de risco)",
  "4": "4 — YOLO total (auto-aprova tudo)",
  "true": "Ligado", "false": "Desligado",
};
const optLabel = v => OPT_LABELS[v] || v;

const SETTING_FIELDS = {
  model: ["Modelo padrão dos agentes (quando a IA não escolher)", "select", ["haiku", "sonnet", "opus"]],
  brain_model: ["Modelo do Maestro (decisões)", "select", ["haiku", "sonnet", "opus"]],
  max_model: ["Modelo MÁXIMO permitido por agente (teto anti-gasto)", "select", ["haiku", "sonnet", "opus"]],
  max_effort: ["Esforço MÁXIMO de raciocínio por agente", "select", ["low", "medium", "high"]],
  autonomy_default: ["Autonomia padrão de novas missões", "select", ["0", "1", "3", "4"]],
  max_cost_per_run: ["Teto de custo por execução (US$, budget hard-stop)", "text"],
  heartbeat_retry_min: ["Heartbeat: nova tentativa após limite (min)", "text"],
  max_concurrency: ["Micro-tarefas em paralelo (máx.)", "text"],
  task_timeout_min: ["Tempo máximo por micro-tarefa (min)", "text"],
  min_installs: ["Popularidade mínima das skills (installs)", "text"],
  max_agents: ["Máximo de agentes por missão", "text"],
  yolo: ["Permissões automáticas do Claude (bypass)", "select", ["true", "false"]],
};

async function renderAjustes() {
  const st = await api("/api/settings");
  main.innerHTML = `
    <div class="row"><div class="col-lg-6">
      ${cardOpen("Configurações")}
        ${Object.entries(SETTING_FIELDS).map(([key, [label, kind, opts]]) => `
        <div class="form-group">
          <label class="small font-weight-bold mb-1">${label}</label>
          ${kind === "select"
            ? `<select class="form-control form-control-sm" data-k="${key}">${opts.map(o =>
                `<option value="${o}" ${String(st[key]) === o ? "selected" : ""}>${esc(optLabel(o))}</option>`).join("")}</select>`
            : `<input class="form-control form-control-sm" data-k="${key}" value="${esc(st[key])}">`}
        </div>`).join("")}
        <button class="btn btn-primary" id="stSave"><i class="fas fa-save mr-1"></i>Salvar ajustes</button>
      ${cardClose}

      ${cardOpen("Acesso externo (API para outros agentes)")}
        <p class="small text-muted">Documentação interativa em
          <a href="/docs" target="_blank">/docs (Swagger)</a>. Gere um link público para
          outro Maestro/agente orquestrar este pela internet — o link exige o token abaixo
          no header <code>X-API-Token</code>.</p>
        <div class="form-group">
          <label class="small font-weight-bold mb-1">Token de API</label>
          <div class="input-group input-group-sm">
            <input class="form-control" id="apiToken" readonly>
            <div class="input-group-append">
              <button class="btn btn-outline-secondary" id="btnCopyToken" title="Copiar"><i class="fas fa-copy"></i></button>
              <button class="btn btn-outline-danger" id="btnRotateToken" title="Gerar novo token"><i class="fas fa-sync"></i></button>
            </div>
          </div>
        </div>
        <div id="tunnelBox">
          <button class="btn btn-primary btn-sm" id="btnTunnelStart">
            <i class="fas fa-globe mr-1"></i>Gerar link público</button>
        </div>
      ${cardClose}

      ${cardOpen("Auditoria (trilha imutável)")}
        <p class="small text-muted">Todos os eventos são encadeados por hash — alterações são detectáveis.</p>
        <button class="btn btn-outline-primary btn-sm mr-2" id="btnVerify"><i class="fas fa-shield-alt mr-1"></i>Verificar integridade</button>
        <a class="btn btn-outline-secondary btn-sm" href="/api/audit/export"><i class="fas fa-download mr-1"></i>Exportar auditoria</a>
        <span id="verifyResult" class="small ml-2"></span>
      ${cardClose}
    </div><div class="col-lg-6">
      ${cardOpen("Catálogo de skills (skills.sh)", `<button class="btn btn-outline-primary btn-sm" id="skRefresh">
        <i class="fas fa-sync mr-1"></i>Atualizar</button>`)}
        <div class="table-responsive" style="max-height:420px;overflow-y:auto">
          <table class="table table-sm" id="skTable"><thead>
            <tr><th>Skill</th><th>Repositório</th><th class="text-right">Instalações</th></tr></thead>
            <tbody></tbody></table>
        </div>
      ${cardClose}

      ${cardOpen("Consentimentos registrados")}
        <div id="consentList" class="feed"></div>
      ${cardClose}
    </div></div>`;

  $id("stSave").onclick = async () => {
    const payload = {};
    main.querySelectorAll("[data-k]").forEach(el => payload[el.dataset.k] = el.value);
    await api("/api/settings", { json: payload });
    toast("Ajustes salvos.");
  };
  // --- acesso externo: token + túnel ---
  async function refreshTunnel() {
    const t = await api("/api/tunnel");
    $id("apiToken").value = t.api_token || "";
    $id("tunnelBox").innerHTML = t.running ? `
      <div class="alert alert-success py-2 px-3 small mb-2">
        <i class="fas fa-globe mr-1"></i>Link público ativo (${esc(t.backend)}):<br>
        <strong class="text-break">${esc(t.url)}</strong>
        <button class="btn btn-outline-secondary btn-sm ml-2 py-0" id="btnCopyUrl"><i class="fas fa-copy"></i></button>
      </div>
      <p class="small text-muted mb-2">Exemplo de uso remoto:<br>
        <code class="small">curl -H "X-API-Token: SEU_TOKEN" ${esc(t.url)}/api/status</code></p>
      <button class="btn btn-outline-danger btn-sm" id="btnTunnelStop">
        <i class="fas fa-stop mr-1"></i>Desligar link público</button>`
      : `<button class="btn btn-primary btn-sm" id="btnTunnelStart">
          <i class="fas fa-globe mr-1"></i>Gerar link público</button>
        ${t.error ? `<div class="small text-danger mt-2">${esc(t.error)}</div>` : ""}`;
    const bs = $id("btnTunnelStart");
    if (bs) bs.onclick = async () => {
      bs.disabled = true; bs.innerHTML = '<i class="fas fa-circle-notch fa-spin mr-1"></i>Criando link…';
      try { await api("/api/tunnel/start", { method: "POST" }); toast("Link público criado."); }
      catch (e) { toast(e.message, true); }
      refreshTunnel();
    };
    const bp = $id("btnTunnelStop");
    if (bp) bp.onclick = async () => { await api("/api/tunnel/stop", { method: "POST" }); refreshTunnel(); };
    const bc = $id("btnCopyUrl");
    if (bc) bc.onclick = () => { navigator.clipboard.writeText(t.url); toast("Link copiado."); };
  }
  $id("btnCopyToken").onclick = () => {
    navigator.clipboard.writeText($id("apiToken").value); toast("Token copiado.");
  };
  $id("btnRotateToken").onclick = async () => {
    if (!confirm("Gerar um novo token? Integrações usando o token atual param de funcionar.")) return;
    await api("/api/token/rotate", { method: "POST" });
    refreshTunnel(); toast("Novo token gerado.");
  };
  refreshTunnel();

  $id("btnVerify").onclick = async () => {
    const r = await api("/api/audit/verify");
    $id("verifyResult").innerHTML = r.ok
      ? `<span class="text-success"><i class="fas fa-check mr-1"></i>Íntegra (${r.total} eventos)</span>`
      : `<span class="text-danger"><i class="fas fa-times mr-1"></i>Quebra no evento ${r.broken_at}</span>`;
  };
  async function loadSkills() {
    const skills = await api("/api/skills");
    main.querySelector("#skTable tbody").innerHTML = skills.map(s => `
      <tr><td class="font-weight-bold small">${esc(s.skill)}</td>
        <td class="small text-muted">${esc(s.owner_repo)}</td>
        <td class="small text-right">${Number(s.installs).toLocaleString("pt-BR")}</td></tr>`).join("")
      || '<tr><td colspan="3" class="text-muted">Catálogo vazio.</td></tr>';
  }
  $id("skRefresh").onclick = async () => {
    const r = await api("/api/skills/refresh", { method: "POST" });
    toast(`Catálogo atualizado (${r.count} skills, fonte: ${r.source}).`);
    loadSkills();
  };
  async function loadConsents() {
    const rows = await api("/api/consents");
    $id("consentList").innerHTML = rows.map(c => `
      <div class="item-row"><time>${fmtTs(c.ts)}</time>
        nível ${c.level} — ${esc(c.scope)} — termo v${esc(c.term_version)}
        <span class="text-muted">(${esc(c.term_hash.slice(0, 12))}…, ${esc(c.user_host)})</span></div>`).join("")
      || '<p class="text-muted small mb-0">Nenhum consentimento registrado.</p>';
  }
  loadSkills(); loadConsents();
}

/* ---------------- boot ---------------- */
(async () => {
  refreshTopbar();
  await Promise.all([fetchJourney(), loadWorkspaces()]);
  const initial = location.hash.slice(1);
  // primeiro uso: leva direto para a jornada guiada (respeita links diretos)
  if (!journey.complete && (!initial || initial === "painel")) {
    location.hash = "#comecar";
  }
  navigate();
})();
