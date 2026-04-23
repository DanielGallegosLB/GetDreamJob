import streamlit as st
import pandas as pd
import json
import os
import re
import time
import logging
import requests
from collections import Counter
from bs4 import BeautifulSoup
from datetime import datetime
from random import randint, choice, sample
from urllib.parse import quote_plus

# ─────────────────────────────────────────────
# 0. LOGGING
# ─────────────────────────────────────────────
LOG_FILE = "dreamjob.log"
logging.basicConfig(
    filename=LOG_FILE, level=logging.DEBUG,
    format="%(asctime)s | %(levelname)s | %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("dreamjob")

OFERTAS_FILE = "ofertas_encontradas.json"

# ─────────────────────────────────────────────
# 1. PERSISTENCIA PERFIL
# ─────────────────────────────────────────────
PERFIL_FILE = "perfil_usuario.json"

DEFAULT_PERFIL = {
    "skills":               ["Python", "SQL", "React"],
    "beneficios":           ["Remoto", "Seguro médico", "Bono"],
    "cargos":               ["Tech Lead", "Software Architect", "Fullstack Developer"],
    "renta_min":            1_200_000,
    "renta_max":            3_000_000,
    "prioridad_cargos":     9,
    "prioridad_skills":     8,
    "prioridad_sueldo":     7,
    "prioridad_beneficios": 6,
    "prioridad_experiencia":5,
    "experiencia_min":      0,
    "experiencia_max":      10,
    "linkedin_ubicacion":   "Chile",
    "linkedin_paginas":     3,
}


def cargar_perfil() -> dict:
    if os.path.exists(PERFIL_FILE):
        try:
            with open(PERFIL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in DEFAULT_PERFIL.items():
                if k not in data:
                    data[k] = v
            log.info("Perfil cargado.")
            return data
        except Exception as e:
            log.error(f"Error cargando perfil: {e}")
    return DEFAULT_PERFIL.copy()


def guardar_perfil(perfil: dict):
    with open(PERFIL_FILE, "w", encoding="utf-8") as f:
        json.dump(perfil, f, indent=2, ensure_ascii=False)
    log.info("Perfil guardado.")


# ─────────────────────────────────────────────
# 2. PERSISTENCIA OFERTAS (JSON)
# ─────────────────────────────────────────────
def guardar_ofertas_json(ofertas_raw: list, resultados_match: list):
    """
    Guarda todas las ofertas encontradas en un JSON con:
    - datos crudos del scraping
    - resultado del match si fue analizada
    """
    match_por_url = {r.get("URL", ""): r for r in resultados_match}

    salida = {
        "fecha_busqueda": datetime.now().isoformat(),
        "total_ofertas": len(ofertas_raw),
        "ofertas": []
    }
    for o in ofertas_raw:
        url = o.get("url", "#")
        match = match_por_url.get(url, {})
        salida["ofertas"].append({
            "nombre":    o.get("nombre", ""),
            "empresa":   o.get("empresa", ""),
            "url":       url,
            "desc":      o.get("desc", ""),
            "puntaje":   match.get("Puntaje"),
            "sueldo":    match.get("Sueldo"),
            "skills":    match.get("Skills"),
            "experiencia": match.get("Experiencia"),
            "beneficios":  match.get("Beneficios"),
        })

    with open(OFERTAS_FILE, "w", encoding="utf-8") as f:
        json.dump(salida, f, indent=2, ensure_ascii=False)
    log.info(f"Ofertas guardadas en {OFERTAS_FILE}: {len(ofertas_raw)} registros.")
    return OFERTAS_FILE


# ─────────────────────────────────────────────
# 3. AUTO-GUARDADO
# ─────────────────────────────────────────────
def sync_and_save(widget_key: str, perfil_key: str):
    st.session_state.perfil[perfil_key] = st.session_state[widget_key]
    guardar_perfil(st.session_state.perfil)


# ─────────────────────────────────────────────
# 4. EXTRACCIÓN
# ─────────────────────────────────────────────
def extraer_sueldo(texto: str):
    patrones = [
        r"\$?\s*([\d]{1,2}[.,][\d]{3}[.,][\d]{3})",
        r"\$?\s*([\d]{1,2}[.,][\d]{3})\s*(?:mil|k)",
        r"\$?\s*([\d]{6,8})",
    ]
    for pat in patrones:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(".", "").replace(",", "")
            try:
                val = int(raw)
                ctx = texto[max(0, m.end()-15): m.end()+5].lower()
                if "mil" in ctx or "k" in ctx:
                    val *= 1000
                return val
            except:
                pass
    return None


def extraer_experiencia(texto: str):
    m = re.search(r"(\d+)\s*(?:años|years|yrs|year|año)", texto, re.IGNORECASE)
    return int(m.group(1)) if m else None


# ─────────────────────────────────────────────
# 5. MOTOR MATCHING
# ─────────────────────────────────────────────
def match_lista(texto: str, lista: list, es_priorizada=False):
    texto_l = texto.lower()
    n = len(lista)
    puntos, display = 0, []
    for i, item in enumerate(lista):
        mult = (n - i) if es_priorizada else 1
        if item.lower() in texto_l:
            display.append(f"✅ {item}")
            puntos += 10 * mult
        else:
            display.append(item)
    return puntos, ", ".join(display)


def match_sueldo(val, rmin, rmax, prio):
    if val is None:
        return 0, "❓ No especificado"
    if rmin <= val <= rmax:
        return 50 * prio, f"✅ ${val:,}"
    elif val < rmin:
        return 0, f"🔴 ${val:,} (bajo)"
    else:
        return 10 * prio, f"🟡 ${val:,} (sobre rango)"


def match_experiencia(val, emin, emax, prio):
    if val is None:
        return 0, "❓ No especificado"
    if emin <= val <= emax:
        return 20 * prio, f"✅ {val} años"
    diff = abs(val - emin) if val < emin else abs(val - emax)
    return max(0, 20 * prio - diff * 5), f"⚠️ {val} años"


def calcular_match(oferta: dict, perfil: dict) -> dict:
    nombre = oferta.get("nombre", "")
    desc   = oferta.get("desc", "")

    cargos = perfil.get("cargos", [])
    pts_c, nombre_display = 0, nombre
    for i, cargo in enumerate(cargos):
        if cargo.lower() in nombre.lower():
            pts_c = 10 * (len(cargos) - i) * perfil.get("prioridad_cargos", 9) // 5
            nombre_display = f"✅ {nombre}"
            break

    pts_sk, txt_sk = match_lista(desc, perfil["skills"], es_priorizada=True)
    pts_sk = pts_sk * perfil["prioridad_skills"] // 5

    pts_s, txt_s = match_sueldo(
        extraer_sueldo(desc),
        perfil["renta_min"], perfil["renta_max"], perfil["prioridad_sueldo"]
    )
    pts_e, txt_e = match_experiencia(
        extraer_experiencia(desc),
        perfil["experiencia_min"], perfil["experiencia_max"], perfil["prioridad_experiencia"]
    )
    pts_b, txt_b = match_lista(desc, perfil["beneficios"], es_priorizada=True)
    pts_b = pts_b * perfil["prioridad_beneficios"] // 5

    total = pts_c + pts_sk + pts_s + pts_e + pts_b
    log.info(f"Match '{nombre}': cargos={pts_c} sk={pts_sk} s={pts_s} e={pts_e} b={pts_b} → {total}")

    return {
        "Puntaje":     total,
        "Nombre":      nombre_display,
        "Empresa":     oferta.get("empresa", ""),
        "Ubicación":   oferta.get("ubicacion", ""),
        "Modalidad":   oferta.get("modalidad", ""),
        "Nivel":       oferta.get("nivel", ""),
        "URL":         oferta.get("url", "#"),
        "Sueldo":      txt_s,
        "Skills":      txt_sk,
        "Experiencia": txt_e,
        "Beneficios":  txt_b,
    }


# ─────────────────────────────────────────────
# 6. ANÁLISIS DE INDUSTRIA
# ─────────────────────────────────────────────
SKILLS_CONOCIDAS = [
    "python", "javascript", "typescript", "java", "c#", "go", "rust", "php", "ruby", "scala",
    "sql", "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
    "react", "angular", "vue", "next.js", "node.js", "django", "flask", "fastapi",
    "spring", "express", "laravel",
    "docker", "kubernetes", "aws", "gcp", "azure", "google cloud", "terraform", "ansible",
    "git", "ci/cd", "jenkins", "github actions",
    "machine learning", "data science", "pandas", "spark", "kafka", "airflow",
    "rest api", "graphql", "microservices", "scrum", "agile", "jira",
]


def analizar_industria(ofertas: list) -> dict:
    skill_counter    = Counter()
    cargo_counter    = Counter()
    empresa_counter  = Counter()
    modalidad_counter= Counter()
    con_sueldo       = 0
    sueldos          = []

    for o in ofertas:
        texto = (o.get("nombre", "") + " " + o.get("desc", "")).lower()
        for sk in SKILLS_CONOCIDAS:
            if sk in texto:
                skill_counter[sk] += 1
        cargo_counter[o.get("nombre", "Desconocido")] += 1
        empresa = o.get("empresa", "Desconocida")
        if empresa not in ("Desconocida", ""):
            empresa_counter[empresa] += 1
        mod = o.get("modalidad", "").strip()
        if mod:
            modalidad_counter[mod] += 1
        s = extraer_sueldo(o.get("desc", ""))
        if s:
            con_sueldo += 1
            sueldos.append(s)

    return {
        "skills":          skill_counter.most_common(20),
        "cargos":          cargo_counter.most_common(15),
        "empresas":        empresa_counter.most_common(10),
        "modalidades":     modalidad_counter.most_common(),
        "pct_con_sueldo":  round(con_sueldo / len(ofertas) * 100, 1) if ofertas else 0,
        "sueldo_promedio": int(sum(sueldos) / len(sueldos)) if sueldos else None,
        "sueldo_max":      max(sueldos) if sueldos else None,
        "sueldo_min":      min(sueldos) if sueldos else None,
    }


def mostrar_analisis_industria(ofertas: list):
    analisis = analizar_industria(ofertas)

    st.subheader("🏭 Análisis de Industria — todas las ofertas encontradas")
    st.caption("Basado en el 100% de las ofertas scrapeadas, incluyendo las que no hicieron match contigo.")

    # Métricas de sueldo
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📋 Total ofertas", len(ofertas))
    col2.metric("💰 Ofertas con sueldo", f"{analisis['pct_con_sueldo']}%")
    col3.metric("📈 Sueldo promedio",
                f"${analisis['sueldo_promedio']:,}" if analisis['sueldo_promedio'] else "N/D")
    col4.metric("🔝 Sueldo máximo",
                f"${analisis['sueldo_max']:,}" if analisis['sueldo_max'] else "N/D")

    col_sk, col_co, col_em = st.columns([2, 2, 1])

    with col_sk:
        st.markdown("#### 🔥 Skills más demandadas")
        if analisis["skills"]:
            df_sk = pd.DataFrame(analisis["skills"], columns=["Skill", "Menciones"])
            df_sk["Skill"] = df_sk["Skill"].str.title()
            st.bar_chart(df_sk.set_index("Skill"), height=320)
        else:
            st.info("No se detectaron skills conocidas en las descripciones.")

    with col_co:
        st.markdown("#### 🏷️ Cargos más frecuentes")
        if analisis["cargos"]:
            df_co = pd.DataFrame(analisis["cargos"], columns=["Cargo", "Cantidad"])
            df_co["Cargo"] = df_co["Cargo"].str[:35]
            st.bar_chart(df_co.set_index("Cargo"), height=320)
        else:
            st.info("Sin datos de cargos.")

    with col_em:
        st.markdown("#### 🏢 Top empresas")
        if analisis["empresas"]:
            df_em = pd.DataFrame(analisis["empresas"], columns=["Empresa", "Ofertas"])
            st.dataframe(df_em, hide_index=True, use_container_width=True, height=200)
        else:
            st.info("Sin datos.")
        if analisis.get("modalidades"):
            st.markdown("#### 🏠 Modalidad")
            df_mod = pd.DataFrame(analisis["modalidades"], columns=["Modalidad", "Ofertas"])
            st.dataframe(df_mod, hide_index=True, use_container_width=True, height=130)


# ─────────────────────────────────────────────
# 7. DATOS DUMMY
# ─────────────────────────────────────────────
EMPRESAS    = ["TechCorp", "Startup XYZ", "BancoCL", "DataHub", "DevFactory", "CloudNine", "FinTech SA"]
ROLES       = ["Desarrollador Backend", "Frontend Engineer", "Fullstack Developer", "Data Engineer",
               "DevOps Senior", "Analista de Datos", "Tech Lead", "Software Architect"]
SKILLS_POOL = ["Python", "SQL", "React", "Node.js", "Docker", "Kubernetes", "TypeScript",
               "Django", "FastAPI", "AWS", "PostgreSQL", "MongoDB", "Go", "Java", "C#"]
BENS_POOL   = ["Remoto", "Seguro médico", "Bono", "Horario flexible", "Stock options",
               "Semana adicional vacaciones", "Capacitaciones", "Home office"]
SUELDOS     = [800_000, 1_000_000, 1_500_000, 1_800_000, 2_000_000,
               2_500_000, 2_800_000, 3_200_000, 4_000_000]


def generar_dummy(n=15) -> list:
    ofertas = []
    for _ in range(n):
        skills  = sample(SKILLS_POOL, randint(2, 5))
        bens    = sample(BENS_POOL, randint(1, 4))
        sueldo  = choice(SUELDOS)
        exp     = randint(1, 10)
        nombre  = choice(ROLES)
        empresa = choice(EMPRESAS)
        desc = (
            f"Buscamos {nombre} con experiencia en {', '.join(skills)}. "
            f"Renta líquida ${sueldo:,}. "
            f"{exp} años de experiencia requeridos. "
            f"Beneficios: {', '.join(bens)}."
        )
        ofertas.append({"nombre": nombre, "empresa": empresa, "desc": desc, "url": "#"})
    log.info(f"Generados {n} dummies.")
    return ofertas


# ─────────────────────────────────────────────
# 8. LINKEDIN SCRAPER CON PROGRESO
# ─────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}


def _fetch_detalle_oferta(job_url: str) -> dict:
    """
    Fase 2: Abre la página individual de una oferta LinkedIn y extrae:
    - descripción completa
    - modalidad (presencial/remoto/híbrido)
    - nivel de experiencia
    - tipo de contrato
    - ubicación detallada
    - sueldo si está mencionado
    """
    if not job_url or job_url == "#":
        return {}
    try:
        resp = requests.get(job_url, headers=HEADERS, timeout=12)
        if resp.status_code != 200:
            log.warning(f"  Detalle HTTP {resp.status_code}: {job_url}")
            return {}

        soup = BeautifulSoup(resp.text, "html.parser")

        # ── Descripción completa ──
        desc_el = (
            soup.find("div", class_=re.compile(r"description__text")) or
            soup.find("div", {"class": re.compile(r"show-more-less-html")}) or
            soup.find("section", class_=re.compile(r"description"))
        )
        desc_completa = desc_el.get_text(separator=" ", strip=True) if desc_el else ""

        # ── Criterios del trabajo (lista de metadatos) ──
        criterios = {}
        criteria_items = soup.find_all("li", class_=re.compile(r"description__job-criteria-item"))
        for item in criteria_items:
            header = item.find("h3")
            value  = item.find("span")
            if header and value:
                criterios[header.get_text(strip=True).lower()] = value.get_text(strip=True)

        # ── Ubicación ──
        loc_el = (
            soup.find("span", class_=re.compile(r"topcard__flavor--bullet")) or
            soup.find("span", class_=re.compile(r"jobs-unified-top-card__bullet"))
        )
        ubicacion = loc_el.get_text(strip=True) if loc_el else ""

        # ── Sueldo (a veces aparece en la página de detalle) ──
        salary_el = soup.find("div", class_=re.compile(r"salary|compensation", re.I))
        sueldo_txt = salary_el.get_text(strip=True) if salary_el else ""

        resultado = {
            "desc_completa":  desc_completa,
            "ubicacion":      ubicacion,
            "sueldo_texto":   sueldo_txt,
        }
        # Agregar criterios mapeados
        resultado["modalidad"]  = criterios.get("tipo de lugar de trabajo", criterios.get("workplace type", ""))
        resultado["nivel"]      = criterios.get("nivel de experiencia", criterios.get("seniority level", ""))
        resultado["tipo_empleo"]= criterios.get("tipo de empleo", criterios.get("employment type", ""))
        resultado["industria"]  = criterios.get("sector", criterios.get("industries", ""))

        log.debug(f"  Detalle OK: {len(desc_completa)} chars, modalidad={resultado['modalidad']}")
        return resultado

    except Exception as e:
        log.warning(f"  Error detalle {job_url}: {e}")
        return {}


def scrape_linkedin(query: str, ubicacion: str, paginas: int,
                    progress_bar, status_text) -> list:
    """
    Scraping en 2 fases:
    Fase 1 (30% barra) — Listado: recoge título, empresa, URL de cada card.
    Fase 2 (70% barra) — Detalle: visita cada URL individual para obtener
                          descripción completa, modalidad, experiencia, contrato.
    """
    # ── FASE 1: LISTADO ──────────────────────────────
    cards_raw = []
    query_enc     = quote_plus(query)
    ubicacion_enc = quote_plus(ubicacion)

    for page in range(paginas):
        pct = 0.30 * (page / paginas)
        status_text.markdown(
            f"📋 **Fase 1/2 — Listado** | Página {page+1}/{paginas} · "
            f"{len(cards_raw)} ofertas recopiladas"
        )
        progress_bar.progress(max(pct, 0.01))

        start = page * 25
        url = (
            f"https://www.linkedin.com/jobs/search?"
            f"keywords={query_enc}&location={ubicacion_enc}"
            f"&start={start}&f_TPR=r2592000"
        )
        log.info(f"[F1] GET {url}")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            log.info(f"  HTTP {resp.status_code}")
            if resp.status_code != 200:
                status_text.markdown(f"⚠️ Página {page+1}: HTTP {resp.status_code}, saltando...")
                time.sleep(1)
                continue

            soup  = BeautifulSoup(resp.text, "html.parser")
            cards = (
                soup.find_all("div", class_=re.compile(r"base-card")) or
                soup.find_all("li",  class_=re.compile(r"result-card"))
            )
            log.info(f"  {len(cards)} cards en página {page+1}")

            for card in cards:
                try:
                    titulo_el = (
                        card.find("h3", class_=re.compile(r"base-search-card__title")) or
                        card.find("h3")
                    )
                    titulo = titulo_el.get_text(strip=True) if titulo_el else ""
                    if not titulo:
                        continue

                    empresa_el = (
                        card.find("h4", class_=re.compile(r"base-search-card__subtitle")) or
                        card.find("a",  class_=re.compile(r"hidden-nested-link"))
                    )
                    empresa = empresa_el.get_text(strip=True) if empresa_el else "Desconocida"

                    link_el = card.find("a", href=True)
                    job_url = link_el["href"].split("?")[0] if link_el else "#"

                    loc_el = card.find("span", class_=re.compile(r"job-search-card__location"))
                    ubicacion_card = loc_el.get_text(strip=True) if loc_el else ""

                    cards_raw.append({
                        "nombre":   titulo,
                        "empresa":  empresa,
                        "url":      job_url,
                        "ubicacion_card": ubicacion_card,
                    })
                except Exception as e:
                    log.warning(f"  Card parse error: {e}")

            if page < paginas - 1:
                time.sleep(1.2)

        except requests.RequestException as e:
            log.error(f"[F1] Red error: {e}")

    status_text.markdown(
        f"✅ **Fase 1 completada** — {len(cards_raw)} ofertas encontradas. "
        f"Iniciando Fase 2: descarga de descripciones completas..."
    )
    progress_bar.progress(0.30)
    log.info(f"[F1] Total cards: {len(cards_raw)}")
    time.sleep(0.5)

    # ── FASE 2: DETALLE POR OFERTA ────────────────────
    ofertas = []
    total = len(cards_raw)

    for i, card in enumerate(cards_raw):
        pct = 0.30 + 0.70 * ((i + 1) / max(total, 1))
        status_text.markdown(
            f"🔎 **Fase 2/2 — Detalle** | {i+1}/{total}: *{card['nombre'][:55]}*"
        )
        progress_bar.progress(min(pct, 0.99))

        detalle = _fetch_detalle_oferta(card["url"])

        # Construir desc enriquecida: priorizar desc completa, fallback a card básica
        desc_base = f"{card['nombre']}. {card['empresa']}. {card.get('ubicacion_card','')}."
        desc_completa = detalle.get("desc_completa", "")
        desc_final = desc_completa if len(desc_completa) > 80 else desc_base

        # Enriquecer con campos estructurados
        extras = []
        if detalle.get("modalidad"):
            extras.append(f"Modalidad: {detalle['modalidad']}.")
        if detalle.get("nivel"):
            extras.append(f"Nivel: {detalle['nivel']}.")
        if detalle.get("tipo_empleo"):
            extras.append(f"Empleo: {detalle['tipo_empleo']}.")
        if detalle.get("industria"):
            extras.append(f"Sector: {detalle['industria']}.")
        if detalle.get("sueldo_texto"):
            extras.append(detalle["sueldo_texto"])

        desc_final = desc_final + " " + " ".join(extras)

        oferta = {
            "nombre":      card["nombre"],
            "empresa":     card["empresa"],
            "url":         card["url"],
            "desc":        desc_final.strip(),
            "ubicacion":   detalle.get("ubicacion") or card.get("ubicacion_card", ""),
            "modalidad":   detalle.get("modalidad", ""),
            "nivel":       detalle.get("nivel", ""),
            "tipo_empleo": detalle.get("tipo_empleo", ""),
            "industria":   detalle.get("industria", ""),
        }
        ofertas.append(oferta)
        log.info(f"[F2] {i+1}/{total} '{card['nombre']}' — {len(desc_completa)} chars desc")

        # Delay respetuoso entre requests
        time.sleep(0.8)

    progress_bar.progress(1.0)
    status_text.markdown(
        f"🎉 **Completado** — **{len(ofertas)} ofertas** con descripción completa."
    )
    log.info(f"Scraping total: {len(ofertas)} ofertas enriquecidas.")
    return ofertas



# ─────────────────────────────────────────────
# 9. GOOGLE JOBS — VISOR VIA STATIC + CDP
# ─────────────────────────────────────────────
# Solución 100% sin puertos extra.
# Todo pasa por el puerto de Streamlit (8501).
#
# Cómo funciona:
#   1. Activar en .streamlit/config.toml:
#        [server]
#        enableStaticServing = true
#   2. Chrome abre en el servidor con Selenium.
#   3. Un hilo daemon guarda screenshots en ./static/screen.png cada 400ms.
#   4. Streamlit sirve esa imagen en /app/static/screen.png
#      (mismo puerto 8501, sin firewall extra).
#   5. El visor HTML recarga la imagen automáticamente.
#   6. Controles manuales (click por coordenadas, escribir texto,
#      ejecutar JS, navegar a URL) permiten resolver el CAPTCHA
#      sin acceso físico al servidor.
# ─────────────────────────────────────────────

import threading
import streamlit.components.v1 as components

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_OK = True
except ImportError:
    SELENIUM_OK = False

STATIC_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SCREEN_FILE = os.path.join(STATIC_DIR, "screen.png")
CONFIG_TOML = os.path.join(".streamlit", "config.toml")


def _asegurar_static() -> bool:
    """Crea ./static/ y config.toml si faltan. Retorna True si ya estaba activo."""
    os.makedirs(STATIC_DIR, exist_ok=True)
    os.makedirs(".streamlit", exist_ok=True)
    if os.path.exists(CONFIG_TOML):
        txt = open(CONFIG_TOML).read()
        if "enableStaticServing" in txt and "true" in txt.lower():
            return True
    with open(CONFIG_TOML, "w") as f:
        f.write("[server]\nenableStaticServing = true\n")
    return False


def _hilo_capturas():
    """Daemon: guarda screenshot de Chrome en static/screen.png cada 400ms."""
    log.info("[Screen] Hilo capturas iniciado.")
    while True:
        if st.session_state.get("google_estado") == "done":
            break
        driver = st.session_state.get("google_driver")
        if not driver:
            break
        try:
            driver.get_screenshot_as_file(SCREEN_FILE)
        except Exception:
            break
        time.sleep(0.4)
    log.info("[Screen] Hilo capturas detenido.")


def _iniciar_capturas():
    threading.Thread(target=_hilo_capturas, daemon=True).start()


def _crear_driver():
    opts = Options()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1600,900")
    opts.add_argument("--lang=es-CL,es;q=0.9")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    )
    svc    = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=svc, options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": (
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
        "window.chrome={runtime:{}};"
    )})
    return driver


