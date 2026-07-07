let RAW = [];
let DATA = [];
let charts = {};

const $ = (id) => document.getElementById(id);

function fmt(n) {
  return Number(n || 0).toLocaleString("pt-BR");
}

function fmtMoney(n) {
  return Number(n || 0).toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL"
  });
}

function pct(parte, total) {
  if (!total) return "0%";
  return ((Number(parte || 0) / Number(total || 0)) * 100)
    .toFixed(1)
    .replace(".", ",") + "%";
}

function pctNum(parte, total) {
  if (!total) return 0;
  return (Number(parte || 0) / Number(total || 0)) * 100;
}

function showToast(msg) {
  const el = $("toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3000);
}

function destroyChart(id) {
  if (charts[id]) {
    charts[id].destroy();
    charts[id] = null;
  }
}

function parseBRDate(value) {
  if (!value) return null;

  const parts = String(value).trim().split("/");
  if (parts.length !== 3) return null;

  const d = Number(parts[0]);
  const m = Number(parts[1]);
  let y = Number(parts[2]);

  if (!d || !m || !y) return null;
  if (y < 100) y += 2000;

  return new Date(y, m - 1, d);
}

function getYear(value) {
  const d = parseBRDate(value);
  return d ? String(d.getFullYear()) : "";
}

function getMonth(value) {
  const d = parseBRDate(value);
  if (!d) return "";
  return String(d.getMonth() + 1).padStart(2, "0");
}

function sameDay(a, b) {
  return a && b &&
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
}

function addDays(base, days) {
  const d = new Date(base);
  d.setDate(d.getDate() + days);
  return d;
}

function optionize(selectId, values, labelDefault) {
  const el = $(selectId);
  if (!el) return;

  const current = el.value;
  el.innerHTML = `<option value="">${labelDefault}</option>`;

  values
    .filter(Boolean)
    .sort((a, b) => String(a).localeCompare(String(b), "pt-BR"))
    .forEach(v => {
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      el.appendChild(opt);
    });

  if ([...el.options].some(o => o.value === current)) {
    el.value = current;
  }
}

function unique(field) {
  return [...new Set(RAW.map(x => x[field]).filter(Boolean))];
}

function bindFilters() {
  ["fAno", "fMes", "fUf", "fCliente", "fUnidade", "fOcorrencia", "fOperacao"].forEach(id => {
    const el = $(id);
    if (el) el.addEventListener("change", applyFilters);
  });

  const clear = $("clearFiltersBtn");
  if (clear) {
    clear.addEventListener("click", () => {
      ["fAno", "fMes", "fUf", "fCliente", "fUnidade", "fOcorrencia", "fOperacao"].forEach(id => {
        if ($(id)) $(id).value = "";
      });
      applyFilters();
    });
  }
}

function populateFilters() {
  RAW.forEach(x => {
    x._ano = getYear(x.emissao);
    x._mes = getMonth(x.emissao);
  });

  optionize("fAno", unique("_ano"), "Todos");
  optionize("fMes", unique("_mes"), "Todos");
  optionize("fUf", unique("uf"), "Todas");
  optionize("fCliente", unique("cliente"), "Todos");
  optionize("fUnidade", unique("unidadeReceptora"), "Todas");
  optionize("fOcorrencia", unique("ocorrencia"), "Todas");
  const operacoes = [...new Set(RAW.map(x => nomeOperacao(x.operacao)).filter(Boolean))];

  const ordemOperacoes = ["COMPLEMENTAR", "CORTESIA", "NORMAL", "REVERSA"];

  optionize(
    "fOperacao",
    ordemOperacoes.filter(op => operacoes.includes(op)),
    "Todas"
  );
}

function applyFilters() {
  const ano = $("fAno")?.value || "";
  const mes = $("fMes")?.value || "";
  const uf = $("fUf")?.value || "";
  const cliente = $("fCliente")?.value || "";
  const unidade = $("fUnidade")?.value || "";
  const ocorrencia = $("fOcorrencia")?.value || "";
  const operacao = $("fOperacao")?.value || "";

  DATA = RAW.filter(x => {
    if (ano && x._ano !== ano) return false;
    if (mes && x._mes !== mes) return false;
    if (uf && x.uf !== uf) return false;
    if (cliente && x.cliente !== cliente) return false;
    if (unidade && x.unidadeReceptora !== unidade) return false;
    if (ocorrencia && x.ocorrencia !== ocorrencia) return false;
    if (operacao && nomeOperacao(x.operacao) !== operacao) return false;
    return true;
  });

  renderDashboard();
}

function groupBy(field, source = DATA) {
  const map = {};

  source.forEach(item => {
    const key = item[field] || "Não informado";

    if (!map[key]) {
      map[key] = {
        label: key,
        total: 0,
        entregue: 0,
        aberto: 0,
        prazo: 0,
        antecipado: 0,
        atraso: 0,
        justificado: 0,
        ocorr73: 0,
        romaneio: 0,
        baixaMobile: 0,
        baixaBase: 0,
        volumes: 0,
        cubagem: 0,
        peso: 0,
        frete: 0
      };
    }

    const r = map[key];
    r.total++;

    if (item.status === "Entregue") r.entregue++;
    if (item.status === "Em aberto") r.aberto++;
    if (item.prazo === "No prazo") r.prazo++;
    if (item.prazo === "Antecipado") r.antecipado++;
    if (item.prazo === "Atrasado") r.atraso++;
    if (item.prazo === "Justificado") r.justificado++;
    if (item.ocorr73 === "SIM") r.ocorr73++;
    if (item.romaneio === "SIM") r.romaneio++;
    if (isMobile(item)) r.baixaMobile++;
    if (hasBaixaInfo(item)) r.baixaBase++;

    r.volumes += Number(item.volumes || 0);
    r.cubagem += Number(item.cubagem || 0);
    r.peso += Number(item.peso || 0);
    r.frete += Number(item.frete || 0);
  });

  return Object.values(map);
}

function topRanking(field, limit = 5, source = DATA) {
  return groupBy(field, source)
    .sort((a, b) => b.total - a.total)
    .slice(0, limit);
}

function renderLines(containerId, rows, totalBase = DATA.length) {
  const el = $(containerId);
  if (!el) return;

  el.innerHTML = rows.map((r, i) => `
    <div class="line">
      <span>${i + 1}. ${r.label}</span>
      <span>
        <b>${fmt(r.total)}</b>
        <em>${pct(r.total, totalBase)}</em>
      </span>
    </div>
  `).join("");
}

function kpis() {
  const total = DATA.length;
  const entregue = DATA.filter(x => x.status === "Entregue").length;
  const aberto = DATA.filter(x => x.status === "Em aberto").length;
  const prazo = DATA.filter(x => x.prazo === "No prazo").length;
  const antecipado = DATA.filter(x => x.prazo === "Antecipado").length;
  const atraso = DATA.filter(x => x.prazo === "Atrasado").length;
  const justificado = DATA.filter(x => x.prazo === "Justificado").length;
  const ocorr73 = DATA.filter(x => x.ocorr73 === "SIM").length;
  const romaneio = DATA.filter(x => x.romaneio === "SIM").length;
  const entregues = DATA.filter(x => x.status === "Entregue");
  const baixaBase = entregues.filter(hasBaixaInfo).length;
  const baixaMobile = entregues.filter(isMobile).length;

  return {
    total,
    entregue,
    aberto,
    prazo,
    antecipado,
    atraso,
    justificado,
    ocorr73,
    romaneio,
    baixaMobile,
    nivelServico: pctNum(prazo + antecipado + justificado, entregue),
    pctMobile: pctNum(baixaMobile, baixaBase),
    pctRomaneio: pctNum(romaneio, entregue)
  };
}

function renderCards() {
  const k = kpis();

  $("kTotalTitle").textContent = fmt(k.total);

  $("kEntregue").textContent = fmt(k.entregue);
  $("kEntreguePct").textContent = pct(k.entregue, k.total);

  $("kAberto").textContent = fmt(k.aberto);
  $("kAbertoPct").textContent = pct(k.aberto, k.total);

  $("kPrazo").textContent = fmt(k.prazo);
  $("kPrazoPct").textContent = pct(k.prazo, k.entregue);

  $("kAntecip").textContent = fmt(k.antecipado);
  $("kAntecipPct").textContent = pct(k.antecipado, k.entregue);

  $("kAtraso").textContent = fmt(k.atraso);
  $("kAtrasoPct").textContent = pct(k.atraso, k.entregue);

  $("kJustificado").textContent = fmt(k.justificado);
  $("kJustificadoPct").textContent = pct(k.justificado, k.entregue);

  $("kOcorr73").textContent = fmt(k.ocorr73);
  $("kOcorr73Pct").textContent = pct(k.ocorr73, k.entregue);

  $("kRomaneioVal").textContent = fmt(k.romaneio);
  $("kRomaneioPct").textContent = pct(k.romaneio, k.entregue);

  $("gaugePct").textContent = k.nivelServico.toFixed(1).replace(".", ",") + "%";
  $("gaugeMobilePct").textContent = k.pctMobile.toFixed(1).replace(".", ",") + "%";

  const meta = $("gaugeMetaStatus");
  meta.textContent = k.nivelServico >= 97 ? "Atingida" : "Abaixo";
  meta.className = "meta-status " + (k.nivelServico >= 97 ? "ok" : "bad");

  const volumes = DATA.reduce((s, x) => s + Number(x.volumes || 0), 0);
  const cubagem = DATA.reduce((s, x) => s + Number(x.cubagem || 0), 0);
  const peso = DATA.reduce((s, x) => s + Number(x.peso || 0), 0);

  $("kVol").textContent = fmt(volumes);
  $("kCub").textContent = cubagem.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
  $("kFre").textContent = (peso / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 2 }) + " t";

  renderGauge("gaugeChart", k.nivelServico);
  renderGauge("gaugeMobile", k.pctMobile);

  renderLines("kCliTop5", topRanking("cliente", 5));
  renderLines("kUniTop5", topRanking("unidadeReceptora", 5));

  renderUfResumo();
  renderPrevisao();
  renderAging();
}

