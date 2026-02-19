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
    """
    Analiza TODAS las ofertas (no solo las que hicieron match) para extraer
    qué skills, cargos y empresas aparecen más en el mercado.
    """
    skill_counter   = Counter()
    cargo_counter   = Counter()
    empresa_counter = Counter()
    con_sueldo      = 0
    sueldos         = []

    for o in ofertas:
        texto = (o.get("nombre", "") + " " + o.get("desc", "")).lower()

        # Skills
        for sk in SKILLS_CONOCIDAS:
            if sk in texto:
                skill_counter[sk] += 1

        # Cargos (palabras clave de títulos)
        cargo_counter[o.get("nombre", "Desconocido")] += 1

        # Empresas
        empresa = o.get("empresa", "Desconocida")
        if empresa not in ("Desconocida", ""):
            empresa_counter[empresa] += 1

        # Sueldos
        s = extraer_sueldo(o.get("desc", ""))
        if s:
            con_sueldo += 1
            sueldos.append(s)

    return {
        "skills":         skill_counter.most_common(20),
        "cargos":         cargo_counter.most_common(15),
        "empresas":       empresa_counter.most_common(10),
        "pct_con_sueldo": round(con_sueldo / len(ofertas) * 100, 1) if ofertas else 0,
        "sueldo_promedio":int(sum(sueldos) / len(sueldos)) if sueldos else None,
        "sueldo_max":     max(sueldos) if sueldos else None,
        "sueldo_min":     min(sueldos) if sueldos else None,
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
            # Truncar nombres largos para el chart
            df_co["Cargo"] = df_co["Cargo"].str[:35]
            st.bar_chart(df_co.set_index("Cargo"), height=320)
        else:
            st.info("Sin datos de cargos.")

    with col_em:
        st.markdown("#### 🏢 Top empresas")
        if analisis["empresas"]:
            df_em = pd.DataFrame(analisis["empresas"], columns=["Empresa", "Ofertas"])
            st.dataframe(df_em, hide_index=True, use_container_width=True, height=320)
        else:
            st.info("Sin datos.")


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


def scrape_linkedin(query: str, ubicacion: str, paginas: int,
                    progress_bar, status_text) -> list:
    """Scrapea LinkedIn Jobs público con barra de progreso en tiempo real."""
    ofertas = []
    query_enc     = quote_plus(query)
    ubicacion_enc = quote_plus(ubicacion)

    for page in range(paginas):
        pct_inicio = page / paginas
        pct_fin    = (page + 1) / paginas

        status_text.markdown(
            f"🔍 **Página {page+1} de {paginas}** — buscando `{query}` en `{ubicacion}`..."
        )
        progress_bar.progress(pct_inicio + 0.01)

        start = page * 25
        url = (
            f"https://www.linkedin.com/jobs/search?"
            f"keywords={query_enc}&location={ubicacion_enc}"
            f"&start={start}&f_TPR=r2592000"
        )
        log.info(f"LinkedIn GET: {url}")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            log.info(f"  HTTP {resp.status_code}")

            if resp.status_code != 200:
                status_text.markdown(f"⚠️ Página {page+1}: HTTP {resp.status_code}, saltando...")
                progress_bar.progress(pct_fin)
                time.sleep(1)
                continue

            soup  = BeautifulSoup(resp.text, "html.parser")
            cards = (
                soup.find_all("div", class_=re.compile(r"base-card")) or
                soup.find_all("li",  class_=re.compile(r"result-card"))
            )

            page_count = 0
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
                    ubicacion_txt = loc_el.get_text(strip=True) if loc_el else ""

                    desc = f"{titulo}. {empresa}. {ubicacion_txt}."
                    ofertas.append({"nombre": titulo, "empresa": empresa,
                                    "desc": desc, "url": job_url})
                    page_count += 1

                    # Actualizar progreso dentro de la página
                    sub_pct = pct_inicio + (pct_fin - pct_inicio) * (page_count / max(len(cards), 1))
                    progress_bar.progress(min(sub_pct, pct_fin - 0.01))

                except Exception as e:
                    log.warning(f"  Card error: {e}")

            status_text.markdown(
                f"✅ Página {page+1}/{paginas} — **{page_count} ofertas** encontradas "
                f"(total acumulado: **{len(ofertas)}**)"
            )
            progress_bar.progress(pct_fin)
            log.info(f"  Página {page+1}: {page_count} ofertas")

            if page < paginas - 1:
                time.sleep(1.5)

        except requests.RequestException as e:
            log.error(f"Red error LinkedIn: {e}")
            status_text.markdown(f"❌ Error de red en página {page+1}: {e}")

    progress_bar.progress(1.0)
    status_text.markdown(f"🎉 Búsqueda completada — **{len(ofertas)} ofertas** encontradas en total.")
    log.info(f"LinkedIn total: {len(ofertas)} ofertas")
    return ofertas


# ─────────────────────────────────────────────
# 9. SIDEBAR
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

    p = sidebar_config(st.session_state.perfil)
    st.session_state.perfil = p

    st.title("🎯 DreamJob v4.0")
    st.caption("Búsqueda y análisis de ofertas laborales con guardado automático.")

    # ── FUENTE DE DATOS ──
    tab_li, tab_dummy = st.tabs(["🔗 LinkedIn Jobs", "🎲 Datos Dummy"])

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
            df_view[["Puntaje", "Nombre", "Empresa", "Sueldo", "Skills", "Experiencia", "Beneficios", "URL"]],
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