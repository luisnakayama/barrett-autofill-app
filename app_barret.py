# app_barrett.py
import re
import json
import os
import io
import shutil
import traceback
import streamlit as st
from PIL import Image, ImageOps, ImageFilter
import pytesseract
from pdf2image import convert_from_bytes

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.chrome.service import Service as ChromeService

st.set_page_config(page_title="Barrett AutoFill (PDF → OCR → Selenium)", layout="wide")
st.title("Barrett AutoFill: OCR do exame + Preenchimento Automático")
st.write("1) Faça upload do PDF da biometria. 2) Confira/edite os campos. 3) Ao escolher uma LIO, a calculadora roda automaticamente. Use Recalcular se ajustar valores.")

# =========================
# IOL PRESETS
# =========================
IOL_PRESETS = [
    {"label": "— selecionar —", "a_constant": "", "lens_factor": ""},
    {"label": "Alcon SN60WF", "a_constant": "118.99", "lens_factor": "1.88"},
    {"label": "Alcon SN6AD", "a_constant": "119.01", "lens_factor": "1.89"},
    {"label": "Alcon SN6ATx", "a_constant": "119.26", "lens_factor": "2.02"},
    {"label": "Alcon SND1Tx", "a_constant": "119.36", "lens_factor": "2.07"},
    {"label": "Alcon SV25Tx", "a_constant": "119.51", "lens_factor": "2.15"},
    {"label": "Alcon TFNTx", "a_constant": "119.26", "lens_factor": "2.02"},
    {"label": "Alcon DFTx", "a_constant": "119.15", "lens_factor": "1.96"},
    {"label": "Alcon SA60AT", "a_constant": "118.53", "lens_factor": "1.64"},
    {"label": "Alcon MN60MA", "a_constant": "119.2", "lens_factor": "1.99"},
    {"label": "Rayner RayOne EMV", "a_constant": "118.29", "lens_factor": "1.51"},
    {"label": "J&J ZCB00", "a_constant": "119.39", "lens_factor": "2.09"},
    {"label": "J&J ZCT", "a_constant": "119.39", "lens_factor": "2.09"},
    {"label": "J&J ZCT(USA)", "a_constant": "119.39", "lens_factor": "2.09"},
    {"label": "J&J ZCU", "a_constant": "119.39", "lens_factor": "2.09"},
    {"label": "J&J DIU", "a_constant": "119.39", "lens_factor": "2.09"},
    {"label": "J&J ZKU", "a_constant": "119.39", "lens_factor": "2.09"},
    {"label": "J&J ZLU", "a_constant": "119.39", "lens_factor": "2.09"},
    {"label": "J&J AR40e", "a_constant": "118.71", "lens_factor": "1.73"},
    {"label": "J&J AR40M", "a_constant": "118.71", "lens_factor": "1.73"},
    {"label": "J&J ZXR00", "a_constant": "119.39", "lens_factor": "2.09"},
    {"label": "J&J ZXT", "a_constant": "119.39", "lens_factor": "2.09"},
    {"label": "J&J ZHR00V", "a_constant": "119.39", "lens_factor": "2.09"},
    {"label": "J&J ZHW", "a_constant": "119.39", "lens_factor": "2.09"},
    {"label": "Zeiss 409M", "a_constant": "118.32", "lens_factor": "1.53"},
    {"label": "Zeiss 709M", "a_constant": "118.5", "lens_factor": "1.62"},
    {"label": "Hoya iSert 251", "a_constant": "118.48", "lens_factor": "1.61"},
    {"label": "Hoya iSert 351", "a_constant": "118.48", "lens_factor": "1.61"},
    {"label": "Bausch & Lomb MX60", "a_constant": "119.15", "lens_factor": "1.96"},
    {"label": "Bausch & Lomb MX60T", "a_constant": "119.15", "lens_factor": "1.96"},
    {"label": "Bausch & Lomb MX60ET", "a_constant": "119.15", "lens_factor": "1.96"},
    {"label": "Bausch & Lomb MX60ET(USA)", "a_constant": "119.15", "lens_factor": "1.96"},
    {"label": "Bausch & Lomb BL1UT", "a_constant": "119.2", "lens_factor": "1.99"},
    {"label": "Bausch & Lomb LI60AO", "a_constant": "118.57", "lens_factor": "1.66"},
    {"label": "MBI T302A", "a_constant": "118.65", "lens_factor": "1.7"},
    {"label": "Lenstec SBL-3", "a_constant": "117.77", "lens_factor": "1.24"},
    {"label": "SIFI Mini WELL", "a_constant": "118.74", "lens_factor": "1.75"},
    {"label": "Ophtec 565", "a_constant": "118.48", "lens_factor": "1.61"},
]
PRESET_BY_LABEL = {p["label"]: p for p in IOL_PRESETS}