function renderUfResumo() {
  const rows = topRanking("uf", 5);
  const uf = rows[0];

  if (!uf) {
    $("kUfTitle").textContent = "—";
    $("kUfResumo").innerHTML = "";
    return;
  }

  $("kUfTitle").textContent = uf.label;

  $("kUfResumo").innerHTML = `
    <div class="line"><span>Total de Entregas</span><span><b>${fmt(uf.total)}</b> <em>${pct(uf.total, DATA.length)}</em></span></div>
    <div class="line"><span>Entregue</span><span><b>${fmt(uf.entregue)}</b> <em>${pct(uf.entregue, uf.total)}</em></span></div>
    <div class="line warn"><span>Em aberto</span><span><b>${fmt(uf.aberto)}</b> <em>${pct(uf.aberto, uf.total)}</em></span></div>
    <div class="line good"><span>No prazo</span><span><b>${fmt(uf.prazo)}</b> <em>${pct(uf.prazo, uf.entregue)}</em></span></div>
    <div class="line accent"><span>Antecipadas</span><span><b>${fmt(uf.antecipado)}</b> <em>${pct(uf.antecipado, uf.entregue)}</em></span></div>
    <div class="line bad"><span>Atrasado</span><span><b>${fmt(uf.atraso)}</b> <em>${pct(uf.atraso, uf.entregue)}</em></span></div>
  `;
}

