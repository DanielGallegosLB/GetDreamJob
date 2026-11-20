# -*- coding: utf-8 -*-
"""
cv_match.py — Módulo independiente "CV Match" para DreamJob.

Qué hace:
  1. Lee tus CVs (.docx / .txt / .pdf) y extrae roles, años de experiencia y skills.
  2. Genera consultas de búsqueda a partir del CV (editables).
  3. Busca EMPLEOS reales en LinkedIn (API pública de invitados, sin login),
     con filtro de antigüedad, modalidad y nivel, y lee la descripción completa.
  4. Puntúa cada oferta (0-100) según su semejanza con tu CV.
  5. PUBLICACIONES: LinkedIn exige sesión para buscar posts, así que aquí se
     guardas tus búsquedas (CRUD), se abren en LinkedIn y se pega el texto de la
     página completa: el sistema separa las publicaciones solo y las puntúa.
  6. Seguimiento: estado (Nueva / Revisada / Postulada / Entrevista / ...) y notas,
     guardados en cv_match_data.json.

Se integra en app.py con:  import cv_match ; cv_match.render_tab()
No toca el motor ni los archivos JSON de la pestaña clásica.
"""
import hashlib
import io
import json
import logging
import os
import re
import time
import unicodedata
import zipfile
from datetime import date, datetime
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("dreamjob.cvmatch")

DATA_FILE = "cv_match_data.json"
CV_DIR = "cvs"

ESTADOS = ["🆕 Nueva", "👀 Revisada", "📨 Postulada", "🗣️ Entrevista", "🎁 Oferta", "❌ Descartada"]
ESTADO_NUEVA = ESTADOS[0]
ESTADO_DESCARTADA = ESTADOS[-1]

# ─────────────────────────────────────────────
# 1. CATÁLOGO DE SKILLS (nombre → alias en regex, sin tildes y en minúscula)
# ─────────────────────────────────────────────
CATALOGO = {
    # Lenguajes
    "Python": r"python",
    "JavaScript": r"javascript|java\s?script|ecmascript|\bjs\b",
    "TypeScript": r"typescript|\bts\b",
    "Java": r"java",
    "PHP": r"php",
    "C++": r"c\+\+",
    "C#": r"c#|c\s?sharp",
    ".NET": r"\.net(?:\s?core)?|asp\.net|dotnet",
    "Golang": r"golang|go\s?lang",
    "Rust": r"rust",
    "Ruby": r"ruby(?:\s+on\s+rails)?|rails",
    "Kotlin": r"kotlin",
    "Scala": r"scala",
    # Frameworks
    "Node.js": r"node\.?\s?js|nodejs",
    "Express": r"express(?:\.?js)?",
    "NestJS": r"nest\.?js",
    "React": r"react(?:\.?js)?",
    "React Native": r"react\s?native",
    "Next.js": r"next\.?\s?js",
    "Vue": r"vue(?:\.?js)?",
    "Angular": r"angular(?:js)?",
    "Django": r"django",
    "Flask": r"flask",
    "FastAPI": r"fast\s?api",
    "Spring": r"spring(?:\s?boot)?",
    "Laravel": r"laravel",
    "Tailwind": r"tailwind(?:\s?css)?",
    "CSS": r"css",
    "HTML": r"html5?",
    # Bases de datos
    "MongoDB": r"mongo\s?db|mongo",
    "PostgreSQL": r"postgres(?:ql)?|postgre|psql",
    "MySQL": r"mysql|mariadb",
    "SQL Server": r"sql\s?server|mssql|t-?sql",
    "SQL": r"sql",
    "Oracle": r"oracle(?:\s?db|\s?database)?|pl/?sql",
    "Redis": r"redis",
    "ElasticSearch": r"elastic\s?search|opensearch|\belk\b",
    "NoSQL": r"no-?sql",
    # Cloud / DevOps
    "Google Cloud": r"google\s?cloud|gcp|bigquery|cloud\s?functions",
    "AWS": r"aws|amazon\s?web\s?services",
    "Azure": r"azure",
    "Serverless": r"serverless|lambda|azure\s?functions",
    "Docker": r"docker",
    "Kubernetes": r"kubernetes|k8s",
    "Git": r"git(?:hub|lab)?",
    "CI/CD": r"ci\s?/\s?cd|cicd|jenkins|github\s?actions|gitlab\s?ci",
    "Linux": r"linux|red\s?hat|rhel|ubuntu",
    "NGINX": r"nginx",
    "VMware": r"vmware",
    "Terraform": r"terraform",
    "Kafka": r"kafka",
    "RabbitMQ": r"rabbit\s?mq",
    # Integración / datos
    "REST": r"rest(?:ful)?",
    "APIs": r"apis?|web\s?services?",
    "SOAP": r"soap|wsdl",
    "GraphQL": r"graphql",
    "ETL": r"etl|elt",
    "Integraciones": r"integracion(?:es)?|integration(?:s)?",
    "Pipelines de datos": r"pipelines?|data\s?pipelines?",
    "Web scraping": r"scraping|scraper|crawler|crawling",
    "Selenium": r"selenium",
    "Postman": r"postman",
    "Testing": r"testing|pruebas|\bqa\b|test\s?automation|pytest|jest|unit\s?tests?",
    "Microservicios": r"microservicios|microservices?",
    "Middleware / ESB": r"middleware|mulesoft|wso2|tibco|boomi|\besb\b|\bsoa\b",
    "Airflow": r"airflow",
    "Spark": r"(?:py)?spark",
    "Pandas": r"pandas",
    "Power BI": r"power\s?bi|tableau",
    "Data Warehouse": r"data\s?warehouse|datawarehouse|\bdwh\b|snowflake|redshift",
    # Seguridad / metodologías
    "OWASP": r"owasp",
    "Scrum": r"scrum",
    "Agile": r"agile|agil",
    "Jira": r"jira",
    "Kanban": r"kanban",
}
_REGEX = {k: re.compile(r"(?<![\w+#.])(?:%s)(?![\w+#])" % v) for k, v in CATALOGO.items()}


def _sin_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn")


def _norm(t: str) -> str:
    return _sin_acentos(t).lower()


def detectar_skills(texto: str, extras=None) -> list:
    """Devuelve las skills (del catálogo + extras literales) presentes en el texto."""
    n = _norm(texto)
    out = [k for k, rx in _REGEX.items() if rx.search(n)]
    for e in extras or []:
        e_n = _norm(e).strip()
        if e_n and re.search(r"(?<![\w+#.])%s(?![\w+#])" % re.escape(e_n), n):
            out.append(e)
    return out


