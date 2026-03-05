import os, re, sqlite3, hashlib, email
import imaplib
from email.header import decode_header
from datetime import datetime

DB_NAME = "database.db"
SAVE_DIR = os.path.join("static", "uploads", "diagnosticos")

GMAIL_USER = os.environ.get("doccar.arg@gmail.com", "")
GMAIL_APP_PASSWORD = os.environ.get("zrdn lysz xwqd dhkd", "")

VIN_REGEX = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")

def db_con():
    return sqlite3.connect(DB_NAME)

def decode_mime(s):
    if not s:
        return ""
    parts = decode_header(s)
    out = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            out += text.decode(enc or "utf-8", errors="ignore")
        else:
            out += text
    return out

def sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()

def guess_vin(text: str):
    if not text:
        return None
    m = VIN_REGEX.search(text.upper())
    return m.group(0) if m else None

def clean_filename(name: str) -> str:
    name = name or "diagnostico.bin"
    name = decode_mime(name)
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", name)

def main():
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise SystemExit("Faltan env vars AUTEL_GMAIL_USER / AUTEL_GMAIL_APP_PASSWORD")

    os.makedirs(SAVE_DIR, exist_ok=True)

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select("inbox")

    # Traer solo NO LEÍDOS
    status, data = mail.search(None, "UNSEEN")
    if status != "OK":
        return

    ids = data[0].split()
    if not ids:
        return

    con = db_con()
    cur = con.cursor()

    for msg_id in ids:
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = decode_mime(msg.get("Subject"))
        from_email = decode_mime(msg.get("From"))
        date_hdr = decode_mime(msg.get("Date"))

        # VIN desde el asunto (si viene)
        vin = guess_vin(subject)

        saved_any = False

        for part in msg.walk():
            if part.get_content_disposition() != "attachment":
                continue

            filename = clean_filename(part.get_filename())
            payload = part.get_payload(decode=True) or b""
            if not payload:
                continue

            file_hash = sha256_bytes(payload)

            # Evitar duplicados
            cur.execute("SELECT id FROM diagnosticos WHERE sha256=?", (file_hash,))
            if cur.fetchone():
                continue

            unique = f"autel_{int(datetime.now().timestamp())}_{filename}"
            filepath = os.path.join(SAVE_DIR, unique)

            with open(filepath, "wb") as f:
                f.write(payload)

            created_at = datetime.now().isoformat(timespec="seconds")

            cur.execute("""
                INSERT INTO diagnosticos
                (fecha_mail, from_email, subject, filename, vin, marca, modelo, created_at, sha256)
                VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            """, (date_hdr, from_email, subject, unique, vin, created_at, file_hash))
            con.commit()
            saved_any = True

        # Si guardó algo, marcamos el mail como leído
        if saved_any:
            mail.store(msg_id, '+FLAGS', '\\Seen')

    con.close()
    mail.logout()

if __name__ == "__main__":
    main()