function renderPrevisao() {
  const hoje = new Date();
  const datas = [0, 1, 2, 3].map(n => addDays(hoje, n));
  const abertos = DATA.filter(x => x.status === "Em aberto");

  const counts = datas.map(d =>
    abertos.filter(x => sameDay(parseBRDate(x.previsao), d)).length
  );

  $("kPrevTitle").textContent = fmt(counts.reduce((a, b) => a + b, 0));

  counts.forEach((count, i) => {
    $("kPrev" + i).textContent = fmt(count);
    $("kPrevDate" + i).textContent = datas[i].toLocaleDateString("pt-BR");
  });
}

function renderAging() {
  const abertos = DATA.filter(x => x.status === "Em aberto");
  const total = abertos.length;

  const buckets = [
    { label: "1 a 3 dias", value: abertos.filter(x => Number(x.diasAtraso || 0) >= 1 && Number(x.diasAtraso || 0) <= 3).length },
    { label: "4 a 7 dias", value: abertos.filter(x => Number(x.diasAtraso || 0) >= 4 && Number(x.diasAtraso || 0) <= 7).length },
    { label: "8 a 15 dias", value: abertos.filter(x => Number(x.diasAtraso || 0) >= 8 && Number(x.diasAtraso || 0) <= 15).length },
    { label: "16 dias ou mais", value: abertos.filter(x => Number(x.diasAtraso || 0) >= 16).length }
  ];

  $("kAgingTitle").textContent = fmt(total);

  $("kAgingResumo").innerHTML = buckets.map(b => `
    <div class="line">
      <span>${b.label}</span>
      <span><b>${fmt(b.value)}</b> <em>${pct(b.value, total)}</em></span>
    </div>
  `).join("");
}

