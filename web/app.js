// Frontend de SecureChain: consulta api-flask y pinta los datos.
// Todo el texto se inserta con textContent (nunca innerHTML) para no ejecutar
// nada que venga en los datos.
"use strict";

const API = (window.SECURECHAIN_API || "").replace(/\/$/, "");
const INTERVALO_MS = 5000;

const estado = document.getElementById("estado");
const tbody = document.getElementById("precios");
const listaIncidentes = document.getElementById("incidentes");

function celda(texto, clase) {
  const td = document.createElement("td");
  td.textContent = texto;
  if (clase) td.className = clase;
  return td;
}

function formatoPrecio(texto) {
  const n = Number(texto);
  const decimales = n >= 100 ? 2 : n >= 1 ? 4 : 6;
  return n.toLocaleString("es-CO", { minimumFractionDigits: decimales, maximumFractionDigits: decimales });
}

function haceCuanto(iso) {
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  if (s < 60) return `hace ${s} s`;
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  return new Date(iso).toLocaleString("es-CO");
}

async function obtener(ruta) {
  const r = await fetch(`${API}${ruta}`, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function actualizarPrecios() {
  try {
    const { datos } = await obtener("/api/precios");
    tbody.replaceChildren();
    if (!datos.length) {
      const tr = document.createElement("tr");
      tr.appendChild(celda("Todavía no hay precios. La ingesta puede tardar unos segundos en empezar."));
      tr.firstChild.colSpan = 5;
      tbody.appendChild(tr);
    }
    for (const p of datos) {
      const tr = document.createElement("tr");
      const variacion = Number(p.variacion_24h_pct);
      tr.append(
        celda(p.simbolo),
        celda(formatoPrecio(p.precio), "num"),
        celda(`${variacion >= 0 ? "+" : ""}${variacion.toFixed(2)} %`, `num ${variacion >= 0 ? "sube" : "baja"}`),
        celda(p.fuente),
        celda(haceCuanto(p.evento_ts)),
      );
      tbody.appendChild(tr);
    }
    estado.textContent = `En vivo · ${new Date().toLocaleTimeString("es-CO")}`;
  } catch (e) {
    estado.textContent = `No se pudo consultar la API (${e.message}). Reintentando…`;
  }
}

async function actualizarIncidentes() {
  try {
    const { datos } = await obtener("/api/incidentes?limite=5");
    listaIncidentes.replaceChildren();
    if (!datos.length) {
      const li = document.createElement("li");
      li.textContent = "Sin incidentes registrados todavía.";
      listaIncidentes.appendChild(li);
    }
    for (const i of datos) {
      const li = document.createElement("li");
      const monto = i.monto_usd ? ` · USD ${Number(i.monto_usd).toLocaleString("es-CO")}` : "";
      li.textContent = `${i.fecha} · ${i.protocolo}${i.tecnica ? ` · ${i.tecnica}` : ""}${monto}`;
      listaIncidentes.appendChild(li);
    }
  } catch {
    listaIncidentes.replaceChildren();
    const li = document.createElement("li");
    li.textContent = "No se pudieron cargar los incidentes.";
    listaIncidentes.appendChild(li);
  }
}

actualizarPrecios();
actualizarIncidentes();
setInterval(actualizarPrecios, INTERVALO_MS);