# =========================
# Utilitários / OCR
# =========================
def _to_f(s: str) -> float:
    return float(str(s).replace(",", ".").strip())

def ocr_top_header_get_text(imagem_pil, top_ratio: float = 0.22, lang: str = "por+eng") -> str:
    w, h = imagem_pil.size
    top_h = int(h * top_ratio)
    header = imagem_pil.crop((0, 0, w, top_h))
    return pytesseract.image_to_string(header, lang=lang, config="--psm 6")

def _normalize(txt: str) -> str:
    return re.sub(r"[ \t]", " ", txt).replace(",", ".")  # normaliza vírgula decimal e NBSP

def _parse_eye_text(txt: str) -> dict:
    """Busca AL/K1/K2/ACD com sinônimos PT/EN e múltiplos formatos."""
    T = _normalize(txt)

    al = None
    for pat in [
        r"Comp\.?\s*AL\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"\bAL\b\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"Axial\s*Length\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"Compr(?:imento)?\s*Axial\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
    ]:
        m = re.search(pat, T, re.IGNORECASE)
        if m:
            al = _to_f(m.group(1)); break

    k1 = k2 = None
    for pat in [
        r"K1\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:D)?\b.*?K2\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"(?:MV|K)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)",
        r"K\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)",
    ]:
        m = re.search(pat, T, re.IGNORECASE | re.DOTALL)
        if m:
            k1 = _to_f(m.group(1)); k2 = _to_f(m.group(2)); break

    acd = None
    for pat in [
        r"\bACD\b\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"(?:C[âa]mara|Camara)\s+Anterior.*?([0-9]+(?:\.[0-9]+)?)\s*mm",
        r"Anterior\s*Chamber\s*Depth\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"Profundidade.*Anterior.*?([0-9]+(?:\.[0-9]+)?)",
    ]:
        m = re.search(pat, T, re.IGNORECASE | re.DOTALL)
        if m:
            acd = _to_f(m.group(1)); break

    return {"AL": al, "K1": k1, "K2": k2, "ACD": acd}

def extrair_biometria_dupla_por_metades(paginas, lang: str = "por+eng", preprocess=None, psm="6"):
    """1ª página em duas metades: esq=OD, dir=OS."""
    if not paginas:
        return {}, "", "", None, None
    img = paginas[0]
    w, h = img.size
    mid = w // 2
    left = img.crop((0, 0, mid, h))
    right = img.crop((mid, 0, w, h))
    if preprocess:
        left_pp = preprocess(left)
        right_pp = preprocess(right)
    else:
        left_pp, right_pp = left, right
    txt_left = pytesseract.image_to_string(left_pp, lang=lang, config=f"--psm {psm}")
    txt_right = pytesseract.image_to_string(right_pp, lang=lang, config=f"--psm {psm}")
    od = _parse_eye_text(txt_left)
    os_ = _parse_eye_text(txt_right)
    ok = all(v is not None for v in [od["AL"], od["K1"], od["K2"], od["ACD"],
                                     os_["AL"], os_["K1"], os_["K2"], os_["ACD"]])
    return ({"OD": od, "OS": os_} if ok else {}), txt_left, txt_right, left_pp, right_pp

