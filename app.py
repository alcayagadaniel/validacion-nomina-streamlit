import streamlit as st
import re
import pandas as pd
import hashlib
from io import BytesIO
from datetime import datetime
from reportlab.lib.pagesizes import A4, legal, landscape
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
import os
import requests
from typing import Optional, Dict


def convertir_df_a_excel(df: pd.DataFrame, meta: dict | None = None, sheet_name: str = "Reajuste") -> bytes:
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        # ---- Cálculo de filas de encabezado según metadatos ----
        header_rows = 0
        if meta:
            pares = list(meta.items())
            cols_por_fila = 3  # hasta 3 pares por fila
            header_rows = (len(pares) + cols_por_fila - 1) // cols_por_fila

        # Dejamos 2 filas de separación entre encabezado y tabla
        start_row = header_rows + 2 if header_rows > 0 else 0

        # Escribir la tabla de resultados a partir de start_row
        df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=start_row)

        ws = writer.sheets[sheet_name]

        # Escribir los metadatos distribuidos en columnas
        if meta:
            pares = list(meta.items())
            cols_por_fila = 3
            fila_excel = 1

            # Cada fila: hasta 3 pares etiqueta/valor → columnas (1-2), (4-5), (7-8)
            for i in range(0, len(pares), cols_por_fila):
                sub_pares = pares[i:i + cols_por_fila]
                for j, (etiqueta, valor) in enumerate(sub_pares):
                    col_base = 1 + j * 3
                    ws.cell(row=fila_excel, column=col_base, value=str(etiqueta))
                    ws.cell(
                        row=fila_excel,
                        column=col_base + 1,
                        value="" if valor is None else str(valor)
                    )
                fila_excel += 1

        # Formato porcentaje con 2 decimales para "Tasa IPC" (valor tipo 3.25 -> 3.25%)
        if "Tasa IPC" in df.columns:
            col_idx = df.columns.get_loc("Tasa IPC") + 1  # Excel es 1-based
            for row in range(start_row + 2, start_row + len(df) + 2):
                cell = ws.cell(row=row, column=col_idx)
                cell.number_format = '0.00"%"'

    output.seek(0)
    return output.read()

def normalizar_porcentaje_a_float(serie: pd.Series) -> pd.Series:
    """
    Convierte valores tipo:
      3,5   -> 3.5
      3.5%  -> 3.5
      "  "  -> NaN
    a float (porcentaje, no factor).
    """
    s = serie.astype(str).str.strip()
    s = s.replace({"": None, "None": None, "nan": None})
    s = s.str.replace("%", "", regex=False)
    s = s.str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")

def preparar_historico(df_hist_raw: pd.DataFrame) -> pd.DataFrame:
    df = df_hist_raw.copy()

    # 1) Eliminar columnas sin nombre (columna A vacía típica)
    df = df.loc[:, [c for c in df.columns if str(c).strip() != "" and str(c).lower() != "unnamed: 0"]]

    # 2) Si viene con 2 fechas al final, ignorar la última columna
    #    (tu regla: considerar penúltima, ignorar última)
    if df.shape[1] >= 8:
        # elimina la última columna por posición
        df = df.iloc[:, :-1]

    # 3) Asegurar que la última columna ahora se llame "Fecha ref"
    #    (penúltima original)
    cols = list(df.columns)
    if len(cols) >= 1:
        # renombra la última columna a "Fecha ref"
        cols[-1] = "Fecha ref"
        df.columns = cols

    return df

def generar_plantilla_validacion_nomina() -> bytes:
    """
    Plantilla oficial para Nómina actual (Validación Banco / Control finiquitos).
    Mantiene nombres y orden EXACTOS según estándar interno.
    """
    columnas = [
        "Nº",
        "Nº   CI",
        "Nombres",
        "Fecha de Finiquito",
        "Banco",
        "COD       BANCO",
        "N° Cuenta",
        "Monto",
        "Observaciones",
    ]

    df = pd.DataFrame(columns=columnas)

    # (Opcional) una fila ejemplo vacía para guiar al usuario
    # df.loc[0] = [1, "12.345.678-9", "Nombre Apellido", "05-12-2025", "Banco X", "00", "123456", "1000000", ""]

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Plantilla")
    output.seek(0)
    return output.read()

def generar_plantilla_excel() -> bytes:
    """
    Genera un archivo Excel con la estructura sugerida:
    - Columnas de identificación: Rut, Nombre, Fecha Ingreso
    - Hasta varios conceptos abiertos (Sueldo Base, Colación, etc.)
    - Columna opcional: Tasa IPC manual (%)
    """
    columnas = [
        "Rut", "Nombre", "Fecha Ingreso",
        "Sueldo Base", "Colación", "Movilización",
        "Viático", "Desgaste Herramientas", "Otro Concepto",
        "Tasa IPC manual (%)",  # ← NUEVA COLUMNA OPCIONAL
    ]
    df = pd.DataFrame(columns=columnas)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Plantilla")
    output.seek(0)
    return output.read()



# -----------------------------------------------------------
# CONSTANTES GENERALES
# -----------------------------------------------------------

COMPANY_NAME = "R&Q Ingeniería SPA"

MESES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
]

MAPA_MES_NOMBRE_A_NUM = {nombre: i + 1 for i, nombre in enumerate(MESES_ES)}
COLUMNA_TASA_MANUAL = "Tasa IPC manual (%)"


# -----------------------------------------------------------
# FUNCIONES AUXILIARES
# -----------------------------------------------------------

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


USUARIO_ADMIN = "admin"
HASH_ADMIN = hash_password("admin123")


def verificar_credenciales(usuario: str, password: str) -> bool:
    if usuario != USUARIO_ADMIN:
        return False
    return hash_password(password) == HASH_ADMIN


def generar_pdf_reajuste(
    df: pd.DataFrame,
    ipc: float,
    periodo: str,
    empresa: str,
    meta: Optional[Dict[str, str]] = None,
    **kwargs,
) -> bytes:
    """
    Genera un PDF tamaño OFICIO horizontal con:
    - Encabezado con datos del proceso (meta) en tabla.
    - Tabla de resultados:
        * Fecha Ingreso: dd/mm/yy
        * Montos numéricos: miles con punto, sin decimales.
        * Tasa IPC (%): porcentaje con dos decimales.
        * Columnas de rentas totalmente vacías/0 ocultas.
        * Cabecera usando todo el ancho útil del documento.
        * Encabezados largos en dos líneas, con 'Reajustado' abreviado a 'Reaj.'.
        * Columna 'Nombre' con ancho mayor para 2 nombres + 2 apellidos aprox.
    - Bloque de firmas al final.
    """
    buffer = BytesIO()

    # Documento tamaño oficio HORIZONTAL con márgenes 0,5"
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(legal),
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    styles = getSampleStyleSheet()
    elements = []

    # ----- Título principal -----
    title_style = styles["Heading1"]
    title_style.fontName = "Helvetica-Bold"
    title_style.fontSize = 16
    title_style.leading = 20

    elements.append(
        Paragraph(f"{empresa} - Certificado de Reajuste IPC", title_style)
    )
    elements.append(Spacer(1, 6))
    df_pdf = df.copy()
    # ----- Datos estándar -----
    normal = styles["Normal"]
    normal.fontName = "Helvetica"
    normal.fontSize = 9
    normal.leading = 11

    fecha_emision = datetime.now().strftime("%d-%m-%Y %H:%M")

    elements.append(Paragraph(f"<b>Fecha emisión:</b> {fecha_emision}", normal))
    elements.append(Paragraph(f"<b>IPC aplicado:</b> {ipc:.2f}%", normal))
    elements.append(Paragraph(f"<b>Periodo remuneracional:</b> {periodo}", normal))
    elements.append(Spacer(1, 8))

    # ----- Tabla de metadatos (paso 1) -----
    if meta:
        orden_claves = [
            "Empresa",
            "Centro de costo",
            "Proyecto",
            "Solicitante",
            "Tipo de procesamiento",
            "Considerar fecha de ingreso",
            "Periodo remuneracional",
            "IPC aplicado (%)",
            "Fecha emisión",
            "Observaciones",
        ]

        pares: list[tuple[str, str]] = []
        for k in orden_claves:
            if k in meta:
                pares.append((k, "" if meta[k] is None else str(meta[k])))
        for k, v in meta.items():
            if k not in orden_claves:
                pares.append((k, "" if v is None else str(v)))

        meta_rows: list[list] = []
        fila_actual: list = []

        for i, (etiqueta, valor) in enumerate(pares):
            texto = f"<b>{etiqueta}:</b> {valor}"
            fila_actual.append(Paragraph(texto, normal))
            if (i + 1) % 3 == 0:
                meta_rows.append(fila_actual)
                fila_actual = []
        if fila_actual:
            meta_rows.append(fila_actual)

        meta_table = Table(
            meta_rows,
            hAlign="LEFT",
            colWidths=[doc.width / 3.0] * 3,
        )
        meta_table.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        elements.append(meta_table)
        elements.append(Spacer(1, 10))

    # ---------- FORMATEO Y LIMPIEZA DE DATOS PARA LA TABLA PRINCIPAL ----------
    df_pdf = df.copy()
    # ----- Formateo de DF para PDF (Tasa IPC en % con 2 decimales) -----
    df_pdf = df.copy()

    # Detectar nombre de la columna de tasa (según cómo esté en tu salida)
    col_tasa = None
    if "Tasa IPC" in df_pdf.columns:
        col_tasa = "Tasa IPC"
    elif "Tasa IPC (%)" in df_pdf.columns:
        col_tasa = "Tasa IPC (%)"

    # Formatear esa columna como texto "0.00%"
    if col_tasa:
        df_pdf[col_tasa] = (
            pd.to_numeric(df_pdf[col_tasa], errors="coerce")
            .fillna(0)
            .map(lambda v: f"{v:.2f}%")
        )

    # 1) Ocultar columnas de rentas totalmente vacías o en cero
    id_like = ["rut", "r.u.t", "nombre", "trabajador", "fecha ingreso"]
    cols_to_drop = []
    for col in df_pdf.columns:
        cl = col.strip().lower()
        if any(k in cl for k in id_like):
            continue

        serie = df_pdf[col]
        if pd.api.types.is_numeric_dtype(serie):
            if serie.fillna(0).eq(0).all():
                cols_to_drop.append(col)
        else:
            if serie.isna().all() or (serie.astype(str).str.strip() == "").all():
                cols_to_drop.append(col)

    if cols_to_drop:
        df_pdf = df_pdf.drop(columns=cols_to_drop)

    # 2) Fecha Ingreso -> dd/mm/yy
    if "Fecha Ingreso" in df_pdf.columns:
        df_pdf["Fecha Ingreso"] = pd.to_datetime(
            df_pdf["Fecha Ingreso"], errors="coerce"
        ).dt.strftime("%d/%m/%y")

    # 3) Formatos numéricos:
    #    - Montos: miles con punto y sin decimales
    #    - Tasa IPC (%): porcentaje con 2 decimales
    def formato_miles(valor):
        try:
            if pd.isna(valor):
                return ""
            n = float(valor)
            n_int = int(round(n))
            return f"{n_int:,}".replace(",", ".")
        except Exception:
            return str(valor)

    def formato_porcentaje(valor):
        try:
            if pd.isna(valor):
                return ""
            n = float(valor)
            return f"{n:.2f}%"
        except Exception:
            return str(valor)

    numeric_cols = df_pdf.select_dtypes(include=["number"]).columns
    for col in numeric_cols:
        col_lower = col.lower()
        if col_lower.strip() == "tasa ipc":
            df_pdf[col] = df_pdf[col].apply(formato_porcentaje)
        elif "tasa" in col_lower and "%" in col_lower:
            df_pdf[col] = df_pdf[col].apply(formato_porcentaje)
        else:
            df_pdf[col] = df_pdf[col].apply(formato_miles)


    # ----- Tabla principal con resultados -----
    cols = list(df_pdf.columns)

    # Encabezados: dos líneas y "Reajustado" -> "Reaj."
    header_cells = []
    for col_name in cols:
        texto = col_name
        if "Reajustado" in texto:
            base = texto.replace("Reajustado", "").strip()
            texto = f"{base}<br/>Reaj."
        header_cells.append(Paragraph(texto, normal))

    data = [header_cells]
    for row in df_pdf.itertuples(index=False):
        data.append([("" if v is None else str(v)) for v in row])

    # Anchos de columna: damos más peso a 'Nombre'
    num_cols = len(cols)
    pesos = [1.0] * num_cols
    for i, col_name in enumerate(cols):
        cl = col_name.lower()
        if "nombre" in cl:
            pesos[i] = 3.0  # más ancho para 2 nombres + 2 apellidos
        elif "empresa" in cl or "periodo" in cl:
            pesos[i] = 1.5
        elif "rut" in cl or "fecha ingreso" in cl or "tasa" in cl:
            pesos[i] = 1.2
        # el resto se queda con 1.0

    factor = doc.width / sum(pesos)
    col_widths = [p * factor for p in pesos]

    results_table = Table(
        data,
        repeatRows=1,
        colWidths=col_widths,
        hAlign="LEFT",
    )

    results_table.setStyle(
        TableStyle(
            [
                # Bordes generales
                ("BOX", (0, 0), (-1, -1), 0.5, colors.lightblue),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightblue),
                # Cabecera
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 7),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                # Cuerpo
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )

    elements.append(results_table)
    elements.append(Spacer(1, 20))

    # ----- Bloque de firmas -----
    firmas_textos = [
        "Preparado por: Equipo de remuneraciones",
        "Revisado por: Gerencia de Gestión de Personas",
        "Autorizado por: Gerencia del Área",
    ]

    firmas_row = [Paragraph(texto, normal) for texto in firmas_textos]
    firmas_table = Table([firmas_row], hAlign="CENTER", colWidths="*")

    firmas_table.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 0.7, colors.black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, 0), 10),
            ]
        )
    )

    elements.append(firmas_table)

    # Construir documento
    doc.build(elements)
    buffer.seek(0)
    return buffer.read()