def _es_captcha(driver) -> bool:
    src = driver.page_source.lower()
    return any(k in src for k in
               ["recaptcha", "captcha-form", "trafico inusual", "unusual traffic"])


def _cdp_click(driver, x: int, y: int, double=False):
    count = 2 if double else 1
    for _ in range(count):
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1, "modifiers": 0})
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1, "modifiers": 0})
        time.sleep(0.05)


def _cdp_type(driver, texto: str):
    for char in texto:
        driver.execute_cdp_cmd("Input.dispatchKeyEvent",
            {"type": "keyDown", "text": char, "unmodifiedText": char})
        driver.execute_cdp_cmd("Input.dispatchKeyEvent",
            {"type": "keyUp",   "text": char, "unmodifiedText": char})


def _extraer_desc_google(driver) -> str:
    js = """
        const KW=['responsabilidades','requisitos','experiencia','funciones',
                  'buscamos','responsibilities','requirements','skills'];
        function score(d){
            const t=(d.innerText||'').trim();
            if(t.length<150||t.length>60000) return {s:0,t:''};
            if(d.children.length>30) return {s:0,t:''};
            return {s:t.length+KW.filter(k=>t.toLowerCase().includes(k)).length*200,t};
        }
        let best={s:0,t:''};
        for(const sel of ['#Sva75c','.cv0dee','.HBvzbc','.pE8vnd',
                          '[jsname="bN97Pc"]','[class*="description"]']){
            const el=document.querySelector(sel);
            if(!el) continue; const r=score(el);
            if(r.s>best.s) best=r;
        }
        if(best.s<500)
            for(const d of document.querySelectorAll('div')){
                const r=score(d); if(r.s>best.s) best=r;
            }
        return best.t;
    """
    driver.switch_to.default_content()
    return driver.execute_script(js) or ""