def extrair_biometria_regex_global(paginas, lang: str = "por+eng", preprocess=None, psm="6"):
    """OCR da página inteira e separa por marcadores OD/OS (O.D./O.S./Right/Left)."""
    if not paginas:
        return {}, ""
    img = paginas[0]
    img_pp = preprocess(img) if preprocess else img
    full_txt = pytesseract.image_to_string(img_pp, lang=lang, config=f"--psm {psm}")
    T = _normalize(full_txt)

    # Quebra bruto em trechos próximos de OD/OS
    def _grab_near(token):
        m = re.search(token, T, re.IGNORECASE)
        if not m:
            return ""
        start = max(0, m.start() - 250)
        end = min(len(T), m.end() + 250)
        return T[start:end]

    od_chunk = _grab_near(r"\bOD\b|O\.D\.|Right\b|Direito\b")
    os_chunk = _grab_near(r"\bOS\b|O\.S\.|Left\b|Esquerdo\b")

    od = _parse_eye_text(od_chunk) if od_chunk else {"AL": None, "K1": None, "K2": None, "ACD": None}
    os_ = _parse_eye_text(os_chunk) if os_chunk else {"AL": None, "K1": None, "K2": None, "ACD": None}

    ok = all(v is not None for v in [od["AL"], od["K1"], od["K2"], od["ACD"],
                                     os_["AL"], os_["K1"], os_["K2"], os_["ACD"]])
    return ({"OD": od, "OS": os_} if ok else {}), full_txt

# =========================
# Sidebar: diagnóstico e parâmetros
# =========================
def _env_diag():
    try:
        pdftoppm = shutil.which("pdftoppm")
        tesseract_bin = shutil.which("tesseract")
        st.sidebar.markdown("### Diagnóstico do ambiente")
        st.sidebar.write(f"pdftoppm: {'OK' if pdftoppm else 'NÃO ENCONTRADO'}")
        st.sidebar.write(f"tesseract: {'OK' if tesseract_bin else 'NÃO ENCONTRADO'}")
        st.sidebar.caption("Se aparecer 'NÃO ENCONTRADO', inclua em packages.txt: `poppler-utils` e `tesseract-ocr`.")
    except Exception:
        pass

_env_diag()
st.sidebar.markdown("---")
dpi = st.sidebar.slider("DPI de conversão do PDF", 200, 500, 320, step=20)
use_grayscale = st.sidebar.checkbox("Pré-processar: tons de cinza + autocontraste + sharpen", value=True)
psm = st.sidebar.selectbox("Tesseract PSM", ["6", "4", "11", "3"], index=0,
                           help="6 (parágrafos), 11 (linha única); experimente se o OCR vier ruim.")
show_debug = st.sidebar.checkbox("Mostrar OCR bruto (debug)", value=False)
layout_mode = st.sidebar.radio("Modo de extração", ["Metades (OD esquerda / OS direita)", "Global (regex)"], index=0)

def preprocess_for_ocr(img: Image.Image) -> Image.Image:
    if not use_grayscale:
        return img.convert("RGB")
    im = img.convert("L")
    im = ImageOps.autocontrast(im, cutoff=2)
    im = im.filter(ImageFilter.UnsharpMask(radius=1.4, percent=150, threshold=3))
    return im

# =========================
# Upload do PDF
# =========================
MAX_MB = 80
arquivo = st.file_uploader("Upload do PDF do exame", type=["pdf"], accept_multiple_files=False)

if "pdf_bytes" not in st.session_state:
    st.session_state.pdf_bytes = None
if "pdf_name" not in st.session_state:
    st.session_state.pdf_name = None

texto_topo = ""
paginas = []
img_preview = None

if arquivo is not None:
    st.caption(f"📄 Arquivo: **{arquivo.name}** | MIME: `{arquivo.type}` | Tamanho: {arquivo.size/1_048_576:.2f} MB")
    if arquivo.type not in {"application/pdf", "application/x-pdf", "application/acrobat"}:
        st.error("O arquivo não parece ser um PDF válido.")
        st.stop()
    if arquivo.size > MAX_MB * 1024 * 1024:
        st.error(f"PDF maior que {MAX_MB} MB. Envie um arquivo menor.")
        st.stop()
    try:
        pdf_bytes = arquivo.getvalue()
        if not pdf_bytes:
            st.error("Não consegui ler os bytes do PDF (arquivo vazio?).")
            st.stop()
        st.session_state.pdf_bytes = pdf_bytes
        st.session_state.pdf_name = arquivo.name
    except Exception as e:
        st.error("Falha ao carregar bytes do PDF.")
        st.code(''.join(traceback.format_exception(None, e, e.__traceback__)))
        st.stop()

