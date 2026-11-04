// Trust Center de SecureChain: consulta api-flask y pinta precios, gráficas e indicadores.
// Todo el texto que viene de la API se inserta con textContent (nunca innerHTML).
"use strict";

const API = (window.SECURECHAIN_API || "").replace(/\/$/, "");
const ACTIVOS = {
  BTCUSDT: { nombre: "Bitcoin", corto: "BTC" },
  ETHUSDT: { nombre: "Ethereum", corto: "ETH" },
  BNBUSDT: { nombre: "BNB", corto: "BNB" },
  SOLUSDT: { nombre: "Solana", corto: "SOL" },
  XRPUSDT: { nombre: "XRP", corto: "XRP" },
  ADAUSDT: { nombre: "Cardano", corto: "ADA" },
};
const PERIODOS = {
  "24h": { intervalo: "1h", limite: 24, nombre: "las últimas 24 horas" },
  "7d": { intervalo: "1h", limite: 168, nombre: "los últimos 7 días" },
  "30d": { intervalo: "1h", limite: 720, nombre: "los últimos 30 días" },
  "1a": { intervalo: "1d", limite: 365, nombre: "el último año" },
  "todo": { intervalo: "1d", limite: 1000, nombre: "todo el histórico" },
};
const VIVO_MAX_S = 90;          // un precio más viejo que esto significa que la ingesta está en pausa
const REFRESCO_VIVO_MS = 5000;
const REFRESCO_PAUSA_MS = 60000;

const estado = {
  precios: {},                   // simbolo -> fila de /api/precios
  ultimoEvento: null,            // ms del precio más reciente
  seleccion: "BTCUSDT",
  periodo: "7d",
  vista: "linea",
  velas: new Map(),              // "1h:168" -> {series, cargado}
  grafica: null,
  temporizador: null,
  explorando: false,             // el usuario recorre la gráfica: no pisar la cabecera con el precio en vivo
};

const $ = (id) => document.getElementById(id);
const nombreDe = (s) => ACTIVOS[s]?.nombre || s;
const cortoDe = (s) => ACTIVOS[s]?.corto || s.replace(/USDT$/, "");

// ---------------------------------------------------------------- formatos

function decimales(n) { const a = Math.abs(n); return a >= 1000 ? 2 : a >= 1 ? (a >= 100 ? 2 : 4) : 5; }
function precio(n, eje = false) {
  const d = eje ? Math.max(0, decimales(n) - (Math.abs(n) >= 1000 ? 2 : 1)) : decimales(n);
  return Number(n).toLocaleString("es-CO", { minimumFractionDigits: d, maximumFractionDigits: d });
}
function porcentaje(n, signo = true) {
  const t = Math.abs(n).toLocaleString("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${signo ? (n > 0 ? "▲ +" : n < 0 ? "▼ −" : "") : ""}${t} %`;
}
function compacto(n) {
  const f = (v) => v.toLocaleString("es-CO", { maximumFractionDigits: 1 });
  if (n >= 1e9) return `${f(n / 1e9)} mil M`;
  if (n >= 1e6) return `${f(n / 1e6)} M`;
  if (n >= 1e3) return `${f(n / 1e3)} mil`;
  return f(n);
}
const FMT_HORA = new Intl.DateTimeFormat("es-CO", { hour: "2-digit", minute: "2-digit" });
const FMT_DIA = new Intl.DateTimeFormat("es-CO", { day: "numeric", month: "short" });
const FMT_MES = new Intl.DateTimeFormat("es-CO", { month: "short", year: "2-digit" });
const FMT_DIA_HORA = new Intl.DateTimeFormat("es-CO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const FMT_FECHA = new Intl.DateTimeFormat("es-CO", { day: "numeric", month: "short", year: "numeric" });
function fecha(ms, detallado, rango) {
  const dia = 86400000;
  if (detallado) return rango > 400 * dia ? FMT_FECHA.format(ms) : (PERIODOS[estado.periodo].intervalo === "1h" ? FMT_DIA_HORA : FMT_FECHA).format(ms);
  if (rango <= 2 * dia) return FMT_HORA.format(ms);
  if (rango <= 120 * dia) return FMT_DIA.format(ms);
  return FMT_MES.format(ms);
}
function haceCuanto(ms) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `hace ${s} s`;
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 172800) return `hace ${Math.round(s / 3600)} h`;
  return FMT_FECHA.format(ms);
}

