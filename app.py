import streamlit as st
import pandas as pd
import json
import os
import re
from jobspy import scrape_jobs

# --- 1. DATOS MAESTROS (CV DANIEL GALLEGOS) ---
PERFIL_DANIEL = {
    "skills_tecnicas": [
        "Python", "JavaScript", "Java", "TypeScript", "PHP", "HTML5", "CSS3", "C", 
        "MySQL", "MongoDB", "PostgreSQL", "React", "React Native", "Next.js", 
        "Flask", "Django", "Spring", "Express.js", "Angular", "Git", "Docker", 
        "Kubernetes", "REST APIs", "Google Cloud", "AWS", "SCRUM", "Jira"
    ],
    "preferencias_busqueda": {
        "cargos_objetivo": ["Data Engineer", "Back End Developer", "Full-stack Software Developer"],
        "ubicacion_default": "Chile",
        "exp_max": 4
    }
}

# --- 2. GESTIÓN DE PERSISTENCIA ---
def cargar_perfil():
    if not os.path.exists('perfil_usuario.json'): return PERFIL_DANIEL
    try:
        with open('perfil_usuario.json', 'r', encoding='utf-8') as f: return json.load(f)
    except: return PERFIL_DANIEL

def cargar_resultados():
    if os.path.exists('resultados_busqueda.json'):
        try:
            with open('resultados_busqueda.json', 'r', encoding='utf-8') as f: return json.load(f)
        except: return []
    return []

def guardar_datos(perfil, resultados):
    with open('perfil_usuario.json', 'w', encoding='utf-8') as f: 
        json.dump(perfil, f, indent=2, ensure_ascii=False)
    with open('resultados_busqueda.json', 'w', encoding='utf-8') as f: 
        json.dump(resultados, f, indent=2, ensure_ascii=False)

# --- 3. CONFIGURACIÓN UI Y CSS REPARADO ---
st.set_page_config(layout="wide", page_title="DreamJob Auditor - Daniel Gallegos")

# CSS para corregir legibilidad y expansión de tags
st.markdown("""
    <style>
        /* Forzar que el multiselect se expanda hacia abajo */
        .stMultiSelect div[data-baseweb="select"] > div:first-child {
            max-height: none !important;
            overflow-y: visible !important;
        }
        
        /* Mejorar legibilidad en el Sidebar */
        section[data-testid="stSidebar"] {
            background-color: #1E1E1E; /* Fondo oscuro profesional */
            color: white;
        }
        
        /* Estilo para los tags seleccionados para que resalten */
        span[data-baseweb="tag"] {
            background-color: #007bff !important;
            color: white !important;
            border-radius: 4px !important;
        }
        
        /* Ajustar color de labels en el sidebar */
        section[data-testid="stSidebar"] .stMarkdown p {
            color: #E0E0E0;
            font-weight: 600;
        }
    </style>
""", unsafe_allow_html=True)

# Inicializar estados
if "perfil" not in st.session_state: st.session_state.perfil = cargar_perfil()
if "resultados" not in st.session_state: st.session_state.resultados = cargar_resultados()
if "v_cargos" not in st.session_state: st.session_state.v_cargos = 0
if "v_skills" not in st.session_state: st.session_state.v_skills = 0

# --- 4. CALLBACKS ---
def cb_agregar_cargo():
    n = st.session_state.in_cargo.strip()
    if n and n not in st.session_state.perfil['preferencias_busqueda']['cargos_objetivo']:
        st.session_state.perfil['preferencias_busqueda']['cargos_objetivo'].append(n)
        guardar_datos(st.session_state.perfil, st.session_state.resultados)
        st.session_state.v_cargos += 1
    st.session_state.in_cargo = ""

def cb_agregar_skill():
    n = st.session_state.in_skill.strip()
    if n and n.lower() not in [s.lower() for s in st.session_state.perfil['skills_tecnicas']]:
        st.session_state.perfil['skills_tecnicas'].append(n)
        guardar_datos(st.session_state.perfil, st.session_state.resultados)
        st.session_state.v_skills += 1
    st.session_state.in_skill = ""

