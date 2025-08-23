# app_barrett.py
import re
import json
import os
import io
import shutil
import inspect
import streamlit as st
from PIL import Image, ImageFilter
import pytesseract
from pdf2image import convert_from_bytes

# PyMuPDF (opcional, para render melhor e extrair texto)
try:
    import fitz  # pymupdf
    HAS_PYMUPDF = True
except Exception:
    HAS_PYMUPDF = False

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.chrome.service import Service as ChromeService

st.set_page_config(page_title="Barrett AutoFill (PDF → OCR → Selenium)", layout="wide")
st.title("Barrett AutoFill: OCR do exame + Preenchimento Automático")
st.write("1) Faça upload do PDF da biometria. 2) Confira/edite os campos. 3) Ao escolher uma LIO ou editar a constante, a calculadora roda automaticamente.")

# ========= Helper de compatibilidade para st.image (Opção B) =========
def _img_kwargs():
    params = inspect.signature(st.image).parameters
    if "use_container_width" in params:
        return dict(use_container_width=True)
    else:
        return dict(use_column_width=True)

# =========================
# IOL PRESETS (scrape consolidado)
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
# Utilitários / OCR & parsing
# =========================
def _to_f(s: str) -> float:
    return float(str(s).replace(",", ".").strip())

def _preprocess(im, do_binarize=True, unsharp_percent=80):
    im2 = im.convert("L")
    if do_binarize:
        im2 = im2.point(lambda p: 255 if p > 200 else 0)
    if unsharp_percent and unsharp_percent > 0:
        im2 = im2.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(unsharp_percent), threshold=2))
    return im2

def _ocr(im, lang="por+eng", oem="3", psm="6"):
    cfg = f"--oem {oem} --psm {psm}"
    return pytesseract.image_to_string(im, lang=lang, config=cfg), cfg

def ocr_top_header_get_text(imagem_pil, top_ratio: float = 0.22, lang: str = "por+eng", oem="3", psm="6") -> str:
    w, h = imagem_pil.size
    top_h = int(h * top_ratio)
    header = imagem_pil.crop((0, 0, w, top_h))
    header = _preprocess(header, do_binarize=True, unsharp_percent=60)
    txt, _ = _ocr(header, lang=lang, oem=oem, psm=psm)
    return txt

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

def _parse_eye_text(txt: str) -> dict:
    """
    Extrai AL / K1 / K2 / ACD com padrões abrangentes.
    Aceita variantes:
      - AL: "AL", "Axial Length", "Comprimento Axial", "Comp. AL"
      - K: "K1/K2", "R1/R2", "SimK", "Km", "K: xx/yy"
      - ACD: "ACD", "ACDopt", "Câmara Anterior"
    """
    t = txt.replace(",", ".")
    t = re.sub(r"Comp[,.\s]*AL", "Comp. AL", t, flags=re.IGNORECASE)

    m_al = re.search(r"(?:\bAL\b|Axial\s*Length|Comprimento\s*Axial|Comp\.\s*AL)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", t, flags=re.IGNORECASE)
    al = float(m_al.group(1)) if m_al else None

    k1 = k2 = None
    m_k1 = re.search(r"\bK1\b\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", t, flags=re.IGNORECASE)
    m_k2 = re.search(r"\bK2\b\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", t, flags=re.IGNORECASE)
    if m_k1 and m_k2:
        k1 = float(m_k1.group(1)); k2 = float(m_k2.group(1))

    if k1 is None or k2 is None:
        m_div = re.search(r"\b(?:K|SimK|Km)\b\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)", t, flags=re.IGNORECASE)
        if m_div:
            k1 = float(m_div.group(1)); k2 = float(m_div.group(2))

    if k1 is None or k2 is None:
        m_r1 = re.search(r"\bR1\b\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", t, flags=re.IGNORECASE)
        m_r2 = re.search(r"\bR2\b\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", t, flags=re.IGNORECASE)
        if m_r1 and m_r2:
            k1 = float(m_r1.group(1)); k2 = float(m_r2.group(1))

    m_acd = re.search(r"(?:\bACD\b|ACDopt|C[âa]mara\s+Anterior)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", t, flags=re.IGNORECASE)
    acd = float(m_acd.group(1)) if m_acd else None

    return {"AL": al, "K1": k1, "K2": k2, "ACD": acd}

