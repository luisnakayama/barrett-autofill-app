# app_barrett.py
import re
import os
import io
import shutil
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
st.write("1) Faça upload do PDF da biometria. 2) Confira/edite os campos. 3) Ao escolher uma LIO ou editar constantes, a calculadora roda automaticamente.")

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
# OCR helpers robustos
# =========================
OCR_DPI = 360  # aumente p/ 380–420 se necessário

NUM = r"([-+]?\d+(?:[.,]\d+)?)"

def _to_f(s: str) -> float:
    return float(str(s).replace(",", ".").strip())

def _clean_common_ocr(text: str) -> str:
    # normaliza e corrige confusões comuns do OCR
    t = text.replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = t.replace("AC0", "ACD").replace("AOD", "ACD")  # 0/O ↔ D
    t = t.replace("M V", "MV").replace("M/V", "MV")
    t = t.replace("Comp ,", "Comp.").replace("Comp ,", "Comp.").replace("Comp .", "Comp.")
    return t

# padrões tolerantes a ruído/pontuação/linhas
PAT_AL  = re.compile(r"C\s*o?\s*m\s*p\.?\s*A\s*L\s*[:=]\s*" + NUM, re.IGNORECASE)
PAT_MV  = re.compile(r"M\s*V\s*[:=]\s*" + NUM + r"\s*/\s*" + NUM, re.IGNORECASE | re.DOTALL)
PAT_ACD = re.compile(r"A\s*C\s*[D0]\s*[:=]\s*" + NUM, re.IGNORECASE)

def _parse_eye_text_exact(txt: str) -> dict:
    T = _clean_common_ocr(txt)

    al = k1 = k2 = acd = None

    m = PAT_AL.search(T)
    if m: al = _to_f(m.group(1))

    m = PAT_MV.search(T)
    if m:
        k1 = _to_f(m.group(1))
        k2 = _to_f(m.group(2))

    m = PAT_ACD.search(T)
    if m: acd = _to_f(m.group(1))

    return {"AL": al, "K1": k1, "K2": k2, "ACD": acd}

def _pp_half(img: Image.Image) -> Image.Image:
    # tons de cinza + autocontraste + sharpen + upscaling ajuda no OCR
    im = img.convert("L")
    im = ImageOps.autocontrast(im, cutoff=2)
    im = im.filter(ImageFilter.UnsharpMask(radius=1.3, percent=160, threshold=3))
    w, h = im.size
    im = im.resize((int(w*1.6), int(h*1.6)), Image.LANCZOS)
    return im

def _tess(page_img: Image.Image, lang="por+eng", psm="6") -> str:
    # whitelist para forçar letras/dígitos relevantes
    cfg = f'--oem 3 --psm {psm} -c preserve_interword_spaces=1 -c tessedit_char_whitelist=0123456789.,:/ACDMLPV '
    return pytesseract.image_to_string(page_img, lang=lang, config=cfg)

def ocr_top_header_get_text(imagem_pil, top_ratio: float = 0.22, lang: str = "por+eng") -> str:
    w, h = imagem_pil.size
    top_h = int(h * top_ratio)
    header = imagem_pil.crop((0, 0, w, top_h)).convert("L")
    header = ImageOps.autocontrast(header, cutoff=2)
    return _tess(header, lang=lang, psm="6")

def extrair_biometria_dupla_por_metades(paginas, lang: str = "por+eng") -> dict:
    """Corta a 1ª página em metades (esq=OD, dir=OS) e extrai AL/MV/ACD."""
    if not paginas:
        return {}

    img = paginas[0].convert("RGB")
    w, h = img.size
    mid = w // 2
    left = img.crop((0, 0, mid, h))   # OD
    right = img.crop((mid, 0, w, h))  # OS

    # pré-processa e tenta PSM 6; se faltar algo, tenta PSM 4
    for psm_try in ("6", "4"):
        L = _pp_half(left)
        R = _pp_half(right)
        txt_left = _tess(L, lang=lang, psm=psm_try)
        txt_right = _tess(R, lang=lang, psm=psm_try)

        od = _parse_eye_text_exact(txt_left)
        os_ = _parse_eye_text_exact(txt_right)

        if all(v is not None for v in [od["AL"], od["K1"], od["K2"], od["ACD"],
                                       os_["AL"], os_["K1"], os_["K2"], os_["ACD"]]):
            return {"OD": od, "OS": os_}

    return {}

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

# =========================
# Diagnóstico simples (sidebar)
# =========================
def _env_diag():
    try:
        pdftoppm = shutil.which("pdftoppm")
        tesseract_bin = shutil.which("tesseract")
        st.sidebar.markdown("### Diagnóstico do ambiente")
        st.sidebar.write(f"pdftoppm: {'OK' if pdftoppm else 'NÃO ENCONTRADO'}")
        st.sidebar.write(f"tesseract: {'OK' if tesseract_bin else 'NÃO ENCONTRADO'}")
        st.sidebar.caption("Se 'NÃO ENCONTRADO', adicione em packages.txt: `poppler-utils` e `tesseract-ocr`.")
    except Exception:
        pass

_env_diag()

# =========================
# Upload do PDF (Cloud-friendly)
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
        st.exception(e)
        st.stop()

# Converte apenas a 1ª página com boa qualidade
if st.session_state.pdf_bytes:
    try:
        paginas = convert_from_bytes(
            st.session_state.pdf_bytes,
            dpi=OCR_DPI,
            first_page=1,
            last_page=1,
            fmt="png"
        )
    except Exception as e:
        st.error("Erro ao converter PDF em imagem. No Streamlit Cloud, adicione 'poppler-utils'.")
        st.exception(e)
        paginas = []

    if paginas:
        try:
            texto_topo = ocr_top_header_get_text(paginas[0], top_ratio=0.22, lang="por+eng")
        except Exception:
            texto_topo = ""
        try:
            img_preview = paginas[0].copy()
            img_preview.thumbnail((1100, 1100))
        except Exception:
            img_preview = None

# =========================
# Extrações (OCR) — metades, parser exato
# =========================
dados = {}
if paginas:
    dados = extrair_biometria_dupla_por_metades(paginas, lang="por+eng")

patient_detected = extrair_patient_name_do_header(texto_topo) if texto_topo else ""

st.divider()

# =============== Estado global ===============
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
# UI principal (enxuta)
# =========================
if st.session_state.pdf_bytes is None:
    st.info("Faça o upload do PDF para extrair os dados.")
else:
    col_preview, col_form = st.columns([1, 1.2], gap="large")

    with col_preview:
        if img_preview is not None:
            st.image(img_preview, caption="Prévia da 1ª página do PDF", use_column_width=True)
        else:
            st.info("Sem prévia disponível.")

    with col_form:
        if not dados:
            st.warning("Não consegui extrair automaticamente. Preencha/ajuste manualmente.")
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

# --------- Auto-execução após seleção/edição ---------
if st.session_state.get("auto_run"):
    st.session_state.auto_run = False
    with st.status("Executando calculadora...", expanded=False):
        try:
            tables, used = run_selenium_and_fetch(nav_choice)
            st.session_state.tables = tables
            st.session_state.used_browser = used
        except Exception as e:
            st.error(f"Erro ao executar Selenium: {e}")

# Persistir nomes digitados
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