def _expandir_desc_google(driver):
    driver.execute_script("""
        const targets=[];
        const w=document.querySelector('[jsname="G7vtgf"]');
        if(w){const rb=w.querySelector('[role="button"]');
              if(rb)targets.push(rb);targets.push(w);}
        for(const b of document.querySelectorAll('[role="button"]')){
            const t=(b.textContent||'').toLowerCase();
            if((t.includes('descripci')||t.includes('description'))
               &&!targets.includes(b)) targets.push(b);
        }
        for(const el of targets) try{
            el.scrollIntoView({block:'center',behavior:'instant'});
            ['mousedown','mouseup','click'].forEach(e=>
                el.dispatchEvent(new MouseEvent(e,{bubbles:true,cancelable:true})));
        }catch(e){}
    """)
    time.sleep(2)


def _click_bloque(driver, idx: int) -> tuple:
    bloques = driver.find_elements(By.CSS_SELECTOR, "div.EimVGf")
    if idx >= len(bloques):
        return f"Oferta #{idx+1}", False
    bloque = bloques[idx]
    titulo = f"Oferta #{idx+1}"
    try:
        inner = driver.execute_script("return arguments[0].innerText;", bloque) or ""
        for l in inner.splitlines():
            l = l.strip()
            if l and len(l)>3 and not any(
                p in l.lower() for p in ["hace ","hours ago","days ago","clp","usd","a través"]):
                titulo = l; break
    except Exception:
        pass
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", bloque)
        time.sleep(0.3)
        driver.execute_script("""
            const b=arguments[0];
            ['mousedown','mouseup','click'].forEach(e=>
                b.dispatchEvent(new MouseEvent(e,{bubbles:true,cancelable:true})));
        """, bloque)
        return titulo, True
    except Exception:
        return titulo, False