function renderGauge(id, value) {
  destroyChart(id);

  const canvas = $(id);
  if (!canvas) return;

  charts[id] = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: ["Realizado", "Restante"],
      datasets: [{
        data: [Math.min(value, 100), Math.max(0, 100 - value)],
        borderWidth: 0
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "75%",
      rotation: -90,
      circumference: 180,
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false }
      }
    }
  });
}

function renderBar(id, labels, values, label, horizontal = false) {
  destroyChart(id);

  const canvas = $(id);
  if (!canvas) return;

  charts[id] = new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label,
        data: values,
        borderWidth: 0
      }]
    },
    options: {
      indexAxis: horizontal ? "y" : "x",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false }
      },
      scales: {
        y: { beginAtZero: true },
        x: { beginAtZero: true }
      }
    }
  });
}

function renderLine(id, labels, values, label) {
  destroyChart(id);

  const canvas = $(id);
  if (!canvas) return;

  charts[id] = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label,
        data: values,
        tension: 0.3,
        fill: false
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false
    }
  });
}

function renderCharts() {
  const ocorr73Dia = groupBy("diaEmissao", DATA.filter(x => x.ocorr73 === "SIM"))
    .sort((a, b) => Number(a.label) - Number(b.label));

  renderLine(
    "chartOcorr73Dia",
    ocorr73Dia.map(x => x.label),
    ocorr73Dia.map(x => x.total),
    "Ocorrência 73"
  );

  const unidades = groupBy("unidadeReceptora")
    .map(x => ({
      ...x,
      ns: pctNum(x.prazo + x.antecipado + x.justificado, x.entregue)
    }))
    .sort((a, b) => a.ns - b.ns);

  renderBar(
    "chartUnidade",
    unidades.map(x => x.label),
    unidades.map(x => Number(x.ns.toFixed(1))),
    "% no prazo",
    unidades.length > 10
  );

  const ufs = groupBy("uf")
    .map(x => ({
      ...x,
      ns: pctNum(x.prazo + x.antecipado + x.justificado, x.entregue)
    }))
    .sort((a, b) => b.ns - a.ns);

  renderBar(
    "chartUf",
    ufs.map(x => x.label),
    ufs.map(x => Number(x.ns.toFixed(1))),
    "% no prazo"
  );

  const clientes = groupBy("cliente")
    .map(x => ({
      ...x,
      ns: pctNum(x.prazo + x.antecipado + x.justificado, x.entregue)
    }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 20);

  renderBar(
    "chartCliente",
    clientes.map(x => x.label),
    clientes.map(x => Number(x.ns.toFixed(1))),
    "% no prazo",
    true
  );

  renderEmissaoEntregaDia();
  renderNivelMensal();
  renderRankings();
}

function renderEmissaoEntregaDia() {
  const map = {};

  DATA.forEach(x => {
    const diaEmissao = x.diaEmissao || "0";
    if (!map[diaEmissao]) map[diaEmissao] = { emissao: 0, entrega: 0 };
    map[diaEmissao].emissao++;

    const entrega = parseBRDate(x.entrega);
    if (entrega) {
      const diaEntrega = String(entrega.getDate()).padStart(2, "0");
      if (!map[diaEntrega]) map[diaEntrega] = { emissao: 0, entrega: 0 };
      map[diaEntrega].entrega++;
    }
  });

  const labels = Object.keys(map).sort((a, b) => Number(a) - Number(b));

  destroyChart("chartDia");

  charts.chartDia = new Chart($("chartDia"), {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Emissão", data: labels.map(d => map[d].emissao), tension: 0.3 },
        { label: "Entrega", data: labels.map(d => map[d].entrega), tension: 0.3 }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false
    }
  });
}

function renderNivelMensal() {
  const rows = groupBy("_mes")
    .map(x => ({
      ...x,
      ns: pctNum(x.prazo + x.antecipado + x.justificado, x.entregue)
    }))
    .sort((a, b) => Number(a.label) - Number(b.label));

  renderLine(
    "chartNivelMensal",
    rows.map(x => x.label),
    rows.map(x => Number(x.ns.toFixed(1))),
    "Nível de Serviço"
  );
}

function renderRankList(id, rows, formatter = r => fmt(r.total)) {
  const el = $(id);
  if (!el) return;

  el.innerHTML = rows.map((r, i) => `
    <li>
      <span>${i + 1}. ${r.label}</span>
      <b>${formatter(r)}</b>
    </li>
  `).join("");
}

function renderRankings() {
  renderRankList("topUsuariosEmissao", topRanking("usuario", 10));

  const ocorr = topRanking("ocorrenciaDescricao", 10)
    .filter(x => x.label && x.label !== "Não informado");

  renderRankList("topOcorrencias", ocorr);
}