// ---------------------------------------------------------------- red

async function obtener(ruta, opciones = {}) {
  const control = new AbortController();
  const corte = setTimeout(() => control.abort(), 20000);
  try {
    const r = await fetch(`${API}${ruta}`, { headers: { Accept: "application/json" }, signal: control.signal, ...opciones });
    const cuerpo = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(cuerpo.error || `HTTP ${r.status}`), { estado: r.status, cuerpo });
    return cuerpo;
  } finally {
    clearTimeout(corte);
  }
}

// ---------------------------------------------------------------- sello (roseta guilloché)

function dibujarRoseta() {
  const svg = $("roseta");
  const NS = "http://www.w3.org/2000/svg";
  // Guilloché: anillos ondulados superpuestos y desfasados, el mismo recurso de billetes y certificados
  // para que una copia se note. Cada banda es una onda r(θ) = base + amplitud · sen(n·θ + fase).
  const anillo = (base, amplitud, n, fase) => {
    let d = "";
    for (let i = 0; i <= 720; i++) {
      const t = i / 720 * Math.PI * 2;
      const r = base + amplitud * Math.sin(n * t + fase);
      d += `${i ? "L" : "M"}${(r * Math.cos(t)).toFixed(2)},${(r * Math.sin(t)).toFixed(2)}`;
    }
    return d + "Z";
  };
  const bandas = [
    // clase, radio base, amplitud, ondas, copias, grosor
    ["r1", 91, 8, 28, 6, 0.45],
    ["r2", 71, 9, 20, 5, 0.5],
    ["r3", 55, 4, 14, 4, 0.5],
  ];
  for (const [clase, base, amp, n, copias, grosor] of bandas) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", clase);
    for (let k = 0; k < copias; k++) {
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", anillo(base, amp, n, k / copias * Math.PI * 2));
      p.setAttribute("fill", "none"); p.setAttribute("stroke-width", grosor);
      g.appendChild(p);
    }
    svg.appendChild(g);
  }
  for (const r of [101.5, 104]) {
    const c = document.createElementNS(NS, "circle");
    c.setAttribute("r", r); c.setAttribute("fill", "none"); c.setAttribute("class", "aro"); c.setAttribute("stroke-width", 0.8);
    svg.appendChild(c);
  }
  const fondo = document.createElementNS(NS, "circle");
  fondo.setAttribute("r", 47); fondo.setAttribute("fill", "var(--papel)"); fondo.setAttribute("class", "aro");
  fondo.setAttribute("stroke-width", 1.2);
  svg.appendChild(fondo);
  for (const [txt, yy, tam] of [["Sello de", -3, 12], ["Confianza", 14, 14.5]]) {
    const t = document.createElementNS(NS, "text");
    t.setAttribute("text-anchor", "middle"); t.setAttribute("y", yy); t.setAttribute("font-size", tam);
    t.textContent = txt;
    svg.appendChild(t);
  }
}

function pintarEstado() {
  const punto = $("estado-punto"), texto = $("estado-texto"), detalle = $("estado-detalle");
  if (estado.ultimoEvento == null) return;
  const edad = (Date.now() - estado.ultimoEvento) / 1000;
  const vivo = edad <= VIVO_MAX_S;
  punto.className = `punto ${vivo ? "vivo" : "pausa"}`;
  texto.textContent = vivo ? "Precios en vivo desde Binance" : "Ingesta en pausa";
  detalle.textContent = vivo
    ? `Último dato ${haceCuanto(estado.ultimoEvento)}`
    : `Mostramos el último precio guardado, de ${haceCuanto(estado.ultimoEvento)}`;
}