# Converte só a 1ª página e força RGB/PNG
if st.session_state.pdf_bytes:
    try:
        # fmt='png' evita algumas falhas de renderização em PPM
        paginas = convert_from_bytes(
            st.session_state.pdf_bytes,
            dpi=int(dpi),
            first_page=1,
            last_page=1,
            fmt="png"
        )
    except Exception as e:
        st.error("Erro ao converter PDF em imagem (precisa de 'poppler-utils').")
        st.code(''.join(traceback.format_exception(None, e, e.__traceback__)))
        paginas = []

    if paginas:
        try:
            base = paginas[0].convert("RGB")
            img_preview = base.copy()
            img_preview.thumbnail((1100, 1100))
        except Exception as e:
            img_preview = None
            st.warning("Falha ao preparar a prévia da imagem.")
            st.code(''.join(traceback.format_exception(None, e, e.__traceback__)))

# =========================
# OCR & Extrações
# =========================
dados = {}
txt_left = txt_right = full_txt = ""
left_pp = right_pp = None

if paginas:
    # Header para nome do paciente (usa página sem cortes)
    try:
        texto_topo = ocr_top_header_get_text(preprocess_for_ocr(paginas[0]), top_ratio=0.22, lang="por+eng")
    except Exception:
        texto_topo = ""

    # Modo 1: metades
    if layout_mode.startswith("Metades"):
        dados, txt_left, txt_right, left_pp, right_pp = extrair_biometria_dupla_por_metades(
            paginas, lang="por+eng", preprocess=preprocess_for_ocr, psm=psm
        )
        # Fallback automático para modo global se falhar algo
        if not dados:
            dados, full_txt = extrair_biometria_regex_global(
                paginas, lang="por+eng", preprocess=preprocess_for_ocr, psm=psm
            )
    # Modo 2: global direto
    else:
        dados, full_txt = extrair_biometria_regex_global(
            paginas, lang="por+eng", preprocess=preprocess_for_ocr, psm=psm
        )
        # Fallback para metades
        if not dados:
            dados, txt_left, txt_right, left_pp, right_pp = extrair_biometria_dupla_por_metades(
                paginas, lang="por+eng", preprocess=preprocess_for_ocr, psm=psm
            )

patient_detected = ""
if texto_topo:
    try:
        def extrair_patient_name_do_header(texto_header: str):
            blacklist = [
                "report date","biometria","cálculo iol","page","id:","dob:","gender:",
                "r. ","av. ","rua ","tel","cep","http","www","e-mail","email",
                "printing images","admin/","instituto","hospital"
            ]
            linhas = [ln.strip() for ln in texto_header.splitlines() if ln.strip()]
            for ln in linhas:
                low = ln.lower()
                if any(b in low for b in blacklist):
                    continue
                candidato = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ' \-\.]", "", ln).strip()
                if len(candidato.split()) >= 2 and 2 <= len(candidato) <= 80:
                    return candidato
            return ""
        patient_detected = extrair_patient_name_do_header(texto_topo)
    except Exception:
        patient_detected = ""

st.divider()

# Debug opcional
if show_debug and paginas:
    st.subheader("Debug OCR")
    if img_preview is not None:
        st.text(f"Prévia: {img_preview.size} | mode={img_preview.mode} | DPI={dpi}")
    if txt_left:
        st.text_area("OCR (metade esquerda / OD)", txt_left, height=150)
    if txt_right:
        st.text_area("OCR (metade direita / OS)", txt_right, height=150)
    if full_txt:
        st.text_area("OCR (página inteira)", full_txt, height=200)

# =============== Estado global (para auto-execução) ===============
if "selected_iol" not in st.session_state:
    st.session_state.selected_iol = "— selecionar —"