def calcular_meses_previos(mes: int, anno: int, n: int):
    meses = []
    m = mes
    y = anno
    for _ in range(n):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        meses.append((m, y))
    return list(reversed(meses))


from datetime import datetime

def ultimo_mes_disponible_por_fecha_actual():
    hoy = datetime.now()
    mes = hoy.month - 1
    anno = hoy.year
    if mes == 0:
        mes = 12
        anno -= 1
    return mes, anno


def generar_meses_rango(inicio_mes: int, inicio_anno: int, fin_mes: int, fin_anno: int):
    meses = []
    m, a = int(inicio_mes), int(inicio_anno)
    fin_m, fin_a = int(fin_mes), int(fin_anno)

    while (a < fin_a) or (a == fin_a and m <= fin_m):
        meses.append((m, a))
        m += 1
        if m == 13:
            m = 1
            a += 1

    return meses




def obtener_ipc_mensual_sii(anno: int) -> pd.DataFrame:
    url = f"https://www.sii.cl/valores_y_fechas/utm/utm{anno}.htm"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    tablas = pd.read_html(resp.text, decimal=",", thousands=".")
    df_tabla = None

    for t in tablas:
        cols = [str(c) for c in t.columns]
        if any("UTM" in c for c in cols) and any("IPC" in c for c in cols):
            df_tabla = t
            break

    if df_tabla is None:
        raise ValueError("No se encontró la tabla UTM-UTA-IPC en el SII.")

    if isinstance(df_tabla.columns, pd.MultiIndex):
        nuevas_cols = []
        for col_tuple in df_tabla.columns.values:
            partes = [str(x) for x in col_tuple if str(x) != "nan"]
            nuevas_cols.append(" ".join(partes).strip())
        df_tabla.columns = nuevas_cols
    else:
        df_tabla.columns = [str(c).strip() for c in df_tabla.columns]

    col_mes = df_tabla.columns[0]
    posibles = [c for c in df_tabla.columns if "Mensual" in c]
    if not posibles:
        raise ValueError("No se encontró columna 'variación mensual' en tabla del SII")
    col_var_mensual = posibles[0]

    df_ipc = df_tabla[[col_mes, col_var_mensual]].copy()
    df_ipc.rename(columns={col_mes: "Mes", col_var_mensual: "Variacion_Mensual"}, inplace=True)

    df_ipc["Mes"] = df_ipc["Mes"].astype(str).str.strip()
    df_ipc["Mes_num"] = df_ipc["Mes"].map(MAPA_MES_NOMBRE_A_NUM)

    df_ipc["Variacion_Mensual"] = pd.to_numeric(df_ipc["Variacion_Mensual"], errors="coerce")
    df_ipc = df_ipc.dropna(subset=["Mes_num", "Variacion_Mensual"])

    return df_ipc[["Mes_num", "Variacion_Mensual"]]


def obtener_ipc_desde_sii_para_meses(meses_previos):
    cache_por_anno = {}
    detalles = []
    ipc_total = 0.0

    for m, a in meses_previos:
        if a not in cache_por_anno:
            cache_por_anno[a] = obtener_ipc_mensual_sii(a)
        df_ipc_anno = cache_por_anno[a]
        fila = df_ipc_anno[df_ipc_anno["Mes_num"] == m]
        if fila.empty:
            raise ValueError(f"No hay IPC para {MESES_ES[m-1]} {a}")
        valor = float(fila["Variacion_Mensual"].iloc[0])
        ipc_total += valor
        detalles.append({"Mes": MESES_ES[m - 1], "Año": a, "IPC mensual (%)": valor})

    return ipc_total, pd.DataFrame(detalles)
def meses_trabajados_hasta_periodo(fecha_ingreso, periodo_mes: int, periodo_anno: int):
    """
    Calcula cuántos meses (aprox) ha trabajado una persona hasta el período de remuneración.
    Regla:
    - Si la persona ingresó después del período, retorna 0.
    - Se cuenta la diferencia en meses entre (año, mes) del período y (año, mes) de ingreso.
    - Si el día de ingreso es mayor a 1, se descuenta 1 mes para reflejar que no trabajó el mes completo.
    """
    if pd.isna(fecha_ingreso):
        return None
    try:
        fi = pd.to_datetime(fecha_ingreso)
    except Exception:
        return None

    # Si la fecha de ingreso es posterior al período, 0 meses
    if (fi.year > periodo_anno) or (fi.year == periodo_anno and fi.month > periodo_mes):
        return 0

    meses_diff = (periodo_anno - fi.year) * 12 + (periodo_mes - fi.month)
    if fi.day > 1:
        meses_diff -= 1

    return max(meses_diff, 0)

# ------------------ IPC por tramos según fecha de ingreso ------------------

MAPA_PERIODO_A_MESES = {
    "Mensual": 1,
    "Trimestral": 3,
    "Semestral": 6,
    "Cuatrimestral": 4,
    "Anual": 12,
}

def calcular_antiguedad_meses(fecha_ingreso, periodo_mes: int, periodo_anno: int):
    """
    Meses completos trabajados hasta (periodo_mes/periodo_anno).
    Regla:
      - Si ingreso día 1, ese mes cuenta completo.
      - Si ingreso día 2 o posterior, ese mes NO cuenta completo (se descuenta 1).
    """
    if pd.isna(fecha_ingreso):
        return None
    try:
        fi = pd.to_datetime(fecha_ingreso)
    except Exception:
        return None

    # Si fecha ingreso posterior al periodo, 0
    if (fi.year > periodo_anno) or (fi.year == periodo_anno and fi.month > periodo_mes):
        return 0

    meses_diff = (periodo_anno - fi.year) * 12 + (periodo_mes - fi.month)

    if fi.day > 11:
        meses_diff -= 1

    return max(int(meses_diff), 0)

def obtener_ipc_acumulado(periodo_mes: int, periodo_anno: int, n_meses: int) -> float:
    """
    IPC acumulado de los últimos n_meses (sumando variaciones mensuales SII).
    """
    if n_meses <= 0:
        return 0.0
    meses_previos = calcular_meses_previos(periodo_mes, periodo_anno, n_meses)
    ipc_total, _ = obtener_ipc_desde_sii_para_meses(meses_previos)
    return float(ipc_total)

def determinar_tasa_ipc_empleado(fecha_ingreso, periodo_mes: int, periodo_anno: int,
                                periodo_ajuste_definido: str, opcion_fecha_ingreso: str):
    """
    Retorna (tasa_ipc, caso, antig_meses)
      Caso A: cumple periodo -> IPC completo (periodo_ajuste_definido)
      Caso B: no cumple y opcion Sí -> IPC pro rata (antig_meses)
      Caso C: no cumple y opcion No -> 0%
    """
    periodo_meses = MAPA_PERIODO_A_MESES.get(periodo_ajuste_definido)
    if periodo_meses is None:
        return 0.0, "C", 0

    antig = calcular_antiguedad_meses(fecha_ingreso, periodo_mes, periodo_anno)
    if antig is None:
        antig = 0

    # A
    if antig >= periodo_meses:
        tasa = obtener_ipc_acumulado(periodo_mes, periodo_anno, periodo_meses)
        return tasa, "A", antig

    # B
    if opcion_fecha_ingreso == "Sí":
        if antig <= 0:
            return 0.0, "B", antig
        tasa = obtener_ipc_acumulado(periodo_mes, periodo_anno, antig)
        return tasa, "B", antig

    # C
    return 0.0, "C", antig





# -----------------------------------------------------------
# VALIDACIÓN NÓMINA BANCO (NUEVO MÓDULO)
# -----------------------------------------------------------

COLS_NOMINA_VALIDACION = [
    "Nº",
    "Nº CI",
    "Nombres",
    "Fecha de Finiquito",
    "Banco",
    "COD BANCO",
    "N° Cuenta",
    "Monto",
    "Observaciones",
]



COLS_HISTORICO_VALIDACION = [
    "Nº",
    "Nombres",
    "Banco",
    "N° Cuenta",
    "Rut",
    "Monto",
    "Fecha ref",
]


def generar_plantilla_nomina_actual() -> bytes:
    """
    Genera un Excel vacío con el orden de columnas sugerido para:
    A) Nómina actual a solicitar.
    """
    df = pd.DataFrame(columns=COLS_NOMINA_VALIDACION)

    meta = {
        "Tipo plantilla": "Nómina actual (A)",
        "Fecha generación": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Nota": "Mantén exactamente estos nombres de columnas.",
    }

    return convertir_df_a_excel(df, meta=meta, sheet_name="NominaActual")


def generar_plantilla_historico() -> bytes:
    """
    Genera un Excel vacío con el orden de columnas sugerido para:
    B) Control histórico de finiquitos.
    """
    df = pd.DataFrame(columns=COLS_HISTORICO_VALIDACION)

    meta = {
        "Tipo plantilla": "Histórico (B)",
        "Fecha generación": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Nota": "Mantén exactamente estos nombres de columnas.",
    }

    return convertir_df_a_excel(df, meta=meta, sheet_name="Historico")


def normalizar_rut(rut) -> str:
    """
    Normaliza RUT para comparación:
    - Elimina puntos, guiones, espacios y espacios invisibles (NBSP).
    - Acepta DV en k/K.
    - Devuelve formato compacto: 11222333K o 11222333 (si no hay DV).
    """
    if rut is None:
        return ""

    s = str(rut)

    # Limpia espacios raros (NBSP) y whitespace general
    s = s.replace("\u00A0", " ").strip()

    # Quita puntos, guiones y todos los espacios
    s = s.replace(".", "").replace("-", "")
    s = "".join(s.split())

    # Si quedó vacío
    if s == "" or s.lower() in ("nan", "none"):
        return ""

    # Upper para DV
    s = s.upper()

    # Dejar solo dígitos y K
    # (ej: "11.279.000-4" -> "112790004")
    import re
    s = re.sub(r"[^0-9K]", "", s)

    if s == "":
        return ""

    # Caso con DV: último char es dígito o K y hay al menos 2 chars
    if len(s) >= 2 and (s[-1].isdigit() or s[-1] == "K"):
        cuerpo = s[:-1]
        dv = s[-1]
        # Si cuerpo no tiene dígitos, invalida
        if cuerpo == "" or not cuerpo.isdigit():
            return ""
        return f"{cuerpo}{dv}"

    # Caso sin DV (solo números)
    if s.isdigit():
        return s

    return ""



def normalizar_monto(valor) -> int:
    """
    Convierte montos con separadores a entero (CLP).
    Ej: '1.234.567' -> 1234567
    """
    if valor is None:
        return 0

    s = str(valor).strip()
    if s == "" or s.lower() == "nan":
        return 0

    # Quita símbolos, deja dígitos y separadores
    s = re.sub(r"[^0-9\.,\-]", "", s)

    neg = s.startswith("-")
    s = s.lstrip("-")

    # Heurística de decimales
    if "," in s and "." in s:
        dec_pos = max(s.rfind(","), s.rfind("."))
        s_int = re.sub(r"[^\d]", "", s[:dec_pos])
    elif "," in s:
        if s.count(",") == 1 and re.match(r".+,\d{1,2}$", s):
            s_int = re.sub(r"[^\d]", "", s.split(",")[0])
        else:
            s_int = re.sub(r"[^\d]", "", s)
    elif "." in s:
        if s.count(".") == 1 and re.match(r".+\.\d{1,2}$", s):
            s_int = re.sub(r"[^\d]", "", s.split(".")[0])
        else:
            s_int = re.sub(r"[^\d]", "", s)
    else:
        s_int = re.sub(r"[^\d]", "", s)

    if s_int == "":
        return 0

    val = int(s_int)
    return -val if neg else val