// ---------------------------------------------------------------- mercado

async function cargarPrecios() {
  let siguiente = REFRESCO_PAUSA_MS;
  try {
    const { datos } = await obtener("/api/precios?fuente=binance");
    for (const p of datos) estado.precios[p.simbolo] = p;
    estado.ultimoEvento = Math.max(...datos.map((p) => Date.parse(p.evento_ts)));
    pintarMercado();
    if (!estado.explorando) pintarCabecera();
    pintarEstado();
    if ((Date.now() - estado.ultimoEvento) / 1000 <= VIVO_MAX_S) siguiente = REFRESCO_VIVO_MS;
  } catch (e) {
    $("estado-punto").className = "punto";
    $("estado-texto").textContent = "Conectando con la API…";
    $("estado-detalle").textContent = "La primera visita puede tardar unos segundos mientras el servicio se enciende.";
    siguiente = 8000;
  }
  programar(siguiente);
}

function programar(ms) {
  clearTimeout(estado.temporizador);
  // Con la pestaña oculta no se consulta nada: así la API puede apagarse sola y no consume.
  if (!document.hidden) estado.temporizador = setTimeout(cargarPrecios, ms);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) cargarPrecios(); else clearTimeout(estado.temporizador); });

function pintarMercado() {
  const tbody = $("filas-mercado");
  const simbolos = Object.keys(ACTIVOS).filter((s) => estado.precios[s]);
  if (!simbolos.length) return;
  const chispas = estado.velas.get("1h:24")?.series || {};
  // Las filas se crean una vez; en cada refresco solo cambia su texto (así no se pierde el foco del teclado).
  if (tbody.rows.length !== simbolos.length || tbody.rows[0].dataset.simbolo !== simbolos[0]) {
    tbody.replaceChildren(...simbolos.map((s) => {
      const tr = document.createElement("tr");
      tr.tabIndex = 0;
      tr.dataset.simbolo = s;
      const tdA = document.createElement("td");
      const caja = document.createElement("span"); caja.className = "activo";
      const b = document.createElement("b"); b.textContent = nombreDe(s);
      const sm = document.createElement("span"); sm.className = "simbolo"; sm.textContent = cortoDe(s);
      caja.append(b, sm); tdA.appendChild(caja);
      const tdP = document.createElement("td"); tdP.className = "num precio";
      const tdC = document.createElement("td");
      const tdS = document.createElement("td");
      const tdV = document.createElement("td"); tdV.className = "num";
      tr.append(tdA, tdP, tdC, tdS, tdV);
      return tr;
    }));
  }
  for (const tr of tbody.rows) {
    const s = tr.dataset.simbolo, p = estado.precios[s];
    const var24 = Number(p.variacion_24h_pct);
    tr.setAttribute("aria-selected", String(s === estado.seleccion));
    tr.cells[1].textContent = precio(Number(p.precio));
    tr.cells[2].className = `num cambio ${var24 >= 0 ? "sube" : "baja"}`;
    tr.cells[2].textContent = porcentaje(var24);
    const serie = (chispas[s] || []).map((k) => k[4]);
    if (serie.length && serie[serie.length - 1] !== undefined) serie[serie.length - 1] = Number(p.precio);
    tr.cells[3].replaceChildren(SCGraficas.chispa(serie, serie.length > 1 ? serie[serie.length - 1] >= serie[0] : var24 >= 0));
    tr.cells[4].textContent = `${compacto(Number(p.volumen_24h) * Number(p.precio))} USDT`;
    tr.cells[4].title = `${Number(p.volumen_24h).toLocaleString("es-CO")} ${cortoDe(s)} negociados en 24 h`;
  }
}

