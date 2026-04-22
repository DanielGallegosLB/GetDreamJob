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
import sys
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager


# ─────────────────────────────────────────────
# 0. LOGGING
# ─────────────────────────────────────────────
LOG_FILE = "dreamjob.log"
file_handler    = logging.FileHandler(LOG_FILE, encoding="utf-8")
console_handler = logging.StreamHandler(sys.stdout)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[file_handler, console_handler]
)
log = logging.getLogger("dreamjob")
log.info("🚀 Sistema DreamJob iniciado.")

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


def cargar_ofertas_historico() -> list:
    """Carga todas las ofertas guardadas en el JSON historico."""
    if os.path.exists(OFERTAS_FILE):
        try:
            with open(OFERTAS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("ofertas", [])
        except Exception as e:
            log.error(f"Error cargando historico: {e}")
    return []

def borrar_historial():
    """Elimina el archivo de ofertas historicas."""
    if os.path.exists(OFERTAS_FILE):
        os.remove(OFERTAS_FILE)
        log.info("Historial de ofertas borrado.")

def cargar_urls_existentes() -> set:
    if os.path.exists(OFERTAS_FILE):
        try:
            with open(OFERTAS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {o.get("url") for o in data.get("ofertas", []) if o.get("url")}
        except Exception as e:
            log.error(f"Error cargando URLs existentes: {e}")
    return set()


# ─────────────────────────────────────────────
# 2. PERSISTENCIA OFERTAS (JSON)
# ─────────────────────────────────────────────
def guardar_ofertas_json(ofertas_raw: list, resultados_match: list):
    match_por_url = {r.get("URL", ""): r for r in resultados_match}
    datos_historicos = {"ofertas": []}
    if os.path.exists(OFERTAS_FILE):
        try:
            with open(OFERTAS_FILE, "r", encoding="utf-8") as f:
                datos_historicos = json.load(f)
        except Exception as e:
            log.error(f"Error leyendo historial: {e}")

    dict_acumulado = {o["url"]: o for o in datos_historicos.get("ofertas", []) if "url" in o}

    for o in ofertas_raw:
        url   = o.get("url", "#")
        match = match_por_url.get(url, {})
        dict_acumulado[url] = {
            "nombre":      o.get("nombre", ""),
            "empresa":     o.get("empresa", ""),
            "url":         url,
            "desc":        o.get("desc", ""),
            "puntaje":     match.get("Puntaje"),
            "sueldo":      match.get("Sueldo"),
            "skills":      match.get("Skills"),
            "experiencia": match.get("Experiencia"),
            "beneficios":  match.get("Beneficios"),
            "ultima_actualizacion": datetime.now().isoformat()
        }

    salida = {
        "fecha_ultima_busqueda": datetime.now().isoformat(),
        "total_historico": len(dict_acumulado),
        "ofertas": list(dict_acumulado.values())
    }
    try:
        with open(OFERTAS_FILE, "w", encoding="utf-8") as f:
            json.dump(salida, f, indent=2, ensure_ascii=False)
        log.info(f"Persistencia exitosa: {len(dict_acumulado)} ofertas.")
    except Exception as e:
        log.error(f"Error escribiendo {OFERTAS_FILE}: {e}")
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
    """
    Extrae años de experiencia REQUERIDOS.
    Prioriza números cerca de 'experiencia' para evitar capturar
    frases como "35 años de trayectoria" de la empresa.
    """
    # Prioridad 1: número justo al lado de "experiencia" (ej: "3 Años de Experiencia", "Experiencia: 2 años")
    m = re.search(r"(\d+)\s*(?:años|years|yrs|year|año)\s*de\s*experiencia", texto, re.IGNORECASE)
    if m:
        return int(m.group(1))

    # Prioridad 2: "experiencia" seguida poco después de un número + años
    m = re.search(r"experiencia[^.]{0,60}?(\d+)\s*(?:años|years|yrs|year|año)", texto, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        if val <= 20:  # sanity check: más de 20 años requeridos es inusual
            return val

    # Prioridad 3: número + años cerca de palabras clave de requisito
    m = re.search(r"(\d+)\s*(?:años|years|yrs|year|año)[^.]{0,40}?(?:experiencia|requerid|mínimo|minimo)", texto, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        if val <= 20:
            return val

    # Fallback: primer match genérico, pero solo si es <= 20 (evita "35 años de trayectoria")
    m = re.search(r"(\d+)\s*(?:años|years|yrs|year|año)", texto, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        if val <= 20:
            return val

    return None


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
    log.info(f"Match '{nombre}': {total} pts")

    return {
        "Puntaje":      total,
        "Nombre":       nombre_display,
        "Empresa":      oferta.get("empresa", ""),
        "URL":          oferta.get("url", "#"),
        "Sueldo":       txt_s,
        "Skills":       txt_sk,
        "Experiencia":  txt_e,
        "Beneficios":   txt_b,
        "Descripcion":  desc,
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
    skill_counter   = Counter()
    cargo_counter   = Counter()
    empresa_counter = Counter()
    con_sueldo      = 0
    sueldos         = []

    for o in ofertas:
        texto = (o.get("nombre", "") + " " + o.get("desc", "")).lower()
        for sk in SKILLS_CONOCIDAS:
            if sk in texto:
                skill_counter[sk] += 1
        cargo_counter[o.get("nombre", "Desconocido")] += 1
        empresa = o.get("empresa", "Desconocida")
        if empresa not in ("Desconocida", ""):
            empresa_counter[empresa] += 1
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
    st.caption("Basado en el 100% de las ofertas scrapeadas.")

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
            st.info("No se detectaron skills conocidas.")
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
        ofertas.append({"nombre": nombre, "empresa": empresa, "desc": desc, "url": f"#dummy-{_}"})
    log.info(f"Generados {n} dummies.")
    return ofertas


# ─────────────────────────────────────────────
# 8. LINKEDIN SCRAPER
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
                    progress_bar, status_text, urls_vistas: set) -> list:
    ofertas = []
    query_enc     = quote_plus(query)
    ubicacion_enc = quote_plus(ubicacion)

    for page in range(paginas):
        pct_inicio = page / paginas
        pct_fin    = (page + 1) / paginas
        status_text.markdown(f"🔍 **Página {page+1} de {paginas}** — `{query}` en `{ubicacion}`...")
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
            if resp.status_code != 200:
                status_text.markdown(f"⚠️ Página {page+1}: HTTP {resp.status_code}")
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
                    loc_el  = card.find("span", class_=re.compile(r"job-search-card__location"))
                    ubicacion_txt = loc_el.get_text(strip=True) if loc_el else ""

                    if job_url in urls_vistas:
                        continue

                    desc = f"{titulo}. {empresa}. {ubicacion_txt}."
                    ofertas.append({"nombre": titulo, "empresa": empresa, "desc": desc, "url": job_url})
                    urls_vistas.add(job_url)
                    page_count += 1

                    sub_pct = pct_inicio + (pct_fin - pct_inicio) * (page_count / max(len(cards), 1))
                    progress_bar.progress(min(sub_pct, pct_fin - 0.01))
                except Exception as e:
                    log.warning(f"Card error: {e}")

            status_text.markdown(f"✅ Página {page+1}/{paginas} — **{page_count} ofertas**")
            progress_bar.progress(pct_fin)
            if page < paginas - 1:
                time.sleep(1.5)

        except requests.RequestException as e:
            log.error(f"Red error LinkedIn: {e}")

    progress_bar.progress(1.0)
    status_text.markdown(f"🎉 **{len(ofertas)} ofertas** encontradas en LinkedIn.")
    return ofertas


# ─────────────────────────────────────────────
# 11. GOOGLE JOBS SCRAPER — TODOS LOS BLOQUES
# ─────────────────────────────────────────────

def _js(driver, script, *args):
    """Shortcut para execute_script."""
    return driver.execute_script(script, *args)


def _click_bloque(driver, bloque, idx):
    """
    Hace click en el bloque de trabajo para abrir el panel derecho.
    Google Jobs usa jsaction para manejar clicks — hay que disparar
    el evento en el elemento hijo con jsaction, no en el contenedor div.EimVGf.
    Retorna (titulo_texto, exito).
    """
    titulo_texto = f"Oferta #{idx+1}"

    # Leer título via innerText del bloque completo
    try:
        inner = _js(driver, "return arguments[0].innerText;", bloque) or ""
        lineas = [l.strip() for l in inner.splitlines() if l.strip()]
        for linea in lineas:
            if not any(p in linea.lower() for p in ["hace ", "hours ago", "days ago", "day ago", "clp", "usd", "a través"]):
                if len(linea) > 3:
                    titulo_texto = linea
                    break
        print(f"   Título del bloque: '{titulo_texto}'")
    except Exception as e:
        print(f"   ⚠️ No se pudo leer innerText: {e}")

    # Scroll al bloque
    try:
        _js(driver, "arguments[0].scrollIntoView({block:'center'});", bloque)
        time.sleep(0.3)
    except Exception:
        pass

    # Estrategia 1: click en el hijo con jsaction dentro del bloque
    # Google Jobs usa jsaction="focus:..." o jsaction="click:..." en el elemento real
    resultado = _js(driver, """
        const bloque = arguments[0];
        // Buscar hijo con jsaction (el elemento que recibe el evento real)
        const conJsaction = bloque.querySelector('[jsaction]');
        if (conJsaction) {
            conJsaction.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            return 'OK:jsaction:' + (conJsaction.getAttribute('jsaction') || '');
        }
        // Fallback: click nativo en el bloque
        bloque.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
        return 'OK:bloque';
    """, bloque)

    print(f"   Click resultado: '{resultado}'")
    if resultado and resultado.startswith("OK:"):
        print(f"   ✅ Click OK.")
        return titulo_texto, True

    print(f"   ❌ Click falló.")
    return titulo_texto, False


def _get_panel_frame(driver):
    """
    El panel de detalle de Google Jobs vive en un iframe.
    Devuelve el iframe que contiene #Sva75c, o None si no existe.
    """
    try:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        for frame in frames:
            try:
                driver.switch_to.frame(frame)
                panel = driver.find_elements(By.ID, "Sva75c")
                if panel:
                    return True  # ya estamos en el frame correcto
                driver.switch_to.default_content()
            except Exception:
                driver.switch_to.default_content()
        return False
    except Exception:
        return False


def _en_frame_panel(driver):
    """
    Cambia al iframe que contiene el panel de descripción.
    Devuelve True si lo encontró, False si el panel está en el doc principal.
    """
    driver.switch_to.default_content()

    # Primero verificar si #Sva75c está en el documento principal
    panel_principal = _js(driver, "return !!document.getElementById('Sva75c');")
    if panel_principal:
        return True  # ya estamos en el contexto correcto

    # Buscar en iframes
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    for frame in frames:
        try:
            driver.switch_to.frame(frame)
            panel = driver.execute_script("return !!document.getElementById('Sva75c');")
            if panel:
                return True
            driver.switch_to.default_content()
        except Exception:
            driver.switch_to.default_content()

    return False  # no encontrado en ningún contexto


def _click_mostrar_descripcion(driver):
    """
    Clickea 'Mostrar descripción completa'.
    El HTML tiene dos niveles:
      - div[jsname="G7vtgf"]  ← contenedor externo (jsaction registrado aquí)
      - div[role="button"]    ← hijo interno (elemento visual clickeable)
    Estrategia: localizar el role=button correcto vía texto, luego intentar
    click nativo de Selenium + dispatchEvent en ambos niveles como fallback.
    """
    print("   🔍 [3/4] Buscando botón 'Mostrar/Ver descripción completa'...")

    # JS: devuelve el XPath del botón correcto para que Selenium haga click nativo
    js_find = """
        // Estrategia A: role=button dentro del wrapper jsname=G7vtgf
        const wrapper = document.querySelector('[jsname="G7vtgf"]');
        if (wrapper) {
            const rb = wrapper.querySelector('[role="button"]');
            if (rb) return 'FOUND_INNER';
        }
        // Estrategia B: cualquier role=button cuyo texto mencione "descripci"
        const allBtns = document.querySelectorAll('[role="button"]');
        for (const b of allBtns) {
            const t = (b.textContent || '').trim().toLowerCase();
            if (t.includes('descripci') || t.includes('description')) {
                return 'FOUND_TEXT';
            }
        }
        // Estrategia C: jsaction directo
        const jsact = document.querySelector('[jsaction*="EMtXr"]');
        if (jsact) return 'FOUND_JSACT';
        return 'NOT_FOUND';
    """

    js_click_all = """
        let targets = [];

        // Nivel 1: role=button dentro de jsname=G7vtgf
        const wrapper = document.querySelector('[jsname="G7vtgf"]');
        if (wrapper) {
            const rb = wrapper.querySelector('[role="button"]');
            if (rb) targets.push({el: rb, label: 'inner-role-button'});
            targets.push({el: wrapper, label: 'jsname-wrapper'});
        }

        // Nivel 2: jsaction directo
        const jsact = document.querySelector('[jsaction*="EMtXr"]');
        if (jsact && !targets.find(t => t.el === jsact)) {
            targets.push({el: jsact, label: 'jsaction'});
        }

        // Nivel 3: role=button con texto de descripción
        const allBtns = document.querySelectorAll('[role="button"]');
        for (const b of allBtns) {
            const t = (b.textContent || '').trim().toLowerCase();
            if ((t.includes('descripci') || t.includes('description')) && !targets.find(x => x.el === b)) {
                targets.push({el: b, label: 'text-match'});
            }
        }

        if (targets.length === 0) return 'NOT_FOUND';

        const results = [];
        for (const {el, label} of targets) {
            try {
                el.scrollIntoView({block: 'center', behavior: 'instant'});
                // dispatchEvent con todos los eventos necesarios para Google
                ['mousedown','mouseup','click'].forEach(evt => {
                    el.dispatchEvent(new MouseEvent(evt, {bubbles: true, cancelable: true, view: window}));
                });
                results.push('OK:' + label);
            } catch(e) {
                results.push('ERR:' + label + ':' + e.message);
            }
        }
        return results.join('|');
    """

    def _intentar_en_contexto(ctx_label):
        # Verificar si el botón existe
        resultado_find = _js(driver, js_find)
        print(f"   {ctx_label} → find: '{resultado_find}'")
        if resultado_find == 'NOT_FOUND':
            return False, 'NOT_FOUND'

        # Intentar click vía JS (dispatchEvent en todos los niveles)
        resultado_js = _js(driver, js_click_all)
        print(f"   {ctx_label} → js_click: '{resultado_js}'")

        # Intentar adicionalmente click nativo de Selenium en el elemento
        try:
            # Buscar primero el role=button dentro del wrapper
            selectors = [
                '[jsname="G7vtgf"] [role="button"]',
                '[jsaction*="EMtXr"] [role="button"]',
                '[jsaction*="EMtXr"]',
                '[jsname="G7vtgf"]',
            ]
            for sel in selectors:
                elements = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in elements:
                    txt = (el.text or '').lower()
                    if sel in ('[jsname="G7vtgf"] [role="button"]', '[jsaction*="EMtXr"] [role="button"]') or                        'descripci' in txt or 'description' in txt or el.get_attribute('jsname') == 'G7vtgf':
                        try:
                            # JS .click() directo — evita el error "has no size and location"
                            driver.execute_script("arguments[0].click();", el)
                            print(f"   {ctx_label} → js.click() OK en '{sel}'")
                            return True, 'js_click:' + sel
                        except Exception as e_nat:
                            print(f"   {ctx_label} → js.click() ERR en '{sel}': {e_nat}")
        except Exception as e:
            print(f"   {ctx_label} → selenium search ERR: {e}")

        # Si al menos el JS click no dio NOT_FOUND, considerarlo exitoso
        if resultado_js and 'OK:' in resultado_js:
            return True, resultado_js
        return False, resultado_js

    # ── Intentar en documento principal ──
    driver.switch_to.default_content()
    ok, info = _intentar_en_contexto("Doc principal")
    if ok:
        print(f"   ✅ Descripción expandida ({info})")
        time.sleep(2.5)
        driver.switch_to.default_content()
        return True

    # ── Intentar en cada iframe ──
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    print(f"   Probando {len(frames)} iframe(s)...")
    for i, frame in enumerate(frames):
        try:
            driver.switch_to.frame(frame)
            ok, info = _intentar_en_contexto(f"iframe[{i}]")
            if ok:
                print(f"   ✅ Descripción expandida en iframe[{i}] ({info})")
                time.sleep(2.5)
                driver.switch_to.default_content()
                return True
            driver.switch_to.default_content()
        except Exception as e:
            print(f"   iframe[{i}] error accediendo: {e}")
            driver.switch_to.default_content()

    driver.switch_to.default_content()
    print("   ⚠️ Botón no encontrado en ningún contexto (descripción puede ya estar completa)")
    return False


def _extraer_descripcion(driver):
    """
    Extrae el texto de la descripción del panel derecho.
    Busca el div con mayor score (longitud + keywords) en toda la página.
    No depende de #Sva75c ya que Google cambia su estructura frecuentemente.
    """
    print("   🔍 [4/4] Extrayendo texto de descripción...")

    js_extraer = """
        const KEYWORDS_DESC = ['responsabilidades','requisitos','experiencia','conocimientos',
                                'habilidades','ofrecemos','funciones','buscamos','perfil',
                                'responsibilities','requirements','experience','skills',
                                'we offer','qualifications','about','role','position'];

        function scoreDiv(d) {
            const t = (d.innerText || '').trim();
            if (t.length < 150 || t.length > 50000) return {score: 0, texto: ''};
            if (d.children.length > 30) return {score: 0, texto: ''};
            const tl = t.toLowerCase();
            const bonus = KEYWORDS_DESC.filter(k => tl.includes(k)).length * 200;
            const uiElems = d.querySelectorAll('button,input,select,nav,header').length;
            const score = t.length + bonus - (uiElems * 100);
            return {score, texto: t};
        }

        let mejor = {score: 0, texto: '', fuente: 'none'};

        // 1. Selectores conocidos del panel de Google Jobs
        const SELECTORES_PANEL = [
            '#Sva75c', '[data-async-context]', '.cv0dee', '.HBvzbc',
            '.pE8vnd', '.YgLbBe', '[jsname="bN97Pc"]', '.NgUYpe',
            '.job-description', '[class*="description"]', '[class*="desc"]'
        ];
        for (const sel of SELECTORES_PANEL) {
            try {
                const el = document.querySelector(sel);
                if (!el) continue;
                const r = scoreDiv(el);
                if (r.score > mejor.score) mejor = {...r, fuente: sel};
            } catch(e) {}
        }

        // 2. Si no hay nada con buen score, barrer todos los divs
        if (mejor.score < 500) {
            const divs = document.querySelectorAll('div');
            for (const d of divs) {
                const r = scoreDiv(d);
                if (r.score > mejor.score) mejor = {...r, fuente: 'div-sweep'};
            }
        }

        const ok = mejor.texto.length > 150;
        return JSON.stringify({ok, fuente: mejor.fuente, score: mejor.score, texto: mejor.texto});
    """

    def _intentar(ctx_label):
        try:
            raw = driver.execute_script(js_extraer)
            data = json.loads(raw)
            print(f"   ↳ {ctx_label}: fuente={data.get('fuente')}, score={data.get('score',0)}, len={len(data.get('texto',''))}")
            return data
        except Exception as e:
            print(f"   ↳ {ctx_label} error: {e}")
            return {"ok": False, "fuente": "error", "texto": ""}

    driver.switch_to.default_content()
    data = _intentar("Doc principal")

    if not data.get("ok") or len(data.get("texto", "")) < 150:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        for i, frame in enumerate(frames):
            try:
                driver.switch_to.frame(frame)
                data2 = _intentar(f"iframe[{i}]")
                if data2.get("ok") and len(data2.get("texto", "")) > len(data.get("texto", "")):
                    data = data2
                driver.switch_to.default_content()
            except Exception as e:
                print(f"   ↳ iframe[{i}] acceso error: {e}")
                driver.switch_to.default_content()

    driver.switch_to.default_content()

    texto = data.get("texto", "")
    if len(texto) > 150:
        print(f"   ✅ Descripción OK: {len(texto)} chars | preview: {texto[:120].replace(chr(10),' ')}")
        return texto

    print(f"   ❌ Sin descripción ({len(texto)} chars)")
    return ""


def _leer_titulo_panel(driver):
    """Lee el h1/h2/h3 del panel de detalle — busca en doc principal y en iframes."""
    js = """
        const raiz = document.getElementById('Sva75c') || document.body;
        const els = raiz.querySelectorAll('h1, h2, h3');
        for (const el of els) {
            const t = (el.innerText || '').trim();
            if (t.length > 3 && t.length < 120) return t;
        }
        return '';
    """
    driver.switch_to.default_content()
    titulo = _js(driver, js) or ""
    if not titulo:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        for frame in frames:
            try:
                driver.switch_to.frame(frame)
                titulo = driver.execute_script(js) or ""
                driver.switch_to.default_content()
                if titulo:
                    break
            except Exception:
                driver.switch_to.default_content()
    return titulo


def scrape_google_jobs(query, ubicacion, progress_bar, status_text, urls_vistas, desde_idx=0, headless=False, max_resultados=3):
    print("\n" + "="*60)
    print("🚀 Iniciando scrape_google_jobs")
    print(f"   Query: {query} | Ubicación: {ubicacion} | Headless: {headless} | Max resultados: {max_resultados}")
    print("="*60)

    chrome_options = Options()
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    # Argumentos anti-deteccion comunes (aplican siempre)
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--lang=es-CL,es;q=0.9")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    )
    if headless:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-infobars")
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--disable-popup-blocking")
        chrome_options.add_argument("--ignore-certificate-errors")
        chrome_options.add_argument("--allow-running-insecure-content")

    print("\n\U0001f527 Iniciando ChromeDriver...")
    service = Service(ChromeDriverManager().install())
    driver  = webdriver.Chrome(service=service, options=chrome_options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": (
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});"
            "Object.defineProperty(navigator, 'languages', {get: () => ['es-CL','es','en-US','en']});"
            "window.chrome = { runtime: {} };"
        )}
    )
    ofertas = []

    try:
        # ── 1. Navegar ────────────────────────────────────────
        full_query = f"{query} {ubicacion}"
        url = f"https://www.google.com/search?q={quote_plus(full_query)}&ibp=htl;jobs"
        print(f"\n🌐 Navegando a: {url}")
        status_text.markdown(f"🌐 Navegando a Google Jobs: `{full_query}`...")
        progress_bar.progress(0.05)
        driver.get(url)
        time.sleep(4)

        # Detectar CAPTCHA / bloqueo de Google
        page_src_lower = driver.page_source.lower()
        if "recaptcha" in page_src_lower or "captcha-form" in page_src_lower or "trafico inusual" in page_src_lower or "unusual traffic" in page_src_lower:
            print("   CAPTCHA detectado. Esperando resolución manual...")
            status_text.markdown(
                "🔒 **CAPTCHA detectado** — Resuélvelo en la ventana de Chrome que se abrió y luego **presiona Enter en la terminal** para continuar."
            )
            input("\n>>> CAPTCHA detectado. Resuélvelo en Chrome y presiona Enter aquí para continuar...")
            # Esperar a que Google cargue los resultados después del CAPTCHA
            time.sleep(3)
            page_src_lower = driver.page_source.lower()
            if "recaptcha" in page_src_lower or "captcha-form" in page_src_lower:
                print("   CAPTCHA aún presente. Abortando.")
                status_text.markdown(":no_entry: **CAPTCHA no resuelto. Abortando.**")
                driver.quit()
                return [], desde_idx, 0
            print("   ✅ CAPTCHA superado. Continuando...")


        # ── 2. Localizar bloques ──────────────────────────────
        print("\n🔎 Buscando bloques de trabajo (div.EimVGf)...")
        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.EimVGf"))
            )
            bloques = driver.find_elements(By.CSS_SELECTOR, "div.EimVGf")
            print(f"   ✅ {len(bloques)} bloques encontrados.")
        except TimeoutException:
            print("   ❌ Timeout: no aparecieron bloques. Imprimiendo página para diagnóstico...")
            print(driver.page_source[:2000])
            status_text.markdown("❌ No se encontraron bloques de trabajo en Google.")
            return []

        total = len(bloques)
        status_text.markdown(f"📋 {total} trabajos detectados inicialmente. Cargando más...")

        # ── JS que encuentra el contenedor scrolleable real de Google Jobs ──
        JS_FIND_SCROLL_CONTAINER = """
            // Buscar el ancestro scrolleable del primer bloque EimVGf
            const bloques = document.querySelectorAll('div.EimVGf');
            if (!bloques.length) return null;

            let el = bloques[0];
            // Subir por el DOM hasta encontrar un contenedor con overflow scrolleable
            while (el && el !== document.body) {
                const style = window.getComputedStyle(el);
                const overflow = style.overflow + ' ' + style.overflowY;
                const scrollable = overflow.includes('auto') || overflow.includes('scroll');
                if (scrollable && el.scrollHeight > el.clientHeight + 50) {
                    return el;
                }
                el = el.parentElement;
            }
            // Fallback: buscar cualquier div con scroll que contenga los bloques
            const divs = document.querySelectorAll('div');
            let mejor = null;
            let mejorH = 0;
            for (const d of divs) {
                const s = window.getComputedStyle(d);
                const ov = s.overflow + ' ' + s.overflowY;
                if ((ov.includes('auto') || ov.includes('scroll')) &&
                    d.scrollHeight > d.clientHeight + 100 &&
                    d.scrollHeight > mejorH &&
                    d.querySelector('div.EimVGf')) {
                    mejor = d;
                    mejorH = d.scrollHeight;
                }
            }
            return mejor;
        """

        JS_SCROLL_CONTAINER = """
            const cont = arguments[0];
            cont.scrollTop += 900;
            return cont.scrollTop;
        """

        JS_SCROLL_FALLBACK = """
            // Hacer scroll al último bloque EimVGf visible
            const bloques = document.querySelectorAll('div.EimVGf');
            if (bloques.length) {
                const ultimo = bloques[bloques.length - 1];
                ultimo.scrollIntoView({behavior: 'instant', block: 'end'});
            }
            // Además hacer scroll de ventana hacia abajo
            window.scrollBy(0, 800);
            return document.querySelectorAll('div.EimVGf').length;
        """

        # Detectar el contenedor scrolleable real via JS
        lista_container = driver.execute_script(JS_FIND_SCROLL_CONTAINER)
        if lista_container:
            print(f"   ✅ Contenedor scrolleable detectado: tag={lista_container.tag_name}, "
                  f"scrollHeight={driver.execute_script('return arguments[0].scrollHeight', lista_container)}")
        else:
            print("   ⚠️ No se detectó contenedor scrolleable — se usará fallback de ventana.")

        # ── Scroll para cargar todos los bloques necesarios ──────────────
        intentos_scroll    = 0
        intentos_sin_cambio = 0
        MAX_SIN_CAMBIO     = 10   # intentos consecutivos sin nuevos bloques antes de rendirse
        max_intentos       = 60   # límite absoluto de scrolls

        while len(bloques) < (desde_idx + max_resultados) and intentos_scroll < max_intentos:
            antes = len(bloques)

            # Intentar scroll en el contenedor; si falla o no existe, usar fallback
            scrolled = False
            if lista_container:
                try:
                    new_top = driver.execute_script(JS_SCROLL_CONTAINER, lista_container)
                    print(f"   Scroll {intentos_scroll+1} (contenedor): scrollTop={new_top}")
                    scrolled = True
                except Exception as e:
                    print(f"   ⚠️ Scroll en contenedor falló ({e}), redetectando...")
                    lista_container = driver.execute_script(JS_FIND_SCROLL_CONTAINER)

            if not scrolled:
                n = driver.execute_script(JS_SCROLL_FALLBACK)
                print(f"   Scroll {intentos_scroll+1} (fallback ventana): bloques visibles={n}")

            # Esperar que Google cargue el siguiente lote
            time.sleep(2.5)

            bloques = driver.find_elements(By.CSS_SELECTOR, "div.EimVGf")
            print(f"   → {len(bloques)} bloques totales (meta: {desde_idx + max_resultados})")
            intentos_scroll += 1

            if len(bloques) == antes:
                intentos_sin_cambio += 1
                if intentos_sin_cambio >= MAX_SIN_CAMBIO:
                    print(f"   ⛔ {MAX_SIN_CAMBIO} scrolls consecutivos sin nuevos bloques. "
                          f"Máximo alcanzable: {len(bloques)}.")
                    break
            else:
                intentos_sin_cambio = 0  # hubo nuevos bloques, resetear contador

        total = len(bloques)
        print(f"   ✅ Total bloques tras scroll: {total}")
        status_text.markdown(f"📋 {total} trabajos detectados. Extrayendo descripciones...")

        # ── 3. Iterar TODOS los bloques ───────────────────────
        LOTE = max_resultados
        idx_inicio = desde_idx
        idx_fin = min(total, desde_idx + LOTE)
        bloques_a_procesar = idx_fin
        status_text.markdown(f"📋 {total} trabajos detectados. Extrayendo ofertas {idx_inicio+1}–{idx_fin}...")

        for idx in range(idx_inicio, bloques_a_procesar):
            pct = 0.05 + 0.90 * (idx / bloques_a_procesar)
            progress_bar.progress(pct)

            sep = "═" * 55
            print(f"\n{sep}")
            print(f"  🏢 TRABAJO {idx+1}/{bloques_a_procesar}")
            print(sep)

            # Siempre volver al doc principal antes de buscar bloques
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

            # Refrescar la referencia al bloque (el DOM puede haber cambiado)
            try:
                bloques = driver.find_elements(By.CSS_SELECTOR, "div.EimVGf")
                if idx >= len(bloques):
                    print(f"  ⚠️ Bloque #{idx+1} desapareció del DOM. Saltando.")
                    continue
                bloque = bloques[idx]
            except Exception as e:
                print(f"  ❌ Error obteniendo bloque: {e}")
                continue

            # ── PASO 1: Click en el bloque ──────────────────────────────────
            titulo_texto, click_ok = _click_bloque(driver, bloque, idx)
            print(f"  [1] Título: '{titulo_texto}' | Click: {'✅' if click_ok else '❌'}")
            if not click_ok:
                print(f"  ❌ Click fallido. Saltando.")
                continue

            status_text.markdown(f"🖱️ [{idx+1}/{bloques_a_procesar}] **{titulo_texto}** — esperando panel...")

            # ── PASO 2: Esperar que la URL cambie (identifica unívocamente el job abierto) ──
            # La URL con #vhid= es el identificador irrefutable del job en el panel.
            # Solo cuando la URL cambió sabemos que el panel ya empezó a cargar ESTE job.
            url_antes = driver.current_url
            url_este_job = None
            t0 = time.time()
            while time.time() - t0 < 8:
                time.sleep(0.4)
                url_nueva = driver.current_url
                if ("#vhid=" in url_nueva or "#sv=" in url_nueva) and url_nueva != url_antes:
                    url_este_job = url_nueva
                    break

            if url_este_job:
                print(f"  [2] ✅ URL cambió en {time.time()-t0:.1f}s → ...{url_este_job[-40:]}")
            else:
                url_este_job = driver.current_url
                print(f"  [2] ⚠️ URL no cambió. Usando: ...{url_este_job[-40:]}")

            # ── PASO 3: Esperar que el panel muestre el contenido del job con ESTA URL ──
            # Google a veces renderiza el panel con el job anterior mientras carga el nuevo.
            # Esperamos hasta que la URL del browser siga siendo url_este_job Y haya texto.
            time.sleep(1.2)  # tiempo mínimo de carga inicial

            # ── PASO 4: Expandir descripción ────────────────────────────────
            status_text.markdown(f"📖 [{idx+1}/{bloques_a_procesar}] **{titulo_texto}** — expandiendo descripción...")
            print(f"  [3] Buscando 'Mostrar descripción completa'...")
            expandido = _click_mostrar_descripcion(driver)
            if expandido:
                print(f"  [3] ✅ Expandida. Esperando 2.5s...")
                time.sleep(2.5)
            else:
                print(f"  [3] ⚠️ Botón no encontrado (puede ya estar completa)")
                time.sleep(0.5)

            # ── PASO 5: Extraer texto y verificar que la URL sigue siendo la correcta ──
            # Si Google cambió la URL mientras expandíamos (raro pero posible), descartamos.
            print(f"  [4] Extrayendo descripción...")
            texto_desc = _extraer_descripcion(driver)

            url_al_extraer = driver.current_url
            if url_al_extraer != url_este_job:
                print(f"  [4] ⚠️ La URL cambió durante la extracción — panel fue a otro job. Reintentando...")
                # El panel saltó a otro job — volver a clickear este bloque
                driver.switch_to.default_content()
                try:
                    bloques = driver.find_elements(By.CSS_SELECTOR, "div.EimVGf")
                    bloque = bloques[idx]
                    _click_bloque(driver, bloque, idx)
                    time.sleep(3.0)
                    _click_mostrar_descripcion(driver)
                    time.sleep(2.5)
                    texto_desc = _extraer_descripcion(driver)
                except Exception as e:
                    print(f"  [4] ❌ Reintento fallido: {e}")

            # ── Resultado ────────────────────────────────────────────────────
            driver.switch_to.default_content()
            current_url = driver.current_url
            tiene_desc = bool(texto_desc)

            print(f"  {'─'*50}")
            print(f"  📊 RESULTADO {idx+1}/{bloques_a_procesar}:")
            print(f"     Título:      {titulo_texto}")
            print(f"     Descripción: {'✅ ' + str(len(texto_desc)) + ' chars' if tiene_desc else '❌ vacía'}")
            print(f"     URL:         ...{current_url[-60:]}")

            if current_url not in urls_vistas:
                ofertas.append({
                    "nombre":  titulo_texto,
                    "empresa": "",
                    "desc":    texto_desc or f"[Sin descripción — {titulo_texto}]",
                    "url":     current_url,
                })
                urls_vistas.add(current_url)
                print(f"  ✅ Guardada como oferta #{len(ofertas)}")
            else:
                print(f"  ⏭️ URL duplicada — omitida")

            time.sleep(0.8)

    except Exception as e:
        print(f"\n💥 Error inesperado: {e}")
        import traceback; traceback.print_exc()

    finally:
        print("\n🔒 Cerrando navegador...")
        time.sleep(2)
        driver.quit()
        print("✅ Navegador cerrado.")

    progress_bar.progress(1.0)
    status_text.markdown(f"🎉 Extracción completada — **{len(ofertas)} oferta(s)**.")
    print(f"\n{'='*60}")
    print(f"✅ scrape_google_jobs finalizado. Total: {len(ofertas)} ofertas.")
    print("="*60 + "\n")
    siguiente_idx = desde_idx + len(ofertas)
    total_disponibles = total if 'total' in locals() else siguiente_idx
    return ofertas, siguiente_idx, total_disponibles


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
            _slider_autosave("Páginas (~25 c/u)", 1, 10, "linkedin_paginas", "sl_li_pag", p)
    return p