function renderTotalEmissao() {
  const dataOperacao = DATA.map(x => ({
    ...x,
    operacaoNome: nomeOperacao(x.operacao)
  }));
  const rows = topRanking("operacaoNome", 10, dataOperacao);
  const total = DATA.length;

  $("kEmissaoTotal").textContent = fmt(total);

  $("kEmissaoBreak").innerHTML = rows.map(r => `
    <div class="line">
      <span>${r.label}</span>
      <span>
        <b>${fmt(r.total)}</b>
        <em>${fmtMoney(r.frete)}</em>
      </span>
    </div>
  `).join("");

  const unidade = topRanking("unidade", 1)[0] || topRanking("unidadeReceptora", 1)[0];

  $("kEmissaoUni").textContent = unidade
    ? `${unidade.label} (${fmt(unidade.total)})`
    : "—";
}

function renderTop5Ufs() {
  const rows = topRanking("uf", 5);

  $("kTop5UfsBody").innerHTML = rows.map((r, i) => `
    <div class="line">
      <span>${i + 1}. ${r.label}</span>
      <span>
        <b>${fmt(r.total)}</b>
        <em>${pct(r.total, DATA.length)}</em>
      </span>
    </div>
  `).join("");
}

function diffDias(dataInicio, dataFim) {
  const ini = parseBRDate(dataInicio);
  const fim = parseBRDate(dataFim);

  if (!ini || !fim) return 0;

  return Math.floor((fim - ini) / (1000 * 60 * 60 * 24));
}

function renderTempoBaixa() {
  const entregues = DATA.filter(x => x.status === "Entregue");

  const dias = entregues.map(x => diffDias(x.entrega, new Date().toLocaleDateString("pt-BR")));

  const b3 = dias.filter(d => d >= 3 && d < 5).length;
  const b5 = dias.filter(d => d >= 5 && d < 15).length;
  const b15 = dias.filter(d => d >= 15 && d < 30).length;
  const b30 = dias.filter(d => d >= 30 && d < 100).length;
  const b100 = dias.filter(d => d >= 100).length;

  const total = b3 + b5 + b15 + b30 + b100;

  $("kBaixaTitle").textContent = fmt(total);

  const rows = [
    ["Mais de 3 dias", b3],
    ["Mais de 5 dias", b5],
    ["Mais de 15 dias", b15],
    ["Mais de 30 dias", b30],
    ["Mais de 100 dias", b100],
  ];

  $("kTempoBaixaBody").innerHTML = rows.map(([label, value]) => `
    <div class="line">
      <span>${label}</span>
      <span>
        <b>${fmt(value)}</b>
        <em>${pct(value, DATA.length)}</em>
      </span>
    </div>
  `).join("");
}

function renderAgendamento() {
  const agendados = DATA.filter(x => {
    const op = nomeOperacao(x.operacao);
    return op.includes("AGEND") || String(x.agendamento || "").trim();
  });

  const rows = groupBy("uf", agendados).sort((a, b) => b.total - a.total);

  $("kAgendTitle").textContent = fmt(agendados.length);

  $("kAgendBody").innerHTML = rows.map(r => `
    <div class="line">
      <span>${r.label}</span>
      <span>
        <b>${fmt(r.total)}</b>
        <em>${pct(r.total, agendados.length)}</em>
      </span>
    </div>
  `).join("") || `<div class="line"><span>Sem dados</span><b>0</b></div>`;
}

function renderUnidadesAbertoAtraso() {
  const abertos = DATA.filter(x => x.status === "Em aberto");
  const atrasados = DATA.filter(x => x.prazo === "Atrasado");

  const abertoRows = groupBy("unidadeReceptora", abertos)
    .sort((a, b) => b.total - a.total);

  const atrasoRows = groupBy("unidadeReceptora", atrasados)
    .sort((a, b) => b.total - a.total);

  renderRankList("topCtrcAberto", abertoRows, r =>
    `${fmt(r.total)} CTRC <em>${pct(r.total, abertos.length)}</em>`
  );

  renderRankList("topCtrcEntregue", atrasoRows, r =>
    `${fmt(r.total)} atrasos <em>${pct(r.total, atrasados.length)}</em>`
  );
}