function elegir(simbolo) {
  if (!ACTIVOS[simbolo]) return;
  estado.seleccion = simbolo;
  if (estado.vista === "comparar") marcar("vista", "linea");
  for (const tr of $("filas-mercado").rows) tr.setAttribute("aria-selected", String(tr.dataset.simbolo === simbolo));
  pintarCabecera();
  cargarGrafica();
}
$("filas-mercado").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-simbolo]");
  if (tr) { elegir(tr.dataset.simbolo); $("grafica").scrollIntoView({ block: "start" }); }
});
$("filas-mercado").addEventListener("keydown", (e) => {
  const tr = e.target.closest("tr[data-simbolo]");
  if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); elegir(tr.dataset.simbolo); }
});

// ---------------------------------------------------------------- gráfica

function pintarCabecera(indice = null, puntos = null) {
  const s = estado.seleccion, p = estado.precios[s];
  const comparar = estado.vista === "comparar";
  $("g-nombre").textContent = comparar ? "Los seis activos" : nombreDe(s);
  $("g-simbolo").textContent = comparar ? "rendimiento en %" : cortoDe(s);
  const cambio = $("g-cambio");
  if (comparar) { $("g-precio").textContent = ""; cambio.textContent = ""; return; }
  if (indice != null && puntos) {
    const q = puntos[indice];
    $("g-precio").textContent = `${precio(q.c)} USDT`;
    const v = (q.c / puntos[0].c - 1) * 100;
    cambio.textContent = `${porcentaje(v)} desde el inicio del periodo`;
    cambio.className = `cambio ${v >= 0 ? "sube" : "baja"}`;
    return;
  }
  if (!p) return;
  $("g-precio").textContent = `${precio(Number(p.precio))} USDT`;
  const v = Number(p.variacion_24h_pct);
  cambio.textContent = `${porcentaje(v)} en 24 h`;
  cambio.className = `cambio ${v >= 0 ? "sube" : "baja"}`;
}

async function velas(intervalo, limite) {
  const clave = `${intervalo}:${limite}`;
  const guardado = estado.velas.get(clave);
  if (guardado && Date.now() - guardado.cargado < 5 * 60000) return guardado.series;
  const { series } = await obtener(`/api/velas?intervalo=${intervalo}&limite=${limite}`);
  estado.velas.set(clave, { series, cargado: Date.now() });
  return series;
}

function aPuntos(crudas, simbolo) {
  const puntos = crudas.map(([t, o, h, l, c, v]) => ({ t, o, h, l, c, v }));
  // Si la ingesta está en vivo, la última vela refleja el precio de este instante.
  const vivo = estado.precios[simbolo];
  const ultimo = puntos[puntos.length - 1];
  if (vivo && ultimo && Date.parse(vivo.evento_ts) >= ultimo.t) {
    const c = Number(vivo.precio);
    Object.assign(ultimo, { c, h: Math.max(ultimo.h, c), l: Math.min(ultimo.l, c) });
  }
  return puntos;
}

async function cargarGrafica() {
  const lienzo = $("lienzo");
  const { intervalo, limite, nombre } = PERIODOS[estado.periodo];
  lienzo.setAttribute("aria-busy", "true");
  let series;
  try {
    series = await velas(intervalo, limite);
  } catch {
    estado.grafica?.destruir();
    lienzo.replaceChildren(Object.assign(document.createElement("p"), {
      className: "vacio", textContent: "No pudimos cargar el histórico. Reintentaremos en unos segundos.",
    }));
    setTimeout(cargarGrafica, 8000);
    return;
  } finally {
    lienzo.removeAttribute("aria-busy");
  }
  const comparar = estado.vista === "comparar";
  const simbolos = comparar ? Object.keys(ACTIVOS).filter((s) => series[s]?.length) : [estado.seleccion];
  const datos = simbolos.map((s) => ({ nombre: nombreDe(s), simbolo: s, puntos: aPuntos(series[s] || [], s) }));

  estado.grafica?.destruir();
  estado.grafica = SCGraficas.dibujar(lienzo, {
    tipo: estado.vista, series: datos, valor: precio, fecha,
    descripcion: comparar ? `Rendimiento de los seis activos en ${nombre}` : `Precio de ${nombreDe(estado.seleccion)} en ${nombre}`,
    alCambiar: (i) => { estado.explorando = i != null; if (!comparar) pintarCabecera(i, datos[0].puntos); },
  });

  const leyenda = $("g-leyenda");
  leyenda.hidden = !comparar;
  if (comparar) {
    leyenda.replaceChildren(...datos.map((d, k) => {
      const span = document.createElement("span");
      const i = document.createElement("i"); i.style.background = estado.grafica.colores[k];
      span.append(i, document.createTextNode(d.nombre));
      return span;
    }));
  }
  pintarIndicadores(datos, nombre);
}