def google_jobs_iniciar(query, ubicacion, max_res, pb, stx) -> bool:
    if not SELENIUM_OK:
        stx.error("❌ pip install selenium webdriver-manager")
        return False
    stx.markdown("🌐 Abriendo Chrome...")
    pb.progress(0.05)
    driver = _crear_driver()
    st.session_state.update({
        "google_driver": driver,
        "google_max":    max_res,
        "google_query":  query,
    })
    driver.get(
        f"https://www.google.com/search?q={quote_plus(f'{query} {ubicacion}')}&ibp=htl;jobs"
    )
    time.sleep(4)
    _iniciar_capturas()
    if _es_captcha(driver):
        st.session_state["google_estado"] = "waiting_captcha"
        return False
    st.session_state["google_estado"] = "scraping"
    return True


def google_jobs_extraer(pb, stx, urls_vistas) -> list:
    driver = st.session_state.get("google_driver")
    if not driver:
        return []
    max_res = st.session_state.get("google_max", 5)
    ofertas = []
    try:
        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.EimVGf")))
        except TimeoutException:
            stx.warning("❌ No se encontraron bloques."); return []

        bloques = driver.find_elements(By.CSS_SELECTOR, "div.EimVGf")
        sc = 0
        for _ in range(40):
            if len(bloques) >= max_res or sc >= 5: break
            antes = len(bloques)
            driver.execute_script("""
                const b=document.querySelectorAll('div.EimVGf');
                if(b.length) b[b.length-1].scrollIntoView({behavior:'instant',block:'end'});
                window.scrollBy(0,600);
            """)
            time.sleep(1.8)
            bloques = driver.find_elements(By.CSS_SELECTOR, "div.EimVGf")
            sc = sc+1 if len(bloques)==antes else 0

        total = min(len(bloques), max_res)
        for idx in range(total):
            pb.progress(0.1 + 0.85*((idx+1)/total))
            titulo, ok = _click_bloque(driver, idx)
            stx.markdown(f"🖱️ **{idx+1}/{total}** — *{titulo[:65]}* {'✅' if ok else '⚠️'}")
            if not ok: continue
            url_antes = driver.current_url
            t0 = time.time()
            while time.time()-t0 < 6:
                time.sleep(0.4)
                if driver.current_url != url_antes: break
            time.sleep(0.8)
            _expandir_desc_google(driver)
            desc = _extraer_desc_google(driver)
            url  = driver.current_url
            if url not in urls_vistas:
                ofertas.append({"nombre":titulo,"empresa":"",
                                "desc":desc or f"[Sin desc — {titulo}]","url":url})
                urls_vistas.add(url)
            time.sleep(0.5)
    except Exception as e:
        log.error(f"[GJ] {e}"); stx.error(f"❌ {e}")
    finally:
        try: driver.quit()
        except Exception: pass
        st.session_state.pop("google_driver", None)
        st.session_state["google_estado"] = "done"
    pb.progress(1.0)
    stx.markdown(f"🎉 **{len(ofertas)} ofertas** extraídas.")
    return ofertas