# ─────────────────────────────────────────────
# 2. LECTURA Y ANÁLISIS DEL CV
# ─────────────────────────────────────────────
def docx_a_texto(data: bytes) -> str:
    """Extrae el texto de un .docx sin dependencias externas."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    lineas = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        partes = re.findall(r"<w:t[^>]*>(.*?)</w:t>|<w:tab/>|<w:br/>", p, flags=re.S)
        txt = "".join(partes)
        txt = (txt.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                  .replace("&quot;", '"').replace("&apos;", "'"))
        if txt.strip():
            lineas.append(txt.strip())
    return "\n".join(lineas)


def archivo_a_texto(nombre: str, data: bytes) -> str:
    ext = nombre.lower().rsplit(".", 1)[-1]
    if ext == "docx":
        return docx_a_texto(data)
    if ext == "pdf":
        try:
            from pypdf import PdfReader  # opcional
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages)
        except Exception as e:
            raise RuntimeError(f"Para leer PDF instala pypdf (pip install pypdf): {e}")
    return data.decode("utf-8", errors="ignore")


# Roles que se reconocen en el CV → (español, inglés) para las consultas
ROL_TRAD = {
    "backenddeveloper": ("Desarrollador Backend", "Backend Developer"),
    "desarrolladorbackend": ("Desarrollador Backend", "Backend Developer"),
    "frontenddeveloper": ("Desarrollador Frontend", "Frontend Developer"),
    "fullstackdeveloper": ("Desarrollador Full Stack", "Full Stack Developer"),
    "desarrolladorfullstack": ("Desarrollador Full Stack", "Full Stack Developer"),
    "integrationengineer": ("Ingeniero de Integración", "Integration Engineer"),
    "dataengineer": ("Ingeniero de Datos", "Data Engineer"),
    "dataanalyst": ("Analista de Datos", "Data Analyst"),
    "softwareengineer": ("Ingeniero de Software", "Software Engineer"),
    "softwaredeveloper": ("Desarrollador de Software", "Software Developer"),
    "devopsengineer": ("Ingeniero DevOps", "DevOps Engineer"),
    "cloudengineer": ("Ingeniero Cloud", "Cloud Engineer"),
    "qaengineer": ("Ingeniero QA", "QA Engineer"),
}
_ROL_RX = re.compile(
    r"\b(?:application\s+)?(?:integration|data|devops|software|cloud|qa)\s+(?:engineer|developer|analyst)"
    r"|\b(?:back\s?-?\s?end|front\s?-?\s?end|full\s?-?\s?stack)\s+(?:developer|engineer)"
    r"|\bdesarrollador(?:a)?\s+(?:back\s?-?\s?end|front\s?-?\s?end|full\s?-?\s?stack)",
    re.I,
)
_SECCIONES = [
    ("exp", r"(experience|experiencia|work experience|experiencia laboral)\b"),
    ("proy", r"(featured projects|projects|proyectos)\b"),
    ("stack", r"(tech stack|technical skills|skills|stack tecnico|habilidades|tecnologias)\b"),
    ("edu", r"(education|educacion|formacion|certificaciones)\b"),
    ("about", r"(about me|about|sobre mi|perfil|summary|resumen)\b"),
]


def _rol_key(r: str) -> str:
    return re.sub(r"[\s\-]+", "", _norm(r))


def analizar_cv(texto: str, nombre: str = "") -> dict:
    lineas = [l for l in texto.splitlines() if l.strip()]
    # roles (conserva el texto tal como aparece en el CV, sin duplicados)
    roles, vistos = [], set()
    for m in _ROL_RX.finditer(_sin_acentos(texto)):
        lit = re.sub(r"\s+", " ", m.group(0)).strip()
        lit = " ".join(w.capitalize() if w.islower() else w for w in lit.split())
        k = _rol_key(lit)
        if k not in vistos:
            vistos.add(k)
            roles.append(lit)
    # años de experiencia
    anios = 0
    for m in re.finditer(r"(\d{1,2})\s*\+?\s*(?:anos|years)", _norm(texto)):
        anios = max(anios, int(m.group(1)))
    # secciones (encabezados en MAYÚSCULAS)
    secciones, actual = {}, None
    for l in lineas:
        n = _norm(l).strip()
        if len(n) < 40 and l.strip() == l.strip().upper() and re.search(r"[A-Za-z]", l):
            hit = next((nom for nom, rx in _SECCIONES if re.match(rx, n)), None)
            if hit:
                actual = hit
                secciones.setdefault(hit, [])
                continue
        if actual:
            secciones[actual].append(l)
    skills = detectar_skills(texto)
    texto_exp = "\n".join(secciones.get("exp", []) + secciones.get("proy", []))
    skills_exp = detectar_skills(texto_exp)
    # extras: elementos del stack que no están en el catálogo
    extras = []
    for l in secciones.get("stack", []):
        l = l.split(":", 1)[1] if ":" in l else l
        l = re.sub(r"\([^)]*\)", "", l)
        for t in re.split(r"\s*(?:·|•|\||,|;|\s[–-]\s)\s*", l):
            t = t.strip(" .")
            if 1 < len(t) < 30 and not detectar_skills(t) and t not in extras:
                extras.append(t)
    return {"archivo": nombre, "roles": roles, "anios": anios, "skills": skills,
            "skills_exp": skills_exp, "extras": extras}


def combinar_cvs(perfiles: list) -> dict:
    def union(campo):
        out = []
        for p in perfiles:
            for x in p.get(campo, []):
                if x not in out:
                    out.append(x)
        return out
    return {"roles": union("roles"), "anios": max([p.get("anios", 0) for p in perfiles] or [0]),
            "skills": union("skills"), "skills_exp": union("skills_exp"), "extras": union("extras")}


def generar_queries(cv: dict) -> list:
    """Consultas de búsqueda (español + inglés) a partir de los roles y skills del CV."""
    qs, ya = [], set()
    roles = sorted(cv.get("roles", []), key=lambda r: len(r))  # los más cortos primero (genéricos)
    claves = [_rol_key(r) for r in roles]
    for r, k in zip(roles, claves):
        # "applicationintegrationengineer" ya está cubierto por "integrationengineer"
        if any(k != o and o in k for o in claves):
            continue
        es, en = ROL_TRAD.get(k, (r, r))
        for q in (es, en):
            if q.lower() not in ya:
                ya.add(q.lower())
                qs.append(q)
    base = "Backend" if any("back" in k for k in claves) else (qs[0] if qs else "Developer")
    for s in [x for x in ["Node.js", "Python", "PHP", "Java", "Golang", "React"] if x in cv.get("skills_exp", [])][:3]:
        q = f"{base} {s}"
        if q.lower() not in ya:
            ya.add(q.lower())
            qs.append(q)
    return qs or ["Developer"]


# ─────────────────────────────────────────────
# 3. PUNTAJE DE SEMEJANZA (0-100)
#    Título 40 · Skills 45 · Nivel/experiencia 15
# ─────────────────────────────────────────────
_GENERICO = r"developer|desarrollador|desarrolladora|engineer|ingeniero|ingeniera|programador|analista|analyst"
_GRUPOS_ROL = {
    "backend": r"back\s?-?end|backend|\bapi\b|servidor|server",
    "integracion": r"integra",
    "datos": r"\bdata\b|datos|\betl\b|\bbi\b",
    "fullstack": r"full\s?-?stack",
    "software": r"software",
}
_SENIOR = r"\bsenior\b|\bsr\b|\bsr\.|\blead\b|\bprincipal\b|\bstaff\b|\bhead\b|\bjefe\b|\bmanager\b|\barquitect|\barchitect|\bgerente\b|\bdirector\b|\blider\b|tech lead"
_JUNIOR = r"\bjunior\b|\bjr\b|\bjr\.|trainee|practicante|\bintern\b|internship|\bpractica\b"
_SEMI = r"semi\s?-?\s?senior|\bssr\b|\bsemi\b|\bmid\b|intermedio"


def _grupos_cv(cv: dict) -> set:
    claves = " ".join(_rol_key(r) for r in cv.get("roles", []))
    g = set()
    if "backend" in claves:
        g.add("backend")
    if "integration" in claves:
        g.add("integracion")
    if "data" in claves:
        g.add("datos")
    if "fullstack" in claves:
        g.add("fullstack")
    if "software" in claves:
        g.add("software")
    return g or {"software"}


def _pts_titulo(titulo_n: str, cv: dict, skills_cv: set) -> tuple:
    gcv = _grupos_cv(cv)
    espec = [g for g in gcv if re.search(_GRUPOS_ROL[g], titulo_n)]
    generico = bool(re.search(_GENERICO, titulo_n))
    skill_en_titulo = [s for s in detectar_skills(titulo_n) if s in skills_cv]
    solo_front = bool(re.search(r"front\s?-?end|frontend", titulo_n)) and "fullstack" not in espec
    if espec and generico:
        pts, razon = 40, f"rol afín ({', '.join(espec)})"
    elif espec:
        pts, razon = 30, f"rol afín ({', '.join(espec)})"
    elif generico:
        pts, razon = 18, "cargo técnico genérico"
    else:
        pts, razon = 0, "cargo sin relación clara"
    if skill_en_titulo and pts < 40:
        pts = min(40, pts + 8)
        razon += f" + skill en título ({', '.join(skill_en_titulo[:2])})"
    if solo_front:
        pts = min(pts, 8 if "fullstack" not in gcv else 14)
        razon = "orientado a frontend"
    return pts, razon


def _anios_requeridos(desc_n: str):
    vals = []
    for m in re.finditer(r"(\d{1,2})\s*\+?\s*(?:a\s*\d{1,2}\s*)?(?:anos|years)\s+(?:de\s+|of\s+)?(?:experiencia|experience)", desc_n):
        vals.append(int(m.group(1)))
    for m in re.finditer(r"experiencia\s+(?:minima\s+)?(?:de\s+)?(\d{1,2})\s*\+?\s*anos", desc_n):
        vals.append(int(m.group(1)))
    vals = [v for v in vals if 0 < v <= 12]
    return min(vals) if vals else None


def puntuar(titulo: str, desc: str, cv: dict, skills_sel=None, anios=None, extras=None) -> dict:
    """Puntúa una oferta (o publicación) contra el CV. Devuelve el desglose completo."""
    skills_cv = set(skills_sel if skills_sel is not None else cv.get("skills", []))
    exp = set(cv.get("skills_exp", []))
    anios = cv.get("anios", 0) if anios is None else anios
    titulo_n, desc_n = _norm(titulo), _norm(desc or "")
    texto = f"{titulo}\n{desc or ''}"

    pts_t, razon_t = _pts_titulo(titulo_n, cv, skills_cv)

    oferta_sk = detectar_skills(texto, extras)
    coinciden = [s for s in oferta_sk if s in skills_cv or s in (extras or [])]
    faltan = [s for s in oferta_sk if s not in coinciden]
    sin_desc = len(desc or "") < 200
    if oferta_sk:
        m = sum(2 if s in exp else 1 for s in coinciden)
        f = len(faltan)
        cobertura = min(1.0, 1.15 * m / (m + f + 1.5))
        pts_s = round(45 * cobertura, 1)
    else:
        pts_s = 0.0

    senior = bool(re.search(_SENIOR, titulo_n)) and not re.search(_SEMI, titulo_n)
    junior = bool(re.search(_JUNIOR, titulo_n))
    req = _anios_requeridos(desc_n)
    if req is not None:
        pts_n = 15 if req <= anios + 1 else (8 if req == anios + 2 else 0)
        razon_n = f"pide {req}+ años (tienes {anios})"
    elif re.search(_SEMI, titulo_n):
        pts_n, razon_n = 15, "nivel semi-senior"
    elif junior:
        pts_n, razon_n = 10, "nivel junior"
    else:
        pts_n, razon_n = 12, "nivel no especificado"
    penal = 0
    if senior:
        pts_n = min(pts_n, 3)
        razon_n = "cargo senior/lead"
        if anios < 5:
            penal = 10

    total = max(0, min(100, round(pts_t + pts_s + pts_n - penal)))
    return {
        "puntaje": total, "pts_titulo": pts_t, "pts_skills": pts_s, "pts_nivel": pts_n, "penal": penal,
        "razon_titulo": razon_t, "razon_nivel": razon_n,
        "coinciden": coinciden, "faltan": faltan, "sin_desc": sin_desc, "anios_req": req,
    }


# ─────────────────────────────────────────────
# 4. LINKEDIN (API pública de invitados, sin login)
# ─────────────────────────────────────────────
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
}
URL_BUSCAR = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
URL_DETALLE = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"

ANTIGUEDAD = {"Últimas 24 horas": 86400, "Últimos 3 días": 259200, "Última semana": 604800,
              "Últimas 2 semanas": 1209600, "Último mes": 2592000}
MODALIDAD = {"Presencial": "1", "Remoto": "2", "Híbrido": "3"}
NIVEL = {"Prácticas": "1", "Júnior": "2", "Asociado": "3", "Intermedio-Senior": "4", "Director": "5"}


def _get(url, params=None, intentos=3):
    """GET con reintentos. Devuelve (texto|None, status)."""
    status = None
    for i in range(intentos):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            status = r.status_code
            if r.status_code == 200:
                return r.text, 200
            if r.status_code in (429, 999):
                time.sleep(4 * (i + 1))
                continue
            if r.status_code in (400, 404):
                return None, r.status_code
            time.sleep(2)
        except requests.RequestException as e:
            log.warning(f"LinkedIn red: {e}")
            time.sleep(2)
    return None, status


def parsear_tarjetas(html: str) -> list:
    """Convierte el HTML de resultados de LinkedIn en una lista de ofertas."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.find_all("li"):
        card = li.find("div", class_=re.compile("base-card")) or li
        titulo_el = card.find("h3")
        if not titulo_el:
            continue
        titulo = titulo_el.get_text(" ", strip=True)
        link = card.find("a", class_=re.compile("base-card__full-link")) or card.find("a", href=True)
        href = link["href"].split("?")[0] if link and link.get("href") else ""
        m = re.search(r"jobPosting:(\d+)", card.get("data-entity-urn", "") or "") or re.search(r"-(\d{8,})$", href)
        if not m or not titulo:
            continue
        jid = m.group(1)
        emp_el = card.find("h4") or card.find("a", class_=re.compile("hidden-nested-link"))
        loc_el = card.find("span", class_=re.compile("job-search-card__location"))
        t_el = card.find("time")
        out.append({
            "id": jid, "tipo": "empleo", "titulo": titulo,
            "empresa": emp_el.get_text(" ", strip=True) if emp_el else "",
            "lugar": loc_el.get_text(" ", strip=True) if loc_el else "",
            "fecha": (t_el.get("datetime", "") if t_el else "")[:10],
            "url": f"https://www.linkedin.com/jobs/view/{jid}",
        })
    return out