if "a_constant_val" not in st.session_state:
    st.session_state.a_constant_val = ""
if "lens_factor_val" not in st.session_state:
    st.session_state.lens_factor_val = ""
if "tables" not in st.session_state:
    st.session_state.tables = None
if "used_browser" not in st.session_state:
    st.session_state.used_browser = None
if "auto_run" not in st.session_state:
    st.session_state.auto_run = False

def on_iol_change():
    preset = PRESET_BY_LABEL.get(st.session_state.selected_iol, {"a_constant": "", "lens_factor": ""})
    st.session_state.a_constant_val = preset.get("a_constant", "") or ""
    st.session_state.lens_factor_val = preset.get("lens_factor", "") or ""
    st.session_state.auto_run = (st.session_state.selected_iol != "— selecionar —")

def on_ac_input_change():
    st.session_state.a_constant_val = st.session_state.get("ac_input", "").strip()
    if st.session_state.a_constant_val:
        st.session_state.selected_iol = st.session_state.selected_iol or "— selecionar —"
        st.session_state.auto_run = True

def on_lf_input_change():
    st.session_state.lens_factor_val = st.session_state.get("lf_input", "").strip()
    if st.session_state.lens_factor_val:
        st.session_state.selected_iol = st.session_state.selected_iol or "— selecionar —"
        st.session_state.auto_run = True

# =========================
# UI principal
# =========================
if st.session_state.pdf_bytes is None:
    st.info("Faça o upload do PDF para extrair os dados.")
else:
    col_preview, col_form = st.columns([1, 1.2], gap="large")

    with col_preview:
        if img_preview is not None:
            try:
                st.image(img_preview, caption="Prévia da 1ª página do PDF", use_container_width=True)
            except Exception as e:
                st.warning("Não consegui renderizar a prévia da imagem.")
                st.code(''.join(traceback.format_exception(None, e, e.__traceback__)))
        else:
            st.info("Sem prévia disponível. Aumente o DPI na barra lateral e tente novamente.")

    with col_form:
        if not dados:
            st.warning("Não consegui extrair automaticamente. Ajuste o DPI/PSM ou troque o modo de extração (barra lateral).")
            dados = {"OD": {"AL": 23.50, "K1": 43.50, "K2": 44.00, "ACD": 3.20},
                     "OS": {"AL": 23.60, "K1": 43.80, "K2": 44.05, "ACD": 3.30}}

        st.subheader("Verifique e edite os dados")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("OD (Right)")
            al_od = st.number_input("AL (OD, mm)", value=float(dados["OD"]["AL"]), format="%.2f")
            k1_od = st.number_input("K1 (OD, D)", value=float(dados["OD"]["K1"]), format="%.2f")
            k2_od = st.number_input("K2 (OD, D)", value=float(dados["OD"]["K2"]), format="%.2f")
            acd_od = st.number_input("ACD (OD, mm)", value=float(dados["OD"]["ACD"]), format="%.2f")
        with c2:
            st.markdown("OS (Left)")
            al_os = st.number_input("AL (OS, mm)", value=float(dados["OS"]["AL"]), format="%.2f")
            k1_os = st.number_input("K1 (OS, D)", value=float(dados["OS"]["K1"]), format="%.2f")
            k2_os = st.number_input("K2 (OS, D)", value=float(dados["OS"]["K2"]), format="%.2f")
            acd_os = st.number_input("ACD (OS, mm)", value=float(dados["OS"]["ACD"]), format="%.2f")

        st.subheader("Identificação")
        colid1, colid2 = st.columns(2)
        with colid1:
            doctor_name = st.text_input("Doctor Name", value="Luis")
        with colid2:
            patient_name = st.text_input("Patient Name (obrigatório)", value=patient_detected or "AutoFill")

        st.subheader("Constante da Lente")
        const_tipo = st.radio(
            "Escolha como quer preencher",
            ["Lens Factor", "A-constant"],
            index=1,
            horizontal=True,
            key="const_tipo_radio",
        )

        labels = [p["label"] for p in IOL_PRESETS]
        st.selectbox(
            "Modelo de LIO (opcional)",
            labels,
            index=labels.index(st.session_state.selected_iol) if st.session_state.selected_iol in labels else 0,
            help="Se escolher um modelo, aplico a constante correspondente e executo a calculadora.",
            key="selected_iol",
            on_change=on_iol_change,
        )

        if const_tipo == "Lens Factor":
            lens_factor = st.text_input(
                "Lens Factor (ex.: 2.00; -2.0 a 5.0)",
                value=st.session_state.lens_factor_val,
                key="lf_input",
                on_change=on_lf_input_change,
            )
            a_constant = st.text_input(
                "A-constant (ex.: 119.0; 112 a 124.7)",
                value=st.session_state.a_constant_val,
                key="ac_input_disabled",
                disabled=True
            )
        else:
            a_constant = st.text_input(
                "A-constant (ex.: 119.0; 112 a 124.7)",
                value=st.session_state.a_constant_val,
                key="ac_input",
                on_change=on_ac_input_change,
            )
            lens_factor = st.text_input(
                "Lens Factor (ex.: 2.00; -2.0 a 5.0)",
                value=st.session_state.lens_factor_val,
                key="lf_input_disabled",
                disabled=True
            )

        st.markdown("[Abrir calculadora Barrett](https://calc.apacrs.org/barrett_universal2105/)", unsafe_allow_html=True)