def _extract_both_from_text(txt: str):
    od = {"AL": None, "K1": None, "K2": None, "ACD": None}
    os_ = {"AL": None, "K1": None, "K2": None, "ACD": None}
    parts = re.split(r'\b(OD|OE|OS|Right|Left)\b', txt, flags=re.IGNORECASE)
    if len(parts) >= 4:
        pairs = [(parts[i].upper(), parts[i+1]) for i in range(1, len(parts)-1, 2)]
        for label, segment in pairs:
            parsed = _parse_eye_text(segment)
            if "OD" in label or "RIGHT" in label:
                od = {**od, **{k: v for k, v in parsed.items() if v is not None}}
            if "OS" in label or "LEFT" in label or "OE" in label:
                os_ = {**os_, **{k: v for k, v in parsed.items() if v is not None}}
    else:
        parsed = _parse_eye_text(txt)
        od = {**od, **{k: v for k, v in parsed.items() if v is not None}}
        os_ = {**os_, **{k: v for k, v in parsed.items() if v is not None}}
    return {"OD": od, "OS": os_}

def extrair_biometria_dupla_por_metades(img_page, lang: str = "por+eng", oem="3", psm="6", do_binarize=True, unsharp=80, show_debug=True):
    """OCR por metades; se falhar, tenta página inteira."""
    if img_page is None:
        return {}
    w, h = img_page.size
    mid = w // 2
    left = img_page.crop((0, 0, mid, h))
    right = img_page.crop((mid, 0, w, h))

    left_pp = _preprocess(left, do_binarize, unsharp)
    right_pp = _preprocess(right, do_binarize, unsharp)

    txt_left, cfg_l = _ocr(left_pp, lang=lang, oem=oem, psm=psm)
    txt_right, cfg_r = _ocr(right_pp, lang=lang, oem=oem, psm=psm)

    if show_debug:
        with st.expander("🔎 OCR bruto - OD (esquerda)"):
            st.caption(f"Config usada: `--oem {oem} --psm {psm}`")
            st.code(txt_left)
        with st.expander("🔎 OCR bruto - OS (direita)"):
            st.caption(f"Config usada: `--oem {oem} --psm {psm}`")
            st.code(txt_right)

    od = _parse_eye_text(txt_left)
    os_ = _parse_eye_text(txt_right)

    # fallback com página inteira
    if not all(od.values()) or not all(os_.values()):
        full_pp = _preprocess(img_page, do_binarize, unsharp)
        full_txt, _ = _ocr(full_pp, lang=lang, oem=oem, psm=psm)
        if show_debug:
            with st.expander("🔁 OCR bruto - Página inteira (fallback)"):
                st.code(full_txt)
        merge = _extract_both_from_text(full_txt)
        od = {**od, **{k: v for k, v in merge["OD"].items() if v is not None}}
        os_ = {**os_, **{k: v for k, v in merge["OS"].items() if v is not None}}

    return {"OD": od, "OS": os_}

# =========================
# Upload do PDF + diagnósticos
# =========================
def _env_diag():
    try:
        pdftoppm = shutil.which("pdftoppm")
        tesseract_bin = shutil.which("tesseract")
        st.sidebar.markdown("### Diagnóstico do ambiente")
        st.sidebar.write(f"pdftoppm: {'OK' if pdftoppm else 'NÃO ENCONTRADO'}")
        st.sidebar.write(f"tesseract: {'OK' if tesseract_bin else 'NÃO ENCONTRADO'}")
        st.sidebar.write(f"PyMuPDF: {'OK' if HAS_PYMUPDF else 'NÃO INSTALADO'}")
        st.sidebar.caption("Se aparecer 'NÃO ENCONTRADO', adicione em packages.txt: `poppler-utils` e/ou `tesseract-ocr`. Para PyMuPDF, adicione em requirements.txt: `pymupdf`.")
    except Exception:
        pass

_env_diag()

