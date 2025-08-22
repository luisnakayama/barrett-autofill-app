# app_barrett.py
import re
import json
import os
import shutil
import streamlit as st
from PIL import Image
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
# Utilitários / OCR
# =========================
def _to_f(s: str) -> float:
    return float(str(s).replace(",", ".").strip())

def ocr_top_header_get_text(imagem_pil, top_ratio: float = 0.22, lang: str = "por+eng") -> str:
    w, h = imagem_pil.size
    top_h = int(h * top_ratio)
    header = imagem_pil.crop((0, 0, w, top_h))
    return pytesseract.image_to_string(header, lang=lang)

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
    """Extrai AL/K1/K2/ACD de um bloco (um olho)."""
    t = re.sub(r"Comp[,.\s]*AL", "Comp. AL", txt, flags=re.IGNORECASE)

    m_al = re.search(r"Comp\.?\s*AL\s*[:=]\s*([0-9]+(?:[.,][0-9]+)?)", t, flags=re.IGNORECASE)
    al = _to_f(m_al.group(1)) if m_al else None

    m_mv = re.search(r"MV\s*[:=]\s*([0-9]+(?:[.,][0-9]+)?)\s*/\s*([0-9]+(?:[.,][0-9]+)?)", t, flags=re.IGNORECASE)
    if m_mv:
        k1 = _to_f(m_mv.group(1))
        k2 = _to_f(m_mv.group(2))
    else:
        k1 = k2 = None

    m_acd = re.search(r"\bACD\b\s*[:=]\s*([0-9]+(?:[.,][0-9]+)?)", t, flags=re.IGNORECASE)
    acd = _to_f(m_acd.group(1)) if m_acd else None

    return {"AL": al, "K1": k1, "K2": k2, "ACD": acd}

def extrair_biometria_dupla_por_metades(paginas, lang: str = "por+eng"):
    """Toma a 1ª página, corta ao meio (esq=OD, dir=OS) e aplica _parse_eye_text."""
    if not paginas:
        return {}
    img = paginas[0]
    w, h = img.size
    mid = w // 2
    left = img.crop((0, 0, mid, h))   # OD
    right = img.crop((mid, 0, w, h))  # OS

    txt_left = pytesseract.image_to_string(left, lang=lang)
    txt_right = pytesseract.image_to_string(right, lang=lang)

    od = _parse_eye_text(txt_left)
    os_ = _parse_eye_text(txt_right)

    if all(v is not None for v in [od["AL"], od["K1"], od["K2"], od["ACD"],
                                   os_["AL"], os_["K1"], os_["K2"], os_["ACD"]]):
        return {"OD": od, "OS": os_}
    return {}

# =========================
# Upload do PDF
# =========================
arquivo = st.file_uploader("Upload do PDF do exame", type=["pdf"])
texto_topo = ""
paginas = []
img_preview = None

if arquivo is not None:
    file_bytes = arquivo.read()
    try:
        paginas = convert_from_bytes(file_bytes, dpi=300)
    except Exception as e:
        st.error(f"Erro ao ler PDF: {e}")

    if paginas:
        texto_topo = ocr_top_header_get_text(paginas[0], top_ratio=0.22, lang="por+eng")
        img_preview = paginas[0].copy()
        img_preview.thumbnail((900, 900))

# =========================
# Extrações (OCR)
# =========================
dados = {}
if paginas:
    dados = extrair_biometria_dupla_por_metades(paginas, lang="por+eng")

patient_detected = extrair_patient_name_do_header(texto_topo) if texto_topo else ""

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
    """Callback: ao mudar a LIO, aplica preset e dispara auto_run."""
    preset = PRESET_BY_LABEL.get(st.session_state.selected_iol, {"a_constant": "", "lens_factor": ""})
    st.session_state.a_constant_val = preset.get("a_constant", "") or ""
    st.session_state.lens_factor_val = preset.get("lens_factor", "") or ""
    st.session_state.auto_run = (st.session_state.selected_iol != "— selecionar —")

def on_ac_input_change():
    """Callback: quando A-constant é digitada/alterada manualmente, dispara auto_run."""
    st.session_state.a_constant_val = st.session_state.get("ac_input", "").strip()
    if st.session_state.a_constant_val:
        # Evita dependência do select de LIO
        st.session_state.selected_iol = st.session_state.selected_iol or "— selecionar —"
        st.session_state.auto_run = True

def on_lf_input_change():
    """Callback: quando Lens Factor é digitado/alterado manualmente, dispara auto_run."""
    st.session_state.lens_factor_val = st.session_state.get("lf_input", "").strip()
    if st.session_state.lens_factor_val:
        st.session_state.selected_iol = st.session_state.selected_iol or "— selecionar —"
        st.session_state.auto_run = True

if arquivo is None:
    st.info("Faça o upload do PDF para extrair os dados.")
else:
    # layout lado a lado
    col_preview, col_form = st.columns([1, 1.2], gap="large")

    with col_preview:
        if img_preview:
            st.image(img_preview, caption="Prévia da 1ª página do PDF", use_container_width=True)

    with col_form:
        # dados default se vazio
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
            index=1,  # A-constant por padrão
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

        # Campos com callbacks de auto-run ao editar:
        if const_tipo == "Lens Factor":
            lens_factor = st.text_input(
                "Lens Factor (ex.: 2.00; -2.0 a 5.0)",
                value=st.session_state.lens_factor_val,
                key="lf_input",
                on_change=on_lf_input_change,   # <<<<<<<<<<<<<<<<<< NEW
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
                on_change=on_ac_input_change,   # <<<<<<<<<<<<<<<<<< NEW
            )
            lens_factor = st.text_input(
                "Lens Factor (ex.: 2.00; -2.0 a 5.0)",
                value=st.session_state.lens_factor_val,
                key="lf_input_disabled",
                disabled=True
            )

        st.markdown("[Abrir calculadora Barrett](https://calc.apacrs.org/barrett_universal2105/)", unsafe_allow_html=True)

    st.divider()

    # ======= Configs de execução =======
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
        service = FirefoxService()          # Selenium Manager resolve geckodriver
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
        # limpar ENV que aponta para chromedriver antigo
        for var in ["WEBDRIVER_CHROME_DRIVER", "webdriver.chrome.driver", "CHROMEDRIVER", "CHROMEWEBDRIVER"]:
            if var in os.environ:
                del os.environ[var]
        # tirar dirs com chromedriver do PATH
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
            service = ChromeService()       # Selenium Manager resolve chromedriver
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

                # Identificação
                fill_by_id("MainContent_DoctorName", doctor_name)
                fill_by_id("MainContent_PatientName", patient_name)

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
                # Aba Universal Formula
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

    # --------- Auto-execução após seleção da LIO OU edição de constantes ---------
    if st.session_state.auto_run:
        st.session_state.auto_run = False
        with st.status("Executando calculadora...", expanded=False):
            try:
                tables, used = run_selenium_and_fetch(nav_choice)
                st.session_state.tables = tables
                st.session_state.used_browser = used
            except Exception as e:
                st.error(f"Erro ao executar Selenium: {e}")

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
    if st.session_state.tables:
        st.success(f"Tabelas importadas com sucesso (navegador: {st.session_state.used_browser}).")
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