# --- 5. MENÚ LATERAL ---
with st.sidebar:
    st.header("👤 Perfil Profesional")
    st.caption("Daniel Gallegos | Ingeniero Informático")
    
    # Cargos
    c_list = st.session_state.perfil['preferencias_busqueda']['cargos_objetivo']
    sel_c = st.multiselect("🎯 CARGOS ACTIVOS:", options=c_list, default=c_list, key=f"ms_c_{st.session_state.v_cargos}")
    if len(sel_c) != len(c_list):
        st.session_state.perfil['preferencias_busqueda']['cargos_objetivo'] = sel_c
        guardar_datos(st.session_state.perfil, st.session_state.resultados); st.rerun()
    st.text_input("Añadir cargo:", key="in_cargo", on_change=cb_agregar_cargo, placeholder="Ej: Backend Developer")

    st.divider()

    # Skills
    s_list = st.session_state.perfil['skills_tecnicas']
    sel_s = st.multiselect("💻 MIS SKILLS:", options=s_list, default=s_list, key=f"ms_s_{st.session_state.v_skills}")
    if len(sel_s) != len(s_list):
        st.session_state.perfil['skills_tecnicas'] = sel_s
        guardar_datos(st.session_state.perfil, st.session_state.resultados); st.rerun()
    st.text_input("Añadir skill:", key="in_skill", on_change=cb_agregar_skill, placeholder="Ej: GCP, FastAPI...")

    st.divider()
    exp_max = st.slider("Experiencia Máxima Requerida:", 0, 15, st.session_state.perfil['preferencias_busqueda']['exp_max'])
    if exp_max != st.session_state.perfil['preferencias_busqueda']['exp_max']:
        st.session_state.perfil['preferencias_busqueda']['exp_max'] = exp_max
        guardar_datos(st.session_state.perfil, st.session_state.resultados)

    if st.button("🗑️ Borrar Resultados Guardados", use_container_width=True):
        st.session_state.resultados = []
        guardar_datos(st.session_state.perfil, [])
        st.rerun()

# --- 6. MOTOR DE ANÁLISIS ---
def analizar_oferta(row, perfil):
    title = str(row.get('title', '')).lower()
    desc = str(row.get('description', '')).lower()
    full_text = f"{title} {desc}"
    
    # Detección de Seniority y Años
    es_senior = any(x in title for x in ["senior", "sr", "lead", "principal", "staff"])
    exp_match = re.search(r'(\d+)\s*(?:\+|a)?\s*(?:años?|years?)', full_text)
    anios_req = int(exp_match.group(1)) if exp_match else 0
    
    match_skills = [s.upper() for s in perfil['skills_tecnicas'] if s.lower() in full_text]
    score = len(match_skills) * 10
    
    alertas = []
    if es_senior:
        score -= 80
        alertas.append("🚫 SENIOR")
    if anios_req > perfil['preferencias_busqueda']['exp_max']:
        score -= 50
        alertas.append(f"⚠️ {anios_req} AÑOS")
    if "junior" in title or "jr" in title:
        score += 30
        alertas.append("⭐ JUNIOR")

    match_str = " | ".join(alertas + [f"✅ {s}" for s in match_skills])
    return max(0, score), match_str, f"{anios_req} años"

# --- 7. CUERPO PRINCIPAL ---
st.title("🚀 DreamJob Auditor Pro")

# Mostrar lo que ya tenemos guardado
if st.session_state.resultados:
    st.subheader(f"📂 Ofertas Guardadas ({len(st.session_state.resultados)})")
    df_mem = pd.DataFrame(st.session_state.resultados).sort_values("Score", ascending=False)
    st.dataframe(df_mem, width='stretch', hide_index=True, column_config={
        "Link": st.column_config.LinkColumn("Postular"),
        "Score": st.column_config.ProgressColumn("Match", min_value=0, max_value=150)
    })

if st.button("🔍 INICIAR BÚSQUEDA MASIVA", type="primary"):
    cargos = st.session_state.perfil['preferencias_busqueda']['cargos_objetivo']
    if not cargos:
        st.warning("Configura los cargos en el menú lateral.")
    else:
        urls_vistas = {res['Link'] for res in st.session_state.resultados}
        contenedor = st.empty()
        barra = st.progress(0)
        
        for idx, cargo in enumerate(cargos):
            st.write(f"Buscando: `{cargo}`...")
            try:
                jobs = scrape_jobs(
                    site_name=["linkedin", "indeed", "glassdoor", "google"],
                    search_term=cargo,
                    location="Chile",
                    results_wanted=15
                )
                if jobs is not None:
                    for _, r in jobs.iterrows():
                        link = r.get('job_url')
                        if link not in urls_vistas:
                            puntos, match, exp = analizar_oferta(r, st.session_state.perfil)
                            nueva = {
                                "Score": puntos, "Cargo": r.get('title'), "Empresa": r.get('company'),
                                "Análisis": match, "Exp": exp, "Link": link
                            }
                            st.session_state.resultados.append(nueva)
                            urls_vistas.add(link)
                            
                            df_v = pd.DataFrame(st.session_state.resultados).sort_values("Score", ascending=False)
                            contenedor.dataframe(df_v, width='stretch', hide_index=True, column_config={
                                "Link": st.column_config.LinkColumn("Postular"),
                                "Score": st.column_config.ProgressColumn("Match", min_value=0, max_value=150)
                            })
                            guardar_datos(st.session_state.perfil, st.session_state.resultados)
            except Exception as e: st.error(f"Error: {e}")
            barra.progress((idx + 1) / len(cargos))