# ---- Controles de qualidade/OCR (sidebar) ----
st.sidebar.markdown("### Qualidade / OCR")
render_method = st.sidebar.selectbox(
    "Render do PDF", 
    (["PyMuPDF (recomendado)"] if HAS_PYMUPDF else []) + ["pdf2image/Poppler"],
    index=0 if HAS_PYMUPDF else 0
)
zoom = st.sidebar.slider("Zoom (PyMuPDF)", 1.0, 4.0, 2.0, 0.25)
dpi = st.sidebar.slider("DPI (Poppler)", 200, 600, 300, 50)
binarize = st.sidebar.checkbox("Binarizar (preto/branco)", True)
unsharp = st.sidebar.slider("Sharpen (UnsharpMask %)", 0, 200, 80, 5)
psm = st.sidebar.selectbox("Tesseract PSM", ["6", "4", "3", "11"], index=0)
oem = st.sidebar.selectbox("Tesseract OEM", ["3", "1"], index=0)
top_ratio = st.sidebar.slider("Corte do topo p/ nome (%)", 10, 40, 22, 1) / 100.0

MAX_MB = 80
arquivo = st.file_uploader("Upload do PDF do exame", type=["pdf"], accept_multiple_files=False)

# Estados de sessão para bytes do PDF
if "pdf_bytes" not in st.session_state:
    st.session_state.pdf_bytes = None
if "pdf_name" not in st.session_state:
    st.session_state.pdf_name = None

texto_topo = ""
page_img = None
img_preview = None

if arquivo is not None:
    st.caption(f"📄 Arquivo: **{arquivo.name}** | MIME: `{arquivo.type}` | Tamanho: {arquivo.size/1_048_576:.2f} MB")

    if arquivo.type not in {"application/pdf", "application/x-pdf", "application/acrobat"}:
        st.error("O arquivo não parece ser um PDF válido. Tente outro.")
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
        with st.expander("Detalhes técnicos (upload)"):
            st.exception(e)
        st.stop()

# ---- Renderizar primeira página (PyMuPDF ou Poppler) ----
def _load_first_page_image(pdf_bytes: bytes):
    if render_method.startswith("PyMuPDF") and HAS_PYMUPDF:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            if doc.page_count == 0:
                return None
            page = doc.load_page(0)
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            im = Image.open(io.BytesIO(pix.tobytes("png")))
            return im
        except Exception as e:
            st.warning("Falha ao renderizar com PyMuPDF; tentando Poppler.")
    # fallback Poppler
    try:
        pages = convert_from_bytes(pdf_bytes, dpi=int(dpi), first_page=1, last_page=1)
        return pages[0] if pages else None
    except Exception as e:
        st.error("Erro ao converter PDF em imagem. No Streamlit Cloud, adicione 'poppler-utils' ao packages.txt.")
        with st.expander("Detalhes técnicos (pdf2image)"):
            st.exception(e)
        return None

# ---- Tentativa de extrair TEXTO nativo (sem OCR) ----
def _try_text_layer(pdf_bytes: bytes) -> str:
    if not HAS_PYMUPDF:
        return ""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.page_count == 0:
            return ""
        page = doc.load_page(0)
        txt = page.get_text("text") or ""
        return txt
    except Exception:
        return ""

# =========================
# Extrações (OCR / texto nativo)
# =========================
if st.session_state.pdf_bytes:
    page_img = _load_first_page_image(st.session_state.pdf_bytes)
    if page_img is not None:
        # Prévia (mostra original) e também a versão pré-processada no debug
        img_preview = page_img.copy()
        preproc_preview = _preprocess(page_img.copy(), do_binarize=binarize, unsharp_percent=unsharp)

        with st.expander("🖼️ Pré-processamento (visual)"):
            st.write("Esquerda: original | Direita: pré-processada")
            col_a, col_b = st.columns(2)
            with col_a:
                st.image(img_preview, caption="Original", **_img_kwargs())
            with col_b:
                st.image(preproc_preview, caption="Pré-processada p/ OCR", **_img_kwargs())

        # OCR do cabeçalho (nome)
        try:
            texto_topo = ocr_top_header_get_text(preproc_preview, top_ratio=top_ratio, lang="por+eng", oem=oem, psm=psm)
        except Exception as e:
            st.warning("Não foi possível extrair o cabeçalho (OCR).")
            with st.expander("Detalhes técnicos (OCR topo)"):
                st.exception(e)

