// Gráficas propias en SVG, sin librerías externas: el sitio sigue siendo solo archivos estáticos.
//   SCGraficas.chispa(valores, sube)            mini línea para la tabla de mercado
//   SCGraficas.dibujar(contenedor, opciones)    gráfica grande interactiva (precio, velas o comparación)
"use strict";

window.SCGraficas = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const oscuro = () => window.matchMedia("(prefers-color-scheme: dark)").matches;
  const PALETA = {
    claro: ["#23479a", "#b8661f", "#1e7047", "#7d3c98", "#a3422b", "#3f6e78"],
    oscuro: ["#8fb0f2", "#f0a868", "#6fcf98", "#c99ae0", "#f0937c", "#7ec8d3"],
  };
  const css = (nombre) => getComputedStyle(document.documentElement).getPropertyValue(nombre).trim();
  const paleta = () => PALETA[oscuro() ? "oscuro" : "claro"];
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function el(nombre, attrs = {}, padre) {
    const n = document.createElementNS(NS, nombre);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (padre) padre.appendChild(n);
    return n;
  }

  // Marcas "redondas" para el eje: 1, 2, 2.5 o 5 por potencia de 10.
  function marcas(min, max, cuantas = 5) {
    const paso0 = (max - min) / cuantas || Math.abs(max) || 1;
    const potencia = 10 ** Math.floor(Math.log10(paso0));
    const paso = [1, 2, 2.5, 5, 10].map((m) => m * potencia).find((p) => p >= paso0);
    const lista = [];
    for (let v = Math.ceil(min / paso) * paso; v <= max + paso * 1e-9; v += paso) lista.push(+v.toPrecision(12));
    return lista;
  }

  function chispa(valores, sube) {
    const svg = el("svg", { class: "chispa", viewBox: "0 0 150 36", "aria-hidden": "true" });
    if (valores.length < 2) return svg;
    const min = Math.min(...valores), max = Math.max(...valores), rango = max - min || 1;
    const d = valores.map((v, i) => `${i ? "L" : "M"}${(i / (valores.length - 1) * 148 + 1).toFixed(1)},${(33 - (v - min) / rango * 30).toFixed(1)}`).join("");
    el("path", { d, stroke: sube ? css("--sube") : css("--baja") }, svg);
    return svg;
  }

  // opciones: { tipo: "linea"|"velas"|"comparar", series: [{nombre, puntos:[{t,o,h,l,c,v}]}],
  //             valor: fn(numero)->texto, fecha: fn(ms, detallado)->texto, alCambiar: fn(indice|null) }
  function dibujar(contenedor, opciones) {
    contenedor.replaceChildren();
    const { tipo, series } = opciones;
    const base = series[0]?.puntos || [];
    if (!base.length) {
      const p = document.createElement("p");
      p.className = "vacio";
      p.textContent = "Todavía no hay histórico para este periodo. El trabajo programado lo carga en pocos minutos.";
      contenedor.appendChild(p);
      return { destruir() {} };
    }

    const colores = paleta();
    const comparar = tipo === "comparar";
    // En comparación cada serie se expresa como % de cambio desde su primer punto.
    const datos = series.map((s, i) => {
      const inicio = s.puntos[0]?.c || 1;
      return {
        nombre: s.nombre, color: comparar ? colores[i % colores.length] : css("--azul"),
        puntos: s.puntos.map((p) => comparar ? { ...p, y: (p.c / inicio - 1) * 100 } : { ...p, y: p.c }),
      };
    });

    const W = contenedor.clientWidth, H = contenedor.clientHeight;
    const M = { izq: 12, der: W < 500 ? 64 : 84, arr: 18, aba: 32 };
    const ancho = W - M.izq - M.der, alto = H - M.arr - M.aba;
    const t0 = base[0].t, t1 = base[base.length - 1].t;
    let yMin = Infinity, yMax = -Infinity;
    for (const s of datos) for (const p of s.puntos) {
      const bajo = tipo === "velas" ? p.l : p.y, alto_ = tipo === "velas" ? p.h : p.y;
      if (bajo < yMin) yMin = bajo;
      if (alto_ > yMax) yMax = alto_;
    }
    const holgura = (yMax - yMin) * 0.08 || Math.abs(yMax) * 0.02 || 1;
    yMin -= holgura; yMax += holgura;
    const x = (t) => M.izq + (t1 === t0 ? ancho / 2 : (t - t0) / (t1 - t0) * ancho);
    const y = (v) => M.arr + (1 - (v - yMin) / (yMax - yMin)) * alto;

    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opciones.descripcion || "Gráfica de precios" });
    const defs = el("defs", {}, svg);
    const grad = el("linearGradient", { id: "degradado", x1: 0, x2: 0, y1: 0, y2: 1 }, defs);
    el("stop", { offset: "0%", "stop-color": css("--azul"), "stop-opacity": oscuro() ? 0.35 : 0.22 }, grad);
    el("stop", { offset: "100%", "stop-color": css("--azul"), "stop-opacity": 0 }, grad);

    // Rejilla y eje de valores (a la derecha, como en las plataformas de trading).
    const eje = el("g", { class: "eje" }, svg);
    for (const v of marcas(yMin, yMax, H < 360 ? 4 : 5)) {
      if (v < yMin || v > yMax) continue;
      el("line", { class: "rejilla", x1: M.izq, x2: M.izq + ancho, y1: y(v), y2: y(v) }, eje);
      const txt = el("text", { x: M.izq + ancho + 8, y: y(v) + 4 }, eje);
      txt.textContent = comparar ? `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("es-CO", { maximumFractionDigits: 1 })} %` : opciones.valor(v, true);
    }
    if (comparar && yMin < 0 && yMax > 0) el("line", { class: "base", x1: M.izq, x2: M.izq + ancho, y1: y(0), y2: y(0) }, svg);
    // Eje de tiempo: unas 6 fechas repartidas.
    const nFechas = Math.max(2, Math.min(6, Math.floor(ancho / 120)));
    for (let k = 0; k <= nFechas; k++) {
      const t = t0 + (t1 - t0) * k / nFechas;
      const txt = el("text", { x: x(t), y: H - 10, "text-anchor": k === 0 ? "start" : k === nFechas ? "end" : "middle" }, eje);
      txt.textContent = opciones.fecha(t, false, t1 - t0);
    }

    if (tipo === "velas") {
      const paso = ancho / Math.max(1, base.length);
      const cuerpo = Math.max(1, Math.min(14, paso * 0.62));
      const g = el("g", {}, svg);
      for (const p of base) {
        const clase = p.c >= p.o ? "vela-sube" : "vela-baja";
        const cx = x(p.t);
        el("line", { class: clase, x1: cx, x2: cx, y1: y(p.h), y2: y(p.l), "stroke-width": 1 }, g);
        const arriba = y(Math.max(p.o, p.c)), abajo = y(Math.min(p.o, p.c));
        el("rect", { class: clase, x: cx - cuerpo / 2, y: arriba, width: cuerpo, height: Math.max(1, abajo - arriba) }, g);
      }
    } else {
      for (const s of datos) {
        const d = s.puntos.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.y).toFixed(1)}`).join("");
        if (!comparar) el("path", { class: "area", d: `${d}L${x(t1)},${M.arr + alto}L${x(t0)},${M.arr + alto}Z` }, svg);
        el("path", { class: "serie", d, stroke: s.color }, svg);
      }
    }

    // Interacción: guía vertical, puntos y globo con el detalle.
    const guia = el("line", { class: "guia", y1: M.arr, y2: M.arr + alto, visibility: "hidden" }, svg);
    const marcadores = datos.map((s) => el("circle", { r: 4, fill: s.color, stroke: css("--papel"), "stroke-width": 2, visibility: "hidden" }, svg));
    contenedor.appendChild(svg);
    const globo = document.createElement("div");
    globo.className = "globo";
    globo.hidden = true;
    contenedor.appendChild(globo);

    let actual = null;
    function mostrar(i) {
      if (i == null) {
        actual = null;
        guia.setAttribute("visibility", "hidden");
        marcadores.forEach((m) => m.setAttribute("visibility", "hidden"));
        globo.hidden = true;
        opciones.alCambiar?.(null);
        return;
      }
      actual = Math.max(0, Math.min(base.length - 1, i));
      const p = base[actual], px = x(p.t);
      guia.setAttribute("x1", px); guia.setAttribute("x2", px); guia.setAttribute("visibility", "visible");
      let html = `<b>${esc(opciones.fecha(p.t, true, t1 - t0))}</b>`;
      datos.forEach((s, k) => {
        const q = s.puntos[actual];
        if (!q) { marcadores[k].setAttribute("visibility", "hidden"); return; }
        marcadores[k].setAttribute("cx", x(q.t)); marcadores[k].setAttribute("cy", y(q.y));
        marcadores[k].setAttribute("visibility", tipo === "velas" ? "hidden" : "visible");
        if (comparar) html += `<br>${esc(s.nombre)}: ${q.y >= 0 ? "+" : "−"}${Math.abs(q.y).toLocaleString("es-CO", { maximumFractionDigits: 2 })} %`;
      });
      if (tipo === "velas") {
        html += `<br>Apertura ${esc(opciones.valor(p.o))}<br>Máximo ${esc(opciones.valor(p.h))}<br>Mínimo ${esc(opciones.valor(p.l))}<br>Cierre ${esc(opciones.valor(p.c))}`;
      } else if (!comparar) {
        html += `<br>${esc(opciones.valor(p.c))}`;
      }
      globo.innerHTML = html;
      globo.hidden = false;
      const cy = comparar || tipo === "velas" ? M.arr + 8 : y(datos[0].puntos[actual].y) - 12;
      const mitad = globo.offsetWidth / 2 + 6;
      globo.style.left = `${Math.max(mitad, Math.min(W - mitad, px))}px`;
      globo.style.top = `${Math.max(globo.offsetHeight + 4, cy)}px`;
      opciones.alCambiar?.(actual);
    }

    function indiceEn(clientX) {
      const r = svg.getBoundingClientRect();
      const t = t0 + ((clientX - r.left) * (W / r.width) - M.izq) / ancho * (t1 - t0);
      let lo = 0, hi = base.length - 1;
      while (hi - lo > 1) { const m = (lo + hi) >> 1; if (base[m].t < t) lo = m; else hi = m; }
      return Math.abs(base[lo].t - t) <= Math.abs(base[hi].t - t) ? lo : hi;
    }
    const mover = (e) => mostrar(indiceEn(e.clientX));
    const salir = () => mostrar(null);
    const tecla = (e) => {
      const salto = e.shiftKey ? 10 : 1;
      if (e.key === "ArrowRight") mostrar((actual ?? -1) + salto);
      else if (e.key === "ArrowLeft") mostrar((actual ?? base.length) - salto);
      else if (e.key === "Home") mostrar(0);
      else if (e.key === "End") mostrar(base.length - 1);
      else if (e.key === "Escape") mostrar(null);
      else return;
      e.preventDefault();
    };
    svg.addEventListener("pointermove", mover);
    svg.addEventListener("pointerdown", mover);
    svg.addEventListener("pointerleave", salir);
    contenedor.addEventListener("keydown", tecla);
    contenedor.addEventListener("blur", salir);
    return {
      colores: datos.map((s) => s.color),
      destruir() { contenedor.removeEventListener("keydown", tecla); contenedor.removeEventListener("blur", salir); },
    };
  }

  return { chispa, dibujar, paleta };
})();