def validar_columnas_minimas(df: pd.DataFrame, requeridas: list[str]) -> tuple[bool, str]:
    cols = list(df.columns)
    faltan = [c for c in requeridas if c not in cols]
    if faltan:
        return False, f"Faltan columnas: {faltan}. Columnas presentes: {cols}"
    return True, "OK"



def build_evidencia_hist(row_hist: pd.Series) -> str:
    nombre = str(row_hist.get("Nombres", "")).strip()
    banco = str(row_hist.get("Banco", "")).strip()
    cuenta = str(row_hist.get("N° Cuenta", "")).strip()
    rut = str(row_hist.get("Rut", "")).strip()
    monto = int(row_hist.get("_monto_int", 0) or 0)
    fecha_ref = str(row_hist.get("Fecha ref", "")).strip()

    if fecha_ref:
        return f"Histórico: {nombre} | {banco} | Cta {cuenta} | Rut {rut} | Monto {monto} | Fecha: {fecha_ref}"
    return f"Histórico: {nombre} | {banco} | Cta {cuenta} | Rut {rut} | Monto {monto}"




def build_evidencia_hist_multi(rows_hist: list[pd.Series]) -> str:
    partes = []
    for h in rows_hist[:3]:
        partes.append(build_evidencia_hist(h))
    extra = f" (+{len(rows_hist) - 3} más)" if len(rows_hist) > 3 else ""
    return " ; ".join(partes) + extra

def es_vacio(x) -> bool:
    if x is None:
        return True
    s = str(x).replace("\u00A0", " ").strip()
    return s == "" or s.lower() in ("nan", "none")

def limpiar_nomina_actual(df: pd.DataFrame) -> pd.DataFrame:
    """
    Elimina filas que NO son registros (totales o filas vacías).
    - Quita filas donde N° Cuenta = TOTAL (case-insensitive)
    - Quita filas donde RUT/Nombres/Monto estén vacíos simultáneamente
    """
    df2 = df.copy()

    if "N° Cuenta" in df2.columns:
        mask_total = df2["N° Cuenta"].astype(str).str.replace("\u00A0", " ", regex=False).str.strip().str.upper().eq("TOTAL")
        df2 = df2[~mask_total].copy()

    # Si vienen filas completamente vacías
    cols_clave = [c for c in ["Nº CI", "Nombres", "Monto"] if c in df2.columns]
    if cols_clave:
        mask_vacia = df2[cols_clave].apply(lambda r: all(es_vacio(v) for v in r), axis=1)
        df2 = df2[~mask_vacia].copy()

    df2 = df2.reset_index(drop=True)
    return df2

def motor_validacion_nomina(df_nomina_raw: pd.DataFrame, df_hist_raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    df_nom = df_nomina_raw.copy()
    df_hist = df_hist_raw.copy()

    # 1) Limpieza de filas no-registro (TOTAL / vacías)
    df_nom = limpiar_nomina_actual(df_nom)

    # 2) Normalizaciones clave
    df_nom["_rut_norm"] = df_nom["Nº CI"].apply(normalizar_rut)
    df_nom["_monto_int"] = df_nom["Monto"].apply(normalizar_monto)

    df_hist["_rut_norm"] = df_hist["Rut"].apply(normalizar_rut)
    df_hist["_monto_int"] = df_hist["Monto"].apply(normalizar_monto)

    # 3) Duplicidad interna en nómina actual (IGNORAR RUT vacío)
    df_nom["_dup_rut"] = False
    df_nom["_dup_rut_monto"] = False

    mask_rut_valido = df_nom["_rut_norm"].astype(str).str.strip().ne("")
    if mask_rut_valido.any():
        df_nom.loc[mask_rut_valido, "_dup_rut"] = df_nom.loc[mask_rut_valido].duplicated(subset=["_rut_norm"], keep=False)
        df_nom.loc[mask_rut_valido, "_dup_rut_monto"] = df_nom.loc[mask_rut_valido].duplicated(subset=["_rut_norm", "_monto_int"], keep=False)

    # 4) Index histórico por RUT (IGNORAR RUT vacío)
    hist_por_rut: dict[str, list[pd.Series]] = {}
    for _, r in df_hist.iterrows():
        rut = str(r.get("_rut_norm", "")).strip()
        if rut == "":
            continue
        if rut not in hist_por_rut:
            hist_por_rut[rut] = []
        hist_por_rut[rut].append(r)

    estados, motivos, evidencias, recomendaciones = [], [], [], []

    for _, row in df_nom.iterrows():
        rut = str(row.get("_rut_norm", "")).strip()
        monto = int(row.get("_monto_int", 0) or 0)

        # A) Duplicidad dentro de nómina actual
        if bool(row.get("_dup_rut_monto", False)):
            estados.append("Duplicado en nómina actual")
            motivos.append("RUT + Monto repetido dentro de la nómina actual.")
            evidencias.append("Duplicidad interna detectada (RUT+Monto).")
            recomendaciones.append("Solicitar una vez verificado")
            continue

        if bool(row.get("_dup_rut", False)):
            estados.append("Duplicado en nómina actual")
            motivos.append("RUT repetido dentro de la nómina actual (montos pueden diferir).")
            evidencias.append("Duplicidad interna detectada (RUT).")
            recomendaciones.append("Revisar")
            continue

        # B) Validación contra histórico
        if rut == "":
            estados.append("Revisar")
            motivos.append("RUT vacío o inválido tras normalización.")
            evidencias.append("No se pudo normalizar RUT.")
            recomendaciones.append("Revisar")
            continue

        registros_hist = hist_por_rut.get(rut, [])

        # B1) Sin coincidencia por rut
        if len(registros_hist) == 0:
            estados.append("Sin registro histórico")
            motivos.append("No existe el RUT en el histórico cargado.")
            evidencias.append("Sin coincidencias por RUT.")
            recomendaciones.append("Solicitar")
            continue

        # B2) Duplicidad crítica: mismo rut y mismo monto
        match_monto = [h for h in registros_hist if int(h.get("_monto_int", 0) or 0) == monto and monto != 0]
        if len(match_monto) >= 1:
            estados.append("Posible duplicidad")
            motivos.append("Existe en histórico el mismo RUT y el mismo monto.")
            evidencias.append(build_evidencia_hist(match_monto[0]))
            recomendaciones.append("NO RECOMIENDA SOLICITAR")
            continue

        # B3) Coincidencia parcial
        estados.append("Revisar")
        motivos.append("RUT existe en histórico, pero no hay coincidencia exacta de monto (o hay múltiples registros).")
        evidencias.append(build_evidencia_hist_multi(registros_hist))
        recomendaciones.append("Revisar")

    df_out = df_nom.copy()
    df_out["Estado Validación"] = estados
    df_out["Motivo"] = motivos
    df_out["Evidencia"] = evidencias
    df_out["Recomendación"] = recomendaciones

    # Resumen (lo vamos a usar en el certificado)
    resumen = {
        "total_registros": int(len(df_out)),
        "ok_solicitar": int((df_out["Recomendación"] == "Solicitar").sum()),
        "revisar": int((df_out["Recomendación"] == "Revisar").sum()),
        "no_solicitar": int((df_out["Recomendación"] == "NO RECOMIENDA SOLICITAR").sum()),
        "duplicado_nomina_actual": int((df_out["Estado Validación"] == "Duplicado en nómina actual").sum()),
        "fecha_emision": datetime.now().strftime("%d de %m de %Y %H:%M"),
    }

    return df_out, resumen



def registrar_log_validacion(usuario: str, resumen: dict):
    """
    Log separado para no mezclar con log.txt de reajustes.
    """
    linea = (
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Usuario: {usuario} | Total: {resumen.get('total_registros', 0)} | "
        f"OK: {resumen.get('ok_solicitar', 0)} | Revisar: {resumen.get('revisar', 0)} | "
        f"No solicitar: {resumen.get('no_solicitar', 0)} | PosibleDup: {resumen.get('posible_duplicidad', 0)} | "
        f"DupNomina: {resumen.get('duplicado_nomina_actual', 0)}\n"
    )
    with open("log_validacion_nomina.txt", "a", encoding="utf-8") as f:
        f.write(linea)


def generar_pdf_validacion_nomina(df_res: pd.DataFrame, resumen: dict, usuario: str = "admin") -> bytes:
    """
    PDF horizontal (landscape) con tabla estilo Excel.
    """
    buffer = BytesIO()

    # Horizontal
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(legal),
        leftMargin=24,
        rightMargin=24,
        topMargin=18,
        bottomMargin=18,
    )

    styles = getSampleStyleSheet()
    story = []

# Estilo para metadatos (interlineado 1.3)
    style_meta = ParagraphStyle(
        name="Meta13",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,   # ~1.3
        spaceAfter=4
)

# Estilo formal para textos largos (interlineado 1.5)
    style_texto = ParagraphStyle(
        name="Texto15",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=14,   # ~1.5
        spaceAfter=6
)

    # Encabezado
    fecha_emision = resumen.get("fecha_emision", datetime.now().strftime("%d de %m de %Y %H:%M"))
    emitido_por = f"Equipo de Remuneraciones – {usuario}"

    story.append(Paragraph("<b>Certificado de Validación de Nómina / Control de No Duplicidad</b>", styles["Title"]))
    story.append(Spacer(1, 6))

    meta_txt = (
        f"<b>Fecha de emisión:</b> {fecha_emision}<br/>"
        f"<b>Emitido por:</b> {emitido_por}<br/>"
        f"<b>Solicitudes de pago de finiquitos (registros):</b> {resumen.get('total_registros', 0)}<br/>"
        f"<b>DUPLICIDAD RUT + MONTO (NO RECOMIENDA SOLICITAR):</b> {resumen.get('no_solicitar', 0)}<br/>"
        f"<b>Duplicado en nómina actual:</b> {resumen.get('duplicado_nomina_actual', 0)}<br/>"
        f"<b>Registros en revisión:</b> {resumen.get('revisar', 0)}<br/>"
        f"<b>Registros sin observación (OK):</b> {resumen.get('ok_solicitar', 0)}"
    )
    story.append(Paragraph(meta_txt, styles["Normal"]))
    story.append(Spacer(1, 10))
    # Texto de alcance / exención (se imprime más abajo, antes de firmas)
    alcance = (
        "El presente certificado se emite exclusivamente en función de la comparación automatizada "
        "entre (i) la nómina actual cargada por el usuario y (ii) el control histórico cargado por el usuario. "
        "Se asume que ambos archivos son íntegros, completos, vigentes y correctamente actualizados. "
        "Por lo tanto, los resultados pueden variar si existen omisiones, inconsistencias, formatos no estándar, "
        "errores de digitación o falta de actualización en el histórico. "
        "Este certificado constituye un apoyo de control y no reemplaza la revisión operativa y aprobación "
        "de Tesorería/Finanzas."
    )


    # -------- TABLA DETALLE (solo alertas) ----------
    df_alertas = df_res[df_res["Recomendación"].isin(["NO RECOMIENDA SOLICITAR", "Revisar", "Solicitar una vez verificado"])].copy()

    story.append(Paragraph("<b>Detalle de registros con alertas</b>", styles["Heading2"]))
    story.append(Spacer(1, 6))

    if df_alertas.empty:
        story.append(Paragraph("No se detectaron alertas en los registros procesados.", styles["Normal"]))
    else:
        # Columnas a mostrar en el PDF (tabla “tipo Excel”)
        cols_pdf = [
            "Nº", "Nº CI", "Nombres", "Fecha de Finiquito", "Banco", "COD BANCO", "N° Cuenta", "Monto",
            "Estado Validación", "Recomendación"
        ]
        cols_pdf = [c for c in cols_pdf if c in df_alertas.columns]

        data = [cols_pdf]
        for _, r in df_alertas.iterrows():
            fila = []
            for c in cols_pdf:
                v = r.get(c, "")
                if v is None:
                    v = ""
                v = str(v).replace("\u00A0", " ").strip()
                # Evita “nan”
                if v.lower() == "nan":
                    v = ""
                fila.append(v)
            data.append(fila)

        # --- Ajuste de anchos para ocupar toda la página ---
        usable_width = doc.pagesize[0] - doc.leftMargin - doc.rightMargin

        # Pesos por columna (distribución visual profesional)
        weights = []
        for col in cols_pdf:
            if col == "Nombres":
                weights.append(4.5)
            elif col in ("Banco",):
                weights.append(3.0)
            elif col in ("Fecha de Finiquito",):
                weights.append(2.0)
            elif col in ("Monto",):
                weights.append(1.8)
            elif col in ("Estado Validación", "Recomendación"):
                weights.append(2.6)
            elif col in ("Nº", "COD BANCO"):
                weights.append(1.0)
            else:
                weights.append(1.5)

        total_weight = sum(weights)
        col_widths = [usable_width * (w / total_weight) for w in weights]

        tabla = Table(
            data,
            repeatRows=1,
            colWidths=col_widths,
            hAlign="LEFT"
        )

        # --- Estilo visual: limpio, corporativo, legible ---
        celeste = colors.Color(0.22, 0.50, 0.78)

        tabla.setStyle(TableStyle([
            # Encabezado
            ("BACKGROUND", (0, 0), (-1, 0), celeste),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 0), (-1, 0), 6),

            # Cuerpo
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),

            # Alineaciones específicas
            ("ALIGN", (0, 1), (0, -1), "CENTER"),   # Nº
            ("ALIGN", (-3, 1), (-1, -1), "CENTER"), # Estado / Recomendación
            ("ALIGN", (-4, 1), (-4, -1), "RIGHT"),  # Monto

            # Bordes
            ("GRID", (0, 0), (-1, -1), 0.5, celeste),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 1), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ]))

        story.append(tabla)

    # Disclaimer abajo, antes de firmas
    story.append(Spacer(1, 12))
    story.append(Paragraph("<b>Alcance y limitación de responsabilidad:</b>", style_texto))

    story.append(Spacer(1, 4))
    story.append(Paragraph(alcance, style_texto))

    # Firmas
    story.append(Spacer(1, 14))
    story.append(Paragraph("<b>Firmas</b>", styles["Heading3"]))
    story.append(Paragraph("Gestión de Personas ____________________    Finanzas / Tesorería ____________________", style_texto))


    doc.build(story)
    buffer.seek(0)
    return buffer.read()