st.divider()

# ======= Execução Selenium =======
st.subheader("Execução")
headless = st.checkbox("Executar em modo headless (sem abrir janela)", value=True)
nav_choice = st.radio("Navegador", ["Firefox", "Chrome"], index=0, horizontal=True)

def parse_table_rows(table_el):
    rows = table_el.find_elements(By.TAG_NAME, "tr")
    out = []
    for r in rows[1:]:
        tds = r.find_elements(By.TAG_NAME, "td")
        if len(tds) >= 3:
            out.append({
                "IOL Power": tds[0].text.strip(),
                "Optic": tds[1].text.strip(),
                "Refraction": tds[2].text.strip()
            })
    return out

def build_firefox(headless_flag: bool):
    opts = webdriver.FirefoxOptions()
    if headless_flag:
        opts.add_argument("-headless")
    service = FirefoxService()
    return webdriver.Firefox(service=service, options=opts)

def build_chrome(headless_flag: bool):
    opts = webdriver.ChromeOptions()
    if headless_flag:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,1000")
    opts.add_argument("--disable-software-rasterizer")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--remote-allow-origins=*")
    for var in ["WEBDRIVER_CHROME_DRIVER", "webdriver.chrome.driver", "CHROMEDRIVER", "CHROMEWEBDRIVER"]:
        if var in os.environ:
            del os.environ[var]
    old_path = os.environ.get("PATH", "")
    parts = old_path.split(os.pathsep)
    filtered = []
    for p in parts:
        try:
            found = shutil.which("chromedriver", path=p)
        except Exception:
            found = None
        if not found:
            filtered.append(p)
    try:
        os.environ["PATH"] = os.pathsep.join(filtered)
        service = ChromeService()
        return webdriver.Chrome(service=service, options=opts)
    finally:
        os.environ["PATH"] = old_path