function pintarIndicadores(datos, nombrePeriodo) {
  const dl = $("indicadores");
  const items = [];
  const item = (titulo, valor, nota) => items.push([titulo, valor, nota]);
  if (estado.vista === "comparar") {
    const rend = datos.filter((d) => d.puntos.length > 1)
      .map((d) => [d.nombre, (d.puntos[d.puntos.length - 1].c / d.puntos[0].c - 1) * 100])
      .sort((a, b) => b[1] - a[1]);
    for (const [n, r] of rend) item(n, porcentaje(r), `en ${nombrePeriodo}`);
  } else {
    const p = datos[0].puntos;
    if (p.length > 1) {
      const ini = p[0].c, fin = p[p.length - 1].c;
      const max = Math.max(...p.map((q) => q.h)), min = Math.min(...p.map((q) => q.l));
      item("Rendimiento", porcentaje((fin / ini - 1) * 100), `en ${nombrePeriodo}`);
      item("Rango", `${precio(min)} – ${precio(max)}`, "mínimo y máximo, en USDT");
      // Volatilidad: desviación de los rendimientos entre velas, llevada a un año.
      const rets = p.slice(1).map((q, i) => Math.log(q.c / p[i].c));
      const media = rets.reduce((a, b) => a + b, 0) / rets.length;
      const desv = Math.sqrt(rets.reduce((a, r) => a + (r - media) ** 2, 0) / Math.max(1, rets.length - 1));
      const porAno = PERIODOS[estado.periodo].intervalo === "1h" ? 24 * 365 : 365;
      item("Volatilidad anualizada", porcentaje(desv * Math.sqrt(porAno) * 100, false), rets.length < 20 ? "pocos datos: tómala con cautela" : "a partir de este periodo");
      item("Caída desde el máximo", porcentaje((fin / max - 1) * 100, false), fin >= max ? "está en su máximo del periodo" : `máximo: ${precio(max)}`);
    }
  }
  dl.replaceChildren(...items.map(([t, v, n]) => {
    const div = document.createElement("div");
    const dt = document.createElement("dt"); dt.textContent = t;
    const dd = document.createElement("dd"); dd.textContent = v;
    const sm = document.createElement("small"); sm.textContent = n; dd.appendChild(sm);
    div.append(dt, dd);
    return div;
  }));
}

function marcar(grupo, valor) {
  estado[grupo] = valor;
  for (const b of document.querySelectorAll(`[data-${grupo}]`)) b.setAttribute("aria-checked", String(b.dataset[grupo] === valor));
}
for (const b of document.querySelectorAll("[data-periodo], [data-vista]")) {
  b.addEventListener("click", () => {
    if (b.dataset.periodo) marcar("periodo", b.dataset.periodo);
    else marcar("vista", b.dataset.vista);
    pintarCabecera();
    cargarGrafica();
  });
}
let redimension;
new ResizeObserver(() => { clearTimeout(redimension); redimension = setTimeout(() => estado.grafica && cargarGrafica(), 150); }).observe($("lienzo"));

// ---------------------------------------------------------------- formulario del Sello