def _acumular_ofertas_google(ofertas_g):
    if ofertas_g:
        ex  = st.session_state.get("ofertas", [])
        ux  = {o.get("url") for o in ex}
        new = [o for o in ofertas_g if o.get("url") not in ux]
        st.session_state.ofertas   = ex + new
        st.session_state.res_final = None
        st.toast(f"✅ {len(new)} nuevas ofertas de Google Jobs", icon="🌍")
    else:
        st.warning("⚠️ Sin resultados.")
    st.session_state["google_estado"] = "idle"


def _panel_visor(base_url: str):
    """
    Muestra el screenshot de Chrome como imagen que se refresca sola.
    La imagen viene de /app/static/screen.png en el mismo puerto de Streamlit.
    """
    img_url = f"{base_url.rstrip('/')}/app/static/screen.png"
    components.html(f"""
    <div style="font-family:sans-serif;font-size:12px;color:#888;margin-bottom:6px;">
      🖥️ Chrome en vivo &nbsp;·&nbsp; refresco automático cada 400ms &nbsp;|&nbsp;
      <a href="{img_url}" target="_blank" style="color:#4A90D9;">
        Ver imagen directa ↗</a>
    </div>
    <div style="border:2px solid #333;border-radius:6px;overflow:hidden;">
      <img id="scr" src="{img_url}?t=0"
           style="width:100%;display:block;" alt="Cargando...">
    </div>
    <script>
      const s = document.getElementById('scr');
      setInterval(() => s.src = '{img_url}?t=' + Date.now(), 400);
    </script>
    """, height=510)