# Mostrar a prévia simplificada (fora do expander também, se quiser)
if img_preview is not None:
    st.image(img_preview, caption="Prévia da 1ª página do PDF", **_img_kwargs())

# Tentar texto nativo (sem OCR) antes
dados = {}
if st.session_state.pdf_bytes:
    native_txt = _try_text_layer(st.session_state.pdf_bytes)
    if native_txt and len(native_txt.strip()) > 50:
        with st.expander("🔤 Texto incorporado detectado (sem OCR)"):
            st.code(native_txt[:3000])
        dados = _extract_both_from_text(native_txt)

# Se não veio suficiente do texto nativo, usa OCR robusto nas metades
if not dados or not all(dados.get(k) for k in ["OD", "OS"]):
    if page_img is not None:
        dados = extrair_biometria_dupla_por_metades(
            page_img, lang="por+eng", oem=oem, psm=psm,
            do_binarize=binarize, unsharp=unsharp, show_debug=True
        )

patient_detected = extrair_patient_name_do_header(texto_topo) if texto_topo else ""

with st.expander("🧪 Detecção atual de métricas (debug)"):
    st.json(dados)

st.divider()

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

    with col_form:
        # dados default se vazio
        if not dados or not dados.get("OD") or not dados.get("OS"):
            st.warning("Não consegui extrair tudo automaticamente. Preencha/ajuste manualmente.")
            dados = dados or {}
            dados.setdefault("OD", {"AL": 23.50, "K1": 43.50, "K2": 44.00, "ACD": 3.20})
            dados.setdefault("OS", {"AL": 23.60, "K1": 43.80, "K2": 44.05, "ACD": 3.30})

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
            index=1, horizontal=True, key="const_tipo_radio",
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

# ======= Configs de execução (Selenium) =======
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
            wait = WebDriverWait(driver, 25)

            driver.get("https://calc.apacrs.org/barrett_universal2105/")

            def fill_by_id(elem_id, value):
                el = wait.until(EC.presence_of_element_located((By.ID, elem_id)))
                el.clear()
                el.send_keys(str(value))

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

            if st.session_state.get("selected_iol") and st.session_state["selected_iol"] != "— selecionar —":
                try:
                    sel_el = wait.until(EC.presence_of_element_located((By.ID, "MainContent_IOLModel")))
                    Select(sel_el).select_by_visible_text(st.session_state["selected_iol"])
                    WebDriverWait(driver, 6).until(EC.staleness_of(sel_el))
                except Exception:
                    pass

            if st.session_state.get("const_tipo_radio") == "Lens Factor" and st.session_state.get("lens_factor_val", "").strip():
                fill_by_id("MainContent_LensFactor", st.session_state["lens_factor_val"].strip())
            if st.session_state.get("const_tipo_radio") == "A-constant" and st.session_state.get("a_constant_val", "").strip():
                fill_by_id("MainContent_Aconstant", st.session_state["a_constant_val"].strip())

            wait.until(EC.element_to_be_clickable((By.ID, "MainContent_Button1"))).click()
            driver.execute_script("__doPostBack('ctl00$MainContent$menuTabs','1');")

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

# --------- Auto-execução quando editar LIO/constantes ---------
if "auto_run" in st.session_state and st.session_state.auto_run:
    st.session_state.auto_run = False
    with st.status("Executando calculadora...", expanded=False):
        try:
            tables, used = run_selenium_and_fetch(nav_choice)
            st.session_state.tables = tables
            st.session_state.used_browser = used
        except Exception as e:
            st.error(f"Erro ao executar Selenium: {e}")

# --------- Persistir nomes para Selenium ---------
st.session_state["doctor_name_val"] = locals().get("doctor_name", st.session_state.get("doctor_name_val", "Luis"))
st.session_state["patient_name_val"] = locals().get("patient_name", st.session_state.get("patient_name_val", "AutoFill"))

# --------- Botão Recalcular ---------
if st.button("Recalcular"):
    with st.status("Executando calculadora...", expanded=False):
        try:
            tables, used = run_selenium_and_fetch(nav_choice)
            st.session_state.tables = tables
            st.session_state.used_browser = used
        except Exception as e:
            st.error(f"Erro ao executar Selenium: {e}")

# --------- Exibição das tabelas importadas ---------
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