def parsear_detalle(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    d = soup.find("div", class_=re.compile("show-more-less-html__markup")) or soup.find("section", class_=re.compile("description"))
    desc = d.get_text("\n", strip=True) if d else ""
    crit = {}
    for it in soup.find_all("li", class_=re.compile("description__job-criteria-item")):
        h, s = it.find("h3"), it.find("span")
        if h and s:
            crit[h.get_text(strip=True)] = s.get_text(strip=True)
    post = soup.find(class_=re.compile("num-applicants__caption"))
    return {"desc": desc, "criterios": crit, "postulantes": post.get_text(strip=True) if post else ""}


MAX_PAGINAS_TOPE = 40  # LinkedIn deja de entregar resultados pasado ~1000 ofertas


def parsear_keywords(texto: str) -> list:
    """'backend, "integration engineer"; APIs' -> ['backend', 'integration engineer', 'APIs'] (sin repetidos)."""
    out, ya = [], set()
    for k in re.split(r"[,;\n]+", texto or ""):
        k = k.strip().strip('"').strip()
        if not k or k.upper() in ("OR", "AND", "NOT") or k.lower() in ya:
            continue
        ya.add(k.lower())
        out.append(k)
    return out


def construir_query(keywords, modo: str = "OR") -> str:
    """UNA sola consulta con todas las palabras clave: ("integration engineer" OR backend OR python)."""
    ks = [f'"{k}"' if " " in k else k for k in keywords]
    if not ks:
        return ""
    return ks[0] if len(ks) == 1 else "(" + f" {modo} ".join(ks) + ")"


def _q(k: str) -> str:
    return f'"{k}"' if " " in k else k


def construir_query_bool(cualquiera=(), todas=(), excluir=()) -> str:
    """Consulta booleana para LinkedIn: ("a" OR b) AND c AND d NOT e NOT "f g".
    Necesita al menos una palabra positiva (OR o AND); un NOT solo no sirve en LinkedIn."""
    positivos = []
    ors = [_q(k) for k in cualquiera]
    if len(ors) == 1:
        positivos.append(ors[0])
    elif ors:
        positivos.append("(" + " OR ".join(ors) + ")")
    positivos += [_q(k) for k in todas]
    if not positivos:
        return ""
    return " AND ".join(positivos) + "".join(f" NOT {_q(k)}" for k in excluir)


def _huella(o: dict) -> str:
    """Misma oferta reposteada con otro ID: mismo título + empresa + lugar."""
    return "|".join(re.sub(r"\W+", " ", _norm(o.get(c, ""))).strip() for c in ("titulo", "empresa", "lugar"))


def buscar_empleos(queries, ubicacion, tpr_seg, paginas, modalidades, niveles, progreso=None,
                   max_resultados=None, dedup=True) -> tuple:
    """Busca cada consulta en LinkedIn.
    paginas=None -> recorre TODAS las páginas hasta que LinkedIn no devuelva más (tope MAX_PAGINAS_TOPE).
    Devuelve (ofertas_unicas, avisos, stats) con stats = {"paginas": n, "duplicadas": n}."""
    ofertas, ids, huellas, avisos = [], set(), set(), []
    stats = {"paginas": 0, "duplicadas": 0}
    tope = min(MAX_PAGINAS_TOPE, paginas or MAX_PAGINAS_TOPE)
    total_pasos = max(1, len(queries) * tope)
    paso = 0
    for q in queries:
        start = 0
        for pg in range(tope):
            paso += 1
            if progreso:
                txt_pg = f"{pg + 1}/{paginas}" if paginas else f"{pg + 1} (todas, hasta agotar)"
                progreso(f"🔍 «{q[:70]}» — página {txt_pg}", paso / total_pasos)
            params = {"keywords": q, "location": ubicacion, "start": start, "f_TPR": f"r{tpr_seg}", "sortBy": "DD"}
            if modalidades:
                params["f_WT"] = ",".join(MODALIDAD[m] for m in modalidades)
            if niveles:
                params["f_E"] = ",".join(NIVEL[n] for n in niveles)
            html, status = _get(URL_BUSCAR, params)
            if html is None:
                avisos.append(f"«{q[:60]}» p.{pg + 1}: LinkedIn respondió {status or 'sin conexión'}")
                break
            tarjetas = parsear_tarjetas(html)
            if not tarjetas:
                break
            stats["paginas"] += 1
            nuevas = 0
            for t in tarjetas:
                if t["id"] in ids:
                    continue
                ids.add(t["id"])
                nuevas += 1
                h = _huella(t)
                if dedup and h.strip("| ") and h in huellas:
                    stats["duplicadas"] += 1
                    continue
                huellas.add(h)
                t["query"] = q
                ofertas.append(t)
            start += len(tarjetas)
            if nuevas == 0:  # la página repitió lo ya visto: LinkedIn no tiene más
                break
            if max_resultados and len(ofertas) >= max_resultados:
                del ofertas[max_resultados:]
                return ofertas, avisos, stats
            time.sleep(1.2)
    return ofertas, avisos, stats


def leer_detalles(ofertas: list, limite: int, progreso=None) -> int:
    """Descarga la descripción completa de hasta `limite` ofertas que aún no la tienen."""
    pend = [o for o in ofertas if not o.get("desc")][:limite]
    for i, o in enumerate(pend, 1):
        if progreso:
            progreso(f"📄 Leyendo descripción {i}/{len(pend)}: {o.get('titulo', '')[:50]}", i / max(1, len(pend)))
        html, status = _get(URL_DETALLE.format(id=o["id"]), intentos=2)
        if html:
            det = parsear_detalle(html)
            o["desc"] = det["desc"]
            o["criterios"] = det["criterios"]
            o["postulantes"] = det["postulantes"]
        time.sleep(1.0)
    return len(pend)


# ─────────────────────────────────────────────
# 5. PERSISTENCIA Y SEGUIMIENTO
# ─────────────────────────────────────────────
BUSQUEDAS_POSTS_INICIALES = [
    {"id": "b1", "nombre": "Chile", "query": "buscamos (integracion OR APIs OR backend) chile NOT senior",
     "periodo": "Última semana", "activa": True},
    {"id": "b2", "nombre": "EE. UU. / Remoto", "query": 'hiring ("integration engineer" OR backend) (US OR latam) NOT senior',
     "periodo": "Última semana", "activa": True},
]


def _datos_vacios() -> dict:
    return {"cfg": {}, "items": {}, "eliminadas": [], "busquedas_posts": [dict(b) for b in BUSQUEDAS_POSTS_INICIALES]}


def cargar() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                d = json.load(f)
            d.setdefault("cfg", {})
            d.setdefault("items", {})
            d.setdefault("eliminadas", [])
            if "busquedas_posts" not in d:  # solo la primera vez; si las borras todas no vuelven
                d["busquedas_posts"] = [dict(b) for b in BUSQUEDAS_POSTS_INICIALES]
            return d
        except Exception as e:
            log.error(f"No se pudo leer {DATA_FILE}: {e}")
    return _datos_vacios()


def guardar(data: dict):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def integrar(data: dict, nuevos: list) -> int:
    """Agrega ofertas nuevas sin pisar el estado ni las notas de las que ya existen."""
    items, agregados = data["items"], 0
    hoy = datetime.now().isoformat(timespec="seconds")
    borradas = set(data.get("eliminadas", []))
    for o in nuevos:
        key = f"{o.get('tipo', 'empleo')}:{o['id']}"
        if key in borradas:  # la eliminaste: no vuelve a aparecer en nuevas búsquedas
            continue
        if key in items:
            ex = items[key]
            for c in ("titulo", "empresa", "lugar", "fecha", "desc", "criterios", "postulantes", "url", "perfil", "links", "oferta", "emails"):
                if o.get(c) and not ex.get(c):
                    ex[c] = o[c]
            if o.get("url") and ex.get("url") != o["url"] and not es_directo(ex.get("url", "")):
                auto = {ex.get("oferta", "")} | set(ex.get("links", []) or [])  # enlaces puestos por el sistema, no por ti
                if es_directo(o["url"]) or ex.get("url", "") in auto or "linkedin.com/search/results/content" in ex.get("url", ""):
                    ex["url"] = o["url"]
        else:
            o = dict(o)
            o.update({"estado": ESTADO_NUEVA, "notas": "", "encontrada": hoy, "fecha_estado": hoy})
            items[key] = o
            agregados += 1
    return agregados


def repuntuar(data: dict, cv: dict, skills_sel, anios, extras):
    for it in data["items"].values():
        r = puntuar(it.get("titulo", ""), it.get("desc", ""), cv, skills_sel, anios, extras)
        it["score"] = r


def dias_desde(fecha_iso: str):
    try:
        return (date.today() - datetime.strptime(fecha_iso[:10], "%Y-%m-%d").date()).days
    except Exception:
        return None


# ─────────────────────────────────────────────
# 6. PUBLICACIONES
# ─────────────────────────────────────────────
_HIRING = r"contratando|estamos buscando|buscamos|vacante|we are hiring|we're hiring|hiring|join our team|nos expandimos|oportunidad laboral|postula"
PERIODOS_POST = {"Últimas 24 horas": "past-24h", "Última semana": "past-week", "Último mes": "past-month"}


def url_busqueda_post(query: str, periodo: str = "Última semana") -> str:
    """Enlace a la búsqueda de PUBLICACIONES de LinkedIn con tu consulta tal cual (admite OR, NOT, comillas, paréntesis)."""
    per = PERIODOS_POST.get(periodo, "past-week")
    return ("https://www.linkedin.com/search/results/content/?keywords=" + quote_plus(query.strip())
            + f"&datePosted=%22{per}%22&sortBy=%22date_posted%22")


def revisar_query(q: str) -> list:
    """Avisos típicos que hacen que LinkedIn ignore los operadores."""
    av = []
    if q.count("(") != q.count(")"):
        av.append("paréntesis sin cerrar")
    if q.count('"') % 2:
        av.append("comillas sin cerrar")
    if re.search(r"\b(or|and|not)\b", q) and not re.search(r"\b(OR|AND|NOT)\b", q):
        av.append("los operadores OR / AND / NOT deben ir en MAYÚSCULAS")
    return av


def guardar_busquedas_posts(data: dict, filas: list) -> tuple:
    """Reemplaza las búsquedas guardadas por las filas editadas (crear/editar/borrar de una vez).
    Devuelve (guardadas, ignoradas_por_estar_vacias)."""
    out, ign, usados = [], 0, set()
    for f in filas:
        q = str(f.get("query") or f.get("Consulta") or "").strip()
        if not q or q == "nan":
            ign += 1
            continue
        bid = str(f.get("id") or "").strip()
        if not bid or bid in usados or bid == "nan":
            bid = hashlib.md5((q + str(len(out)) + str(time.time())).encode("utf-8")).hexdigest()[:8]
        usados.add(bid)
        act = f.get("activa", f.get("Activa"))
        per = f.get("periodo") or f.get("Periodo")
        nom = str(f.get("nombre") or f.get("Nombre") or "").strip()
        out.append({"id": bid, "nombre": nom if nom and nom != "nan" else q[:30],
                    "query": q, "periodo": per if per in PERIODOS_POST else "Última semana",
                    "activa": True if act is None or act != act else bool(act)})
    data["busquedas_posts"] = out
    return len(out), ign


def analizar_publicacion(texto: str) -> dict:
    n = _norm(texto)
    return {"contratando": bool(re.search(_HIRING, n)),
            "emails": sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", texto)))}


# ─────────────────────────────────────────────
# 7. FECHAS, FILTROS Y LIMPIEZA (lógica sin interfaz)
# ─────────────────────────────────────────────
GRUPOS_ESTADO = {
    "Activas": [e for e in ESTADOS if e != ESTADO_DESCARTADA],
    "Por revisar": [ESTADOS[0]],
    "Revisadas": [ESTADOS[1]],
    "Postuladas": [ESTADOS[2]],
    "En proceso": [ESTADOS[3], ESTADOS[4]],
    "Descartadas": [ESTADO_DESCARTADA],
    "Todas": list(ESTADOS),
}
FILTRO_DIAS = {"Hoy / ayer": 1, "3 días": 3, "1 semana": 7, "2 semanas": 14, "1 mes": 31, "Todas": None}
FILTRO_PUNTAJE = {"🔥 70+": 70, "👍 50+": 50, "Todas": 0}


def fecha_ef(it: dict):
    """(fecha_iso, aproximada). Si LinkedIn no dio fecha se usa el día en que se encontró (aprox.)."""
    f = (it.get("fecha") or "")[:10]
    if f:
        return f, False
    return (it.get("encontrada") or "")[:10], True


def hace_txt(dias) -> str:
    if dias is None:
        return "—"
    ico = "🟢" if dias <= 3 else ("🟡" if dias <= 14 else "🔴")
    if dias <= 0:
        t = "hoy"
    elif dias == 1:
        t = "ayer"
    elif dias < 14:
        t = f"hace {dias} días"
    elif dias < 60:
        t = f"hace {dias // 7} sem"
    else:
        t = f"hace {dias // 30} meses"
    return f"{ico} {t}"


def filas(data: dict) -> list:
    out = []
    for key, it in data["items"].items():
        tipo = it.get("tipo", "empleo")
        f, aprox = fecha_ef(it)
        dias = dias_desde(f)
        sc = it.get("score", {})
        out.append({
            "Sel": False, "_key": key, "Puntaje": sc.get("puntaje", 0),
            "Estado": it.get("estado", ESTADO_NUEVA), "Hace": hace_txt(dias),
            "Publicada": (f or "—") + (" ~" if aprox and f else ""),
            "Tipo": "💼 Empleo" if tipo == "empleo" else "💬 Post",
            "Título": (it.get("titulo") or "")[:140], "Empresa": it.get("empresa", ""), "Lugar": it.get("lugar", ""),
            "Coincide": ", ".join(sc.get("coinciden", [])[:8]), "Notas": it.get("notas", ""), "Link": it.get("url", ""), "Oferta": it.get("oferta", "") or next(iter(it.get("links", []) or []), ""), "Perfil": it.get("perfil", ""),
            "_dias": dias if dias is not None else 9999, "_fest": it.get("fecha_estado", ""), "_tipo": tipo,
        })
    return out


def filtrar_df(df, minimo=0, dias_max=None, tipo="Todo", grupo="Activas", texto="", orden="Mejor puntaje"):
    """Aplica los filtros rápidos de la pestaña Resultados."""
    if df.empty:
        return df
    m = (df["Puntaje"] >= minimo) & df["Estado"].isin(GRUPOS_ESTADO[grupo])
    if dias_max is not None:
        m &= df["_dias"] <= dias_max
    if tipo == "Empleos":
        m &= df["_tipo"] == "empleo"
    elif tipo == "Publicaciones":
        m &= df["_tipo"] == "publicacion"
    if texto.strip():
        t = _norm(texto.strip())
        base = (df["Título"] + " " + df["Empresa"] + " " + df["Lugar"] + " " + df["Coincide"]).map(_norm)
        m &= base.str.contains(re.escape(t), regex=True)
    df = df[m]
    if orden == "Más recientes":
        return df.sort_values(["_dias", "Puntaje"], ascending=[True, False])
    return df.sort_values(["Puntaje", "_dias"], ascending=[False, True])


def eliminar(data: dict, keys, recordar=True) -> int:
    """Borra ítems. Con recordar=True los empleos no vuelven a aparecer en próximas búsquedas."""
    n = 0
    for k in list(keys):
        if data["items"].pop(k, None) is not None:
            n += 1
            if recordar and k.startswith("empleo:") and k not in data["eliminadas"]:
                data["eliminadas"].append(k)
    return n


def marcar(data: dict, keys, estado: str) -> int:
    ahora = datetime.now().isoformat(timespec="seconds")
    n = 0
    for k in keys:
        it = data["items"].get(k)
        if it and it.get("estado") != estado:
            it["estado"], it["fecha_estado"] = estado, ahora
            n += 1
    return n


# Separador inteligente de publicaciones pegadas ------------------------------------------------
_PIE = re.compile(r"\b(?:me gusta|recomendar|comentar|compartir|republicar|reaccionar|enviar|like|comment|repost|send|share|react)\b")
_MARCA_FEED = re.compile(r"^\W*(?:feed post(?: number)?|publicacion(?: del| de)? feed(?: numero)?|publicacion(?: de)? feed|post del feed)\W*\d*\W*$")
_INSIGNIA = re.compile(r"[•·]\s*(?:1(?:er|st|o|º|°)|2(?:do|nd|o|º|°)|3(?:er|rd|o|º|°)\+?|tu|you|seguidor|siguiendo|following|follower)\b")
_RUIDO = {"seguir", "follow", "+ seguir", "+ follow", "conectar", "connect", "… más", "...más", "… mas", "...mas", "ver más", "ver mas",
          "see more", "mostrar más", "mostrar mas", "ver traducción", "ver traduccion", "traducir", "see translation",
          "promocionado", "promoted", "mostrar traducción", "mostrar traduccion", "más", "mas", "more", "…more", "… more"}
_UNID_DIAS = {"min": 0, "minuto": 0, "minutos": 0, "m": 0, "h": 0, "hora": 0, "horas": 0, "d": 1, "dia": 1, "dias": 1,
              "sem": 7, "semana": 7, "semanas": 7, "s": 7, "w": 7, "mes": 30, "meses": 30, "mo": 30,
              "a": 365, "ano": 365, "anos": 365, "y": 365, "yr": 365}
_TIEMPO = re.compile(r"^(?:hace\s+)?(\d{1,3})\s*(min|minutos?|horas?|dias?|semanas?|meses|mes|anos?|yr|mo|sem|[mhdsway])\b(?:\s+(?:editado|edited))?$")
_URL_POST = re.compile(r"https?://(?:[\w-]+\.)?linkedin\.com/(?:posts|feed/update|pulse)/[^\s)>\]]+")


def _es_pie(linea: str) -> int:
    """>0 si la línea es solo botones de LinkedIn (Me gusta / Comentar / Compartir / Enviar...): cuántos contiene."""
    n = _norm(linea)
    encontrados = _PIE.findall(n)
    if not encontrados:
        return 0
    resto = re.sub(r"[\W_\d]+", "", _PIE.sub("", n))
    return len(encontrados) if resto == "" else 0


def _tiene_bloque_pie(lineas, i):
    """Si en la línea i empieza un grupo de botones (>=2), devuelve el índice de su última línea."""
    j, total, ultimo, n = i, 0, i, len(lineas)
    while j < n:
        if not lineas[j].strip():
            j += 1
            continue
        c = _es_pie(lineas[j])
        if not c:
            break
        total += c
        ultimo = j
        j += 1
    return ultimo if total >= 2 else None


def _bloques_por_marcas(lineas):
    idx = [i for i, l in enumerate(lineas) if _MARCA_FEED.match(_norm(l.strip()))]
    if len(idx) < 1:
        return None
    cortes = idx + [len(lineas)]
    bl = [lineas[cortes[k] + 1:cortes[k + 1]] for k in range(len(idx))]
    previo = lineas[:idx[0]]
    if any(x.strip() for x in previo):
        bl.insert(0, previo)
    return bl


def _bloques_por_pie(lineas):
    bl, ini, i, n = [], 0, 0, len(lineas)
    while i < n:
        fin = _tiene_bloque_pie(lineas, i) if _es_pie(lineas[i]) else None
        if fin is not None:
            bl.append(lineas[ini:fin + 1])
            ini, i = fin + 1, fin + 1
        else:
            i += 1
    if ini < n:
        bl.append(lineas[ini:])
    return bl if len(bl) >= 1 else None


def _bloques_por_encabezado(lineas):
    starts = []
    for i, l in enumerate(lineas):
        if _INSIGNIA.search(_norm(l)):
            antes = re.split(r"[•·]", l)[0].strip()
            if antes:
                st_ = i
            else:
                st_ = i - 1
                while st_ > 0 and not lineas[st_].strip():
                    st_ -= 1
                if st_ > 0 and lineas[st_ - 1].strip() and lineas[st_ - 1].strip() == lineas[st_].strip():
                    st_ -= 1  # el nombre viene duplicado
            starts.append(max(st_, 0))
    starts = sorted(set(starts))
    if len(starts) < 2:
        return None
    bl = [lineas[starts[k]:(starts[k + 1] if k + 1 < len(starts) else len(lineas))] for k in range(len(starts))]
    if any(x.strip() for x in lineas[:starts[0]]):
        bl.insert(0, lineas[:starts[0]])
    return bl


def _limpiar_bloque(lineas) -> str:
    out = []
    for l in lineas:
        t = l.strip()
        n = _norm(t)
        if _MARCA_FEED.match(n) or _es_pie(t) or n in _RUIDO or re.fullmatch(r"[\d.,\s]+", t or "x"):
            continue
        out.append(t)
    txt = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", txt).strip()


def _palabras(t: str) -> int:
    return len(re.findall(r"\w+", t))


def _huella_texto(t: str) -> str:
    return re.sub(r"\W+", " ", _norm(t)).strip()


def parsear_posts(texto: str) -> dict:
    """Separa un texto pegado con varias publicaciones.
    Orden de confianza: 1) líneas '---' que pongas tú, 2) marcas 'Feed post number N', 3) botones de cada post
    (Me gusta · Comentar · Compartir · Enviar), 4) encabezados con autor y '• 3er+', 5) 3+ líneas en blanco.
    Devuelve {"posts": [...], "metodo": str, "descartados": n, "duplicados": n}; cada post lleva texto, autor, fecha, url, titulo."""
    texto = (texto or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not texto:
        return {"posts": [], "metodo": "vacío", "descartados": 0, "duplicados": 0}
    metodo, bloques = None, None
    manual = [p for p in re.split(r"\n[ \t]*-{3,}[ \t]*\n", texto) if p.strip()]
    if len(manual) >= 2:
        metodo, bloques = "tus separadores ---", [p.split("\n") for p in manual]
    if bloques is None:
        lineas = texto.split("\n")
        for nombre, fn in (("marcas «Feed post»", _bloques_por_marcas), ("botones Me gusta/Comentar/Compartir", _bloques_por_pie),
                           ("encabezados de autor", _bloques_por_encabezado)):
            b = fn(lineas)
            validos = [x for x in (b or []) if _palabras(_limpiar_bloque(x)) >= 8]
            if len(validos) >= 2:
                metodo, bloques = nombre, b
                break
        if bloques is None:
            partes = [p for p in re.split(r"\n[ \t]*\n[ \t]*\n[ \t]*\n+", texto) if p.strip()]
            if len(partes) >= 2:
                metodo, bloques = "espacios grandes entre textos", [p.split("\n") for p in partes]
            else:
                metodo, bloques = "un solo texto", [lineas]
    posts, descartados, duplicados, vistos = [], 0, 0, set()
    for b in bloques:
        t = _limpiar_bloque(b)
        if _palabras(t) < 8:
            if t:
                descartados += 1
            continue
        h = _huella_texto(t)
        if h in vistos:
            duplicados += 1
            continue
        vistos.add(h)
        posts.append(extraer_meta_post(t))
    return {"posts": posts, "metodo": metodo, "descartados": descartados, "duplicados": duplicados}


def dividir_posts(texto: str) -> list:
    """Compatibilidad: solo los textos separados."""
    return [p["texto"] for p in parsear_posts(texto)["posts"]]


def fecha_relativa(linea: str, hoy=None):
    """'hace 3 d' · '3 d •' · '1 sem' · '2 mo' · '5h' -> fecha ISO aproximada, o None."""
    n = re.sub(r"[^\w\s]|_", " ", _norm(linea))
    n = re.sub(r"\s+", " ", n).strip()
    m = _TIEMPO.match(n)
    if not m:
        return None
    unidad = m.group(2)
    if unidad not in _UNID_DIAS:
        return None
    from datetime import timedelta
    return ((hoy or date.today()) - timedelta(days=int(m.group(1)) * _UNID_DIAS[unidad])).isoformat()


def extraer_meta_post(texto: str) -> dict:
    """Autor, fecha aproximada ('hace 3 d'), URL y un título útil a partir de lo pegado."""
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    autor, fecha, cab = "", None, 0
    for i, l in enumerate(lineas[:8]):
        if fecha is None:
            f = fecha_relativa(l)
            if f:
                fecha, cab = f, max(cab, i + 1)
        if not autor and _INSIGNIA.search(_norm(l)):
            antes = re.split(r"[•·]", l)[0].strip()
            autor = antes or (lineas[i - 1] if i > 0 else "")
            cab = max(cab, i + 1)
    if not autor and fecha and cab > 1 and len(lineas[0]) <= 80:
        autor = lineas[0]
    autor = autor[:80]
    m = _URL_POST.search(texto)
    cuerpo = lineas[cab:] if cab else lineas
    titulo = next((l for l in cuerpo if re.search(_HIRING, _norm(l)) and l != autor), "") \
        or next((l for l in cuerpo if len(l) >= 30 and l != autor), "") \
        or (cuerpo[0] if cuerpo else lineas[0] if lineas else "")
    return {"texto": texto, "autor": autor, "fecha": fecha or "", "url": m.group(0) if m else "", "titulo": titulo[:120]}


# Lectura del HTML guardado desde LinkedIn (Ctrl+S) -----------------------------------------------
def fecha_de_nombre(nombre: str):
    """'Buscar | LinkedIn (19-11-2026 5_49 p. m.).html' -> date(2026, 11, 19). None si el nombre no trae fecha."""
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", nombre or "")
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def link_buscar_post(texto: str) -> str:
    """Enlace que busca la publicación en LinkedIn por sus primeras palabras (plan B cuando no hay enlace directo)."""
    frase = " ".join(re.findall(r"[\w'’]+", texto or "")[:10])
    return "https://www.linkedin.com/search/results/content/?keywords=" + quote_plus(f'"{frase}"') + "&sortBy=%22date_posted%22"


def _desenvolver(href: str) -> str:
    """linkedin.com/safety/go/?url=<destino> -> destino real."""
    from urllib.parse import urlparse, parse_qs, unquote
    if "linkedin.com/safety/go" in href:
        q = parse_qs(urlparse(href.replace("&amp;", "&")).query).get("url")
        if q:
            return unquote(q[0])
    return href.replace("&amp;", "&")


def es_directo(url: str) -> bool:
    """True si el enlace apunta al post mismo (linkedin.com/feed/update/..., /posts/..., /pulse/...)."""
    return bool(_URL_POST.search(url or ""))


def mejor_link(p: dict) -> str:
    """Enlace directo al post si se pudo reconstruir; si no, una búsqueda en LinkedIn por el inicio exacto del texto."""
    return p.get("url") or link_buscar_post(p.get("texto", ""))


_URN_POST = re.compile(r"urn(?:%3A|:)li(?:%3A|:)(activity|ugcPost|share)(?:%3A|:)(\d{10,})")


def permalink_de_urn(trozo: str) -> str:
    """Busca un urn:li:activity:<id> (o ugcPost/share) en un trozo de HTML y arma el enlace directo al post."""
    m = _URN_POST.search(trozo or "")
    return f"https://www.linkedin.com/feed/update/urn:li:{m.group(1)}:{m.group(2)}/" if m else ""


def parsear_html_posts(html, ref=None) -> dict:
    """Extrae publicaciones de un HTML de «Buscar | LinkedIn» guardado con Ctrl+S (resultados de contenido).
    Por post: texto completo (con saltos de línea), autor, enlace al perfil/empresa, enlaces externos, emails y fecha
    (el «hace 4 d» se calcula respecto a `ref`, que debería ser el día en que guardaste la página)."""
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    cajas = soup.find_all(attrs={"data-testid": "expandable-text-box"})
    posts, vistos, duplicados = [], {}, 0
    for caja in cajas:
        cont = caja
        for _ in range(8):  # sube mientras el contenedor siga siendo de UN solo post
            padre = cont.parent
            if padre is None or len(padre.find_all(attrs={"data-testid": "expandable-text-box"})) != 1:
                break
            cont = padre
        for br in caja.find_all("br"):
            br.replace_with("\n")
        texto = re.sub(r"[ \t\u200b]+\n", "\n", caja.get_text("")).strip()
        texto = re.sub(r"\n{3,}", "\n\n", texto)
        if _palabras(texto) < 5:
            continue
        autor = ""
        for el in cont.find_all(attrs={"aria-label": True}):
            m = re.match(r"(?:Abrir el menú de controles para la publicación de|Open control menu for post by)\s+(.+)", el["aria-label"])
            if m:
                autor = m.group(1).strip()
                break
        perfil, externos, emails = "", [], []
        for a in cont.find_all("a", href=True):
            h = a["href"].strip()
            if h.startswith("mailto:"):
                em = h[7:].split("?")[0]
                if em and em not in emails:
                    emails.append(em)
            elif re.match(r"https?://(?:[\w-]+\.)?linkedin\.com/(?:in|company|school)/", h):
                if not perfil:
                    perfil = h.split("?")[0]
            elif re.match(r"https?://(?:[\w-]+\.)?linkedin\.com/(?:search|feed/hashtag|safety/go)", h):
                d = _desenvolver(h)
                if d.startswith("http") and "linkedin.com/search" not in d and d not in externos and "linkedin.com/safety" not in d:
                    externos.append(d)
            elif h.startswith("http") and "linkedin.com" not in h and h not in externos:
                externos.append(h)
        directo = ""
        for a in cont.find_all("a", href=True):
            mm = _URL_POST.search(a["href"])
            if mm:
                directo = mm.group(0)
                break
        if not directo:  # posts compartidos en grupos traen su urn:li:activity en los enlaces del autor/grupo
            directo = permalink_de_urn(str(cont))
        fecha = ""
        cabecera = [t for t in cont.stripped_strings][:14]
        for t in cabecera:
            f = fecha_relativa(t, ref)
            if f:
                fecha = f
                break
        h = _huella_texto(texto)
        if h in vistos:
            duplicados += 1
            if directo and not vistos[h]["url"]:  # el repetido trae el enlace directo: conservarlo
                vistos[h]["url"] = directo
            continue
        meta = extraer_meta_post(texto)
        posts.append({"texto": texto, "autor": autor, "fecha": fecha, "url": directo, "titulo": meta["titulo"],
                      "perfil": perfil, "links": externos, "oferta": externos[0] if externos else "", "emails": emails})
        vistos[h] = posts[-1]
    return {"posts": posts, "metodo": "HTML guardado de LinkedIn", "descartados": 0, "duplicados": duplicados}


def buscar_existente(data: dict, texto: str):
    """Clave de una publicación ya guardada con el mismo contenido (aunque se haya pegado con otro formato), o None."""
    h = _huella_texto(texto)[:80]
    if len(h) < 30:
        return None
    for k, it in data["items"].items():
        if it.get("tipo") == "publicacion" and h in _huella_texto(it.get("desc", "")):
            return k
    return None


# ─────────────────────────────────────────────
# 8. INTERFAZ STREAMLIT
# ─────────────────────────────────────────────
def listar_cvs() -> list:
    os.makedirs(CV_DIR, exist_ok=True)
    return [n for n in sorted(os.listdir(CV_DIR))
            if not n.startswith("~$") and os.path.isfile(os.path.join(CV_DIR, n))
            and n.rsplit(".", 1)[-1].lower() in ("docx", "pdf", "txt")]


def guardar_cvs(archivos) -> int:
    """archivos: lista de (nombre, bytes). Quedan en cvs/ y siguen ahí entre sesiones."""
    os.makedirs(CV_DIR, exist_ok=True)
    n = 0
    for nombre, contenido in archivos:
        with open(os.path.join(CV_DIR, os.path.basename(nombre)), "wb") as out:
            out.write(contenido)
        n += 1
    return n


def quitar_cvs(nombres) -> int:
    """No borra: mueve a cvs/_quitados/ para poder recuperarlos."""
    papelera = os.path.join(CV_DIR, "_quitados")
    os.makedirs(papelera, exist_ok=True)
    n = 0
    for nombre in nombres:
        src = os.path.join(CV_DIR, os.path.basename(nombre))
        if os.path.isfile(src):
            os.replace(src, os.path.join(papelera, os.path.basename(nombre)))
            n += 1
    return n


def _cargar_perfiles(st) -> list:
    perfiles = []
    for nombre in listar_cvs():
        try:
            with open(os.path.join(CV_DIR, nombre), "rb") as fh:
                perfiles.append(analizar_cv(archivo_a_texto(nombre, fh.read()), nombre))
        except Exception as e:
            st.warning(f"No pude leer {nombre}: {e}")
    return perfiles


def _aviso(st, msg=None):
    """Mensaje que sobrevive al st.rerun()."""
    if msg:
        st.session_state["cvm_aviso"] = msg
    elif st.session_state.get("cvm_aviso"):
        st.success(st.session_state.pop("cvm_aviso"))


def _seccion_cvs(st):
    """CVs guardados SIEMPRE visibles + formulario con botón explícito para terminar de subir."""
    cvs = listar_cvs()
    _aviso(st)
    if cvs:
        st.markdown(f"**📄 CVs en uso ({len(cvs)}):** " + " · ".join(f"✅ {n}" for n in cvs))
    else:
        st.warning("Todavía no hay ningún CV guardado. Súbelo abajo y pulsa **Guardar CVs**.")
    with st.expander("➕ Subir, cambiar o quitar CVs", expanded=not cvs):
        with st.form("cvm_form_cv", clear_on_submit=True):
            subidos = st.file_uploader("Elige uno o más CVs (.docx, .pdf, .txt)", type=["docx", "pdf", "txt"],
                                       accept_multiple_files=True, key="cvm_up")
            guardar_btn = st.form_submit_button("✅ Guardar CVs", type="primary")
        if guardar_btn:
            if subidos:
                n = guardar_cvs([(f.name, f.getvalue()) for f in subidos])
                _aviso(st, f"{n} CV(s) guardado(s) en la carpeta {CV_DIR}/ y ya se usan para el puntaje.")
                st.rerun()
            else:
                st.warning("Primero elige al menos un archivo y luego pulsa «Guardar CVs».")
        if cvs:
            quitar = st.multiselect("Quitar de la lista (se mueven a cvs/_quitados, no se borran)", cvs, key="cvm_quitar")
            if st.button("🗑️ Quitar seleccionados", disabled=not quitar, key="cvm_btn_quitar"):
                n = quitar_cvs(quitar)
                _aviso(st, f"{n} CV(s) quitado(s) de la lista.")
                st.rerun()


def _refrescar(st, data):
    guardar(data)
    st.session_state["cvm_ver"] = st.session_state.get("cvm_ver", 0) + 1
    st.rerun()


# etiqueta que ve el usuario -> columna interna
COLUMNAS_TABLA = {"✔ Casilla": "Sel", "Match": "Puntaje", "Estado": "Estado", "Hace": "Hace", "Fecha": "Publicada",
                  "Título": "Título", "Empresa": "Empresa", "Tipo": "Tipo", "Lugar": "Lugar", "Coincide": "Coincide",
                  "Notas": "Notas", "Link": "Link", "Oferta": "Oferta", "Perfil": "Perfil"}
COLUMNAS_DEFECTO = ["✔ Casilla", "Match", "Estado", "Hace", "Fecha", "Título", "Empresa", "Notas", "Link", "Oferta", "Perfil"]


def _ancho(st, px, fallback="small"):
    """Ancho en píxeles si esta versión de Streamlit lo permite (>=1.41); si no, el tamaño equivalente."""
    try:
        v = tuple(int(x) for x in st.__version__.split(".")[:2])
    except Exception:
        v = (0, 0)
    return px if v >= (1, 41) else fallback


def _tabla(st, df, seleccionar_todo=False, alto=470, orden=None):
    """Tabla editable. `orden` = etiquetas de columnas visibles, en el orden en que se muestran (lista vacía = ninguna).
    Devuelve la tabla editada (siempre trae todas las columnas internas, estén visibles o no)."""
    orden = COLUMNAS_DEFECTO if orden is None else orden
    cols = list(COLUMNAS_TABLA.values()) + ["_key"]
    vista = df[cols].copy().reset_index(drop=True)
    vista["Sel"] = bool(seleccionar_todo)
    visibles = [COLUMNAS_TABLA[e] for e in orden if e in COLUMNAS_TABLA]
    if not visibles:  # Streamlit mostraría todo con una lista vacía: mejor no dibujar la tabla
        return vista
    w = lambda px, fb="small": _ancho(st, px, fb)
    cfg = {
        "_key": None,
        "Sel": st.column_config.CheckboxColumn("✔", width=w(38), help="Marca filas para aplicarles una acción"),
        "Puntaje": st.column_config.ProgressColumn("Match", min_value=0, max_value=100, format="%d", width=w(80)),
        "Estado": st.column_config.SelectboxColumn("Estado", options=ESTADOS, required=True, width=w(135, "medium")),
        "Hace": st.column_config.TextColumn("Hace", width=w(105)),
        "Publicada": st.column_config.TextColumn("Fecha", width=w(92), help="~ = fecha aproximada (día en que la encontré)"),
        "Título": st.column_config.TextColumn("Título", width=w(330, "large")),
        "Empresa": st.column_config.TextColumn("Empresa", width=w(150, "medium")),
        "Tipo": st.column_config.TextColumn("Tipo", width=w(90)),
        "Lugar": st.column_config.TextColumn("Lugar", width=w(130, "medium")),
        "Coincide": st.column_config.TextColumn("Coincide", width=w(220, "medium")),
        "Link": st.column_config.LinkColumn("Link", display_text="Abrir ↗", width=w(70), help="Enlace al post. Si LinkedIn no lo entrega, abre una búsqueda que lo encuentra por su texto."),
        "Oferta": st.column_config.LinkColumn("Oferta", display_text="Postular ↗", width=w(85), help="Enlace de postulación o vacante que viene dentro de la publicación"),
        "Perfil": st.column_config.LinkColumn("Perfil", display_text="Autor ↗", width=w(70), help="Perfil o página de empresa de quien publicó"),
        "Notas": st.column_config.TextColumn("Notas", width=w(170, "medium")),
    }
    bloq = [c for c in cols if c not in ("Sel", "Estado", "Notas")]
    sig = hashlib.md5(("|".join(vista["_key"]) + str(seleccionar_todo)).encode()).hexdigest()[:8]
    ver = st.session_state.get("cvm_ver", 0)
    return st.data_editor(vista, column_config=cfg, column_order=visibles, disabled=bloq, hide_index=True,
                          use_container_width=True, height=alto, key=f"cvm_tabla_{ver}_{sig}")


def _mover_col(st, delta):
    col = st.session_state.get("cvm_mover_col")
    lst = list(st.session_state.get("cvm_cols_vis", []))
    if col in lst:
        i, j = lst.index(col), lst.index(col) + delta
        if 0 <= j < len(lst):
            lst[i], lst[j] = lst[j], lst[i]
            st.session_state["cvm_cols_vis"] = lst


def _restablecer_cols(st):
    st.session_state["cvm_cols_vis"] = list(COLUMNAS_DEFECTO)


def _selector_columnas(st, data) -> list:
    """Elegir qué columnas se ven y en qué orden. Se recuerda entre sesiones (cv_match_data.json)."""
    if "cvm_cols_vis" not in st.session_state:
        guard = data.get("cols_tabla")
        st.session_state["cvm_cols_vis"] = [c for c in guard if c in COLUMNAS_TABLA] if isinstance(guard, list) else list(COLUMNAS_DEFECTO)
    with st.expander("🧱 Columnas: mostrar, ocultar y mover"):
        st.multiselect("Columnas visibles (se muestran en este orden; quita las que no quieras, incluso todas)",
                       list(COLUMNAS_TABLA), key="cvm_cols_vis")
        vis = st.session_state["cvm_cols_vis"]
        if vis:
            m1, m2, m3, m4 = st.columns([2, 1, 1, 1])
            m1.selectbox("Mover la columna…", vis, key="cvm_mover_col")
            m2.button("◀ Izquierda", on_click=_mover_col, args=(st, -1), use_container_width=True, key="cvm_mv_izq")
            m3.button("Derecha ▶", on_click=_mover_col, args=(st, 1), use_container_width=True, key="cvm_mv_der")
            m4.button("↺ Restablecer", on_click=_restablecer_cols, args=(st,), use_container_width=True, key="cvm_mv_rst")
        else:
            st.button("↺ Restablecer", on_click=_restablecer_cols, args=(st,), key="cvm_mv_rst0")
        st.caption("Para añadir una columna, elígela en la lista; aparece al final y la puedes mover con ◀ ▶. "
                   "La tabla también deja ajustar el ancho arrastrando el borde de cada encabezado.")
    vis = list(st.session_state["cvm_cols_vis"])
    if data.get("cols_tabla") != vis:
        data["cols_tabla"] = vis
        guardar(data)
    return vis


def _guardar_ediciones(st, data, ed):
    """Persiste los cambios de Estado/Notas hechos directamente en la tabla."""
    cambios, ahora = 0, datetime.now().isoformat(timespec="seconds")
    for _, f in ed.iterrows():
        it = data["items"].get(f["_key"])
        if not it:
            continue
        if f["Estado"] != it.get("estado"):
            it["estado"], it["fecha_estado"] = f["Estado"], ahora
            cambios += 1
        notas = f["Notas"] if isinstance(f["Notas"], str) else ""
        if notas != it.get("notas", ""):
            it["notas"] = notas
            cambios += 1
    if cambios:
        _refrescar(st, data)


def _detalle(st, data, keys):
    if not keys:
        return
    with st.expander("🔎 Ver detalle de una oferta (por qué tiene ese puntaje)"):
        def fmt(k):
            it = data["items"][k]
            return f"{it.get('score', {}).get('puntaje', 0):>3} pts · {it.get('titulo', '')[:70]} — {it.get('empresa', '')}"
        sel = st.selectbox("Oferta", keys, format_func=fmt, key="cvm_det_sel")
        it = data["items"][sel]
        sc = it.get("score", {})
        f, aprox = fecha_ef(it)
        st.caption(f"📅 Publicada: {f or '—'}{' (aprox.)' if aprox else ''} · {hace_txt(dias_desde(f))}"
                   + (f" · 👥 {it['postulantes']}" if it.get("postulantes") else ""))
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", f"{sc.get('puntaje', 0)}/100")
        c2.metric("Título", f"{sc.get('pts_titulo', 0)}/40", help=sc.get("razon_titulo", ""))
        c3.metric("Skills", f"{sc.get('pts_skills', 0)}/45")
        c4.metric("Nivel", f"{sc.get('pts_nivel', 0)}/15", help=sc.get("razon_nivel", ""))
        st.caption(f"Título: {sc.get('razon_titulo', '')} · Nivel: {sc.get('razon_nivel', '')}"
                   + (f" · Penalización: -{sc['penal']}" if sc.get("penal") else ""))
        if sc.get("sin_desc"):
            st.warning("Sin descripción completa: el puntaje de skills es parcial.")
        st.markdown(f"✅ **Coinciden:** {', '.join(sc.get('coinciden', [])) or '—'}")
        st.markdown(f"➖ **Pide y no tienes marcado:** {', '.join(sc.get('faltan', [])) or '—'}")
        if it.get("criterios"):
            st.caption(" · ".join(f"{k}: {v}" for k, v in it["criterios"].items()))
        st.text_area("Descripción", it.get("desc", "") or "(sin descripción)", height=220, disabled=True, key=f"cvm_desc_{sel}")
        enlaces = []
        if it.get("url"):
            enlaces.append(f"🔗 [Abrir]({it['url']})")
        if it.get("perfil"):
            enlaces.append(f"👤 [Perfil del autor]({it['perfil']})")
        if it.get("tipo") == "publicacion":
            enlaces.append(f"🔎 [Buscar este post en LinkedIn]({link_buscar_post(it.get('desc', ''))})")
        if enlaces:
            st.markdown(" · ".join(enlaces))
        for l in it.get("links", []) or []:
            st.markdown(f"↗ {l}")
        if it.get("emails"):
            st.markdown("✉️ " + ", ".join(it["emails"]))


def render_tab():
    import pandas as pd
    import streamlit as st

    data = cargar()
    cfg = data["cfg"]
    st.subheader("📄 CV Match — empleos y publicaciones de LinkedIn según tu CV")

    _seccion_cvs(st)
    perfiles = _cargar_perfiles(st)
    if not perfiles:
        st.info("Guarda al menos un CV para empezar.")
        return
    cv = combinar_cvs(perfiles)

    t_bus, t_res, t_pub = st.tabs(["🔎 1. Buscar empleos", "📋 2. Resultados y seguimiento", "💬 3. Agregar publicaciones"])

    # ═════════ 1. BUSCAR ═════════
    with t_bus:
        qs_auto = generar_queries(cv)
        st.markdown("**¿Qué buscar?** Se hace **una sola búsqueda** en LinkedIn combinando tus palabras con OR / AND / NOT.")
        kw_modo = st.radio("Cómo escribir la búsqueda", ["Guiado (3 casillas)", "Escribir la consulta completa"],
                           horizontal=True, index=1 if cfg.get("kw_avanzado") else 0, key="cvm_kw_modo")
        avanzado = kw_modo.startswith("Escribir")
        or_ini = cfg["kw_or"] if "kw_or" in cfg else (cfg.get("keywords") or ", ".join(cfg.get("queries") or qs_auto[:4]))
        kw_or = cfg.get("kw_or", or_ini)
        kw_and, kw_not, kw_raw = cfg.get("kw_and", ""), cfg.get("kw_not", ""), cfg.get("kw_raw", "")
        if not avanzado:
            g1, g2, g3 = st.columns([2, 1.2, 1.2])
            kw_or = g1.text_area("Alguna de estas (OR)", value=or_ini, height=100, key="cvm_kw_or",
                                 help="Separa con coma. La oferta debe tener AL MENOS UNA.",
                                 placeholder='Desarrollador Backend, Backend Developer, Integration Engineer')
            kw_and = g2.text_area("Todas estas (AND)", value=kw_and, height=100, key="cvm_kw_and",
                                  help="Separa con coma. La oferta debe tener TODAS.", placeholder="python, APIs")
            kw_not = g3.text_area("Excluir (NOT)", value=kw_not, height=100, key="cvm_kw_not",
                                  help="Separa con coma. Se descartan ofertas con cualquiera de estas.", placeholder="senior, lead")
            consulta = construir_query_bool(parsear_keywords(kw_or), parsear_keywords(kw_and), parsear_keywords(kw_not))
            kw_list = parsear_keywords(kw_or) + parsear_keywords(kw_and)
            if not consulta and parsear_keywords(kw_not):
                st.warning("Un NOT solo no funciona en LinkedIn: agrega al menos una palabra en «Alguna de estas» o «Todas estas».")
        else:
            ini_raw = kw_raw or construir_query_bool(parsear_keywords(or_ini), parsear_keywords(kw_and), parsear_keywords(kw_not))
            kw_raw = st.text_area("Consulta (usa OR, AND, NOT en MAYÚSCULAS, comillas y paréntesis)", value=ini_raw, height=100,
                                  key="cvm_kw_raw", placeholder='("Desarrollador Backend" OR "Backend Developer") AND python NOT senior')
            consulta = re.sub(r"\s+", " ", kw_raw).strip()
            kw_list = [k for k in parsear_keywords(re.sub(r"[()]|\b(?:OR|AND|NOT)\b", ",", consulta))]
            for av in revisar_query(consulta):
                st.warning(f"⚠️ {av}")
        queries = [consulta] if consulta else []
        st.caption(f"🔎 Consulta única que se enviará a LinkedIn: `{consulta or '—'}`")
        sug = [q for q in qs_auto if q.lower() not in {k.lower() for k in kw_list}]
        if sug:
            st.caption("💡 Del CV también salen: " + " · ".join(sug[:8]) + " (cópialas arriba si te sirven)")
        c1, c2 = st.columns(2)
        ant_lbl = c1.selectbox("Publicadas en", list(ANTIGUEDAD), index=list(ANTIGUEDAD).index(cfg.get("antiguedad", "Última semana")), key="cvm_ant")
        ubic = c2.text_input("Ubicación", value=cfg.get("ubicacion", "Chile"), key="cvm_ubic")
        st.markdown("**¿Cuántos resultados traer?**")
        r1, r2, r3 = st.columns([1.3, 1, 1])
        modo_pag = r1.radio("Páginas de LinkedIn", ["Todas las que haya", "Solo las primeras…"], key="cvm_modo_pag",
                            index=1 if cfg.get("pag_modo") == "n" else 0, horizontal=False,
                            help=f"Cada página trae ~10 ofertas. «Todas» sigue hasta que LinkedIn no tenga más (tope {MAX_PAGINAS_TOPE} páginas).")
        todas_p = modo_pag.startswith("Todas")
        pags = r2.number_input("Cantidad de páginas", 1, MAX_PAGINAS_TOPE, int(cfg.get("paginas", 10)), key="cvm_pags2",
                               disabled=todas_p)
        maxres = r3.number_input("Máx. ofertas a traer", 10, 1000, int(cfg.get("max_ofertas", 500)), key="cvm_maxres2",
                                 help="Corta la búsqueda al llegar a esta cantidad, aunque queden páginas.")
        zona_boton = st.container()

        with st.expander("⚙️ Más opciones (modalidad, nivel, descripciones)"):
            o1, o2 = st.columns(2)
            modal = o1.multiselect("Modalidad (vacío = todas)", list(MODALIDAD), default=cfg.get("modalidad", []), key="cvm_mod")
            nivel = o2.multiselect("Nivel (vacío = todos)", list(NIVEL), default=cfg.get("nivel", ["Júnior", "Asociado", "Intermedio-Senior"]), key="cvm_niv")
            o4, o5 = st.columns(2)
            dedup = o4.checkbox("Descartar reposteos", value=cfg.get("dedup", True), key="cvm_dedup",
                                help="Misma empresa, título y lugar con otro ID: se cuenta una sola vez.")
            leer = o5.checkbox("Leer descripción completa", value=cfg.get("leer", True), key="cvm_leer",
                               help="Más lento, pero el puntaje es mucho más preciso")
            limite = o5.number_input("Máx. descripciones a leer", 5, 500, int(cfg.get("limite_desc", 150)), key="cvm_lim2",
                                     help="Las ofertas sin descripción puntúan peor. Sube este número si traes muchas.")

        with st.expander(f"🧩 Mi perfil ({len(cv['skills'])} skills · {cv['anios']} años) — skills que cuentan para el puntaje"):
            for p in perfiles:
                st.caption(f"📄 {p['archivo']} — roles: {', '.join(p['roles']) or '—'}")
            opciones_sk = cv["skills"] + [e for e in cv["extras"] if e not in cv["skills"]]
            extra_txt = st.text_input("Skills extra (separadas por coma)", value=cfg.get("extras_user", ""), key="cvm_extra")
            extras_user = [x.strip() for x in extra_txt.split(",") if x.strip()]
            opciones_sk += [e for e in extras_user if e not in opciones_sk]
            previo = [s for s in cfg.get("skills", []) if s in opciones_sk]
            skills_sel = st.multiselect("Skills activas", opciones_sk, default=previo or cv["skills"], key="cvm_skills")
            anios = st.number_input("Años de experiencia", 0, 30, int(cfg.get("anios", cv["anios"] or 3)), key="cvm_anios")

        data["cfg"] = cfg = {"skills": skills_sel, "anios": int(anios), "extras_user": extra_txt, "kw_avanzado": avanzado, "kw_or": kw_or, "kw_and": kw_and, "kw_not": kw_not, "kw_raw": kw_raw,
                             "ubicacion": ubic, "antiguedad": ant_lbl, "paginas": int(pags), "pag_modo": "todas" if todas_p else "n",
                             "max_ofertas": int(maxres), "dedup": bool(dedup),
                             "modalidad": modal, "nivel": nivel, "leer": leer, "limite_desc": int(limite)}
        extras_sc = [e for e in cv["extras"] if e in skills_sel] + [e for e in extras_user if e in skills_sel]

        with zona_boton:
            b1, b2 = st.columns([3, 1])
            buscar = b1.button("🚀 Buscar empleos en LinkedIn", type="primary", use_container_width=True, disabled=not queries)
            if not queries:
                st.caption("Agrega al menos una palabra clave para poder buscar.")
            recalc = b2.button("♻️ Recalcular puntajes", use_container_width=True,
                               help="Aplica tus skills/años actuales a todo lo ya guardado")
        if buscar:
            barra, estado = st.progress(0.0), st.empty()

            def prog(msg, frac):
                estado.markdown(msg)
                barra.progress(min(1.0, max(0.0, frac)))

            ofertas, avisos, stats = buscar_empleos(queries, ubic, ANTIGUEDAD[ant_lbl], None if todas_p else int(pags),
                                                    modal, nivel, prog, max_resultados=int(maxres), dedup=dedup)
            for a in avisos:
                st.warning(a)
            borradas = set(data.get("eliminadas", []))
            nuevas = [o for o in ofertas if f"empleo:{o['id']}" not in data["items"] and f"empleo:{o['id']}" not in borradas]
            if leer and nuevas:
                nuevas.sort(key=lambda o: -puntuar(o["titulo"], "", cv, skills_sel, anios, extras_sc)["puntaje"])
                leer_detalles(nuevas, int(limite), prog)
            n_add = integrar(data, nuevas)
            repuntuar(data, cv, skills_sel, anios, extras_sc)
            guardar(data)
            barra.progress(1.0)
            estado.success(f"🎉 {len(ofertas)} ofertas en {stats['paginas']} página(s)"
                           + (f" ({stats['duplicadas']} reposteos descartados)" if stats["duplicadas"] else "")
                           + f" · **{n_add} nuevas** guardadas. Míralas en «2. Resultados y seguimiento».")
            if not ofertas and not avisos:
                st.warning("LinkedIn no devolvió resultados. Prueba ampliar la antigüedad o quitar nivel/modalidad.")
        if recalc:
            repuntuar(data, cv, skills_sel, anios, extras_sc)
            guardar(data)
            st.success("Puntajes recalculados.")
        guardar(data)

    skills_sel = data["cfg"].get("skills", cv["skills"])
    anios = data["cfg"].get("anios", cv["anios"])
    extras_sc = [e for e in cv["extras"] if e in skills_sel]

    # ═════════ 2. RESULTADOS ═════════
    with t_res:
        todas = pd.DataFrame(filas(data))
        if todas.empty:
            st.info("Todavía no hay nada guardado. Busca empleos en la pestaña 1 o agrega publicaciones en la 3.")
        else:
            conteo = {g: int(todas["Estado"].isin(est).sum()) for g, est in GRUPOS_ESTADO.items()}
            st.markdown("**Estado**")
            grupo = st.radio("Estado", list(GRUPOS_ESTADO), horizontal=True, key="cvm_f_grupo",
                             format_func=lambda g: f"{g} ({conteo[g]})", label_visibility="collapsed")
            f1, f2, f3 = st.columns([1.1, 1.5, 1.1])
            pl = f1.radio("Parecido a mi CV", list(FILTRO_PUNTAJE), horizontal=True, index=len(FILTRO_PUNTAJE) - 1, key="cvm_f_pun2")
            dl = f2.radio("Publicada hace", list(FILTRO_DIAS), horizontal=True, index=len(FILTRO_DIAS) - 1, key="cvm_f_dias")
            tipo = f3.radio("Mostrar", ["Todo", "Empleos", "Publicaciones"], horizontal=True, key="cvm_f_tipo")
            g1, g2 = st.columns([3, 1])
            texto = g1.text_input("Buscar (título, empresa, skill…)", key="cvm_f_txt", placeholder="ej: node, banco, remoto")
            orden = g2.selectbox("Ordenar", ["Mejor puntaje", "Más recientes"], key="cvm_f_ord")

            df = filtrar_df(todas, FILTRO_PUNTAJE[pl], FILTRO_DIAS[dl], tipo, grupo, texto, orden)
            total_est = int(todas["Estado"].isin(GRUPOS_ESTADO[grupo]).sum())
            st.caption(f"Mostrando **{len(df)}** de {total_est} en «{grupo}»"
                       + (f" · {total_est - len(df)} ocultas por los filtros de arriba" if total_est > len(df) else "")
                       + " · 🟢 ≤3 días · 🟡 ≤2 semanas · 🔴 más antiguas")
            if df.empty:
                st.warning("Ningún resultado con estos filtros. Prueba «Todas» en «Parecido a mi CV» o «Publicada hace».")
            else:
                sel_todo = st.checkbox("Seleccionar todas las visibles", key="cvm_selall")
                orden_cols = _selector_columnas(st, data)
                if not orden_cols:
                    st.info("No hay columnas visibles. Elige al menos una en «🧱 Columnas: mostrar, ocultar y mover».")
                ed = _tabla(st, df, sel_todo, orden=orden_cols)
                _guardar_ediciones(st, data, ed)
                marcadas = list(ed.loc[ed["Sel"], "_key"])
                st.caption(f"✔ {len(marcadas)} seleccionada(s)."
                           + ("" if "✔ Casilla" in orden_cols else " (Activa la columna «✔ Casilla» para usar los botones de acción.)")
                           + " Cambia **Estado** o **Notas** directo en la tabla.")
                a1, a2, a3, a4 = st.columns(4)
                if a1.button("👀 Marcar revisada", disabled=not marcadas, use_container_width=True):
                    marcar(data, marcadas, ESTADOS[1]); _refrescar(st, data)
                if a2.button("📨 Marcar postulada", disabled=not marcadas, use_container_width=True):
                    marcar(data, marcadas, ESTADOS[2]); _refrescar(st, data)
                if a3.button("❌ Descartar", disabled=not marcadas, use_container_width=True):
                    marcar(data, marcadas, ESTADO_DESCARTADA); _refrescar(st, data)
                if a4.button("🗑️ Eliminar", disabled=not marcadas, use_container_width=True,
                             help="Borra de lo guardado. Los empleos eliminados no vuelven a aparecer al buscar."):
                    eliminar(data, marcadas); _refrescar(st, data)
                _detalle(st, data, list(df["_key"]))

            with st.expander("🧹 Limpiar lo guardado"):
                st.caption("Los empleos eliminados no vuelven a aparecer en próximas búsquedas (salvo «Eliminar TODO»).")
                k1, k2, k3 = st.columns(3)
                n_desc = int((todas["Estado"] == ESTADO_DESCARTADA).sum())
                if k1.button(f"🗑️ Eliminar descartadas ({n_desc})", disabled=not n_desc, use_container_width=True):
                    eliminar(data, todas.loc[todas["Estado"] == ESTADO_DESCARTADA, "_key"]); _refrescar(st, data)
                lim_p = k2.number_input("Match menor a", 0, 100, 30, key="cvm_lim_p")
                n_bajo = int(((todas["Puntaje"] < lim_p) & (todas["Estado"] == ESTADO_NUEVA)).sum())
                if k2.button(f"🗑️ Eliminar nuevas con match bajo ({n_bajo})", disabled=not n_bajo, use_container_width=True):
                    eliminar(data, todas.loc[(todas["Puntaje"] < lim_p) & (todas["Estado"] == ESTADO_NUEVA), "_key"]); _refrescar(st, data)
                lim_d = k3.number_input("Publicadas hace más de (días)", 1, 365, 30, key="cvm_lim_d")
                n_viejo = int(((todas["_dias"] > lim_d) & (todas["_dias"] < 9999) & (todas["Estado"] == ESTADO_NUEVA)).sum())
                if k3.button(f"🗑️ Eliminar nuevas antiguas ({n_viejo})", disabled=not n_viejo, use_container_width=True):
                    eliminar(data, todas.loc[(todas["_dias"] > lim_d) & (todas["_dias"] < 9999) & (todas["Estado"] == ESTADO_NUEVA), "_key"]); _refrescar(st, data)
                st.markdown("---")
                conf = st.checkbox("Confirmo que quiero borrar TODO lo guardado (empleos, publicaciones, estados y notas)", key="cvm_conf_all")
                if st.button("⚠️ Eliminar TODO", disabled=not conf, type="secondary"):
                    data["items"], data["eliminadas"] = {}, []
                    _refrescar(st, data)
            with st.expander("⬇️ Exportar (CSV)"):
                st.download_button("Descargar lo filtrado", df.drop(columns=["Sel", "_key", "_dias", "_fest", "_tipo"]).to_csv(index=False).encode("utf-8-sig"),
                                   file_name=f"cv_match_{datetime.now():%Y%m%d}.csv", mime="text/csv")

    # ═════════ 3. PUBLICACIONES ═════════
    with t_pub:
        st.info("LinkedIn **no deja buscar publicaciones sin iniciar sesión**, así que no se traen solas. "
                "1) Abre una de tus búsquedas guardadas, 2) selecciona y copia todo el texto de la página, "
                "3) pégalo abajo: el sistema separa las publicaciones, las puntúa y las guarda con su fecha.")

        # ---- CRUD de búsquedas ----
        st.markdown("##### 🔎 Mis búsquedas de publicaciones")
        bps = data.get("busquedas_posts", [])
        base = pd.DataFrame([{"Activa": b.get("activa", True), "Nombre": b.get("nombre", ""), "Consulta": b.get("query", ""),
                              "Periodo": b.get("periodo", "Última semana"), "id": b.get("id", "")} for b in bps],
                            columns=["Activa", "Nombre", "Consulta", "Periodo", "id"])
        ver_b = st.session_state.get("cvm_ver_bp", 0)
        ed_b = st.data_editor(
            base, num_rows="dynamic", hide_index=True, use_container_width=True, key=f"cvm_bp_{ver_b}",
            column_config={
                "Activa": st.column_config.CheckboxColumn("Activa", default=True, width="small"),
                "Nombre": st.column_config.TextColumn("Nombre", width="small"),
                "Consulta": st.column_config.TextColumn("Consulta (admite OR, NOT, comillas y paréntesis)", width="large", required=True),
                "Periodo": st.column_config.SelectboxColumn("Periodo", options=list(PERIODOS_POST), default="Última semana", width="small"),
                "id": None,
            })
        st.caption("Agrega filas con la fila vacía de abajo, edita celdas directo, y borra seleccionando la fila y pulsando Supr. "
                   "Nada se guarda hasta que pulses el botón.")
        bb1, bb2, _ = st.columns([1, 1, 2])
        if bb1.button("💾 Guardar búsquedas", type="primary", use_container_width=True, key="cvm_bp_save"):
            filas_b = [{"id": r["id"], "activa": r["Activa"], "nombre": r["Nombre"], "query": r["Consulta"], "periodo": r["Periodo"]}
                       for r in ed_b.to_dict("records")]
            n_ok, n_ign = guardar_busquedas_posts(data, filas_b)
            guardar(data)
            st.session_state["cvm_ver_bp"] = ver_b + 1
            _aviso(st, f"{n_ok} búsqueda(s) guardada(s)" + (f" · {n_ign} fila(s) vacía(s) ignorada(s)" if n_ign else ""))
            st.rerun()
        if bb2.button("↩️ Restaurar ejemplos", use_container_width=True, key="cvm_bp_reset",
                      help="Vuelve a agregar las dos búsquedas de ejemplo (Chile y EE. UU./Remoto)"):
            ya = {b["query"] for b in data.get("busquedas_posts", [])}
            for b in BUSQUEDAS_POSTS_INICIALES:
                if b["query"] not in ya:
                    data.setdefault("busquedas_posts", []).append(dict(b))
            guardar(data)
            st.session_state["cvm_ver_bp"] = ver_b + 1
            st.rerun()
        _aviso(st)
        guardadas = [b for b in data.get("busquedas_posts", []) if b.get("activa", True)]
        if not guardadas:
            st.caption("No hay búsquedas activas. Agrega una arriba.")
        cols = st.columns(2)
        for i, b in enumerate(guardadas):
            av = revisar_query(b["query"])
            cols[i % 2].link_button(f"🔗 {b.get('nombre') or b['query'][:30]} ({b.get('periodo', 'Última semana')})",
                                    url_busqueda_post(b["query"], b.get("periodo", "Última semana")), use_container_width=True)
            if av:
                cols[i % 2].caption("⚠️ " + "; ".join(av))

        # ---- Subir HTML o pegar texto ----
        st.markdown("##### ➕ Agregar publicaciones")
        n_p = st.session_state.get("cvm_paste_n", 0)
        st.caption("**Mejor opción: guarda la página de LinkedIn como HTML** (en el navegador: Ctrl+S → «Página web, solo HTML» o «completa») "
                   "y súbela aquí. Trae el texto completo, el autor, el link de su perfil, los links de postulación y la fecha. "
                   "LinkedIn no incluye el enlace directo a cada post en esa página; el sistema usa el link de la oferta y, si no hay, el perfil del autor.")
        subidos = st.file_uploader("Archivos HTML guardados de LinkedIn (uno o varios)", type=["html", "htm"],
                                   accept_multiple_files=True, key=f"cvm_html_{n_p}")
        txt = st.text_area("…o pega el texto de la página (varias publicaciones juntas). Ctrl+Enter o clic fuera para analizar.",
                           height=140, key=f"cvm_paste_{n_p}")
        fecha_def = st.date_input("📅 Fecha para las que no traigan «hace X días»", value=date.today(), max_value=date.today(),
                                  key="cvm_fecha_def", help="Con HTML la fecha se calcula sola (respecto al día en que guardaste la página). "
                                  "Esta es solo el respaldo.")
        posts, metodos, dupl, descart = [], [], 0, 0
        for fh in subidos or []:
            try:
                r = parsear_html_posts(fh.getvalue(), fecha_de_nombre(fh.name))
                if not r["posts"]:
                    st.warning(f"«{fh.name}»: no encontré publicaciones. Debe ser una búsqueda de **Publicaciones** de LinkedIn guardada con Ctrl+S. "
                               "Si LinkedIn no cargó todo, baja hasta el final de la página antes de guardarla.")
                posts += r["posts"]
                dupl += r["duplicados"]
                metodos.append(f"{fh.name}: {len(r['posts'])} (HTML)")
            except Exception as e:
                st.error(f"No pude leer «{fh.name}»: {e}")
        if txt.strip():
            r = parsear_posts(txt)
            posts += r["posts"]
            dupl += r["duplicados"]
            descart += r["descartados"]
            metodos.append(f"texto pegado: {len(r['posts'])} ({r['metodo']})")
        if (subidos or txt.strip()) and not posts:
            st.warning("No encontré publicaciones con texto suficiente. Si pegaste texto con formato raro, sepáralas con una línea que diga ---")
        if posts:
            borradas = set(data.get("eliminadas", []))
            vistos_pid, filas_p, por_pid = set(), [], {}
            for p in posts:
                p.setdefault("perfil", ""); p.setdefault("links", []); p.setdefault("emails", analizar_publicacion(p["texto"])["emails"])
                existente = buscar_existente(data, p["texto"])
                pid = existente.split(":", 1)[1] if existente else hashlib.md5((p["url"] or _huella_texto(p["texto"])).encode("utf-8")).hexdigest()[:12]
                if pid in vistos_pid:
                    dupl += 1
                    continue
                vistos_pid.add(pid)
                key = f"publicacion:{pid}"
                ex = data["items"].get(key)
                nuevo_link = mejor_link(p)
                completa = bool(ex) and ((nuevo_link and not ex.get("url")) or (es_directo(nuevo_link) and not es_directo(ex.get("url", "")))
                                         or (p["perfil"] and not ex.get("perfil")))
                estado_p = ("ya guardada · completar links" if completa else "ya guardada") if ex else ("eliminada antes" if key in borradas else "nueva")
                sc = puntuar(p["titulo"], p["texto"], cv, skills_sel, anios, extras_sc)
                por_pid[pid] = p
                filas_p.append({"Guardar": (not ex and key not in borradas) or completa, "Estado": estado_p, "Match": sc["puntaje"],
                                "Fecha": date.fromisoformat(p["fecha"]) if p["fecha"] else fecha_def,
                                "Fecha detectada": "sí" if p["fecha"] else "no (respaldo)", "Autor": p["autor"], "Título": p["titulo"],
                                "Tipo de link": "✅ directo al post" if es_directo(nuevo_link) else "🔎 búsqueda por texto",
                                "Link": nuevo_link, "Perfil": p["perfil"], "_pid": pid})
            st.success(f"Encontré **{len(filas_p)}** publicación(es): " + " · ".join(metodos)
                       + (f" · {dupl} repetida(s) omitida(s)" if dupl else "") + (f" · {descart} fragmento(s) muy cortos descartados" if descart else ""))
            sig = hashlib.md5((txt + "|".join(f.name for f in subidos or []) + str(fecha_def) + str(len(posts))).encode("utf-8")).hexdigest()[:8]
            ed_p = st.data_editor(
                pd.DataFrame(filas_p), hide_index=True, use_container_width=True, key=f"cvm_prev_{n_p}_{sig}",
                disabled=["Estado", "Match", "Fecha detectada", "Título", "Tipo de link", "Perfil", "_pid"],
                column_config={"Guardar": st.column_config.CheckboxColumn("Guardar", width=_ancho(st, 70)),
                               "Match": st.column_config.ProgressColumn("Match", min_value=0, max_value=100, format="%d", width=_ancho(st, 80)),
                               "Fecha": st.column_config.DateColumn("Fecha publicada", max_value=date.today(), format="DD-MM-YYYY"),
                               "Título": st.column_config.TextColumn(width="large"),
                               "Link": st.column_config.TextColumn("Link (editable)", help="Pega aquí el enlace directo del post: en LinkedIn, menú ··· → «Copiar enlace a la publicación»."),
                               "Perfil": st.column_config.LinkColumn("Perfil", display_text="Autor ↗", width=_ancho(st, 70)), "_pid": None})
            with st.expander("👁️ Ver el texto y los links de cada publicación"):
                for i, p in enumerate(por_pid.values(), 1):
                    st.markdown(f"**{i}. {p['autor'] or 'sin autor'}** — {p['titulo']}")
                    st.text(p["texto"][:700] + ("…" if len(p["texto"]) > 700 else ""))
                    ls = ([f"[perfil]({p['perfil']})"] if p["perfil"] else []) + [f"[{l[:60]}]({l})" for l in p["links"]] \
                        + [f"✉️ {e}" for e in p["emails"]]
                    if ls:
                        st.markdown(" · ".join(ls))
            marc = [r for r in ed_p.to_dict("records") if r["Guardar"]]
            if st.button(f"💾 Guardar {len(marc)} publicación(es)", type="primary", disabled=not marc, key="cvm_pub_save"):
                nuevos = []
                for r in marc:
                    p = por_pid[r["_pid"]]
                    f = r["Fecha"]
                    f = f.isoformat() if hasattr(f, "isoformat") else (str(f)[:10] if f else fecha_def.isoformat())
                    lk = str(r["Link"] or "").strip()
                    nuevos.append({"id": r["_pid"], "tipo": "publicacion", "titulo": p["titulo"], "empresa": r["Autor"] or p["autor"],
                                   "lugar": "", "fecha": f, "url": "" if lk == "nan" else lk, "perfil": p["perfil"], "links": p["links"], "oferta": p.get("oferta", ""),
                                   "desc": p["texto"], "emails": p["emails"]})
                n_add = integrar(data, nuevos)
                repuntuar(data, cv, skills_sel, anios, extras_sc)
                guardar(data)
                st.session_state["cvm_paste_n"] = n_p + 1  # vacía el cuadro de texto y los archivos
                _aviso(st, f"{n_add} publicación(es) nueva(s) guardada(s)" + (f" · {len(nuevos) - n_add} ya existían (links completados si faltaban)" if len(nuevos) > n_add else "")
                       + ". Revísalas en «Resultados» → Mostrar: Publicaciones.")
                st.rerun()