def _panel_controles():
    """Controles manuales de Chrome: click por coordenadas, teclado, JS, navegación."""
    driver = st.session_state.get("google_driver")
    if not driver:
        return
    with st.expander("🖱️ Controles de Chrome (para resolver CAPTCHA)", expanded=True):
        st.caption("Usa estos controles para interactuar con Chrome en el servidor.")

        # Navegación
        col1, col2 = st.columns([4, 1])
        nav_url = col1.text_input("Navegar a URL:", key="g_nav", placeholder="https://...")
        if col2.button("Ir →", key="g_go", use_container_width=True):
            if nav_url:
                driver.get(nav_url); time.sleep(2); st.rerun()

        # Click por coordenadas
        st.markdown("**Click por coordenadas** (resolución Chrome: 1600×900)")
        c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
        cx = c1.number_input("X", 0, 1600, 800, key="g_cx")
        cy = c2.number_input("Y", 0, 900,  450, key="g_cy")
        if c3.button("Click", key="g_clk", use_container_width=True):
            _cdp_click(driver, cx, cy); time.sleep(0.5); st.rerun()
        if c4.button("Doble", key="g_dbl", use_container_width=True):
            _cdp_click(driver, cx, cy, double=True); time.sleep(0.5); st.rerun()

        # Teclado
        col3, col4 = st.columns([4, 1])
        texto = col3.text_input("Escribir texto:", key="g_txt",
                                 placeholder="texto a escribir en Chrome...")
        if col4.button("Enviar", key="g_send", use_container_width=True):
            if texto:
                _cdp_type(driver, texto); time.sleep(0.3); st.rerun()

        # Teclas especiales
        especiales = {"Enter": "\r", "Tab": "\t", "Escape": "\x1b",
                      "Backspace": "\b", "↑": "ArrowUp", "↓": "ArrowDown"}
        cols = st.columns(len(especiales))
        for i, (label, key) in enumerate(especiales.items()):
            if cols[i].button(label, key=f"g_key_{label}"):
                char = key if len(key)==1 else ""
                if char:
                    driver.execute_cdp_cmd("Input.dispatchKeyEvent",
                        {"type":"keyDown","text":char,"unmodifiedText":char})
                    driver.execute_cdp_cmd("Input.dispatchKeyEvent",
                        {"type":"keyUp","text":char,"unmodifiedText":char})
                st.rerun()

        # Scroll
        col5, col6, col7 = st.columns(3)
        if col5.button("⬆️ Scroll arriba", key="g_sup", use_container_width=True):
            driver.execute_script("window.scrollBy(0,-300);"); st.rerun()
        if col6.button("⬇️ Scroll abajo",  key="g_sdn", use_container_width=True):
            driver.execute_script("window.scrollBy(0,300);"); st.rerun()

        # JavaScript libre
        col8, col9 = st.columns([4, 1])
        js_code = col8.text_input("JS libre:", key="g_js",
                                   placeholder="document.querySelector('input').click()")
        if col9.button("▶️", key="g_exec", use_container_width=True):
            if js_code:
                try:
                    r = driver.execute_script(f"return {js_code}")
                    st.info(f"→ {r}")
                except Exception:
                    try: driver.execute_script(js_code)
                    except Exception as e: st.error(str(e))
                st.rerun()


