import os
import re
import imaplib
import email
from email.header import decode_header
from datetime import datetime
from pathlib import Path

from app import registrar_diagnostico_autel, UPLOAD_DIAG_FOLDER, file_sha256

# =========================================
# CONFIG
# =========================================
IMAP_HOST = os.getenv("AUTEL_IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("AUTEL_IMAP_PORT", "993"))
IMAP_USER = os.getenv("AUTEL_IMAP_USER", "doccar.arg@gmail.com")
IMAP_PASS = os.getenv("AUTEL_IMAP_PASS", "")
MAILBOX = os.getenv("AUTEL_IMAP_MAILBOX", "INBOX")

# filtro simple
SUBJECT_KEYWORDS = [
    "autel",
    "diagnóstico",
    "diagnostico",
    "vehicle diagnostic",
    "maxi",
    "informe"
]

# =========================================
# HELPERS MAIL
# =========================================
def decode_mime_words(s):
    if not s:
        return ""
    parts = decode_header(s)
    out = []
    for value, enc in parts:
        if isinstance(value, bytes):
            out.append(value.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(value)
    return "".join(out)


def sanitize_filename(name: str) -> str:
    name = (name or "").strip()
    name = os.path.basename(name)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or f"adjunto_{int(datetime.now().timestamp())}.pdf"


# =========================================
# HELPERS PDF TEXTO
# =========================================
def extraer_texto_pdf(pdffile):
    """
    Intenta con pdftotext del sistema.
    Si no está, devuelve texto vacío.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["pdftotext", pdffile, "-"],
            capture_output=True,
            text=True,
            timeout=20
        )
        if result.returncode == 0:
            return result.stdout or ""
    except Exception:
        pass

    return ""


def extraer_vin(texto: str) -> str:
    if not texto:
        return ""
    # VIN suele tener 17 chars
    m = re.search(r"\bVIN[:\s\-]*([A-HJ-NPR-Z0-9]{17})\b", texto, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return ""


def extraer_odometro(texto: str):
    if not texto:
        return None

    patrones = [
        r"Lectura del od[oó]metro[:\s\-]*([0-9\.\,\s]+)\s*km",
        r"Odometer[:\s\-]*([0-9\.\,\s]+)\s*km",
        r"Kilometraje[:\s\-]*([0-9\.\,\s]+)\s*km",
    ]

    for patron in patrones:
        m = re.search(patron, texto, re.IGNORECASE)
        if m:
            bruto = m.group(1)
            limpio = re.sub(r"[^\d]", "", bruto)
            if limpio.isdigit():
                return int(limpio)

    return None


def extraer_marca_modelo(texto: str):
    """
    Busca la línea típica del Autel:
    2013 MY(Model Year) Ford Informe de diagnóstico de vehículo
    y/o:
    2013 MY(Model Year)/Ford/Fiesta/
    """
    marca = ""
    modelo = ""

    if not texto:
        return marca, modelo

    # caso 1: 2013 MY(Model Year)/Ford/Fiesta/
    m = re.search(
        r"MY\(Model Year\)\s*/\s*([A-Za-z0-9\-]+)\s*/\s*([A-Za-z0-9\-\s]+)\s*/",
        texto,
        re.IGNORECASE
    )
    if m:
        marca = m.group(1).strip()
        modelo = m.group(2).strip()
        return marca, modelo

    # caso 2: 2013 MY(Model Year) Ford Informe...
    m = re.search(
        r"MY\(Model Year\)\)\s*([A-Za-z0-9\-]+)\s+Informe",
        texto,
        re.IGNORECASE
    )
    if m:
        marca = m.group(1).strip()

    return marca, modelo


def extraer_datos_autel_desde_pdf(pdf_path):
    texto = extraer_texto_pdf(pdf_path)

    vin = extraer_vin(texto)
    odometro = extraer_odometro(texto)
    marca, modelo = extraer_marca_modelo(texto)

    return {
        "texto": texto,
        "vin": vin,
        "odometro": odometro,
        "marca": marca,
        "modelo": modelo
    }


# =========================================
# IMAP
# =========================================
def conectar_imap():
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(IMAP_USER, IMAP_PASS)
    mail.select(MAILBOX)
    return mail


def buscar_mails_no_leidos(mail):
    typ, data = mail.search(None, '(UNSEEN)')
    if typ != "OK":
        return []
    return data[0].split()


def subject_parece_autel(subject: str):
    s = (subject or "").lower()
    return any(k in s for k in SUBJECT_KEYWORDS)


def procesar_mail(mail, msg_id):
    typ, data = mail.fetch(msg_id, "(RFC822)")
    if typ != "OK":
        return

    raw = data[0][1]
    msg = email.message_from_bytes(raw)

    subject = decode_mime_words(msg.get("Subject", ""))
    from_email = decode_mime_words(msg.get("From", ""))
    fecha_mail = decode_mime_words(msg.get("Date", ""))

    if not subject_parece_autel(subject):
        return

    for part in msg.walk():
        content_disposition = str(part.get("Content-Disposition", ""))
        filename = part.get_filename()

        if not filename:
            continue

        filename_decoded = decode_mime_words(filename)
        filename_decoded = sanitize_filename(filename_decoded)

        if not filename_decoded.lower().endswith(".pdf"):
            continue

        if "attachment" not in content_disposition.lower():
            continue

        payload = part.get_payload(decode=True)
        if not payload:
            continue

        Path(UPLOAD_DIAG_FOLDER).mkdir(parents=True, exist_ok=True)

        unique_name = f"autel_{int(datetime.now().timestamp())}_{filename_decoded}"
        save_path = os.path.join(UPLOAD_DIAG_FOLDER, unique_name)

        with open(save_path, "wb") as f:
            f.write(payload)

        sha = file_sha256(save_path)
        datos = extraer_datos_autel_desde_pdf(save_path)

        res = registrar_diagnostico_autel(
            fecha_mail=fecha_mail,
            from_email=from_email,
            subject=subject,
            filename=unique_name,
            vin=datos["vin"],
            marca=datos["marca"],
            modelo=datos["modelo"],
            odometro=datos["odometro"],
            sha256=sha,
            intentar_autovinculo=True
        )

        print("Diagnóstico procesado:", res)


def main():
    if not IMAP_PASS:
        print("Falta AUTEL_IMAP_PASS en variables de entorno.")
        return

    mail = conectar_imap()
    ids = buscar_mails_no_leidos(mail)

    print(f"Mails sin leer: {len(ids)}")

    for msg_id in ids:
        try:
            procesar_mail(mail, msg_id)
        except Exception as e:
            print("Error procesando mail", msg_id, "->", e)

    try:
        mail.close()
    except Exception:
        pass
    mail.logout()


if __name__ == "__main__":
    main()