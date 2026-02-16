import streamlit as st
import pandas as pd
import json
import os
import re
import io
import logging
from datetime import datetime
from jobspy import scrape_jobs

# --- 1. CONFIGURACIÓN DE AUDITORÍA (LOGGING) ---
log_filename = "auditoria_busqueda.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

def registrar_log(mensaje):
    logging.info(mensaje)
    if 'log_visual' not in st.session_state:
        st.session_state.log_visual = ""
    st.session_state.log_visual += f"{datetime.now().strftime('%H:%M:%S')} - {mensaje}\n"

# --- 2. GESTIÓN DE DATOS (JSON) ---
def cargar_perfil():
    if os.path.exists('perfil_usuario.json'):
        with open('perfil_usuario.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def guardar_perfil(perfil):
    with open('perfil_usuario.json', 'w', encoding='utf-8') as f:
        json.dump(perfil, f, indent=2, ensure_ascii=False)

# Biblioteca de detección (puedes expandirla)
TECH_LIBRARY = [
    "python", "javascript", "react", "nextjs", "nodejs", "html", "css", "sap", "erp",
    "sql", "nosql", "mongodb", "gcp", "aws", "azure", "docker", "kubernetes", "git",
    "power bi", "tableau", "excel", "java", "c#", "net core", "selenium", "scrum"
]

# --- 3. MOTOR DE ANÁLISIS DETALLADO ---
def analizar_oferta(row, perfil):
    texto_analisis = f"{row['title']} {row['description']}".lower()
    pref = perfil['preferencias_busqueda']
    mis_skills = [s.lower() for s in perfil['skills_tecnicas']]
    
    registrar_log(f"Procesando: {row['title']} | Empresa: {row['company']}")
    
    # Análisis de Skills
    encontradas = []
    score = 0
    for tech in TECH_LIBRARY:
        if re.search(rf'\b{re.escape(tech)}\b', texto_analisis):
            if tech in mis_skills:
                encontradas.append(f"✅ {tech.upper()}")
                score += 25
            else:
                encontradas.append(tech.upper())
    
    # Análisis de Experiencia
    exp_info = "No detectada"
    match_exp = re.search(r'(\d+)\s*(?:años?|years?|año)', texto_analisis)
    if match_exp:
        anios = int(match_exp.group(1))
        exp_info = f"{anios} años"
        if anios <= pref['exp_max']:
            score += 20
            registrar_log(f"   [OK] Experiencia: {anios} años cumple con max {pref['exp_max']}")
        else:
            score -= 40
            registrar_log(f"   [X] Experiencia: {anios} años excede {pref['exp_max']}")

    # Análisis de Renta (Búsqueda de montos)
    renta_status = "No informada"
    montos = re.findall(r'\$?\s?(\d+(?:\.\d+)+)', texto_analisis)
    if montos:
        for m in montos:
            valor = int(m.replace('.', ''))
            if pref['renta_min'] <= valor <= pref['renta_max']:
                score += 50
                renta_status = "✅ En Rango"
                break
            else:
                renta_status = "❌ Fuera de Rango"

    registrar_log(f"   Resultados: Tech({len(encontradas)}) | Renta: {renta_status} | Score Final: {score}")
    return score, ", ".join(encontradas), exp_info, renta_status

# --- 4. INTERFAZ DE USUARIO (STREAMLIT) ---
st.set_page_config(layout="wide", page_title="DreamJob Auditor Pro")
perfil = cargar_perfil()

if not perfil:
    st.error("Archivo perfil_usuario.json no encontrado.")
    st.stop()

# BARRA LATERAL: GESTIÓN TOTAL
with st.sidebar:
    st.title("⚙️ Panel de Control")
    
    # SECCIÓN CARGOS
    st.subheader("🎯 Cargos Objetivo")
    nuevo_cargo = st.text_input("Agregar nuevo cargo:")
    if st.button("➕ Añadir Cargo") and nuevo_cargo:
        perfil['preferencias_busqueda']['cargos_objetivo'].append(nuevo_cargo)
        guardar_perfil(perfil)
        st.rerun()
    
    cargo_borrar = st.selectbox("Eliminar cargo:", ["Seleccionar..."] + perfil['preferencias_busqueda']['cargos_objetivo'])
    if cargo_borrar != "Seleccionar..." and st.button("🗑️ Quitar Cargo"):
        perfil['preferencias_busqueda']['cargos_objetivo'].remove(cargo_borrar)
        guardar_perfil(perfil)
        st.rerun()

    st.divider()

    # SECCIÓN SKILLS
    st.subheader("💻 Mis Skills")
    nueva_skill = st.text_input("Agregar skill:")
    if st.button("➕ Añadir Skill") and nueva_skill:
        perfil['skills_tecnicas'].append(nueva_skill.lower())
        guardar_perfil(perfil)
        st.rerun()
    
    skill_borrar = st.selectbox("Eliminar skill:", ["Seleccionar..."] + sorted(perfil['skills_tecnicas']))
    if skill_borrar != "Seleccionar..." and st.button("🗑️ Quitar Skill"):
        perfil['skills_tecnicas'].remove(skill_borrar)
        guardar_perfil(perfil)
        st.rerun()

    st.divider()

    # SECCIÓN RENTAS Y FILTROS
    st.subheader("💰 Rango Salarial y Exp")
    p = perfil['preferencias_busqueda']
    r_min = st.number_input("Renta Mínima ($)", value=p.get('renta_min', 0))
    r_max = st.number_input("Renta Máxima ($)", value=p.get('renta_max', 0))
    e_max = st.slider("Años Exp. Máxima", 0, 15, p.get('exp_max', 5))
    
    if st.button("💾 Guardar Preferencias"):
        perfil['preferencias_busqueda'].update({"renta_min": r_min, "renta_max": r_max, "exp_max": e_max})
        guardar_perfil(perfil)
        st.success("Preferencias actualizadas")

# ÁREA PRINCIPAL
st.title("🚀 Buscador con Auditoría de Datos")

if st.button("🔍 INICIAR BÚSQUEDA Y ANÁLISIS", type="primary"):
    st.session_state.log_visual = "" # Reiniciar log
    res_final = []
    cargos_a_buscar = perfil['preferencias_busqueda']['cargos_objetivo']
    
    registrar_log(f"Iniciando ciclo para {len(cargos_a_buscar)} cargos.")
    
    progreso = st.progress(0)
    for idx, c in enumerate(cargos_a_buscar):
        registrar_log(f"--- BUSCANDO EN PORTALES: {c} ---")
        try:
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed"],
                search_term=c,
                location=perfil['preferencias_busqueda']['ubicacion_default'],
                results_wanted=5
            )
            for _, r in jobs.iterrows():
                sc, tc, ex, rn = analizar_oferta(r, perfil)
                res_final.append({
                    "Puntos": sc, "Cargo": r['title'], "Empresa": r['company'],
                    "Tecnologías": tc, "Exp. Req": ex, "Renta": rn, "Link": r['job_url']
                })
        except Exception as e:
            registrar_log(f"Error en cargo {c}: {str(e)}")
        progreso.progress((idx + 1) / len(cargos_a_buscar))

    if res_final:
        df = pd.DataFrame(res_final).sort_values("Puntos", ascending=False)
        st.subheader("📋 Resultados")
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Descargas
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Descargar Tabla (CSV)", csv, "resultados.csv", "text/csv")
        with col_d2:
            with open(log_filename, "rb") as f:
                st.download_button("📄 Descargar Log Completo (.txt)", f, "auditoria.txt")

# VISOR DE LOGS (Siempre visible si hay contenido)
if 'log_visual' in st.session_state and st.session_state.log_visual:
    with st.expander("👁️ Ver Auditoría del Proceso (Detalle Técnico)"):
        st.code(st.session_state.log_visual)