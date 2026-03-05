import os, imaplib, email
from email.header import decode_header

GMAIL_USER = os.environ.get("AUTEL_GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("AUTEL_GMAIL_APP_PASSWORD", "")

def decode_mime(s):
    if not s:
        return ""
    out = ""
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            out += part.decode(enc or "utf-8", errors="ignore")
        else:
            out += part
    return out

mail = imaplib.IMAP4_SSL("imap.gmail.com")
mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)

status, boxes = mail.list()
print("MAILBOXES:", status)
# print(boxes)

mail.select("INBOX")

# probamos UNSEEN primero
status, data = mail.search(None, "UNSEEN")
ids = data[0].split()
print("UNSEEN:", len(ids))

# si no hay, probamos ALL (para ver si hay adjuntos aunque estén leídos)
status2, data2 = mail.search(None, "ALL")
ids_all = data2[0].split()
print("ALL:", len(ids_all))

# miramos los últimos 5 (de ALL)
last = ids_all[-5:]
print("Last 5 IDs:", [x.decode() for x in last])

for msg_id in last:
    status, msg_data = mail.fetch(msg_id, "(RFC822)")
    msg = email.message_from_bytes(msg_data[0][1])

    subject = decode_mime(msg.get("Subject"))
    frm = decode_mime(msg.get("From"))
    print("\n---")
    print("ID:", msg_id.decode())
    print("FROM:", frm)
    print("SUBJECT:", subject)

    att = 0
    inline = 0

    for part in msg.walk():
        disp = part.get_content_disposition()  # None / 'attachment' / 'inline'
        fname = part.get_filename()
        if fname:
            fname = decode_mime(fname)
            if disp == "attachment":
                att += 1
                print("  ATTACHMENT:", fname)
            elif disp == "inline":
                inline += 1
                print("  INLINE:", fname)

    print("Attachments:", att, "Inline:", inline)

mail.logout()