const formulario = $("formulario");
function limpiarErrores() {
  for (const n of formulario.querySelectorAll(".error-campo")) n.remove();
  for (const n of formulario.querySelectorAll("[aria-invalid]")) { n.removeAttribute("aria-invalid"); n.removeAttribute("aria-describedby"); }
  $("f-error").hidden = true;
}
function marcarError(nombre, mensaje) {
  const campo = formulario.elements[nombre];
  const control = campo instanceof RadioNodeList ? campo[0] : campo;
  if (!control) return;
  const p = document.createElement("p");
  p.className = "error-campo"; p.id = `e-${nombre}`;
  p.textContent = `${mensaje.charAt(0).toUpperCase()}${mensaje.slice(1)}.`;
  const caja = control.closest(".campo");
  if (caja) caja.appendChild(p); else control.closest("label").after(p);
  control.setAttribute("aria-invalid", "true");
  control.setAttribute("aria-describedby", p.id);
}
const NOMBRES = { empresa: "empresa", tipo: "tipo de empresa", pais: "país", sitio_web: "sitio web", contacto: "nombre de contacto",
                  correo: "correo", interes: "lo que necesitas", acepta: "autorización", cargo: "cargo", mensaje: "mensaje" };

formulario.addEventListener("submit", async (e) => {
  e.preventDefault();
  limpiarErrores();
  const f = formulario.elements;
  const datos = {
    empresa: f.empresa.value, tipo: f.tipo.value, pais: f.pais.value, sitio_web: f.sitio_web.value,
    contacto: f.contacto.value, cargo: f.cargo.value, correo: f.correo.value, interes: f.interes.value,
    mensaje: f.mensaje.value, acepta: f.acepta.checked, fax: f.fax.value,
  };
  // Validación rápida en el navegador; la API vuelve a validar todo.
  const locales = {};
  for (const n of ["empresa", "tipo", "pais", "sitio_web", "contacto", "correo"]) if (!datos[n].trim()) locales[n] = "es obligatorio";
  if (datos.correo && !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(datos.correo.trim())) locales.correo = "no parece un correo válido";
  if (!datos.acepta) locales.acepta = "necesitamos tu autorización para responderte";
  if (Object.keys(locales).length) return mostrarErrores(locales);

  const boton = formulario.querySelector("button[type=submit]");
  boton.disabled = true; boton.textContent = "Enviando…";
  try {
    const r = await obtener("/api/solicitudes", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(datos),
    });
    $("c-referencia").textContent = r.referencia;
    formulario.hidden = true;
    $("confirmacion").hidden = false;
    $("confirmacion").focus();
  } catch (err) {
    if (err.estado === 400 && err.cuerpo?.campos) mostrarErrores(err.cuerpo.campos);
    else {
      const m = err.estado === 429 ? "Recibimos varias solicitudes seguidas desde tu conexión. Intenta de nuevo en unos minutos."
        : err.estado === 503 ? "El formulario no está disponible en este momento. Escríbenos más tarde."
        : "No pudimos enviar la solicitud. Revisa tu conexión e inténtalo otra vez.";
      $("f-error").textContent = m; $("f-error").hidden = false;
    }
  } finally {
    boton.disabled = false; boton.textContent = "Enviar solicitud";
  }
});
function mostrarErrores(campos) {
  for (const [n, m] of Object.entries(campos)) marcarError(n, m);
  const total = Object.keys(campos).length;
  $("f-error").textContent = total === 1
    ? `Revisa el campo ${NOMBRES[Object.keys(campos)[0]] || ""}.`
    : `Revisa los ${total} campos marcados.`;
  $("f-error").hidden = false;
  formulario.querySelector("[aria-invalid=true]")?.focus();
}
$("c-otra").addEventListener("click", () => {
  formulario.reset(); limpiarErrores();
  $("confirmacion").hidden = true; formulario.hidden = false;
  formulario.elements.empresa.focus();
});

// ---------------------------------------------------------------- inicio

dibujarRoseta();
(async () => {
  await cargarPrecios();
  try { await velas("1h", 24); pintarMercado(); } catch { /* las chispas son opcionales */ }
  cargarGrafica();
})();