def registrar_log(usuario: str, ipc: float, cantidad_registros: int):
    linea = (
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Usuario: {usuario} | IPC: {ipc:.2f}% | Filas: {cantidad_registros}\n"
    )
    with open("log.txt", "a", encoding="utf-8") as f:
        f.write(linea)
def leer_resumen_log():
    """
    Lee log.txt (si existe) y devuelve un DataFrame con:
    - fecha (datetime)
    - usuario (str)
    - ipc (float)
    - filas (int)
    Si no hay log, devuelve None.
    """
    if not os.path.exists("log.txt"):
        return None

    registros = []
    with open("log.txt", "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                # [YYYY-MM-DD HH:MM:SS]
                parte_fecha, resto = linea.split("]", 1)
                fecha_str = parte_fecha.strip("[")
                fecha = datetime.strptime(fecha_str, "%Y-%m-%d %H:%M:%S")

                # Usuario: X | IPC: 3.00% | Filas: 45
                partes = [p.strip() for p in resto.split("|")]

                usuario = partes[0].split("Usuario:")[1].strip()

                ipc_str = partes[1].split("IPC:")[1].strip().replace("%", "").replace(",", ".")
                ipc_val = float(ipc_str)

                filas_str = partes[2].split("Filas:")[1].strip()
                filas_val = int(filas_str)

                registros.append({
                    "fecha": fecha,
                    "usuario": usuario,
                    "ipc": ipc_val,
                    "filas": filas_val,
                })
            except Exception:
                # Si alguna línea viene rara, se ignora
                continue

    if not registros:
        return None

    return pd.DataFrame(registros)


# -----------------------------------------------------------
# ESTILOS VISUALES
# -----------------------------------------------------------

def aplicar_estilos_login():
    """
    Aplica estilos globales para la pantalla de login:
    - Paleta Indigo / Emerald
    - Modo oscuro / claro según st.session_state.tema
    - Ajuste de título y botón Ingresar
    """
    tema = st.session_state.get("tema", "dark")

    # Colores específicos PARA EL LOGIN (no para la app principal)
    if tema == "dark":
        bg_main = "#0F172A"   # fondo oscuro
        text_color = "#E5E7EB"
        btn_bg = "#CBD5E1"        # fondo botón
        btn_text = "#1E293B"      # texto botón
        btn_bg_hover = "#E2E8F0"  # hover botón
    else:
        bg_main = "#F8FAFC"   # fondo claro
        text_color = "#0F172A"
        btn_bg = "#E0E7FF"
        btn_text = "#3730A3"
        btn_bg_hover = "#C7D2FE"

    st.markdown(f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        html, body, [data-testid="stAppViewContainer"] {{
            background-color: {bg_main} !important;
            color: {text_color};
            font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
        }}

        /* Ocultar header superior de Streamlit en LOGIN */
        header[data-testid="stHeader"],
        .stAppHeader,
        [data-testid="stToolbar"] {{
            display: none !important;
            height: 0 !important;
            min-height: 0 !important;
            padding: 0 !important;
            margin: 0 !important;
            border: none !important;
        }}

        /* Ocultar sidebar en login */
        [data-testid="stSidebar"] {{
            display: none !important;
        }}

        /* Contenedor central */
        .block-container {{
            max-width: 1100px;
            padding-top: 6vh;
        }}

        /* Contenedor de login SIN rectángulo visible */
        .login-box {{
            background: transparent;
            padding: 8px 4px 26px 4px;
            border-radius: 0;
            box-shadow: none;
            border: none;
        }}

        /* Título: nombre del sistema */
        .login-title {{
            text-align: center;
            font-size: 18px;
            font-weight: 500;
            color: #6B7280;  /* gris suave */
            margin-bottom: 4px;
        }}

        /* Subtítulo: nombre de la empresa (26px, ya ajustado por ti) */
        .login-subtitle {{
            text-align: center;
            font-size: 26px;
            font-weight: 700;
            color: #6366F1;  /* indigo */
            letter-spacing: 0.03em;
            margin-bottom: 22px;
        }}

        /* Alinear verticalmente login e imagen */
        .login-align, .image-align {{
            margin-top: 40px;
        }}

        /* BOTÓN INGRESAR (LOGIN) */
        .stButton>button {{
            width: 100%;
            border-radius: 12px;
            height: 44px;
            font-weight: 600;
            font-size: 15px;
            border: none;
            background-color: {btn_bg};
            color: {btn_text};
            transition: all 0.25s ease;
        }}

        .stButton>button:hover {{
            background-color: {btn_bg_hover};
            transform: scale(1.01);
        }}
        </style>
    """, unsafe_allow_html=True)




def aplicar_estilos_app():
    """
    Estilos para la aplicación principal (después del login):
    - Fondo general según tema
    - Sidebar siempre visible
    - Topbar oscuro (modo dark) o celeste pastel (modo light)
    - Tarjetas KPI y tarjeta de resumen
    """
    tema = st.session_state.get("tema", "dark")

    if tema == "dark":
        bg_main = "#0F172A"
        text_color = "#E5E7EB"
        sidebar_bg = "#020617"
        sidebar_text = "#E5E7EB"

        sb_btn_bg = "#020617"
        sb_btn_text = "#E5E7EB"
        sb_btn_bg_hover = "#111827"

        topbar_upper_bg = "#020617"
        topbar_lower_bg = "#020617"
        topbar_link_color = "#E5E7EB"
        topbar_help_bg = "#111827"
        topbar_lower_text = "#E5E7EB"

        # Tarjetas KPI
        kpi_bg = "rgba(15,23,42,0.95)"
        kpi_subtext = "#9CA3AF"
        kpi_accent = "#22C55E"

        # Tarjeta de resumen de cálculo
        summary_bg = "rgba(15,23,42,0.85)"

    else:
        bg_main = "#F8FAFC"
        text_color = "#0F172A"
        sidebar_bg = "#FFFFFF"
        sidebar_text = "#0F172A"

        sb_btn_bg = "#E5E7EB"
        sb_btn_text = "#0F172A"
        sb_btn_bg_hover = "#CBD5E1"

        topbar_upper_bg = "#E0F2FE"
        topbar_lower_bg = "#E5F0FF"
        topbar_link_color = "#0F172A"
        topbar_help_bg = "#BFDBFE"
        topbar_lower_text = "#0F172A"

        # Tarjetas KPI
        kpi_bg = "#FFFFFF"
        kpi_subtext = "#6B7280"
        kpi_accent = "#22C55E"

        # Tarjeta de resumen de cálculo
        summary_bg = "#FFFFFF"

    st.markdown(f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        html, body, [data-testid="stAppViewContainer"] {{
            background-color: {bg_main} !important;
            color: {text_color};
            font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
        }}

        /* OCULTAR HEADER DE STREAMLIT */
        header[data-testid="stHeader"],
        .stAppHeader,
        [data-testid="stToolbar"] {{
            display: none !important;
            height: 0 !important;
            min-height: 0 !important;
            padding: 0 !important;
            margin: 0 !important;
            border: none !important;
        }}

        /* Reducir espacio superior del contenido */
        .block-container {{
            padding-top: 0.4rem !important;
        }}

        /* Sidebar SIEMPRE visible en la app (aunque el navegador lo tenga colapsado) */
        [data-testid="stSidebar"] {{
            display: block !important;
            min-width: 260px !important;
            max-width: 260px !important;
            width: 260px !important;
            transform: translateX(0px) !important;
            visibility: visible !important;
            opacity: 1 !important;
        }}

        /* Ocultar cualquier control flotante de colapsar/expandir */
        [data-testid="collapsedControl"] {{
            display: none !important;
        }}

        [data-testid="stSidebar"] > div:first-child {{
            background-color: {sidebar_bg} !important;
            color: {sidebar_text} !important;
        }}

        section[data-testid="stSidebar"] .stMarkdown p {{
            color: {sidebar_text} !important;
            font-size: 14px;
        }}

        section[data-testid="stSidebar"] .stButton>button {{
            width: 100%;
            justify-content: flex-start;
            border-radius: 10px;
            border: none;
            font-size: 14px;
            font-weight: 500;
            padding: 0.4rem 0.8rem;
            margin-bottom: 0.25rem;
            background-color: {sb_btn_bg} !important;
            color: {sb_btn_text} !important;
            transition: all 0.2s ease-in-out;
        }}

        section[data-testid="stSidebar"] .stButton>button:hover {{
            background-color: {sb_btn_bg_hover} !important;
            transform: translateX(2px);
        }}

        /* ---------- TOPBAR SUPERIOR (links) ---------- */
        .topbar-upper {{
            width: 100%;
            background-color: {topbar_upper_bg};
            padding: 6px 1.5rem;
            display: flex;
            justify-content: flex-end;
            align-items: center;
            box-shadow: 0 1px 3px rgba(0,0,0,0.35);
        }}

        .topbar-links a {{
            color: {topbar_link_color};
            text-decoration: none;
            margin-left: 18px;
            font-size: 13px;
            font-weight: 500;
        }}

        .topbar-links a:hover {{
            text-decoration: underline;
        }}

        .topbar-help {{
            display: inline-flex;
            justify-content: center;
            align-items: center;
            margin-left: 18px;
            width: 22px;
            height: 22px;
            border-radius: 999px;
            background-color: {topbar_help_bg};
            color: {topbar_link_color};
            font-size: 14px;
            font-weight: 700;
            border: 1px solid rgba(148,163,184,0.6);
            cursor: default;
        }}

        /* ---------- TOPBAR INFERIOR (título + período) ---------- */
        .topbar-lower {{
            width: 100%;
            background: {topbar_lower_bg};
            padding: 8px 1.5rem 10px 1.5rem;
            margin-bottom: 12px;
            box-shadow: 0 2px 4px rgba(15,23,42,0.45);
        }}

        .calendar-icon {{
            font-size: 18px;
            margin-top: 4px;
        }}

        .period-label {{
            font-size: 18px;
            font-weight: 600;
            color: {topbar_lower_text};
            margin-bottom: 4px;
        }}

        /* -------- TARJETAS KPI DEL HOME -------- */
        .kpi-card {{
            border-radius: 14px;
            padding: 12px 16px;
            background-color: {kpi_bg};
            box-shadow: 0 10px 25px rgba(15,23,42,0.35);
            border: 1px solid rgba(148,163,184,0.25);
        }}

        .kpi-label {{
            font-size: 12px;
            font-weight: 500;
            color: {kpi_subtext};
            margin-bottom: 4px;
        }}

        .kpi-value {{
            font-size: 22px;
            font-weight: 600;
            color: {text_color};
            margin-bottom: 2px;
        }}

        .kpi-extra {{
            font-size: 11px;
            color: {kpi_subtext};
        }}

        .kpi-chip {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 999px;
            background-color: rgba(34,197,94,0.16);
            color: {kpi_accent};
            font-size: 11px;
            font-weight: 500;
        }}

        /* -------- TARJETA DE RESUMEN DEL CÁLCULO -------- */
        .summary-card {{
            border-radius: 12px;
            padding: 10px 14px;
            margin-top: 8px;
            margin-bottom: 8px;
            background-color: {summary_bg};
            border: 1px solid rgba(148,163,184,0.35);
            font-size: 13px;
        }}

        .summary-card-title {{
            font-weight: 600;
            margin-bottom: 4px;
        }}

        .summary-card ul {{
            padding-left: 18px;
            margin: 4px 0 0 0;
        }}

        .summary-card li {{
            margin-bottom: 2px;
        }}
        </style>
    """, unsafe_allow_html=True)










# -----------------------------------------------------------
# LOGIN EN DOS COLUMNAS (IZQUIERDA LOGIN – DERECHA IMAGEN)
# -----------------------------------------------------------

def mostrar_login():
    aplicar_estilos_login()

    # Interruptor de tema
    col_t1, col_t2 = st.columns([0.7, 0.3])
    with col_t2:
        tema_actual = st.session_state.get("tema", "dark")
        label = "🌙 Modo oscuro" if tema_actual == "light" else "☀️ Modo claro"
        if st.button(label, key="toggle_tema_login"):
            st.session_state.tema = "light" if tema_actual == "dark" else "dark"
            st.rerun()

    # Layout: izquierda login, derecha imagen
    col1, col2 = st.columns([0.55, 0.45])

    with col1:
        st.markdown('<div class="login-box login-align">', unsafe_allow_html=True)

        st.markdown(
            '<div class="login-title">Sistema de Reajustes de Sueldos por IPC</div>',
            unsafe_allow_html=True
        )

        st.markdown(
            f'<div class="login-subtitle">{COMPANY_NAME.upper()}</div>',
            unsafe_allow_html=True
        )

        usuario = st.text_input(
            "Usuario",
            placeholder="Ingresa tu usuario",
            key="login_usuario"
        )

        password = st.text_input(
            "Clave",
            type="password",
            placeholder="Ingresa tu clave",
            key="login_clave"
        )

        if st.button("Ingresar", use_container_width=True, key="btn_login_principal"):
            if verificar_credenciales(usuario, password):
                st.session_state.autenticado = True
                st.session_state.usuario = usuario

                # Entrar siempre al Home después del login
                st.session_state.menu_actual = "home"
                st.session_state.post_login_redirect_done = False

                st.rerun()
            else:
                st.error("Usuario o clave incorrectos. Intente nuevamente.")

        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="image-align">', unsafe_allow_html=True)

        tema_actual = st.session_state.get("tema", "dark")
        from pathlib import Path

        base_dir = Path(__file__).resolve().parent
        tema_actual = st.session_state.get("tema", "dark")

        nombre_img = "login_image_dark.png" if tema_actual == "dark" else "login_image_light.png"
        img_path = base_dir / "assets" / nombre_img

        if img_path.exists():
            st.image(str(img_path), width=430)
        else:
            st.warning(f"Imagen no encontrada: assets/{nombre_img}")


        st.markdown("</div>", unsafe_allow_html=True)






# -----------------------------------------------------------
# APLICACIÓN PRINCIPAL
# -----------------------------------------------------------
def _norm_colname(x) -> str:
    """
    Normaliza nombres de columnas:
    - quita espacios extremos
    - colapsa espacios múltiples a 1
    - unifica variantes típicas
    """
    if x is None:
        return ""
    s = str(x).strip()
    s = " ".join(s.split())  # colapsa espacios
    # unificaciones puntuales
    s = s.replace("COD BANCO", "COD BANCO")
    s = s.replace("COD       BANCO", "COD BANCO")
    s = s.replace("Nº CI", "Nº CI")
    s = s.replace("Nº   CI", "Nº CI")
    s = s.replace("N° Cuenta", "N° Cuenta")
    s = s.replace("N° Cuenta", "N° Cuenta")
    return s


def _drop_unnamed_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols_ok = []
    for c in df.columns:
        cs = str(c).strip()
        if cs == "":
            continue
        if cs.lower().startswith("unnamed"):
            continue
        cols_ok.append(c)
    return df[cols_ok].copy()


def leer_excel_con_header_auto(file, sheet_name=0, max_scan_rows=25) -> pd.DataFrame:
    """
    Lee un Excel buscando automáticamente la fila donde están los encabezados reales.
    (Sirve para tu archivo A que tiene títulos arriba)
    """
    df_raw = pd.read_excel(file, sheet_name=sheet_name, header=None, dtype=str)

    header_row = None
    for i in range(min(max_scan_rows, len(df_raw))):
        fila = df_raw.iloc[i].tolist()
        fila_norm = {_norm_colname(v) for v in fila if v is not None and str(v).strip() != ""}

        # Señales claras de tu plantilla A
        if {"Nº", "Nº CI", "Nombres"}.issubset(fila_norm):
            header_row = i
            break

    if header_row is None:
        # fallback: intenta la primera fila
        header_row = 0

    df = pd.read_excel(file, sheet_name=sheet_name, header=header_row, dtype=str)

    # limpia columnas "Unnamed" típicas de merges
    df = _drop_unnamed_columns(df)

    # normaliza nombres de columnas
    df.columns = [_norm_colname(c) for c in df.columns]

    # elimina filas totalmente vacías
    df = df.dropna(how="all")

    return df


def preparar_historico(df_hist_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Histórico (archivo B):
    - elimina columnas vacías/unnamed
    - normaliza columnas
    - considera SOLO la fecha de PAGO como fecha relevante (Fecha ref)
      e ignora Solicitud.
    """
    df = df_hist_raw.copy()
    df = _drop_unnamed_columns(df)
    df.columns = [_norm_colname(c) for c in df.columns]
    df = df.dropna(how="all")

    # Mantener solo columnas que interesan (si existen)
    cols_keep = [c for c in ["Nº", "Nombres", "Banco", "N° Cuenta", "Rut", "Monto", "Solicitud", "Pago", "Observaciones"] if c in df.columns]
    df = df[cols_keep].copy()

    # Regla: usar la penúltima fecha relevante -> aquí usaremos "Pago" como fecha relevante
    # e ignoramos "Solicitud". Si no existe "Pago", usamos "Solicitud".
    if "Pago" in df.columns:
        df["Fecha ref"] = df["Pago"]
    elif "Solicitud" in df.columns:
        df["Fecha ref"] = df["Solicitud"]
    else:
        df["Fecha ref"] = ""

    return df

def mostrar_validacion_nomina():
    st.title("Validación de nómina – Control finiquitos")
    st.markdown("Carga 2 archivos Excel: **nómina actual** y **control histórico**. El sistema marcará duplicidades y emitirá Excel/PDF.")
    st.markdown("### Plantilla sugerida (nómina actual)")
    plantilla_validacion = generar_plantilla_validacion_nomina()
    st.download_button(
        label="Descargar plantilla Excel (formato oficial nómina actual)",
        data=plantilla_validacion,
        file_name="plantilla_validacion_nomina_actual.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_plantilla_validacion_nomina_v1",
    )

    st.markdown("---")
    st.markdown("### Plantilla sugerida (control histórico)")
    plantilla_historico = generar_plantilla_historico()
    st.download_button(
        label="Descargar plantilla Excel (formato histórico)",
        data=plantilla_historico,
        file_name="plantilla_validacion_nomina_historico.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_plantilla_validacion_historico_v1",
    )

    st.markdown("---")

    col1, col2 = st.columns(2)

    with col1:
        archivo_nomina = st.file_uploader(
            "A) Nómina actual a solicitar (.xlsx)",
            type=["xlsx"],
            key="upl_nomina_actual_v1"
        )

    with col2:
        archivo_hist = st.file_uploader(
            "B) Control histórico finiquitos (.xlsx)",
            type=["xlsx"],
            key="upl_hist_validacion_v1"
        )




    st.markdown("---")
    ejecutar = st.button("Ejecutar validación", type="primary", key="btn_ejecutar_validacion_v1")

    if not ejecutar:
        return

    if archivo_nomina is None or archivo_hist is None:
        st.error("Debes cargar ambos archivos (nómina actual e histórico).")
        return

    try:
        # A) Nómina actual: header no está en la primera fila -> lectura inteligente
        df_nomina = leer_excel_con_header_auto(archivo_nomina, sheet_name=0)

        # B) Histórico: normalmente header sí está arriba, pero viene con columnas extra
        df_hist_raw = pd.read_excel(archivo_hist, sheet_name=0, dtype=str)
        df_hist = preparar_historico(df_hist_raw)

    except Exception as e:
        st.error(f"No se pudo leer uno de los Excel. Detalle: {e}")
        return


    ok1, msg1 = validar_columnas_minimas(df_nomina, COLS_NOMINA_VALIDACION)
    if not ok1:
        st.error(f"Nómina actual inválida: {msg1}")
        return

    ok2, msg2 = validar_columnas_minimas(df_hist, COLS_HISTORICO_VALIDACION)
    if not ok2:
        st.error(f"Histórico inválido: {msg2}")
        return



    df_res, resumen = motor_validacion_nomina(df_nomina, df_hist)

    st.success("Validación completada.")
    st.json(resumen)

    st.dataframe(df_res, use_container_width=True)

    usuario = st.session_state.get("usuario", "admin")

    # Excel de salida: reutiliza tu función existente convertir_df_a_excel (ya está en tu código) :contentReference[oaicite:5]{index=5}
    meta = {
        "Fecha emisión": resumen.get("fecha_emision", ""),
        "Usuario": usuario,
        "Total registros": resumen.get("total_registros", 0),
        "OK": resumen.get("ok_solicitar", 0),
        "Revisar": resumen.get("revisar", 0),
        "No solicitar": resumen.get("no_solicitar", 0),
        "Posible duplicidad": resumen.get("posible_duplicidad", 0),
        "Duplicado nómina": resumen.get("duplicado_nomina_actual", 0),
    }

    excel_bytes = convertir_df_a_excel(df_res, meta=meta, sheet_name="Validacion")
    st.download_button(
        
        "Descargar Excel resultado",
        data=excel_bytes,
        file_name=f"resultado_validacion_nomina_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_excel_validacion_v1"
    )
    pdf_bytes = generar_pdf_validacion_nomina(df_res, resumen, usuario=usuario)
    st.download_button(
        "Descargar PDF certificado",
        data=pdf_bytes,
        file_name=f"certificado_validacion_nomina_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
        mime="application/pdf",
        key="dl_pdf_validacion_v1"
    )

    registrar_log_validacion(usuario, resumen)

def mostrar_app_reajuste():
    # 1) Estilos de la app (evita que quede aplicado el CSS del login)
    aplicar_estilos_app()

    # 2) Asegurar menú por defecto
    if "menu_actual" not in st.session_state:
        st.session_state.menu_actual = "home"

    # ---------------- SIDEBAR (tema + menú) ----------------
    tema_actual = st.session_state.get("tema", "dark")
    st.sidebar.markdown("### Apariencia")

    modo_oscuro = st.sidebar.checkbox(
        "Modo oscuro",
        value=(tema_actual == "dark"),
        key="chk_tema_sidebar"
    )

    nuevo_tema = "dark" if modo_oscuro else "light"
    if nuevo_tema != tema_actual:
        st.session_state.tema = nuevo_tema
        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Menú")

    # Menú único (botones)
    menu_actual = st.session_state.get("menu_actual", "home")

    if st.sidebar.button("🏠 Home", key="menu_home"):
        menu_actual = "home"
    if st.sidebar.button("👤 Mi perfil", key="menu_profile"):
        menu_actual = "profile"
    if st.sidebar.button("🕒 Historial", key="menu_history"):
        menu_actual = "history"
    if st.sidebar.button("✅ Procesos", key="menu_processes"):
        menu_actual = "processes"
    if st.sidebar.button("🧾 Validación de nómina", key="menu_validacion_nomina"):
        menu_actual = "validacion_nomina"

    if st.sidebar.button("⏻ Cerrar sesión", key="menu_logout"):
        st.session_state.autenticado = False
        st.session_state.usuario = None
        st.session_state.menu_actual = "home"
        st.session_state.post_login_redirect_done = False
        st.rerun()

    st.session_state.menu_actual = menu_actual

    # ---------------- TOPBAR PARTE 1: ENLACES ----------------
    topbar_upper_html = """
    <div class="topbar-upper">
        <div class="topbar-links">
            <a href="https://www.sii.cl/valores_y_fechas/utm/utm2025.htm"
               target="_blank" rel="noopener noreferrer">IPC SII</a>
            <a href="https://calculadoraipc.ine.cl/"
               target="_blank" rel="noopener noreferrer">Calculadora IPC INE</a>
            <span class="topbar-help" title="Ayuda y contacto">?</span>
        </div>
    </div>
    """
    st.markdown(topbar_upper_html, unsafe_allow_html=True)

    # =========================================================
    # 3) ROUTER PRINCIPAL: si NO es procesos, renderiza y SALE.
    # =========================================================

    # HOME
    if menu_actual == "home":
        st.title("Panel principal – RentAdjust Pro")

        st.markdown(
            """
            ### ¿Qué es RentAdjust Pro?

            RentAdjust Pro es un sistema diseñado para **reajustar masivamente sueldos o rentas**
            utilizando la variación del **Índice de Precios al Consumidor (IPC)** publicada por
            organismos oficiales (por ejemplo SII / INE en Chile).

            #### ¿Qué hace el sistema?

            - Permite cargar una planilla Excel con los trabajadores y sus conceptos de renta  
              (sueldo base, colación, movilización, etc.).
            - Obtiene el IPC de forma **manual o automática** (mensual, trimestral, semestral,
              cuatrimestral o anual).  
            - Aplica el reajuste a todos los conceptos seleccionados.
            - Genera:
              - Un archivo **Excel** con el detalle de los montos reajustados.
              - Un **PDF** tipo certificado para respaldo y firmas de autorización.
            - Registra cada operación en un **log de auditoría** (fecha, usuario, IPC utilizado, filas).
            """
        )

        st.markdown("---")

        df_log = leer_resumen_log()
        if df_log is None:
            st.markdown(
                """
                Aún no hay operaciones de reajuste registradas en el sistema.  
                Ejecuta al menos un cálculo en la sección **Procesos** para ver aquí
                un resumen automático.
                """
            )
            return

        total_ops = len(df_log)
        total_filas = int(df_log["filas"].sum())
        ipc_promedio = float(df_log["ipc"].mean())
        ultima = df_log.sort_values("fecha").iloc[-1]

        fecha_primera = df_log["fecha"].min().strftime("%d-%m-%Y")
        fecha_ultima = ultima["fecha"].strftime("%d-%m-%Y %H:%M")

        col_a, col_b, col_c = st.columns(3)

        with col_a:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">Operaciones de reajuste</div>
                    <div class="kpi-value">{total_ops}</div>
                    <div class="kpi-extra">
                        Desde: {fecha_primera}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with col_b:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">Filas totales procesadas</div>
                    <div class="kpi-value">{total_filas}</div>
                    <div class="kpi-extra">
                        IPC promedio: {ipc_promedio:.2f}%
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with col_c:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">Última operación</div>
                    <div class="kpi-value">{fecha_ultima}</div>
                    <div class="kpi-extra">
                        Usuario: {ultima.get("usuario", "N/D")}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        return

    # PERFIL
    if menu_actual == "profile":
        st.title("Mi perfil")
        st.markdown(
            f"""
            Usuario conectado: **{st.session_state.usuario}**  

            En versiones futuras aquí podrás editar tus datos, preferencias de tema
            y ver información básica de contacto.
            """
        )
        return

    # HISTORIAL
    if menu_actual == "history":
        st.title("Historial de reajustes")

        df_log = leer_resumen_log()
        if df_log is None:
            st.info("Aún no hay registros en el log de operaciones.")
            return

        df_log = df_log.sort_values("fecha", ascending=False)

        st.markdown(
            """
            A continuación se muestra el **log de operaciones** registradas en el sistema.  
            Cada fila corresponde a un cálculo de reajuste ejecutado.
            """
        )

        st.dataframe(
            df_log.assign(
                fecha=df_log["fecha"].dt.strftime("%Y-%m-%d %H:%M:%S")
            ),
            use_container_width=True
        )

        st.markdown(
            f"""
            - Total de operaciones registradas: **{len(df_log)}**  
            - Total de filas procesadas (suma de todas las operaciones): **{int(df_log["filas"].sum()):,}**
            """
        )
        return
    # VALIDACIÓN NÓMINA (NUEVO)
    if menu_actual == "validacion_nomina":
        mostrar_validacion_nomina()
        return

    # =========================================================
    # 4) SI LLEGA ACÁ, ES "processes": recién ahora dibuja el flujo
    # =========================================================

    # ---------------- TOPBAR PARTE 2: PERÍODO ----------------
    ahora = datetime.now()
    mes_default = ahora.month
    anno_default = ahora.year

    st.markdown('<div class="topbar-lower">', unsafe_allow_html=True)
    col_per_1, col_per_2, col_per_3, col_spacer = st.columns([0.05, 0.22, 0.22, 0.51])

    with col_per_1:
        st.markdown('<span class="calendar-icon">📅</span>', unsafe_allow_html=True)

    with col_per_2:
        st.markdown(
            '<div class="period-label">Período remuneracional</div>',
            unsafe_allow_html=True
        )
        mes_idx = st.selectbox(
            "Mes",
            options=list(range(1, 13)),
            format_func=lambda m: MESES_ES[m - 1],
            index=mes_default - 1,
            key="topbar_periodo_mes"
        )

    with col_per_3:
        st.markdown("<br>", unsafe_allow_html=True)
        anno = st.number_input(
            "Año",
            min_value=2000,
            max_value=2100,
            value=anno_default,
            step=1,
            key="topbar_periodo_anno"
        )

    st.markdown("</div>", unsafe_allow_html=True)

    # ---------------- CONTENIDO PRINCIPAL (PROCESOS) ----------------
    st.title("Reajuste de Sueldos por IPC")

    # A partir de aquí deja tu bloque de Procesos tal como lo tienes
    # (Información general, carga Excel, IPC, cálculo, descargas...)
    # ----------------------------------------------------------------
    # NOTA: desde aquí puedes pegar tu código existente de "Procesos"
    # empezando en: "st.subheader('1. Información general del proceso')"
    # ----------------------------------------------------------------

    # === PEGA AQUÍ TU CÓDIGO DE PROCESOS EXISTENTE ===

    # ---------------- 2. Cargar archivo Excel de trabajadores ----------------
    st.subheader("2. Cargar archivo Excel de trabajadores")

    plantilla_bytes = generar_plantilla_excel()
    st.download_button(
    label="Descargar plantilla Excel (formato sugerido)",
    data=plantilla_bytes,
    file_name="plantilla_reajuste_ipc.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    key="dl_plantilla_excel_reajuste_ipc",
    )


    columnas_identificacion = ["Rut", "Nombre", "Fecha Ingreso"]
    columnas_conceptos = []
    df_original = None
    st.session_state["col_tasa_archivo"] = None

    archivo = st.file_uploader(
        "Selecciona un archivo Excel (.xlsx) con las columnas de identificación y conceptos",
        type=["xlsx"],
        key="uploader_excel_trabajadores",
    )

    if archivo is None:
        st.info("Carga un archivo Excel para continuar con el proceso.")
        st.stop()

    try:
        df_original = pd.read_excel(archivo, engine="openpyxl")
    except Exception as e:
        st.error(f"Error al leer el archivo Excel: {e}")
        st.stop()

    st.write("Vista previa de los datos cargados:")
    st.dataframe(df_original.head(), use_container_width=True)

    # --- Detectar columna de tasa IPC individual (opcional) ---
    posibles_cols_tasa = [
        "Tasa IPC manual (%)",
        "IPC Archivo (%)",
        "IPC archivo",
        "IPC (%)",
        "Tasa IPC",
        "Tasa (%)",
        "IPC Individual",
        "IPC Efectivo (%)",
    ]

    col_tasa_archivo = next(
        (c for c in posibles_cols_tasa if c in df_original.columns),
        None
    )
    st.session_state["col_tasa_archivo"] = col_tasa_archivo

    if col_tasa_archivo:
        st.info(
            f"Se detectó una columna de tasa IPC individual: **{col_tasa_archivo}**. "
            "Esta tasa tendrá prioridad sobre la tasa calculada."
        )

    # Validar columnas mínimas
    faltantes = [c for c in columnas_identificacion if c not in df_original.columns]
    if faltantes:
        st.error(
            "Faltan columnas obligatorias: " + ", ".join(faltantes)
        )
        st.stop()

    # Detectar columnas de conceptos
    excluir = set(columnas_identificacion)
    if col_tasa_archivo:
        excluir.add(col_tasa_archivo)

    columnas_conceptos = [c for c in df_original.columns if c not in excluir]

    if not columnas_conceptos:
        st.error("No se encontraron columnas de conceptos remuneracionales.")
        st.stop()

    if len(columnas_conceptos) > 7:
        columnas_conceptos = columnas_conceptos[:7]
        st.warning(
            "Se usarán solo las primeras 7 columnas de conceptos: "
            + ", ".join(columnas_conceptos)
        )

    for col in columnas_conceptos:
        df_original[col] = pd.to_numeric(df_original[col], errors="coerce").fillna(0)

    # Normalizar mes/año a int con valores seguros
    try:
        mes_idx = int(mes_idx)
    except Exception:
        mes_idx = mes_default

    try:
        anno = int(anno)
    except Exception:
        anno = anno_default

    periodo_str = f"{MESES_ES[mes_idx - 1]} {anno}"
    st.session_state["periodo_str"] = periodo_str

    # ---------- CONTENIDO SEGÚN MENÚ ----------

    # HOME
    if menu_actual == "home":
        st.title("Panel principal – RentAdjust Pro")

        st.markdown(
            """
            ### ¿Qué es RentAdjust Pro?

            RentAdjust Pro es un sistema diseñado para **reajustar masivamente sueldos o rentas**
            utilizando la variación del **Índice de Precios al Consumidor (IPC)** publicada por
            organismos oficiales (por ejemplo SII / INE en Chile).

            #### ¿Qué hace el sistema?

            - Permite cargar una planilla Excel con los trabajadores y sus conceptos de renta  
              (sueldo base, colación, movilización, etc.).
            - Obtiene el IPC de forma **manual o automática** (mensual, trimestral, semestral,
              cuatrimestral o anual).  
            - Aplica el reajuste a todos los conceptos seleccionados.
            - Genera:
              - Un archivo **Excel** con el detalle de los montos reajustados.
              - Un **PDF** tipo certificado para respaldo y firmas de autorización.
            - Registra cada operación en un **log de auditoría** (fecha, usuario, IPC utilizado, filas).

            #### Recomendaciones de uso

            - Verifica siempre que el IPC utilizado coincida con las fuentes oficiales (SII / INE).
            - Conserva los Excel y PDF generados como respaldo de auditoría interna y externa.
            - Realiza primero una **prueba con pocos registros** antes de ejecutar un reajuste masivo.
            - Define un procedimiento interno de aprobación (jefatura / gerencia) antes de aplicar
              los cambios definitivos en tu sistema de remuneraciones.
            """
        )

        st.markdown("---")

        df_log = leer_resumen_log()
        if df_log is None:
            st.markdown(
                """
                Aún no hay operaciones de reajuste registradas en el sistema.  
                Ejecuta al menos un cálculo en la sección **Procesos** para ver aquí
                un resumen automático.
                """
            )
            return

        total_ops = len(df_log)
        total_filas = int(df_log["filas"].sum())
        ipc_promedio = float(df_log["ipc"].mean())
        ultima = df_log.sort_values("fecha").iloc[-1]

        fecha_primera = df_log["fecha"].min().strftime("%d-%m-%Y")
        fecha_ultima = ultima["fecha"].strftime("%d-%m-%Y %H:%M")

        col_a, col_b, col_c = st.columns(3)

        with col_a:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">Operaciones de reajuste</div>
                    <div class="kpi-value">{total_ops}</div>
                    <div class="kpi-extra">
                        Desde: {fecha_primera}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with col_b:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">Filas totales procesadas</div>
                    <div class="kpi-value">{total_filas}</div>
                    <div class="kpi-extra">
                        IPC promedio: {ipc_promedio:.2f}%
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with col_c:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">Última operación</div>
                    <div class="kpi-value">{fecha_ultima}</div>
                    <div class="kpi-extra">
                        Usuario: {ultima.get("usuario", "N/D")}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        return

    # PERFIL
    if menu_actual == "profile":
        st.title("Mi perfil")
        st.markdown(
            f"""
            Usuario conectado: **{st.session_state.usuario}**  

            En versiones futuras aquí podrás editar tus datos, preferencias de tema
            y ver información básica de contacto.
            """
        )
        return

    # HISTORIAL
    if menu_actual == "history":
        st.title("Historial de reajustes")

        df_log = leer_resumen_log()
        if df_log is None:
            st.info("Aún no hay registros en el log de operaciones.")
            return

        df_log = df_log.sort_values("fecha", ascending=False)

        st.markdown(
            """
            A continuación se muestra el **log de operaciones** registradas en el sistema.  
            Cada fila corresponde a un cálculo de reajuste ejecutado.
            """
        )

        st.dataframe(
            df_log.assign(
                fecha=df_log["fecha"].dt.strftime("%Y-%m-%d %H:%M:%S")
            ),
            use_container_width=True
        )

        st.markdown(
            f"""
            - Total de operaciones registradas: **{len(df_log)}**  
            - Total de filas procesadas (suma de todas las operaciones): **{int(df_log["filas"].sum()):,}**
            """
        )
        return
    # VALIDACIÓN NÓMINA (NUEVO MÓDULO INDEPENDIENTE)
    if menu_actual == "validacion_nomina":
        mostrar_validacion_nomina()
        return

    # Si no es ninguno de los anteriores, asumimos "processes"
    # ---------------- CONTENIDO PRINCIPAL (PROCESOS) ----------------
    st.title("Reajuste de Sueldos por IPC")

    st.markdown(
        """
        Esta aplicación permite:
        - Cargar un archivo Excel con las columnas: **Rut, Nombre, Fecha Ingreso** y hasta 7 columnas de conceptos abiertos.  
        - Definir el período remuneracional (mes y año).  
        - Definir el IPC:
          - Manual (un solo valor), o  
          - Automático, sumando IPC mensuales (mensual / trimestral / semestral / cuatrimestral / anual) obtenidos desde el SII.  
        - Calcular los nuevos valores reajustados por IPC para cada concepto.  
        - Descargar el resultado en Excel.  
        - Generar un PDF horizontal con el detalle por trabajador, indicando empresa y período.
        """
    )

    # ---------------- 1. INFORMACIÓN GENERAL DEL PROCESO ----------------
    st.subheader("1. Información general del proceso")

    empresa = st.text_input(
        "Empresa",
        value=st.session_state.proceso_empresa or COMPANY_NAME,
        placeholder="Ejemplo: R&Q Ingeniería SpA"
    )

    col_meta_1, col_meta_2 = st.columns(2)

    with col_meta_1:
        cc = st.text_input(
            "Centro de costo",
            value=st.session_state.proceso_cc,
            placeholder="Ejemplo: 1205 - GINFRA Chuqui"
        )
        solicitante = st.text_input(
            "Nombre de quien solicita",
            value=st.session_state.proceso_solicitante,
            placeholder="Ejemplo: Daniel Alcayaga"
        )

    with col_meta_2:
        proyecto = st.text_input(
            "Nombre del proyecto",
            value=(
                st.session_state.proyecto
                if hasattr(st.session_state, "proyecto")
                else st.session_state.proceso_proyecto
            ),
            placeholder="Ejemplo: Reajuste IPC contratos GINFRA 2025"
        )
        tipo_proc = st.radio(
            "Tipo de procesamiento",
            options=["Prueba", "Definitivo"],
            index=0 if st.session_state.proceso_tipo == "Prueba" else 1,
            horizontal=True
        )

    considerar_fi = st.radio(
        "¿Considerar fecha de ingreso para el cálculo del IPC?",
        options=["No", "Sí"],
        index=0 if st.session_state.proceso_considera_fecha == "No" else 1,
        horizontal=True,
        help=(
            "Si eliges 'Sí', el IPC se aplicará solo a trabajadores que cumplan el tramo "
            "según su fecha de ingreso."
        )
    )

    obs = st.text_area(
        "Observaciones del proceso (opcional)",
        value=st.session_state.proceso_obs,
        height=60,
        placeholder=(
            "Ejemplo: Primera corrida de validación; pendiente revisión "
            "de Gerencia de Finanzas."
        )
    )

    # Guardar en session_state
    st.session_state.proceso_empresa = empresa or COMPANY_NAME
    st.session_state.proceso_cc = cc
    st.session_state.proceso_proyecto = proyecto
    st.session_state.proceso_solicitante = solicitante
    st.session_state.proceso_tipo = tipo_proc
    st.session_state.proceso_obs = obs
    st.session_state.proceso_considera_fecha = considerar_fi

    st.markdown(
        """
        <div class="summary-card">
            <div class="summary-card-title">Resumen del proceso configurado</div>
            <ul>
                <li><strong>Empresa:</strong> {emp}</li>
                <li><strong>Centro de costo:</strong> {cc}</li>
                <li><strong>Proyecto:</strong> {proy}</li>
                <li><strong>Solicitante:</strong> {sol}</li>
                <li><strong>Tipo de procesamiento:</strong> {tipo}</li>
                <li><strong>Considerar fecha de ingreso:</strong> {cons}</li>
            </ul>
        </div>
        """.format(
            emp=empresa or COMPANY_NAME,
            cc=cc or "No indicado",
            proy=proyecto or "No indicado",
            sol=solicitante or "No indicado",
            tipo=tipo_proc,
            cons=considerar_fi,
        ),
        unsafe_allow_html=True
    )

    # ---------------- 2. CARGA DE ARCHIVO ----------------
    st.subheader("2. Cargar archivo Excel de trabajadores")

    plantilla_bytes = generar_plantilla_excel()
    st.download_button(
        label="Descargar plantilla Excel (formato sugerido)",
        data=plantilla_bytes,
        file_name="plantilla_reajuste_ipc.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # ---------------- 3. IPC ----------------
    st.subheader("3. Definir IPC a aplicar")

    modo_ipc = st.radio(
        "¿Cómo quieres definir el IPC?",
        (
            "Ingresar un valor manual único",
            "Calcular IPC en base a meses (mensual / trimestral / semestral / cuatrimestral / anual / rango libre)",
        ),
        key="modo_ipc_radio",
    )

    ipc = 0.0

    if modo_ipc == "Ingresar un valor manual único":
        ipc = st.number_input(
            "IPC (%)",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=0.1,
            help="Ejemplo: si el IPC acumulado es 3%, ingresa 3.0",
            key="ipc_manual_unico",
        )
        st.session_state.tipo_periodo_ipc = None
        st.session_state.n_meses_ipc = None
        meses_previos = []

    else:
        tipo_periodo_ipc = st.selectbox(
            "Tipo de período para calcular el IPC",
            options=[
                "Mensual",
                "Trimestral",
                "Semestral",
                "Cuatrimestral",
                "Anual",
                "Rango libre",
            ],
            key="tipo_periodo_ipc_select",
        )

        # --- Construcción de meses_previos ---
        if tipo_periodo_ipc == "Rango libre":
            lim_mes, lim_anno = ultimo_mes_disponible_por_fecha_actual()
            st.info(f"Máximo permitido (no futuro): {MESES_ES[lim_mes - 1]} {lim_anno}")

            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**Desde**")
                desde_anno = st.number_input(
                    "Año desde",
                    min_value=2000,
                    max_value=lim_anno,
                    value=2021 if lim_anno >= 2021 else lim_anno,
                    step=1,
                    key="rango_desde_anno",
                )
                desde_mes = st.selectbox(
                    "Mes desde",
                    options=list(range(1, 13)),
                    format_func=lambda m: MESES_ES[m - 1],
                    index=0,
                    key="rango_desde_mes",
                )

            with col2:
                st.markdown("**Hasta**")
                hasta_anno = st.number_input(
                    "Año hasta",
                    min_value=2000,
                    max_value=lim_anno,
                    value=lim_anno,
                    step=1,
                    key="rango_hasta_anno",
                )

                max_mes_hasta = lim_mes if int(hasta_anno) == int(lim_anno) else 12
                hasta_mes = st.selectbox(
                    "Mes hasta",
                    options=list(range(1, max_mes_hasta + 1)),
                    format_func=lambda m: MESES_ES[m - 1],
                    index=max_mes_hasta - 1,
                    key="rango_hasta_mes",
                )

            # Validación: no futuro
            if (int(hasta_anno) > lim_anno) or (
                int(hasta_anno) == lim_anno and int(hasta_mes) > lim_mes
            ):
                st.error(
                    f"No puedes seleccionar meses futuros. Máximo: {MESES_ES[lim_mes - 1]} {lim_anno}."
                )
                st.stop()

            # Validación: orden
            if (int(desde_anno) > int(hasta_anno)) or (
                int(desde_anno) == int(hasta_anno) and int(desde_mes) > int(hasta_mes)
            ):
                st.error("El rango 'Desde' no puede ser mayor que el rango 'Hasta'.")
                st.stop()

            meses_previos = generar_meses_rango(
                int(desde_mes), int(desde_anno), int(hasta_mes), int(hasta_anno)
            )

        else:
            if tipo_periodo_ipc == "Mensual":
                n_meses = 1
            elif tipo_periodo_ipc == "Trimestral":
                n_meses = 3
            elif tipo_periodo_ipc == "Semestral":
                n_meses = 6
            elif tipo_periodo_ipc == "Cuatrimestral":
                n_meses = 4
            else:  # Anual
                n_meses = 12

            meses_previos = calcular_meses_previos(mes_idx, int(anno), n_meses)

        # Guardar siempre en session_state
        st.session_state.tipo_periodo_ipc = tipo_periodo_ipc
        st.session_state.n_meses_ipc = len(meses_previos)

        st.markdown(
            "La aplicación intentará obtener automáticamente el IPC mensual desde el SII "
            "(UTM-UTA-IPC). Verifica siempre que los resultados coincidan con las fuentes "
            "oficiales (SII / INE)."
        )

        try:
            ipc_auto, df_detalle_ipc = obtener_ipc_desde_sii_para_meses(meses_previos)
            st.write("Detalle IPC mensual obtenido desde SII:")
            st.table(df_detalle_ipc)

            ipc = ipc_auto
            st.success(
                f"IPC aplicado al período {periodo_str}: "
                f"{ipc:.2f}% (suma de variaciones mensuales SII)"
            )
        except Exception as e:
            st.error(f"No se pudo obtener automáticamente el IPC desde el SII: {e}")
            st.markdown("Ingresa manualmente el IPC mensual (en %) para cada mes indicado.")

            valores_ipc = []
            for m, a in meses_previos:
                etiqueta_mes = MESES_ES[m - 1]
                valor = st.number_input(
                    f"IPC {etiqueta_mes} {a} (%)",
                    min_value=-100.0,
                    max_value=100.0,
                    value=0.0,
                    step=0.1,
                    key=f"ipc_manual_{m}_{a}",
                )
                valores_ipc.append(valor)

            ipc = sum(valores_ipc)
            st.info(
                f"IPC total aplicado al período {periodo_str}: "
                f"{ipc:.2f}% (ingresado manualmente)"
            )




    # ---------------- 4. CÁLCULO DEL REAJUSTE ----------------
    st.subheader("4. Calcular reajuste")

    if st.button("Calcular reajuste de sueldos", key="btn_calcular_reajuste"):
        if df_original is None:
            st.error("Primero debes cargar un archivo Excel válido.")
            return
        if ipc <= 0:
            st.error("El IPC debe ser mayor a 0 para realizar el cálculo.")
            return
        if not columnas_conceptos:
            st.error("No hay columnas de conceptos configuradas para el cálculo.")
            return

        df_resultado = df_original.copy()

        # Empresa desde el formulario (si no, constante)
        empresa_proceso = st.session_state.get("proceso_empresa", COMPANY_NAME)

        # Metadatos base
        df_resultado["Empresa"] = empresa_proceso
        df_resultado["Periodo Remuneracional"] = periodo_str
        df_resultado["IPC Global (%)"] = float(ipc)  # siempre existe

        # --------- LÓGICA DE FECHA DE INGRESO / TRAMOS (Calcula "Tasa IPC (%)") ---------
        considerar_fecha = st.session_state.get("proceso_considera_fecha", "No") == "Sí"
        tipo_periodo_ipc = st.session_state.get("tipo_periodo_ipc", None)
        n_meses_ipc = st.session_state.get("n_meses_ipc", None)

        periodo_mes = int(mes_idx)
        periodo_anno = int(anno)

        reglas_tramo_validas = (
            considerar_fecha
            and tipo_periodo_ipc is not None
            and n_meses_ipc is not None
        )

        if reglas_tramo_validas:
            if "Fecha Ingreso" not in df_resultado.columns:
                st.warning(
                    "Se seleccionó 'Considerar fecha de ingreso', "
                    "pero el archivo no contiene la columna 'Fecha Ingreso'. "
                    "Se aplicará el IPC a todos por igual."
                )
                reglas_tramo_validas = False

        if reglas_tramo_validas:
            # Asegurar tipo fecha
            df_resultado["Fecha Ingreso"] = pd.to_datetime(df_resultado["Fecha Ingreso"], errors="coerce")

            antiguedades = []
            aplica = []

            for _, fila in df_resultado.iterrows():
                fi = fila.get("Fecha Ingreso", pd.NaT)
                meses_trab = meses_trabajados_hasta_periodo(fi, periodo_mes, periodo_anno)
                antiguedades.append(meses_trab)

                if meses_trab is None:
                    aplica.append(False)
                else:
                    aplica.append(meses_trab >= int(n_meses_ipc))

            df_resultado["Antigüedad (meses)"] = antiguedades
            df_resultado["Aplica IPC por fecha ingreso"] = aplica
            df_resultado["Caso IPC"] = df_resultado["Aplica IPC por fecha ingreso"].map(lambda x: "A" if x else "C")

            # Tasa calculada por fecha: si no aplica, 0; si aplica, IPC global
            df_resultado["Tasa IPC (%)"] = df_resultado["Aplica IPC por fecha ingreso"].astype(float) * float(ipc)
        else:
            # Comportamiento tradicional: todos con la misma tasa
            df_resultado["Antigüedad (meses)"] = None
            df_resultado["Caso IPC"] = "A"
            df_resultado["Tasa IPC (%)"] = float(ipc)

        # --------- PRIORIDAD DE TASA (Manual > Calculada) ---------
        # Tasa calculada base (puede venir como "Tasa IPC (%)")
        if "Tasa IPC (%)" in df_resultado.columns:
            df_resultado["Tasa IPC Calculada"] = pd.to_numeric(df_resultado["Tasa IPC (%)"], errors="coerce")
        elif "Tasa IPC" in df_resultado.columns:
            df_resultado["Tasa IPC Calculada"] = pd.to_numeric(df_resultado["Tasa IPC"], errors="coerce")
        else:
            df_resultado["Tasa IPC Calculada"] = float(ipc)

        # Tasa manual desde archivo (plantilla)
        col_manual = None
        for c in ["Tasa IPC Manual (%)", "Tasa IPC manual (%)", "Tasa IPC Manual", "Tasa IPC manual"]:
            if c in df_resultado.columns:
                col_manual = c
                break

        if col_manual:
            df_resultado["Tasa IPC Manual"] = pd.to_numeric(df_resultado[col_manual], errors="coerce")
        else:
            df_resultado["Tasa IPC Manual"] = pd.Series([pd.NA] * len(df_resultado))

        # Regla final: si hay manual, usa manual; si no, usa calculada
        df_resultado["Tasa IPC"] = df_resultado["Tasa IPC Manual"].where(
            df_resultado["Tasa IPC Manual"].notna(),
            df_resultado["Tasa IPC Calculada"]
        )


        # --------- CÁLCULO REAJUSTE (usar IPC EFECTIVO) ---------
        # Asegurar que los conceptos sean numéricos para poder reajustar
        for col in columnas_conceptos:
            df_resultado[col] = pd.to_numeric(df_resultado[col], errors="coerce").fillna(0)

        # ----------------- Normalizar tasa final (una sola columna) -----------------
        # La lógica anterior deja la tasa calculada en "Tasa IPC (%)".
        # Creamos la columna final "Tasa IPC" para usarla en Excel/PDF y en el reajuste.
        # --------- LÓGICA DE FECHA DE INGRESO / TRAMOS (Calcula tasa base) ---------
        considerar_fecha = st.session_state.get("proceso_considera_fecha", "No") == "Sí"
        tipo_periodo_ipc = st.session_state.get("tipo_periodo_ipc", None)
        n_meses_ipc = st.session_state.get("n_meses_ipc", None)

        periodo_mes = int(mes_idx)
        periodo_anno = int(anno)

        reglas_tramo_validas = (
            considerar_fecha
            and tipo_periodo_ipc is not None
            and n_meses_ipc is not None
        )

        if reglas_tramo_validas and "Fecha Ingreso" not in df_resultado.columns:
            st.warning(
                "Se seleccionó 'Considerar fecha de ingreso', "
                "pero el archivo no contiene la columna 'Fecha Ingreso'. "
                "Se aplicará el IPC a todos por igual."
            )
            reglas_tramo_validas = False

        # 1) TASA CALCULADA (según regla)
        if reglas_tramo_validas:
            df_resultado["Fecha Ingreso"] = pd.to_datetime(df_resultado["Fecha Ingreso"], errors="coerce")

            antiguedades = []
            aplica = []

            for _, fila in df_resultado.iterrows():
                fi = fila.get("Fecha Ingreso", pd.NaT)
                meses_trab = meses_trabajados_hasta_periodo(fi, periodo_mes, periodo_anno)
                antiguedades.append(meses_trab)

                if meses_trab is None:
                    aplica.append(False)
                else:
                    aplica.append(meses_trab >= int(n_meses_ipc))

            df_resultado["Antigüedad (meses)"] = antiguedades
            df_resultado["Aplica IPC por fecha ingreso"] = aplica

            # tasa calculada por fila: 0 si no aplica; ipc global si aplica
            tasa_calculada = df_resultado["Aplica IPC por fecha ingreso"].astype(float) * float(ipc)
        else:
            df_resultado["Antigüedad (meses)"] = None
            tasa_calculada = pd.Series(float(ipc), index=df_resultado.index)

        # 2) TASA MANUAL (si viene en la plantilla)
        col_manual = None
        if "Tasa IPC Manual (%)" in df_resultado.columns:
            col_manual = "Tasa IPC Manual (%)"
        elif "Tasa IPC manual (%)" in df_resultado.columns:
            col_manual = "Tasa IPC manual (%)"

        if col_manual:
            tasa_manual = normalizar_porcentaje_a_float(df_resultado[col_manual])
        else:
            tasa_manual = pd.Series(pd.NA, index=df_resultado.index)

        # 3) TASA FINAL (Manual > Calculada)
        df_resultado["Tasa IPC"] = tasa_manual.where(tasa_manual.notna(), tasa_calculada)
        df_resultado["Tasa IPC"] = pd.to_numeric(df_resultado["Tasa IPC"], errors="coerce").fillna(0)

        # ----------------- CONSTRUIR TASA FINAL (Manual > Calculada) -----------------

        # 1) Tasa calculada por regla (fecha ingreso / global)
        if reglas_tramo_validas:
            df_resultado["Fecha Ingreso"] = pd.to_datetime(df_resultado["Fecha Ingreso"], errors="coerce")

            antiguedades = []
            aplica = []

            for _, fila in df_resultado.iterrows():
                fi = fila.get("Fecha Ingreso", pd.NaT)
                meses_trab = meses_trabajados_hasta_periodo(fi, periodo_mes, periodo_anno)
                antiguedades.append(meses_trab)

                if meses_trab is None:
                    aplica.append(False)
                else:
                    aplica.append(meses_trab >= int(n_meses_ipc))

            df_resultado["Antigüedad (meses)"] = antiguedades
            df_resultado["Aplica IPC por fecha ingreso"] = aplica

            tasa_calculada = df_resultado["Aplica IPC por fecha ingreso"].astype(float) * float(ipc)
        else:
            df_resultado["Antigüedad (meses)"] = None
            tasa_calculada = pd.Series(float(ipc), index=df_resultado.index)

        # 2) Tasa manual desde plantilla (si viene informada)
        col_manual = None
        for c in ["Tasa IPC Manual (%)", "Tasa IPC manual (%)", "Tasa IPC Manual", "Tasa IPC manual"]:
            if c in df_resultado.columns:
                col_manual = c
                break

        if col_manual:
            tasa_manual = normalizar_porcentaje_a_float(df_resultado[col_manual])
        else:
            tasa_manual = pd.Series(pd.NA, index=df_resultado.index)

        # 3) Tasa final (la que se debe MOSTRAR y USAR)
        df_resultado["Tasa IPC"] = tasa_manual.where(tasa_manual.notna(), tasa_calculada)
        df_resultado["Tasa IPC"] = pd.to_numeric(df_resultado["Tasa IPC"], errors="coerce").fillna(0)

        # (Opcional) ocultar Caso IPC en salida
        if "Caso IPC" in df_resultado.columns:
            df_resultado = df_resultado.drop(columns=["Caso IPC"])

        # ----------------- CÁLCULO REAJUSTE (usar Tasa IPC FINAL) -----------------
        for col in columnas_conceptos:
            df_resultado[col] = pd.to_numeric(df_resultado[col], errors="coerce").fillna(0)

        factor = 1 + (df_resultado["Tasa IPC"] / 100.0)

        columnas_reajustadas = []
        for col in columnas_conceptos:
            col_reaj = f"{col} Reajustado"
            df_resultado[col_reaj] = df_resultado[col] * factor
            columnas_reajustadas.append(col_reaj)


        # Calcular reajuste vectorizado (rápido y consistente)
        columnas_reajustadas = []
        for col in columnas_conceptos:
            col_reaj = f"{col} Reajustado"
            df_resultado[col_reaj] = df_resultado[col] * factor
            columnas_reajustadas.append(col_reaj)




        # (Opcional) si existe "Caso IPC", la eliminamos para que no salga en el Excel/PDF
        if "Caso IPC" in df_resultado.columns:
            df_resultado = df_resultado.drop(columns=["Caso IPC"])

        cols_salida = (
            ["Empresa", "Periodo Remuneracional"]
            + columnas_identificacion
            + columnas_conceptos
            + ["Tasa IPC", "Antigüedad (meses)"]
            + columnas_reajustadas
        )
        cols_salida = [c for c in cols_salida if c in df_resultado.columns]

        df_resultado["Tasa IPC"] = pd.to_numeric(
    df_resultado["Tasa IPC"], errors="coerce"
).fillna(0)


        df_salida = df_resultado[cols_salida].copy()





        st.success("Cálculo de reajuste realizado correctamente.")
        st.subheader("Resultados del reajuste")
        st.dataframe(df_salida, use_container_width=True)

        try:
            registrar_log(
                usuario=st.session_state.usuario or "desconocido",
                ipc=float(ipc),
                cantidad_registros=len(df_salida)
            )
            st.info("Se registró la operación en el archivo log.txt.")
        except Exception as e:
            st.warning(f"No se pudo registrar en log.txt: {e}")

        st.subheader("5. Descargas")

        excel_bytes = convertir_df_a_excel(df_salida)
        st.download_button(
            label="Descargar Excel con reajuste",
            data=excel_bytes,
            file_name="reajuste_ipc_rq.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        try:
            meta_pdf = {
                "Empresa": empresa_proceso,
                "Centro de costo": st.session_state.get("proceso_cc", ""),
                "Proyecto": st.session_state.get("proceso_proyecto", ""),
                "Solicitante": st.session_state.get("proceso_solicitante", ""),
                "Tipo de procesamiento": st.session_state.get("proceso_tipo", ""),
                "Considerar fecha de ingreso": st.session_state.get("proceso_considera_fecha", "No"),
                "Periodo remuneracional": periodo_str,
                "IPC aplicado (%)": f"{float(ipc):.2f}%",
                "Fecha emisión": datetime.now().strftime("%d-%m-%Y %H:%M"),
                "Observaciones": st.session_state.get("proceso_obs", ""),
            }

            pdf_bytes = generar_pdf_reajuste(
                df=df_salida,
                ipc=float(ipc),
                periodo=periodo_str,
                empresa=empresa_proceso,
                meta=meta_pdf,
            )

            st.download_button(
                label="Descargar PDF (Certificado de reajuste)",
                data=pdf_bytes,
                file_name="certificado_reajuste_ipc_rq.pdf",
                mime="application/pdf",
            )
        except Exception as e:
            st.error(f"Error al generar el PDF: {e}")


# CONFIGURACIÓN INICIAL DE LA APLICACIÓN STREAMLIT
# -----------------------------------------------------------

st.set_page_config(
    page_title=f"{COMPANY_NAME} - Reajuste de Sueldos por IPC",
    layout="wide"
)

# ---- Estado global de la aplicación ----
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False

if "usuario" not in st.session_state:
    st.session_state.usuario = None

if "tema" not in st.session_state:
    st.session_state.tema = "light"

# >>>>> Menú inicial al abrir la app (Home por defecto)
if "menu_actual" not in st.session_state:
    st.session_state.menu_actual = "home"
    # Redirección post-login (para entrar a Home sí o sí)
if "post_login_redirect_done" not in st.session_state:
    st.session_state.post_login_redirect_done = False

# Datos generales del proceso de reajuste (para el formulario inicial)
if "proceso_empresa" not in st.session_state:
    st.session_state.proceso_empresa = COMPANY_NAME
if "proceso_cc" not in st.session_state:
    st.session_state.proceso_cc = ""
if "proceso_proyecto" not in st.session_state:
    st.session_state.proceso_proyecto = ""
if "proceso_solicitante" not in st.session_state:
    st.session_state.proceso_solicitante = ""
if "proceso_tipo" not in st.session_state:
    st.session_state.proceso_tipo = "Prueba"
if "proceso_obs" not in st.session_state:
    st.session_state.proceso_obs = ""
if "proceso_considera_fecha" not in st.session_state:
    st.session_state.proceso_considera_fecha = "No"  # "No" o "Sí"

# Para poder usar el tipo de período IPC y meses en el cálculo
if "tipo_periodo_ipc" not in st.session_state:
    st.session_state.tipo_periodo_ipc = None
if "n_meses_ipc" not in st.session_state:
    st.session_state.n_meses_ipc = None
if "periodo_mes" not in st.session_state:
    st.session_state.periodo_mes = None
if "periodo_anno" not in st.session_state:
    st.session_state.periodo_anno = None


# >>>>> NUEVO: Estado del sidebar (visible / oculto)

# -----------------------------------------------------------
# FLUJO PRINCIPAL
# -----------------------------------------------------------

def main():
    if not st.session_state.autenticado:
        mostrar_login()
        st.stop()

    # Forzar Home solo la primera vez después de iniciar sesión
    if not st.session_state.get("post_login_redirect_done", False):
        st.session_state.menu_actual = "home"
        st.session_state.post_login_redirect_done = True
        st.rerun()

    mostrar_app_reajuste()



# En Streamlit este bloque igual se ejecuta
if __name__ == "__main__":
    main()