function renderHqCard(sigla, rowsId, nsId) {
  const rows = DATA.filter(x =>
    String(x.unidadeReceptora || x.unidade || "").toUpperCase() === sigla
  );

  const grouped = groupBy("unidadeReceptora", rows)[0] || {
    total: 0,
    romaneio: 0,
    baixaMobile: 0,
    ocorr73: 0,
    entregue: 0,
    prazo: 0,
    antecipado: 0,
    justificado: 0
  };

  const ns = pctNum(
    grouped.prazo + grouped.antecipado + grouped.justificado,
    grouped.entregue
  );

  const nsEl = $(nsId);
  nsEl.textContent = ns ? `${ns.toFixed(1).replace(".", ",")}%` : "—";
  nsEl.className = "title-right " + (ns >= 97 ? "ok" : "bad");

  $(rowsId).innerHTML = `
    <div class="line"><span>Total de Entrega</span><span><b>${fmt(grouped.total)}</b> <em>${pct(grouped.total, DATA.length)}</em></span></div>
    <div class="line"><span>Romaneio</span><span><b>${fmt(grouped.romaneio)}</b> <em>${pct(grouped.romaneio, grouped.entregue)}</em></span></div>
    <div class="line"><span>Baixa Mobile</span><span><b>${fmt(grouped.baixaMobile)}</b> <em>${pct(grouped.baixaMobile, grouped.baixaBase)}</em></span></div>
    <div class="line"><span>Ocorrência 73</span><span><b>${fmt(grouped.ocorr73)}</b> <em>${pct(grouped.ocorr73, grouped.entregue)}</em></span></div>
    <div class="line good"><span>Nível de Serviço</span><span><b>${ns.toFixed(1).replace(".", ",")}%</b> <em>meta 97%</em></span></div>
  `;
}

function isMobile(item) {
  return String(item.h || item.baixaMobile || "").toUpperCase() === "MOBILE";
}

function hasBaixaInfo(item) {
  const h = String(item.h || item.baixaMobile || "").toUpperCase();
  return h === "MOBILE" || h === "MANUAL";
}

function renderHqCards() {
  renderHqCard("CWB", "hqCWBrows", "hqCWBns");
  renderHqCard("JOI", "hqJOIrows", "hqJOIns");
  renderHqCard("POA", "hqPOArows", "hqPOAns");
}

function parceiroKey(item) {
  return item.parceiro || item.unidadeReceptora || "Não informado";
}

function cidadeParceiroKey(item) {
  return item.cidParceiros || item.cidade || "Não informado";
}

function ufParceiroKey(item) {
  return item.ufParceiro || item.uf || "Não informado";
}

function nomeOperacao(valor) {
  const v = String(valor || "").trim().toUpperCase();

  const mapa = {
    "CP": "COMPLEMENTAR",
    "FP": "CORTESIA",
    "FV": "NORMAL",
    "FR": "REVERSA",
    "COMPLEMENTAR": "COMPLEMENTAR",
    "CORTESIA": "CORTESIA",
    "NORMAL": "NORMAL",
    "REVERSA": "REVERSA",
  };

  return mapa[v] || v || "Não informado";
}

function groupParceiros() {
  const map = {};

  DATA.forEach(item => {
    const uf = ufParceiroKey(item);
    const parceiro = parceiroKey(item);
    const cidade = cidadeParceiroKey(item);
    const key = `${uf}|${parceiro}|${cidade}`;

    if (!map[key]) {
      map[key] = {
        uf,
        parceiro,
        cidade,
        total: 0,
        entregue: 0,
        aberto: 0,
        prazo: 0,
        antecipado: 0,
        atraso: 0,
        justificado: 0,
        ocorr73: 0,
        mobile: 0,
        baixaBase: 0
      };
    }

    const r = map[key];
    r.total++;

    if (item.status === "Entregue") r.entregue++;
    if (item.status === "Em aberto") r.aberto++;
    if (item.prazo === "No prazo") r.prazo++;
    if (item.prazo === "Antecipado") r.antecipado++;
    if (item.prazo === "Atrasado") r.atraso++;
    if (item.prazo === "Justificado") r.justificado++;
    if (item.ocorr73 === "SIM" || item.l === "SIM") r.ocorr73++;
    if (isMobile(item)) r.mobile++;
    if (hasBaixaInfo(item)) r.baixaBase++;
  });

  return Object.values(map);
}