def run_selenium_and_fetch(preferred: str):
    last_error = None
    order = [preferred] + (["Firefox", "Chrome"] if preferred == "Chrome" else ["Chrome"])
    for choice in order:
        try:
            driver = build_firefox(headless) if choice == "Firefox" else build_chrome(headless)
            wait = WebDriverWait(driver, 30)

            driver.get("https://calc.apacrs.org/barrett_universal2105/")

            def fill_by_id(elem_id, value):
                el = wait.until(EC.presence_of_element_located((By.ID, elem_id)))
                el.clear()
                el.send_keys(str(value))

            # Identificação
            fill_by_id("MainContent_DoctorName", st.session_state.get("doctor_name_val", "Luis"))
            fill_by_id("MainContent_PatientName", st.session_state.get("patient_name_val", "AutoFill"))

            # OD
            fill_by_id("MainContent_Axlength", al_od)
            fill_by_id("MainContent_MeasuredK1", k1_od)
            fill_by_id("MainContent_MeasuredK2", k2_od)
            fill_by_id("MainContent_OpticalACD", acd_od)

            # OS
            fill_by_id("MainContent_Axlength0", al_os)
            fill_by_id("MainContent_MeasuredK10", k1_os)
            fill_by_id("MainContent_MeasuredK20", k2_os)
            fill_by_id("MainContent_OpticalACD0", acd_os)

            # Modelo de LIO (se houver)
            if st.session_state.get("selected_iol") and st.session_state["selected_iol"] != "— selecionar —":
                try:
                    sel_el = wait.until(EC.presence_of_element_located((By.ID, "MainContent_IOLModel")))
                    Select(sel_el).select_by_visible_text(st.session_state["selected_iol"])
                    WebDriverWait(driver, 6).until(EC.staleness_of(sel_el))
                except Exception:
                    pass

            # Constantes manuais (sobrescrevem)
            if const_tipo == "Lens Factor" and st.session_state.get("lens_factor_val", "").strip():
                fill_by_id("MainContent_LensFactor", st.session_state["lens_factor_val"].strip())
            if const_tipo == "A-constant" and st.session_state.get("a_constant_val", "").strip():
                fill_by_id("MainContent_Aconstant", st.session_state["a_constant_val"].strip())

            # Calcular
            wait.until(EC.element_to_be_clickable((By.ID, "MainContent_Button1"))).click()
            driver.execute_script("__doPostBack('ctl00$MainContent$menuTabs','1');")

            # Tabelas
            wait.until(EC.presence_of_element_located((By.ID, "MainContent_Panel14")))
            grid_od = driver.find_element(By.ID, "MainContent_GridView1")
            grid_os = driver.find_element(By.ID, "MainContent_GridView2")
            table_od = parse_table_rows(grid_od)
            table_os = parse_table_rows(grid_os)

            try:
                driver.quit()
            except Exception:
                pass

            return {"OD": table_od, "OS": table_os}, choice

        except Exception as e:
            last_error = e
            try:
                driver.quit()
            except Exception:
                pass
            continue
    raise last_error or RuntimeError("Falha ao iniciar navegador")

# --------- Auto-execução ---------
if st.session_state.get("auto_run"):
    st.session_state.auto_run = False
    with st.status("Executando calculadora...", expanded=False):
        try:
            tables, used = run_selenium_and_fetch(nav_choice)
            st.session_state.tables = tables
            st.session_state.used_browser = used
        except Exception as e:
            st.error(f"Erro ao executar Selenium: {e}")
            st.code(''.join(traceback.format_exception(None, e, e.__traceback__)))

# Persistir nomes
st.session_state["doctor_name_val"] = locals().get("doctor_name", st.session_state.get("doctor_name_val", "Luis"))
st.session_state["patient_name_val"] = locals().get("patient_name", st.session_state.get("patient_name_val", "AutoFill"))

# Botão Recalcular
if st.button("Recalcular"):
    with st.status("Executando calculadora...", expanded=False):
        try:
            tables, used = run_selenium_and_fetch(nav_choice)
            st.session_state.tables = tables
            st.session_state.used_browser = used
        except Exception as e:
            st.error(f"Erro ao executar Selenium: {e}")
            st.code(''.join(traceback.format_exception(None, e, e.__traceback__)))

# Exibição das tabelas importadas
if st.session_state.get("tables"):
    st.success(f"Tabelas importadas com sucesso (navegador: {st.session_state.get('used_browser')}).")
    colod, colos = st.columns(2)
    with colod:
        st.subheader("Sugestões (OD)")
        if st.session_state.tables["OD"]:
            st.dataframe(st.session_state.tables["OD"], use_container_width=True)
        else:
            st.info("Sem linhas em OD.")
    with colos:
        st.subheader("Sugestões (OS)")
        if st.session_state.tables["OS"]:
            st.dataframe(st.session_state.tables["OS"], use_container_width=True)
        else:
            st.info("Sem linhas em OS.")