def render_tab_google(p: dict):
    """Pestaña Google Jobs. El visor va a través del mismo puerto de Streamlit."""

    if not SELENIUM_OK:
        st.warning("⚠️ Instala: `pip install selenium webdriver-manager`")
        return

    static_ok = _asegurar_static()
    if not static_ok:
        st.error(
            "⚙️ Se creó `.streamlit/config.toml` con `enableStaticServing = true`.\n\n"
            "**Reinicia Streamlit** (`Ctrl+C` y `streamlit run dreamjob_app.py`) "
            "para activar el visor. Solo es necesario una vez."
        )
        return

    estado = st.session_state.get("google_estado", "idle")

    base_url = st.text_input(
        "🌐 URL de Streamlit (como la ves en tu browser):",
        value="http://localhost:8501",
        key="g_base",
        help="Ej: http://192.168.1.100:8501 — el visor usará el mismo puerto.",
        disabled=(estado != "idle"),
    )

    # ── CAPTCHA ──────────────────────────────────────────────────────────────
    if estado == "waiting_captcha":
        st.error(
            "🔒 **CAPTCHA detectado** — Usa el visor y los controles para resolverlo.\n\n"
            "La imagen se actualiza sola cada 400ms. Los controles manuales te permiten "
            "hacer click, escribir y navegar en Chrome remotamente."
        )
        _panel_visor(base_url)
        _panel_controles()

        c1, c2 = st.columns(2)
        if c1.button("✅ CAPTCHA resuelto — Continuar",
                      type="primary", use_container_width=True):
            d = st.session_state.get("google_driver")
            if d and not _es_captcha(d):
                st.session_state["google_estado"] = "resuming"
                st.rerun()
            elif d:
                st.warning("⚠️ Aún parece haber CAPTCHA en la página.")
            else:
                st.session_state["google_estado"] = "idle"
        if c2.button("❌ Cancelar búsqueda", use_container_width=True):
            d = st.session_state.pop("google_driver", None)
            if d:
                try: d.quit()
                except Exception: pass
            st.session_state["google_estado"] = "idle"
            st.rerun()
        return

    # ── RESUMING ─────────────────────────────────────────────────────────────
    if estado == "resuming":
        st.info("⏳ Reanudando extracción tras CAPTCHA...")
        _panel_visor(base_url)
        pb  = st.progress(0)
        stx = st.empty()
        urls = {o.get("url") for o in st.session_state.get("ofertas",[]) if o.get("url")}
        _acumular_ofertas_google(google_jobs_extraer(pb, stx, urls))
        st.rerun()
        return

    # ── SCRAPING ─────────────────────────────────────────────────────────────
    if estado == "scraping":
        st.info("⏳ Extrayendo ofertas... Chrome visible abajo.")
        _panel_visor(base_url)
        return

    # ── IDLE ─────────────────────────────────────────────────────────────────
    query_default = " OR ".join(p.get("cargos",[])[:3]) if p.get("cargos") else "Developer"
    cq, cn = st.columns([3, 1])
    query_g = cq.text_input("Palabras clave:", value=query_default, key="g_query")
    max_res  = cn.number_input("Resultados", 1, 50, 5, key="g_max")

    st.info(
        "💡 Chrome abrirá en el servidor. "
        "Lo verás en tiempo real en el visor de abajo (misma URL, mismo puerto). "
        "Si hay CAPTCHA, usa los controles para resolverlo sin acceso físico al servidor."
    )

    if st.button("🔍 Buscar en Google Jobs", type="primary", use_container_width=True):
        pb  = st.progress(0)
        stx = st.empty()
        urls = {o.get("url") for o in st.session_state.get("ofertas",[]) if o.get("url")}
        ok = google_jobs_iniciar(query_g, p.get("linkedin_ubicacion","Chile"),
                                  max_res, pb, stx)
        if not ok:
            st.rerun()
        else:
            # Extraer directamente (sin CAPTCHA)
            _acumular_ofertas_google(
                google_jobs_extraer(pb, stx, urls)
            )


# ─────────────────────────────────────────────
# 10. SIDEBAR
# ─────────────────────────────────────────────
def _lista_editable(p, campo, label, prefix):
    nuevo = st.text_input(f"Agregar {label}:", key=f"input_{prefix}")
    if st.button("➕ Añadir", key=f"add_{prefix}"):
        val = nuevo.strip()
        if val and val not in p[campo]:
            p[campo].insert(0, val)
            guardar_perfil(p)
            st.rerun()
    for idx, item in enumerate(p[campo]):
        c1, c2, c3, c4 = st.columns([4, 1, 1, 1])
        c1.write(f"**{idx+1}.** {item}")
        if c2.button("↑", key=f"{prefix}_up_{idx}") and idx > 0:
            p[campo][idx], p[campo][idx-1] = p[campo][idx-1], p[campo][idx]
            guardar_perfil(p); st.rerun()
        if c3.button("↓", key=f"{prefix}_dn_{idx}") and idx < len(p[campo])-1:
            p[campo][idx], p[campo][idx+1] = p[campo][idx+1], p[campo][idx]
            guardar_perfil(p); st.rerun()
        if c4.button("🗑", key=f"{prefix}_del_{idx}"):
            p[campo].pop(idx)
            guardar_perfil(p); st.rerun()


def _slider_autosave(label, pmin, pmax, perfil_key, widget_key, p):
    if widget_key not in st.session_state:
        st.session_state[widget_key] = p[perfil_key]
    st.slider(label, pmin, pmax, key=widget_key,
              on_change=sync_and_save, args=(widget_key, perfil_key))
    p[perfil_key] = st.session_state[widget_key]


def _number_autosave(label, step, perfil_key, widget_key, p):
    if widget_key not in st.session_state:
        st.session_state[widget_key] = p[perfil_key]
    st.number_input(label, step=step, key=widget_key,
                    on_change=sync_and_save, args=(widget_key, perfil_key))
    p[perfil_key] = st.session_state[widget_key]


def _text_autosave(label, perfil_key, widget_key, p):
    if widget_key not in st.session_state:
        st.session_state[widget_key] = p.get(perfil_key, "")
    st.text_input(label, key=widget_key,
                  on_change=sync_and_save, args=(widget_key, perfil_key))
    p[perfil_key] = st.session_state[widget_key]


def sidebar_config(p):
    with st.sidebar:
        st.markdown("## ⚙️ Configuración")
        st.caption("💾 Guardado automático en cada cambio.")

        with st.expander("🏷️ Cargos Deseados", expanded=True):
            _lista_editable(p, "cargos", "cargo", "cargo")
            _slider_autosave("Peso Cargos", 1, 10, "prioridad_cargos", "sl_pc", p)

        with st.expander("💻 Skills", expanded=False):
            _lista_editable(p, "skills", "skill", "skill")
            _slider_autosave("Peso Skills", 1, 10, "prioridad_skills", "sl_ps", p)

        with st.expander("🎁 Beneficios", expanded=False):
            _lista_editable(p, "beneficios", "beneficio", "ben")
            _slider_autosave("Peso Beneficios", 1, 10, "prioridad_beneficios", "sl_pb", p)

        with st.expander("💰 Sueldo Ideal", expanded=False):
            _number_autosave("Mínimo ($)", 100_000, "renta_min", "ni_rmin", p)
            _number_autosave("Máximo ($)", 100_000, "renta_max", "ni_rmax", p)
            _slider_autosave("Peso Sueldo", 1, 10, "prioridad_sueldo", "sl_psu", p)

        with st.expander("🎓 Experiencia", expanded=False):
            _number_autosave("Mín años", 1, "experiencia_min", "ni_emin", p)
            _number_autosave("Máx años", 1, "experiencia_max", "ni_emax", p)
            _slider_autosave("Peso Experiencia", 1, 10, "prioridad_experiencia", "sl_pe", p)

        with st.expander("🔗 LinkedIn", expanded=False):
            _text_autosave("Ubicación", "linkedin_ubicacion", "ti_li_ubi", p)
            _slider_autosave("Páginas (~25 ofertas c/u)", 1, 10, "linkedin_paginas", "sl_li_pag", p)

    return p