function resumoParceiroUf(uf) {
  const rows = DATA.filter(x => ufParceiroKey(x) === uf);

  const total = rows.length;
  const entregue = rows.filter(x => x.status === "Entregue").length;
  const aberto = rows.filter(x => x.status === "Em aberto").length;
  const prazo = rows.filter(x => x.prazo === "No prazo").length;
  const antecipado = rows.filter(x => x.prazo === "Antecipado").length;
  const atraso = rows.filter(x => x.prazo === "Atrasado").length;
  const justificado = rows.filter(x => x.prazo === "Justificado").length;
  const ocorr73 = rows.filter(x => x.ocorr73 === "SIM" || x.l === "SIM").length;
  const mobile = rows.filter(isMobile).length;
  const baixaBase = rows.filter(hasBaixaInfo).length;

  const ns = pctNum(prazo + antecipado + justificado, entregue);

  return {
    total,
    entregue,
    aberto,
    prazo,
    antecipado,
    atraso,
    justificado,
    ocorr73,
    mobile,
    baixaBase,
    ns
  };
}

function renderParceiroUf(uf, rowsId, nsId) {
  const r = resumoParceiroUf(uf);

  const nsEl = $(nsId);
  nsEl.textContent = r.entregue ? `${r.ns.toFixed(1).replace(".", ",")}%` : "—";
  nsEl.className = "title-right " + (r.ns >= 97 ? "ok" : "bad");

  $(rowsId).innerHTML = `
    <div class="line"><span>Total de Entrega</span><span><b>${fmt(r.total)}</b> <em>${pct(r.total, DATA.length)}</em></span></div>
    <div class="line"><span>Entregue</span><span><b>${fmt(r.entregue)}</b> <em>${pct(r.entregue, r.total)}</em></span></div>
    <div class="line warn"><span>Em aberto</span><span><b>${fmt(r.aberto)}</b> <em>${pct(r.aberto, r.total)}</em></span></div>
    <div class="line bad"><span>Atrasado</span><span><b>${fmt(r.atraso)}</b> <em>${pct(r.atraso, r.entregue)}</em></span></div>
    <div class="line"><span>Ocorrência 73</span><span><b>${fmt(r.ocorr73)}</b> <em>${pct(r.ocorr73, r.entregue)}</em></span></div>
    <div class="line"><span>Baixa Mobile</span><span><b>${fmt(r.mobile)}</b> <em>${pct(r.mobile, r.baixaBase)}</em></span></div>
  `;
}