# ─────────────────────────────────────────────
# 10. TABLA CON BOTÓN DE RE-ANÁLISIS POR FILA
# ─────────────────────────────────────────────
def mostrar_tabla_resultados(resultados: list, ofertas_brutas: list, perfil: dict):
    """
    Muestra la tabla de resultados con:
    - Columna 'Descripción' con el texto completo (expandible).
    - Botón '🔄 Re-analizar' por cada fila para recalcular el match
      con el perfil actual (útil si cambiaste pesos/skills en la sidebar).
    """
    if not resultados:
        return

    df = pd.DataFrame(resultados)
    df = df.drop_duplicates(subset=["URL"])
    df = df.sort_values("Puntaje", ascending=False).reset_index(drop=True)

    # Mapa url → oferta bruta (para re-analizar)
    raw_por_url = {o.get("url", "#"): o for o in ofertas_brutas}

    # Inicializar override de puntajes en session_state
    if "puntajes_override" not in st.session_state:
        st.session_state.puntajes_override = {}

    st.subheader(f"📊 Resultados de Match — {len(df)} ofertas únicas")

    # Métricas resumen
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🥇 Mejor Puntaje", f"{df['Puntaje'].iloc[0]} pts")
    m2.metric("📈 Promedio",       f"{round(df['Puntaje'].mean(), 1)} pts")
    m3.metric("💼 En rango salarial", int(df["Sueldo"].str.contains("✅").sum()))
    m4.metric("🏷️ Cargo match",       int(df["Nombre"].str.contains("✅").sum()))

    filtro = st.text_input("🔍 Filtrar por palabra clave:", key="filtro_tabla")

    # ── Tabla principal con dataframe de Streamlit ───────────
    # Incluye columna Descripcion como texto largo
    cols_tabla = ["Puntaje", "Nombre", "Empresa", "Sueldo", "Skills", "Experiencia", "Beneficios", "URL", "Descripcion"]
    df_view = df[cols_tabla].copy()

    if filtro:
        mask = df_view.apply(lambda r: r.astype(str).str.contains(filtro, case=False).any(), axis=1)
        df_view = df_view[mask]

    st.dataframe(
        df_view,
        column_config={
            "URL": st.column_config.LinkColumn("🔗 Ver Oferta", display_text="Abrir →"),
            "Puntaje": st.column_config.NumberColumn(format="%d pts"),
            "Descripcion": st.column_config.TextColumn(
                "📄 Descripción completa",
                width="large",
                help="Texto completo extraído de la oferta",
            ),
        },
        width="stretch",
        hide_index=False,
        height=420,
    )

    # ── Sección de re-análisis individual ────────────────────
    st.markdown("---")
    st.subheader("🔄 Re-analizar oferta individual")
    st.caption(
        "Haz click en **Re-analizar** en cualquier oferta para recalcular su puntaje "
        "con el perfil actual de la sidebar (útil si cambiaste pesos o skills)."
    )

    for i, row in df.iterrows():
        url      = row["URL"]
        nombre   = row["Nombre"].replace("✅ ", "")
        empresa  = row["Empresa"] or ""
        puntaje  = st.session_state.puntajes_override.get(url, row["Puntaje"])

        # Color del badge de puntaje
        color = "#2ecc71" if puntaje >= 150 else "#e67e22" if puntaje >= 80 else "#e74c3c"

        with st.expander(
            f"{'✅' if puntaje >= 150 else '🟡' if puntaje >= 80 else '🔴'}  "
            f"**{nombre}** {'· ' + empresa if empresa else ''}  —  "
            f"<span style='color:{color};font-weight:700'>{puntaje} pts</span>",
            expanded=False
        ):
            col_info, col_btn = st.columns([3, 1])

            with col_info:
                # Mostrar desglose actual
                st.markdown(f"**Sueldo:** {row['Sueldo']}")
                st.markdown(f"**Skills:** {row['Skills']}")
                st.markdown(f"**Experiencia:** {row['Experiencia']}")
                st.markdown(f"**Beneficios:** {row['Beneficios']}")

                # Descripción completa con scroll
                if row.get("Descripcion") and str(row["Descripcion"]).strip():
                    with st.container():
                        st.markdown("**📄 Descripción completa:**")
                        st.text_area(
                            label="Descripción",
                            value=str(row["Descripcion"]),
                            height=220,
                            disabled=True,
                            key=f"desc_area_{url}_{i}",
                            label_visibility="collapsed",
                        )

                st.markdown(f"🔗 [Ver oferta original]({url})")

            with col_btn:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("🔄 Re-analizar", key=f"reanalizar_{url}_{i}", use_container_width=True):
                    oferta_raw = raw_por_url.get(url)
                    if oferta_raw:
                        nuevo_match = calcular_match(oferta_raw, perfil)
                        st.session_state.puntajes_override[url] = nuevo_match["Puntaje"]

                        # Actualizar también en res_final para que la tabla principal refleje el cambio
                        for r in st.session_state.res_final:
                            if r.get("URL") == url:
                                r.update(nuevo_match)
                                break

                        log.info(f"Re-análisis '{nombre}': {nuevo_match['Puntaje']} pts")
                        st.success(f"✅ Nuevo puntaje: **{nuevo_match['Puntaje']} pts**")
                        st.rerun()
                    else:
                        st.warning("⚠️ No se encontró la oferta original para re-analizar.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    st.set_page_config(layout="wide", page_title="DreamJ v4.1", page_icon="🎯")
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
    if "puntajes_override" not in st.session_state:
        st.session_state.puntajes_override = {}
    # Cargar historial local al inicio de sesion
    if "ofertas" not in st.session_state:
        hist = cargar_ofertas_historico()
        st.session_state.ofertas = hist
        if hist:
            log.info(f"Historial cargado: {len(hist)} ofertas desde JSON.")

    p = sidebar_config(st.session_state.perfil)
    st.session_state.perfil = p

    st.title("🎯 DreamJob v4.1")
    st.caption("Búsqueda, extracción completa y análisis de ofertas laborales.")

    # ── FUENTE DE DATOS ──
    tab_li, tab_google, tab_dummy = st.tabs(["🔗 LinkedIn Jobs", "🔍 Google Jobs", "🎲 Datos Dummy"])

    with tab_li:
        cargos_activos = p.get("cargos", [])
        query_default  = " OR ".join(cargos_activos[:3]) if cargos_activos else "Developer"
        query_li = st.text_input("Query (LinkedIn):", value=query_default, key="li_query")
        st.caption(
            f"📍 **{p.get('linkedin_ubicacion','Chile')}** · "
            f"📄 **{p.get('linkedin_paginas',3)} páginas**"
        )
        if st.button("🔍 Buscar en LinkedIn", type="primary", use_container_width=True):
            progress_bar = st.progress(0)
            status_text  = st.empty()
            urls_vistas  = cargar_urls_existentes()
            ofertas_nuevas = scrape_linkedin(
                query_li, p.get("linkedin_ubicacion", "Chile"),
                p.get("linkedin_paginas", 3), progress_bar, status_text, urls_vistas
            )
            if ofertas_nuevas:
                existentes = st.session_state.get("ofertas", [])
                urls_existentes = {o.get("url") for o in existentes}
                nuevas_unicas = [o for o in ofertas_nuevas if o.get("url") not in urls_existentes]
                st.session_state.ofertas = existentes + nuevas_unicas
                st.session_state.res_final = None
                st.toast(f"✨ {len(nuevas_unicas)} nuevas ofertas de LinkedIn (total: {len(st.session_state.ofertas)})", icon="🔥")
            else:
                st.warning("⚠️ No se encontraron ofertas nuevas en LinkedIn.")

    with tab_google:
        query_default  = " OR ".join(p.get("cargos", [])[:3]) if p.get("cargos") else "Developer"
        query_g = st.text_input("Palabras clave (Google):", value=query_default, key="g_query")
        st.info("💡 Abre Chrome, extrae la descripción completa de CADA oferta encontrada.")

        col_cfg1, col_cfg2 = st.columns([1, 1])
        max_resultados_g = col_cfg1.number_input(
            "🔢 Resultados a buscar", min_value=1, max_value=50, value=5, step=1, key="g_max_resultados"
        )
        headless_g = col_cfg2.checkbox("🕶️ Modo headless (sin ventana)", value=False, key="g_headless")

        if st.button("🔍 Buscar en Google Jobs", type="primary", use_container_width=True):
            progress_bar = st.progress(0)
            status_text  = st.empty()
            urls_vistas  = cargar_urls_existentes()
            result = scrape_google_jobs(
                query_g, p.get("linkedin_ubicacion", "Chile"),
                progress_bar, status_text, urls_vistas,
                desde_idx=0,
                headless=headless_g,
                max_resultados=max_resultados_g,
            )
            ofertas_g, siguiente_idx, total_g = result
            if ofertas_g:
                # Acumular a las existentes (evitar duplicados por URL)
                existentes = st.session_state.get("ofertas", [])
                urls_existentes = {o.get("url") for o in existentes}
                nuevas_unicas = [o for o in ofertas_g if o.get("url") not in urls_existentes]
                st.session_state.ofertas = existentes + nuevas_unicas
                st.session_state.google_siguiente_idx = siguiente_idx
                st.session_state.google_total = total_g
                st.session_state.google_query = query_g
                st.session_state.res_final = None
                st.toast(f"✅ {len(nuevas_unicas)} nuevas ofertas agregadas (total: {len(st.session_state.ofertas)})", icon="🌍")
            else:
                st.warning("No se encontraron resultados nuevos en Google.")

    with tab_dummy:
        n_dummy = st.number_input("Cantidad de ofertas dummy", 5, 100, 20, key="ndummy")
        if st.button("🎲 Generar", use_container_width=True):
            st.session_state.ofertas = generar_dummy(n_dummy)
            st.session_state.res_final = None
            st.success(f"✅ {n_dummy} ofertas dummy generadas.")

    # ── ANALIZAR ──
    st.divider()
    ofertas_brutas = st.session_state.get("ofertas", [])

    if ofertas_brutas:
        df_temp = pd.DataFrame(ofertas_brutas).drop_duplicates(subset=["url"])
        ofertas_cargadas = df_temp.to_dict("records")
    else:
        ofertas_cargadas = []

    n_cargadas = len(ofertas_cargadas)
    col_info, col_btn, col_trash = st.columns([3, 1, 1])
    col_info.caption(
        f"📦 **{n_cargadas} ofertas únicas** listas para analizar (historial persistente)."
        if n_cargadas else "Sin ofertas. Busca en LinkedIn, Google o genera Dummies."
    )

    if col_trash.button("🗑️ Borrar historial", use_container_width=True, disabled=not n_cargadas):
        borrar_historial()
        st.session_state.ofertas = []
        st.session_state.res_final = None
        st.session_state.puntajes_override = {}
        st.toast("Historial borrado.", icon="🗑️")
        st.rerun()

    if col_btn.button("🚀 Analizar", type="primary", use_container_width=True, disabled=not n_cargadas):
        with st.spinner("Calculando match..."):
            resultados = [calcular_match(o, p) for o in ofertas_cargadas]
            st.session_state.res_final = resultados
            st.session_state.puntajes_override = {}  # limpiar overrides al re-analizar todo
            json_path = guardar_ofertas_json(ofertas_cargadas, resultados)
            st.session_state.ofertas_json_path = json_path
            log.info(f"Análisis completado: {len(resultados)} resultados.")

    # ── RESULTADOS ──
    if st.session_state.get("res_final"):
        st.markdown("---")
        mostrar_tabla_resultados(
            st.session_state.res_final,
            ofertas_cargadas,
            p
        )

        if st.session_state.get("ofertas_json_path") and os.path.exists(OFERTAS_FILE):
            with open(OFERTAS_FILE, "rb") as f:
                st.download_button(
                    label="⬇️ Descargar Historial Completo (JSON)",
                    data=f,
                    file_name=f"dreamjob_export_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                    mime="application/json",
                    use_container_width=True
                )

        st.divider()
        mostrar_analisis_industria(ofertas_cargadas)

        with st.expander("📋 Log del Sistema (últimas 150 líneas)"):
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, encoding="utf-8") as f:
                    lines = f.readlines()
                st.code("".join(lines[-150:]), language="text")


if __name__ == "__main__":
    main()