# ─────────────────────────────────────────────
# 10. MAIN
# ─────────────────────────────────────────────
def main():
    st.set_page_config(layout="wide", page_title="DreamJob v4.0", page_icon="🎯")
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;700;800&display=swap');
    html, body, [class*="css"]  { font-family: 'Syne', sans-serif; }
    h1, h2, h3                  { font-family: 'Syne', sans-serif; font-weight: 800; letter-spacing: -0.5px; }
    .stDataFrame td             { font-family: 'Space Mono', monospace; font-size: 0.8rem; }
    .block-container            { padding-top: 2rem; }
    a { color: #4A90D9 !important; text-decoration: underline !important; }
    </style>
    """, unsafe_allow_html=True)

    if "perfil" not in st.session_state:
        st.session_state.perfil = cargar_perfil()
    if "google_estado" not in st.session_state:
        st.session_state["google_estado"] = "idle"

    p = sidebar_config(st.session_state.perfil)
    st.session_state.perfil = p

    st.title("🎯 DreamJob v4.1")
    st.caption("Búsqueda y análisis de ofertas laborales con guardado automático.")

    # ── FUENTE DE DATOS ──
    tab_li, tab_google, tab_dummy = st.tabs(["🔗 LinkedIn Jobs", "🔍 Google Jobs", "🎲 Datos Dummy"])

    with tab_li:
        cargos_activos = p.get("cargos", [])
        query_default  = " OR ".join(cargos_activos[:3]) if cargos_activos else "Developer"
        query = st.text_input("Query de búsqueda:", value=query_default, key="li_query")
        st.caption(
            f"📍 **{p.get('linkedin_ubicacion','Chile')}** · "
            f"📄 **{p.get('linkedin_paginas',3)} páginas** (~{p.get('linkedin_paginas',3)*25} ofertas máx)"
        )

        if st.button("🔍 Buscar en LinkedIn", type="primary", use_container_width=True):
            progress_bar  = st.progress(0)
            status_text   = st.empty()

            ofertas = scrape_linkedin(
                query,
                p.get("linkedin_ubicacion", "Chile"),
                p.get("linkedin_paginas", 3),
                progress_bar, status_text,
            )

            if not ofertas:
                st.warning(
                    "⚠️ Sin resultados. LinkedIn puede estar bloqueando temporalmente. "
                    "Intenta con menos páginas o espera unos minutos."
                )
            else:
                st.session_state.ofertas = ofertas
                st.session_state.res_final = None

    with tab_google:
        render_tab_google(p)

    with tab_dummy:
        n_dummy = st.number_input("Cantidad de ofertas dummy", 5, 100, 20, key="ndummy")
        if st.button("🎲 Generar", use_container_width=True):
            st.session_state.ofertas = generar_dummy(n_dummy)
            st.session_state.res_final = None
            st.success(f"✅ {n_dummy} ofertas dummy generadas.")

    # ── ANALIZAR ──
    st.divider()
    ofertas_cargadas = st.session_state.get("ofertas", [])
    n_cargadas = len(ofertas_cargadas)
    col_info, col_btn = st.columns([3, 1])
    col_info.caption(
        f"📦 **{n_cargadas} ofertas** cargadas y listas para analizar."
        if n_cargadas else "Sin ofertas. Genera dummy o busca en LinkedIn."
    )
    if col_btn.button("🚀 Analizar", type="primary", use_container_width=True, disabled=not n_cargadas):
        with st.spinner("Calculando match..."):
            resultados = [calcular_match(o, p) for o in ofertas_cargadas]
        st.session_state.res_final = resultados

        # Guardar JSON con todas las ofertas + resultados match
        json_path = guardar_ofertas_json(ofertas_cargadas, resultados)
        st.session_state.ofertas_json_path = json_path
        log.info(f"Análisis completado: {len(resultados)} resultados.")

    # ── RESULTADOS ──
    if st.session_state.get("res_final"):
        resultados = st.session_state.res_final
        df = pd.DataFrame(resultados)
        df = df.sort_values("Puntaje", ascending=False).reset_index(drop=True)
        df.index += 1

        st.markdown("---")
        st.subheader(f"📊 Resultados de Match — {len(df)} ofertas")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("🥇 Mejor Puntaje", df["Puntaje"].iloc[0])
        m2.metric("📈 Promedio",      round(df["Puntaje"].mean(), 1))
        m3.metric("💼 En rango salarial", int(df["Sueldo"].str.startswith("✅").sum()))
        m4.metric("🏷️ Cargo match",       int(df["Nombre"].str.startswith("✅").sum()))

        filtro = st.text_input("🔍 Filtrar tabla:", key="filtro")
        df_view = df.copy()
        if filtro:
            mask = df_view.apply(lambda r: r.astype(str).str.contains(filtro, case=False).any(), axis=1)
            df_view = df_view[mask]

        # ── TABLA CON URL CLICKEABLE ──
        # Usar st.dataframe con column_config para URLs clicables
        st.dataframe(
            df_view[["Puntaje", "Nombre", "Empresa", "Ubicación", "Modalidad", "Nivel",
                     "Sueldo", "Skills", "Experiencia", "Beneficios", "URL"]],
            column_config={
                "URL": st.column_config.LinkColumn(
                    "🔗 Ver Oferta",
                    display_text="Abrir →",
                    help="Abre la oferta en una nueva pestaña",
                ),
            },
            use_container_width=True,
            hide_index=False,
        )

        # ── DESCARGA JSON ──
        if st.session_state.get("ofertas_json_path") and os.path.exists(OFERTAS_FILE):
            with open(OFERTAS_FILE, "rb") as f:
                st.download_button(
                    label="⬇️ Descargar ofertas_encontradas.json",
                    data=f,
                    file_name=f"ofertas_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                    mime="application/json",
                )

        st.divider()

        # ── ANÁLISIS DE INDUSTRIA ──
        mostrar_analisis_industria(st.session_state.get("ofertas", []))

        # ── LOG ──
        with st.expander("📋 Log (últimas 150 líneas)"):
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, encoding="utf-8") as f:
                    lines = f.readlines()
                st.code("".join(lines[-150:]), language="text")


if __name__ == "__main__":
    main()