function renderRankingParceiros() {
  const rows = groupParceiros();

  const parceiros = {};
  const cidades = {};

  rows.forEach(r => {
    const p = r.parceiro || "Não informado";
    const c = `${r.cidade || "Não informado"} / ${r.uf || ""}`;

    parceiros[p] = (parceiros[p] || 0) + r.total;
    cidades[c] = (cidades[c] || 0) + r.total;
  });

  const parceirosRows = Object.entries(parceiros)
    .map(([label, total]) => ({ label, total }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 30);

  const cidadesRows = Object.entries(cidades)
    .map(([label, total]) => ({ label, total }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 30);

  renderRankList("rankingParceiros", parceirosRows, r =>
    `${fmt(r.total)} <em>${pct(r.total, DATA.length)}</em>`
  );

  renderRankList("rankingCidadesParceiras", cidadesRows, r =>
    `${fmt(r.total)} <em>${pct(r.total, DATA.length)}</em>`
  );
}

function renderTabelaParceiros() {
  const rows = groupParceiros()
    .map(r => ({
      ...r,
      ns: pctNum(r.prazo + r.antecipado + r.justificado, r.entregue),
      mobilePct: pctNum(r.mobile, r.baixaBase)
    }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 300);

  const tbody = $("tabelaParceirosBody");
  if (!tbody) return;

  tbody.innerHTML = rows.map(r => `
    <tr>
      <td><span class="uf-pill ${r.uf}">${r.uf}</span></td>
      <td>${r.parceiro}</td>
      <td>${r.cidade}</td>
      <td class="num">${fmt(r.total)}</td>
      <td class="num">${fmt(r.entregue)}</td>
      <td class="num">${fmt(r.aberto)}</td>
      <td class="num">${fmt(r.atraso)}</td>
      <td class="num">${fmt(r.ocorr73)}</td>
      <td class="num">${r.mobilePct.toFixed(1).replace(".", ",")}%</td>
      <td>
        <span class="ns-pill ${r.ns >= 97 ? "ok" : "bad"}">
          ${r.entregue ? r.ns.toFixed(1).replace(".", ",") + "%" : "—"}
        </span>
      </td>
    </tr>
  `).join("");
}

function renderParceiros() {
  renderParceiroUf("PR", "parcPRrows", "parcPRns");
  renderParceiroUf("SC", "parcSCrows", "parcSCns");
  renderParceiroUf("RS", "parcRSrows", "parcRSns");

  renderRankingParceiros();
  renderTabelaParceiros();
}

function renderColetas() {
  const coletas = window.DATA_COLETA || [];

  $("coletaTotal").textContent = fmt(coletas.length);

  $("coletaResumo").innerHTML = `
    <div class="line"><span>Status</span><b>${coletas.length ? "Com dados" : "Sem dados"}</b></div>
    <div class="line"><span>Fonte</span><b>DATA_COLETA</b></div>
  `;

  $("coletaUnidades").innerHTML = coletas.length
    ? ""
    : `<div class="line"><span>Aguardando integração</span><b>0</b></div>`;

  $("coletaStatus").innerHTML = coletas.length
    ? ""
    : `<div class="line"><span>Nenhum registro importado</span><b>0</b></div>`;

  renderLine("chartColetasDia", [], [], "Coletas");
}

function renderAvaliacao() {
  const rows = window.DATA_AVALIACAO || [];

  $("avaliacaoTotal").textContent = fmt(rows.length);

  $("avaliacaoResumo").innerHTML = `
    <div class="line"><span>Status</span><b>${rows.length ? "Com dados" : "Sem dados"}</b></div>
    <div class="line"><span>Fonte</span><b>DATA_AVALIACAO</b></div>
  `;

  $("avaliacaoUnidades").innerHTML = rows.length
    ? ""
    : `<div class="line"><span>Aguardando integração</span><b>0</b></div>`;

  $("avaliacaoStatus").innerHTML = rows.length
    ? ""
    : `<div class="line"><span>Nenhum registro importado</span><b>0</b></div>`;

  renderLine("chartAvaliacaoDia", [], [], "Avaliações");
}

function renderCustos() {
  const rows = window.DATA_CUSTO || [];

  $("custoTotal").textContent = fmtMoney(0);

  $("custoResumo").innerHTML = `
    <div class="line"><span>Status</span><b>${rows.length ? "Com dados" : "Sem dados"}</b></div>
    <div class="line"><span>Fonte</span><b>DATA_CUSTO</b></div>
  `;

  $("custoUnidades").innerHTML = rows.length
    ? ""
    : `<div class="line"><span>Aguardando integração</span><b>R$ 0,00</b></div>`;

  $("custoTipos").innerHTML = rows.length
    ? ""
    : `<div class="line"><span>Nenhum custo importado</span><b>R$ 0,00</b></div>`;

  renderLine("chartCustosDia", [], [], "Custos");
}

function renderDashboard() {
  $("recCount").innerHTML = `<span class="dot"></span> ${fmt(DATA.length)} registros`;

  renderCards();
  renderCharts();

  renderTotalEmissao();
  renderTop5Ufs();
  renderTempoBaixa();
  renderAgendamento();
  renderUnidadesAbertoAtraso();
  renderHqCards();

  renderParceiros();
}

async function carregarDados() {
  try {
    const res = await fetch("/api/metricas/dashboard");
    const payload = await res.json();

    if (!payload.has_data && !payload.DATA) {
      $("recCount").innerHTML = `<span class="dot"></span> Nenhum dado`;
      showToast(payload.message || "Nenhuma execução encontrada.");
      return;
    }

    RAW = payload.DATA || [];

    window.DATA_COLETA = payload.DATA_COLETA || [];
    window.DATA_AVALIACAO = payload.DATA_AVALIACAO || [];
    window.DATA_CUSTO = payload.DATA_CUSTO || [];

    populateFilters();
    applyFilters();

    if (payload.run?.finished_at) {
      $("screenSubtitle").textContent = `Análise • Entregas • Atualizado em ${payload.run.finished_at}`;
    }

  } catch (e) {
    showToast("Erro ao carregar dashboard: " + e.message);
  }
}

async function atualizarMetricas() {
  const btn = $("btn-refresh");

  btn.disabled = true;
  btn.textContent = "Atualizando...";

  try {
    const res = await fetch("/api/metricas/refresh", { method: "POST" });
    const data = await res.json();

    if (!res.ok || !data.success) {
      showToast(data.error || "Erro ao atualizar métricas.");
      return;
    }

    showToast("Métricas atualizadas com sucesso.");
    await carregarDados();

  } catch (e) {
    showToast("Erro inesperado: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Atualizar agora";
  }
}

function bindTabs() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;

      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

      btn.classList.add("active");

      const content = document.getElementById("tab-" + tab);
      if (content) content.classList.add("active");
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindTabs();
  bindFilters();
  carregarDados();
});