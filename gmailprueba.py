import imaplib

EMAIL = "doccar.arg@gmail.com"
PASSWORD = "zrdn lysz xwqd dhkd"

mail = imaplib.IMAP4_SSL("imap.gmail.com")
mail.login(EMAIL, PASSWORD)

mail.select("inbox")

status, messages = mail.search(None, "ALL")

print("Conectado correctamente")
print("Cantidad de mails:", len(messages[0].split()))

mail.logout()