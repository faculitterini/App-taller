# app.py
from flask import Flask, render_template, request, redirect, session, url_for, flash, send_from_directory
import sqlite3
import os
from datetime import date, datetime, timedelta
import shutil
from functools import wraps
from werkzeug.utils import secure_filename
import time
import re
import hashlib

# =========================
# Config general
# =========================
app = Flask(__name__)
app.secret_key = "clave_ultra_secreta_local"

DB_NAME = "database.db"
BACKUP_FOLDER = "backups"

UPLOAD_FOLDER = os.path.join("static", "uploads")
UPLOAD_DIAG_FOLDER = os.path.join("static", "uploads", "diagnosticos")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

# Estados permitidos
ESTADOS = ["Presupuesto", "Ingresado", "Entregado", "Facturado"]


# =========================
# Helpers generales
# =========================
def get_con():
    con = sqlite3.connect(DB_NAME)
    con.row_factory = sqlite3.Row
    return con


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def normalizar_telefono(raw):
    if not raw:
        return None
    return re.sub(r"\D", "", raw)


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def limpiar_vin(vin: str) -> str:
    if not vin:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(vin).upper().strip())


def limpiar_texto_corto(txt: str) -> str:
    return (txt or "").strip()


def limpiar_marca_modelo(txt: str) -> str:
    return (txt or "").strip()


def parsear_km(valor):
    """
    Convierte cosas como:
    '203508', '203.508', '203508km', '203 508 KM'
    a int(203508)
    """
    if valor is None:
        return None

    s = str(valor).strip().lower()
    if not s:
        return None

    s = s.replace("km", "")
    s = re.sub(r"[^\d]", "", s)

    if not s:
        return None

    try:
        return int(s)
    except Exception:
        return None


def km_a_texto(km):
    if km is None:
        return ""
    try:
        return str(int(km))
    except Exception:
        return str(km).strip()


def parsear_fecha_texto(txt):
    """
    Intenta convertir textos comunes a YYYY-MM-DD.
    Si no puede, devuelve hoy.
    """
    if not txt:
        return date.today().isoformat()

    txt = str(txt).strip()

    formatos = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
    ]

    for fmt in formatos:
        try:
            return datetime.strptime(txt, fmt).date().isoformat()
        except Exception:
            pass

    # intenta detectar YYYY-MM-DD dentro del string
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", txt)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # intenta detectar DD/MM/YYYY
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", txt)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    return date.today().isoformat()


# =========================
# Helpers AUTEL / Diagnósticos
# =========================
def obtener_diagnostico(cur, diag_id):
    cur.execute("SELECT * FROM diagnosticos WHERE id=?", (diag_id,))
    return cur.fetchone()


def obtener_vehiculo_por_vin(cur, vin):
    vin = limpiar_vin(vin)
    if not vin:
        return None
    cur.execute("""
        SELECT *
        FROM vehiculos
        WHERE UPPER(COALESCE(vin,'')) = ?
        LIMIT 1
    """, (vin,))
    return cur.fetchone()


def obtener_reparacion_abierta_vehiculo(cur, vehiculo_id):
    """
    Toma la última reparación no facturada.
    """
    cur.execute("""
        SELECT *
        FROM reparaciones
        WHERE vehiculo_id = ?
          AND estado IN ('Presupuesto', 'Ingresado', 'Entregado')
        ORDER BY fecha DESC, id DESC
        LIMIT 1
    """, (vehiculo_id,))
    return cur.fetchone()


def registrar_historial_km(cur, vehiculo_id, km, fuente, diagnostico_id=None, reparacion_id=None, fecha=None):
    km_num = parsear_km(km)
    if km_num is None:
        return

    fecha = parsear_fecha_texto(fecha or date.today().isoformat())

    # evitar duplicado exacto muy obvio
    cur.execute("""
        SELECT id
        FROM vehiculo_km_historial
        WHERE vehiculo_id = ?
          AND km = ?
          AND fecha = ?
          AND COALESCE(fuente,'') = COALESCE(?, '')
        LIMIT 1
    """, (vehiculo_id, km_num, fecha, fuente))
    existe = cur.fetchone()
    if existe:
        return

    cur.execute("""
        INSERT INTO vehiculo_km_historial (
            vehiculo_id, fecha, km, fuente, diagnostico_id, reparacion_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (vehiculo_id, fecha, km_num, fuente, diagnostico_id, reparacion_id))


def actualizar_km_vehiculo_si_corresponde(cur, vehiculo_id, km_nuevo, fuente="Diagnóstico", diagnostico_id=None, reparacion_id=None, fecha=None):
    km_num = parsear_km(km_nuevo)
    if km_num is None:
        return False

    cur.execute("SELECT km FROM vehiculos WHERE id=?", (vehiculo_id,))
    row = cur.fetchone()
    if not row:
        return False

    km_actual = parsear_km(row["km"])
    actualizado = False

    if km_actual is None or km_num >= km_actual:
        cur.execute("UPDATE vehiculos SET km=? WHERE id=?", (str(km_num), vehiculo_id))
        actualizado = True

    registrar_historial_km(
        cur,
        vehiculo_id=vehiculo_id,
        km=km_num,
        fuente=fuente,
        diagnostico_id=diagnostico_id,
        reparacion_id=reparacion_id,
        fecha=fecha
    )

    return actualizado


def actualizar_km_reparacion_si_corresponde(cur, reparacion_id, km_nuevo):
    km_num = parsear_km(km_nuevo)
    if km_num is None:
        return False

    cur.execute("SELECT km FROM reparaciones WHERE id=?", (reparacion_id,))
    row = cur.fetchone()
    if not row:
        return False

    km_actual = parsear_km(row["km"])
    if km_actual is None or km_num >= km_actual:
        cur.execute("UPDATE reparaciones SET km=? WHERE id=?", (str(km_num), reparacion_id))
        return True

    return False


def completar_marca_modelo_vehiculo_si_vacio(cur, vehiculo_id, marca=None, modelo=None):
    cur.execute("SELECT marca, modelo FROM vehiculos WHERE id=?", (vehiculo_id,))
    veh = cur.fetchone()
    if not veh:
        return

    marca_actual = (veh["marca"] or "").strip()
    modelo_actual = (veh["modelo"] or "").strip()

    nueva_marca = limpiar_marca_modelo(marca)
    nuevo_modelo = limpiar_marca_modelo(modelo)

    marca_final = marca_actual or nueva_marca
    modelo_final = modelo_actual or nuevo_modelo

    cur.execute("""
        UPDATE vehiculos
        SET marca=?, modelo=?
        WHERE id=?
    """, (marca_final, modelo_final, vehiculo_id))


def vincular_diagnostico_existente(cur, diagnostico_id, vehiculo_id=None, reparacion_id=None, crear_reparacion=False):
    """
    Hace toda la lógica central:
    - busca vehículo por VIN si no se pasó vehiculo_id
    - vincula el diagnóstico al vehículo
    - intenta vincularlo a reparación abierta
    - opcionalmente crea reparación si no hay
    - actualiza km de vehículo y reparación
    """
    diag = obtener_diagnostico(cur, diagnostico_id)
    if not diag:
        return {
            "ok": False,
            "msg": "Diagnóstico inexistente."
        }

    vin = limpiar_vin(diag["vin"])
    marca = diag["marca"]
    modelo = diag["modelo"]
    odometro = parsear_km(diag["odometro"])
    fecha_diag = parsear_fecha_texto(diag["fecha_mail"] or diag["created_at"] or date.today().isoformat())

    # Si no pasaron vehículo, buscar por VIN
    vehiculo = None
    if vehiculo_id:
        cur.execute("SELECT * FROM vehiculos WHERE id=?", (vehiculo_id,))
        vehiculo = cur.fetchone()
    elif vin:
        vehiculo = obtener_vehiculo_por_vin(cur, vin)

    if not vehiculo:
        cur.execute("""
            UPDATE diagnosticos
            SET vehiculo_id = NULL,
                reparacion_id = NULL,
                estado_vinculacion = 'PENDIENTE',
                vinculado_auto = 0,
                updated_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(sep=" ", timespec="seconds"), diagnostico_id))
        return {
            "ok": False,
            "msg": "No se encontró vehículo con ese VIN. Queda pendiente para vinculación manual."
        }

    vehiculo_id_real = vehiculo["id"]

    # completar marca/modelo si el vehículo está vacío
    completar_marca_modelo_vehiculo_si_vacio(cur, vehiculo_id_real, marca=marca, modelo=modelo)

    # actualizar km del vehículo
    if odometro is not None:
        actualizar_km_vehiculo_si_corresponde(
            cur,
            vehiculo_id=vehiculo_id_real,
            km_nuevo=odometro,
            fuente="Diagnóstico Autel",
            diagnostico_id=diagnostico_id,
            reparacion_id=reparacion_id,
            fecha=fecha_diag
        )

    reparacion = None
    if reparacion_id:
        cur.execute("SELECT * FROM reparaciones WHERE id=?", (reparacion_id,))
        reparacion = cur.fetchone()
    else:
        reparacion = obtener_reparacion_abierta_vehiculo(cur, vehiculo_id_real)

    # crear reparación si fue pedido y no hay abierta
    if not reparacion and crear_reparacion:
        descripcion_auto = "Diagnóstico Autel"
        if marca or modelo:
            descripcion_auto += f" - {(marca or '').strip()} {(modelo or '').strip()}".strip()

        notas_auto = f"Reparación creada desde diagnóstico Autel #{diagnostico_id}"
        cur.execute("""
            INSERT INTO reparaciones (vehiculo_id, fecha, descripcion, notas, estado, km)
            VALUES (?, ?, ?, ?, 'Ingresado', ?)
        """, (
            vehiculo_id_real,
            fecha_diag,
            descripcion_auto.strip(),
            notas_auto,
            km_a_texto(odometro)
        ))
        reparacion_id = cur.lastrowid
        cur.execute("SELECT * FROM reparaciones WHERE id=?", (reparacion_id,))
        reparacion = cur.fetchone()

    reparacion_id_final = reparacion["id"] if reparacion else None

    # actualizar km de la reparación vinculada
    if reparacion_id_final and odometro is not None:
        actualizar_km_reparacion_si_corresponde(cur, reparacion_id_final, odometro)

    estado_vinculacion = "VINCULADO" if reparacion_id_final else "VEHICULO_ENCONTRADO"

    cur.execute("""
        UPDATE diagnosticos
        SET vehiculo_id = ?,
            reparacion_id = ?,
            estado_vinculacion = ?,
            vinculado_auto = ?,
            updated_at = ?
        WHERE id = ?
    """, (
        vehiculo_id_real,
        reparacion_id_final,
        estado_vinculacion,
        1 if reparacion_id is None else 0,
        datetime.now().isoformat(sep=" ", timespec="seconds"),
        diagnostico_id
    ))

    return {
        "ok": True,
        "vehiculo_id": vehiculo_id_real,
        "reparacion_id": reparacion_id_final,
        "msg": "Diagnóstico vinculado correctamente." if reparacion_id_final else "Vehículo encontrado. No había reparación abierta, quedó vinculado al vehículo."
    }


def registrar_diagnostico_autel(
    fecha_mail=None,
    from_email="",
    subject="",
    filename="",
    vin="",
    marca="",
    modelo="",
    odometro=None,
    sha256="",
    intentar_autovinculo=True
):
    """
    Esta función está pensada para ser usada desde tu script fetch_autel_gmail.py
    cuando entra un mail nuevo.

    Devuelve un dict con info del insert/vinculación.
    """
    con = get_con()
    cur = con.cursor()

    vin = limpiar_vin(vin)
    marca = limpiar_marca_modelo(marca)
    modelo = limpiar_marca_modelo(modelo)
    odometro_num = parsear_km(odometro)
    fecha_mail_norm = parsear_fecha_texto(fecha_mail or date.today().isoformat())
    now_txt = datetime.now().isoformat(sep=" ", timespec="seconds")

    # si ya existe por sha256, actualiza datos útiles y vuelve ese id
    if sha256:
        cur.execute("SELECT id FROM diagnosticos WHERE sha256=?", (sha256,))
        existente = cur.fetchone()
        if existente:
            diag_id = existente["id"]
            cur.execute("""
                UPDATE diagnosticos
                SET fecha_mail = COALESCE(NULLIF(?, ''), fecha_mail),
                    from_email = COALESCE(NULLIF(?, ''), from_email),
                    subject = COALESCE(NULLIF(?, ''), subject),
                    filename = COALESCE(NULLIF(?, ''), filename),
                    vin = COALESCE(NULLIF(?, ''), vin),
                    marca = COALESCE(NULLIF(?, ''), marca),
                    modelo = COALESCE(NULLIF(?, ''), modelo),
                    odometro = COALESCE(?, odometro),
                    updated_at = ?
                WHERE id = ?
            """, (
                fecha_mail_norm,
                from_email.strip(),
                subject.strip(),
                filename.strip(),
                vin,
                marca,
                modelo,
                odometro_num,
                now_txt,
                diag_id
            ))
            if intentar_autovinculo:
                res = vincular_diagnostico_existente(cur, diag_id)
            else:
                res = {"ok": True, "msg": "Diagnóstico ya existente, datos actualizados.", "diagnostico_id": diag_id}

            con.commit()
            con.close()
            res["diagnostico_id"] = diag_id
            return res

    cur.execute("""
        INSERT INTO diagnosticos (
            fecha_mail, from_email, subject, filename,
            vin, marca, modelo, odometro,
            created_at, updated_at, sha256,
            vehiculo_id, reparacion_id,
            estado_vinculacion, vinculado_auto
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'PENDIENTE', 0)
    """, (
        fecha_mail_norm,
        from_email.strip(),
        subject.strip(),
        filename.strip(),
        vin,
        marca,
        modelo,
        odometro_num,
        now_txt,
        now_txt,
        sha256.strip() if sha256 else None
    ))
    diag_id = cur.lastrowid

    if intentar_autovinculo:
        res = vincular_diagnostico_existente(cur, diag_id)
    else:
        res = {"ok": True, "msg": "Diagnóstico registrado.", "diagnostico_id": diag_id}

    con.commit()
    con.close()

    res["diagnostico_id"] = diag_id
    return res


# =========================
# DB init / migraciones
# =========================
def init_db():
    con = get_con()
    cur = con.cursor()

    # ---------------- LISTA DE PRECIOS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS lista_precios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        concepto TEXT NOT NULL,
        categoria TEXT,
        tipo TEXT DEFAULT 'SERVICIO',
        precio REAL DEFAULT 0,
        notas TEXT,
        activo INTEGER DEFAULT 1
    )
    """)

    # ---------------- DIAGNÓSTICOS (AUTEL) ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS diagnosticos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha_mail TEXT,
        from_email TEXT,
        subject TEXT,
        filename TEXT,
        vin TEXT,
        marca TEXT,
        modelo TEXT,
        odometro INTEGER,
        vehiculo_id INTEGER,
        reparacion_id INTEGER,
        estado_vinculacion TEXT DEFAULT 'PENDIENTE',
        vinculado_auto INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT,
        sha256 TEXT UNIQUE
    )
    """)

    cur.execute("PRAGMA table_info(diagnosticos)")
    cols = [c[1] for c in cur.fetchall()]
    if "odometro" not in cols:
        cur.execute("ALTER TABLE diagnosticos ADD COLUMN odometro INTEGER")
    if "vehiculo_id" not in cols:
        cur.execute("ALTER TABLE diagnosticos ADD COLUMN vehiculo_id INTEGER")
    if "reparacion_id" not in cols:
        cur.execute("ALTER TABLE diagnosticos ADD COLUMN reparacion_id INTEGER")
    if "estado_vinculacion" not in cols:
        cur.execute("ALTER TABLE diagnosticos ADD COLUMN estado_vinculacion TEXT DEFAULT 'PENDIENTE'")
    if "vinculado_auto" not in cols:
        cur.execute("ALTER TABLE diagnosticos ADD COLUMN vinculado_auto INTEGER DEFAULT 0")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE diagnosticos ADD COLUMN updated_at TEXT")

    # ---------------- HISTORIAL KM ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vehiculo_km_historial (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehiculo_id INTEGER NOT NULL,
        fecha TEXT,
        km INTEGER,
        fuente TEXT,
        diagnostico_id INTEGER,
        reparacion_id INTEGER
    )
    """)

    # ---------------- USERS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        rol TEXT
    )
    """)
    cur.execute("PRAGMA table_info(users)")
    cols = [c[1] for c in cur.fetchall()]
    if "rol" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN rol TEXT")

    # asegurar usuario admin
    cur.execute("SELECT id FROM users WHERE username='admin'")
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE users SET password=?, rol=? WHERE id=?",
            ("1234", "admin", row[0])
        )
    else:
        cur.execute(
            "INSERT INTO users (username, password, rol) VALUES (?, ?, ?)",
            ("admin", "1234", "admin")
        )

    # ---------------- CLIENTES ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT,
        apellido TEXT,
        telefono TEXT,
        email TEXT,
        direccion TEXT,
        notas TEXT
    )
    """)

    cur.execute("PRAGMA table_info(clientes)")
    cols = [c[1] for c in cur.fetchall()]
    if "documento" not in cols:
        cur.execute("ALTER TABLE clientes ADD COLUMN documento TEXT")

    cur.execute("PRAGMA table_info(clientes)")
    cols = [c[1] for c in cur.fetchall()]
    if "razon_social" not in cols:
        cur.execute("ALTER TABLE clientes ADD COLUMN razon_social TEXT")

    # ---------------- VEHICULOS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vehiculos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER,
        patente TEXT,
        marca TEXT,
        modelo TEXT,
        anio TEXT,
        km TEXT,
        notas TEXT,
        vin TEXT,
        FOREIGN KEY(cliente_id) REFERENCES clientes(id)
    )
    """)

    cur.execute("PRAGMA table_info(vehiculos)")
    cols = [c[1] for c in cur.fetchall()]
    if "vin" not in cols:
        cur.execute("ALTER TABLE vehiculos ADD COLUMN vin TEXT")

    # ---------------- REPARACIONES ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reparaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehiculo_id INTEGER,
        fecha TEXT,
        descripcion TEXT,
        notas TEXT,
        estado TEXT,
        km TEXT,
        FOREIGN KEY(vehiculo_id) REFERENCES vehiculos(id)
    )
    """)

    cur.execute("PRAGMA table_info(reparaciones)")
    cols = [c[1] for c in cur.fetchall()]
    if "estado" not in cols:
        cur.execute("ALTER TABLE reparaciones ADD COLUMN estado TEXT")
    if "km" not in cols:
        cur.execute("ALTER TABLE reparaciones ADD COLUMN km TEXT")

    cur.execute("UPDATE reparaciones SET estado='Presupuesto' WHERE estado IS NULL OR TRIM(estado)=''")
    cur.execute("""
        UPDATE reparaciones
        SET estado='Facturado'
        WHERE estado IN ('Facturada','FACTURADA')
    """)
    cur.execute("""
        UPDATE reparaciones
        SET estado='Entregado'
        WHERE estado IN ('Terminado','TERMINADO','Entregada','ENTREGADA')
    """)
    cur.execute("""
        UPDATE reparaciones
        SET estado='Presupuesto'
        WHERE estado NOT IN ('Presupuesto','Ingresado','Entregado','Facturado')
    """)

    # ---------------- ITEMS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reparacion_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reparacion_id INTEGER,
        concepto TEXT,
        cantidad REAL,
        precio_unitario REAL,
        descuento REAL,
        tipo TEXT DEFAULT 'SERVICIO',
        FOREIGN KEY(reparacion_id) REFERENCES reparaciones(id)
    )
    """)

    cur.execute("PRAGMA table_info(reparacion_items)")
    cols = [c[1] for c in cur.fetchall()]
    if "descuento" not in cols:
        cur.execute("ALTER TABLE reparacion_items ADD COLUMN descuento REAL")

    cur.execute("PRAGMA table_info(reparacion_items)")
    cols = [c[1] for c in cur.fetchall()]
    if "tipo" not in cols:
        cur.execute("ALTER TABLE reparacion_items ADD COLUMN tipo TEXT DEFAULT 'SERVICIO'")

    # ---------------- CONCEPTOS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS item_conceptos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE
    )
    """)

    # ---------------- IMAGENES ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reparacion_imagenes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reparacion_id INTEGER,
        filename TEXT,
        descripcion TEXT,
        FOREIGN KEY(reparacion_id) REFERENCES reparaciones(id)
    )
    """)

    # ---------------- FACTURAS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS facturas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reparacion_id INTEGER,
        fecha TEXT,
        total REAL,
        descuento_global REAL,
        es_presupuesto INTEGER DEFAULT 1,
        total_servicios REAL,
        total_repuestos REAL,
        FOREIGN KEY(reparacion_id) REFERENCES reparaciones(id)
    )
    """)

    cur.execute("PRAGMA table_info(facturas)")
    cols = [c[1] for c in cur.fetchall()]
    if "descuento_global" not in cols:
        cur.execute("ALTER TABLE facturas ADD COLUMN descuento_global REAL")

    cur.execute("PRAGMA table_info(facturas)")
    cols = [c[1] for c in cur.fetchall()]
    if "es_presupuesto" not in cols:
        cur.execute("ALTER TABLE facturas ADD COLUMN es_presupuesto INTEGER DEFAULT 1")
        cur.execute("UPDATE facturas SET es_presupuesto = 1 WHERE es_presupuesto IS NULL")

    cur.execute("PRAGMA table_info(facturas)")
    cols = [c[1] for c in cur.fetchall()]
    if "total_servicios" not in cols:
        cur.execute("ALTER TABLE facturas ADD COLUMN total_servicios REAL")

    cur.execute("PRAGMA table_info(facturas)")
    cols = [c[1] for c in cur.fetchall()]
    if "total_repuestos" not in cols:
        cur.execute("ALTER TABLE facturas ADD COLUMN total_repuestos REAL")

    # ---------------- CITAS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS citas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        hora TEXT,
        cliente_nombre TEXT,
        telefono TEXT,
        descripcion TEXT
    )
    """)

    # ---------------- GASTOS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS gastos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        categoria TEXT,
        descripcion TEXT,
        monto REAL,
        pagador TEXT,
        medio_pago TEXT,
        notas TEXT,
        pagado INTEGER DEFAULT 0,
        fecha_pago TEXT,
        reparacion_id INTEGER
    )
    """)

    cur.execute("PRAGMA table_info(gastos)")
    cols = [c[1] for c in cur.fetchall()]
    if "pagador" not in cols:
        cur.execute("ALTER TABLE gastos ADD COLUMN pagador TEXT")
    if "medio_pago" not in cols:
        cur.execute("ALTER TABLE gastos ADD COLUMN medio_pago TEXT")
    if "notas" not in cols:
        cur.execute("ALTER TABLE gastos ADD COLUMN notas TEXT")
    if "pagado" not in cols:
        cur.execute("ALTER TABLE gastos ADD COLUMN pagado INTEGER DEFAULT 0")
    if "fecha_pago" not in cols:
        cur.execute("ALTER TABLE gastos ADD COLUMN fecha_pago TEXT")
    if "reparacion_id" not in cols:
        cur.execute("ALTER TABLE gastos ADD COLUMN reparacion_id INTEGER")

    cur.execute("UPDATE gastos SET pagado = 0 WHERE pagado IS NULL")

    # índices útiles
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vehiculos_vin ON vehiculos(vin)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_diagnosticos_vin ON diagnosticos(vin)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_diagnosticos_reparacion ON diagnosticos(reparacion_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_diagnosticos_vehiculo ON diagnosticos(vehiculo_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_km_historial_vehiculo ON vehiculo_km_historial(vehiculo_id)")

    con.commit()
    con.close()


# =========================
# Backups (1 solo, sobreescribe)
# =========================
def backup_db_if_changed():
    """
    Genera 1 solo backup: backups/backup_latest.db
    Sólo lo actualiza si el DB cambió.
    """
    if not os.path.exists(BACKUP_FOLDER):
        os.makedirs(BACKUP_FOLDER, exist_ok=True)

    if not os.path.exists(DB_NAME):
        return None

    dst = os.path.join(BACKUP_FOLDER, "backup_latest.db")
    src_hash = file_sha256(DB_NAME)
    dst_hash = file_sha256(dst) if os.path.exists(dst) else None

    if src_hash != dst_hash:
        shutil.copy2(DB_NAME, dst)
        return dst

    return None


def get_last_backup_datetime():
    path = os.path.join(BACKUP_FOLDER, "backup_latest.db")
    if not os.path.exists(path):
        return None
    try:
        return datetime.fromtimestamp(os.path.getmtime(path))
    except Exception:
        return None


# =========================
# Decoradores auth
# =========================
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session or session.get("rol") != "admin":
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrapper


# =========================
# Diagnósticos (AUTEL)
# =========================
@app.route("/diagnosticos")
@login_required
def diagnosticos_listado():
    q = (request.args.get("q") or "").strip()

    con = get_con()
    cur = con.cursor()

    sql = """
        SELECT
            d.id,
            d.fecha_mail,
            d.vin,
            d.marca,
            d.modelo,
            d.odometro,
            d.subject,
            d.filename,
            d.created_at,
            d.vehiculo_id,
            d.reparacion_id,
            d.estado_vinculacion,
            v.patente,
            v.marca AS veh_marca,
            v.modelo AS veh_modelo,
            r.estado AS reparacion_estado
        FROM diagnosticos d
        LEFT JOIN vehiculos v ON v.id = d.vehiculo_id
        LEFT JOIN reparaciones r ON r.id = d.reparacion_id
        WHERE 1=1
    """
    params = []

    if q:
        p = f"%{q}%"
        sql += """
            AND (
                d.vin LIKE ? OR
                d.marca LIKE ? OR
                d.modelo LIKE ? OR
                d.subject LIKE ? OR
                d.filename LIKE ? OR
                v.patente LIKE ?
            )
        """
        params.extend([p, p, p, p, p, p])

    sql += " ORDER BY d.id DESC"

    cur.execute(sql, params)
    rows = cur.fetchall()

    # listado de vehículos para vinculación manual
    cur.execute("""
        SELECT
            id,
            patente,
            marca,
            modelo,
            vin
        FROM vehiculos
        ORDER BY patente ASC, marca ASC, modelo ASC
    """)
    vehiculos_lookup = cur.fetchall()

    con.close()

    return render_template(
        "diagnosticos.html",
        diagnosticos=rows,
        q=q,
        vehiculos_lookup=vehiculos_lookup
    )


@app.route("/diagnosticos/<int:diag_id>/descargar")
@login_required
def diagnostico_descargar(diag_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("SELECT filename FROM diagnosticos WHERE id=?", (diag_id,))
    row = cur.fetchone()
    con.close()

    if not row:
        flash("Diagnóstico no encontrado.", "warning")
        return redirect(url_for("diagnosticos_listado"))

    filename = row["filename"]
    return send_from_directory(UPLOAD_DIAG_FOLDER, filename, as_attachment=True)


@app.route("/diagnosticos/<int:diag_id>/autovincular", methods=["POST"])
@login_required
def diagnostico_autovincular(diag_id):
    con = get_con()
    cur = con.cursor()

    res = vincular_diagnostico_existente(cur, diag_id, vehiculo_id=None, reparacion_id=None, crear_reparacion=False)
    con.commit()
    con.close()

    flash(res["msg"], "success" if res["ok"] else "warning")
    backup_db_if_changed()
    return redirect(request.referrer or url_for("diagnosticos_listado"))


@app.route("/diagnosticos/<int:diag_id>/crear_reparacion", methods=["POST"])
@login_required
def diagnostico_crear_reparacion(diag_id):
    con = get_con()
    cur = con.cursor()

    res = vincular_diagnostico_existente(cur, diag_id, vehiculo_id=None, reparacion_id=None, crear_reparacion=True)
    con.commit()
    con.close()

    backup_db_if_changed()

    if res["ok"] and res.get("reparacion_id"):
        flash("Reparación creada y diagnóstico vinculado.", "success")
        return redirect(url_for("reparacion_detalle", reparacion_id=res["reparacion_id"]))

    flash(res["msg"], "warning")
    return redirect(request.referrer or url_for("diagnosticos_listado"))


@app.route("/diagnosticos/<int:diag_id>/vincular_manual", methods=["POST"])
@login_required
def diagnostico_vincular_manual(diag_id):
    vehiculo_id = request.form.get("vehiculo_id", "").strip()
    reparacion_id = request.form.get("reparacion_id", "").strip()
    crear_reparacion = request.form.get("crear_reparacion") == "1"

    vehiculo_id = int(vehiculo_id) if vehiculo_id.isdigit() else None
    reparacion_id = int(reparacion_id) if reparacion_id.isdigit() else None

    con = get_con()
    cur = con.cursor()

    res = vincular_diagnostico_existente(
        cur,
        diag_id,
        vehiculo_id=vehiculo_id,
        reparacion_id=reparacion_id,
        crear_reparacion=crear_reparacion
    )
    con.commit()
    con.close()

    backup_db_if_changed()

    if res["ok"] and res.get("reparacion_id"):
        flash("Diagnóstico vinculado correctamente.", "success")
        return redirect(url_for("reparacion_detalle", reparacion_id=res["reparacion_id"]))

    flash(res["msg"], "success" if res["ok"] else "warning")
    return redirect(request.referrer or url_for("diagnosticos_listado"))


# =========================
# Dashboard
# =========================
@app.route("/")
@login_required
def dashboard():
    con = get_con()
    cur = con.cursor()

    hoy = date.today().isoformat()
    desde_7 = (date.today() - timedelta(days=6)).isoformat()

    # Reparaciones visibles
    cur.execute("""
        SELECT 
            r.id, r.fecha, r.estado, r.descripcion,
            v.patente, v.marca, v.modelo,
            c.nombre, c.apellido
        FROM reparaciones r
        JOIN vehiculos v ON v.id = r.vehiculo_id
        JOIN clientes c ON c.id = v.cliente_id
        WHERE r.estado IN ('Ingresado','Entregado')
        ORDER BY 
            CASE r.estado
                WHEN 'Ingresado' THEN 1
                WHEN 'Entregado' THEN 2
                ELSE 9
            END,
            r.fecha DESC, r.id DESC
    """)
    trabajos = cur.fetchall()

    # Próximas citas
    cur.execute("""
        SELECT id, fecha, hora, cliente_nombre, telefono, descripcion
        FROM citas
        WHERE fecha >= ?
        ORDER BY fecha, hora
        LIMIT 10
    """, (hoy,))
    citas = cur.fetchall()

    # Gastos pendientes
    cur.execute("""
        SELECT id, fecha, categoria, descripcion, monto, pagador
        FROM gastos
        WHERE COALESCE(pagado,0)=0
        ORDER BY fecha DESC, id DESC
        LIMIT 15
    """)
    gastos_pendientes = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(SUM(monto),0)
        FROM gastos
        WHERE COALESCE(pagado,0)=0
    """)
    total_gastos_pendientes = (cur.fetchone()[0] or 0)

    # Métricas
    cur.execute("SELECT COUNT(*) FROM reparaciones WHERE estado='Ingresado'")
    reparaciones_en_proceso = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM reparaciones WHERE estado IN ('Ingresado','Entregado')")
    reparaciones_pendientes = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM reparaciones WHERE fecha=? AND estado='Ingresado'", (hoy,))
    reparaciones_hoy = cur.fetchone()[0] or 0

    # Diagnósticos pendientes de vinculación
    cur.execute("""
        SELECT COUNT(*)
        FROM diagnosticos
        WHERE COALESCE(estado_vinculacion, 'PENDIENTE') <> 'VINCULADO'
    """)
    diagnosticos_pendientes = cur.fetchone()[0] or 0

    # Ingresos últimos 7 días
    cur.execute("""
        SELECT COALESCE(SUM(COALESCE(total_servicios,total)),0)
        FROM facturas
        WHERE COALESCE(es_presupuesto,1)=0
          AND fecha BETWEEN ? AND ?
    """, (desde_7, hoy))
    total_ingresos_7 = cur.fetchone()[0] or 0

    # Gastos últimos 7 días
    cur.execute("""
        SELECT COALESCE(SUM(monto),0)
        FROM gastos
        WHERE fecha BETWEEN ? AND ?
    """, (desde_7, hoy))
    total_gastos_7 = cur.fetchone()[0] or 0

    balance_7 = total_ingresos_7 - total_gastos_7

    # Serie diaria
    labels = []
    ingresos_por_dia = []
    gastos_por_dia = []

    for i in range(7):
        d = (date.today() - timedelta(days=(6 - i))).isoformat()
        labels.append(d)

        cur.execute("""
            SELECT COALESCE(SUM(COALESCE(total_servicios,total)),0)
            FROM facturas
            WHERE COALESCE(es_presupuesto,1)=0 AND fecha = ?
        """, (d,))
        ingresos_por_dia.append(float(cur.fetchone()[0] or 0))

        cur.execute("""
            SELECT COALESCE(SUM(monto),0)
            FROM gastos
            WHERE fecha = ?
        """, (d,))
        gastos_por_dia.append(float(cur.fetchone()[0] or 0))

    # Pendientes de cobro
    cur.execute("""
        SELECT
            r.id, r.fecha,
            c.nombre, c.apellido,
            v.patente, v.marca, v.modelo,
            r.descripcion,
            r.estado,
            f.id as factura_id,
            f.total as factura_total,
            f.es_presupuesto
        FROM reparaciones r
        JOIN vehiculos v ON v.id = r.vehiculo_id
        JOIN clientes c ON c.id = v.cliente_id
        LEFT JOIN facturas f
          ON f.id = (
              SELECT id
              FROM facturas
              WHERE reparacion_id = r.id
              ORDER BY id DESC
              LIMIT 1
          )
        WHERE r.estado IN ('Ingresado','Entregado')
        ORDER BY r.fecha DESC, r.id DESC
        LIMIT 20
    """)
    pendientes_cobro = cur.fetchall()

    con.close()

    last_backup_dt = get_last_backup_datetime()

    return render_template(
        "dashboard.html",
        trabajos=trabajos,
        citas=citas,
        reparaciones_en_proceso=reparaciones_en_proceso,
        reparaciones_pendientes=reparaciones_pendientes,
        reparaciones_hoy=reparaciones_hoy,
        total_ingresos_7=total_ingresos_7,
        total_gastos_7=total_gastos_7,
        balance_7=balance_7,
        gastos_pendientes=gastos_pendientes,
        total_gastos_pendientes=total_gastos_pendientes,
        diagnosticos_pendientes=diagnosticos_pendientes,
        last_backup_dt=last_backup_dt,
        pendientes_cobro=pendientes_cobro,
        labels=labels,
        ingresos_por_dia=ingresos_por_dia,
        gastos_por_dia=gastos_por_dia,
    )


# =========================
# Auth
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form["username"]
        password = request.form["password"]

        con = get_con()
        cur = con.cursor()
        cur.execute("SELECT id, rol FROM users WHERE username=? AND password=?", (user, password))
        data = cur.fetchone()
        con.close()

        if data:
            session["user_id"] = data[0]
            session["username"] = user
            session["rol"] = data[1] or "operador"
            return redirect(url_for("dashboard"))

        return render_template("login.html", error="Credenciales incorrectas")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================
# Usuarios (admin)
# =========================
@app.route("/usuarios")
@admin_required
def usuarios():
    con = get_con()
    cur = con.cursor()
    cur.execute("SELECT id, username, rol FROM users ORDER BY username")
    usuarios = cur.fetchall()
    con.close()
    return render_template("usuarios.html", usuarios=usuarios)


@app.route("/usuarios/nuevo", methods=["GET", "POST"])
@admin_required
def usuario_nuevo():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        rol = request.form.get("rol") or "operador"

        con = get_con()
        cur = con.cursor()
        cur.execute("INSERT INTO users (username, password, rol) VALUES (?, ?, ?)", (username, password, rol))
        con.commit()
        con.close()
        return redirect(url_for("usuarios"))

    return render_template("usuario_form.html")


@app.route("/backup", methods=["POST"])
@admin_required
def backup_manual():
    backup_db_if_changed()
    return redirect(url_for("dashboard"))


# =========================
# Clientes
# =========================
@app.route("/clientes")
@login_required
def clientes():
    q = request.args.get("q", "").strip()

    con = get_con()
    cur = con.cursor()

    if q:
        patron = f"%{q}%"
        cur.execute("""
            SELECT * FROM clientes
            WHERE nombre LIKE ?
               OR apellido LIKE ?
               OR telefono LIKE ?
               OR email LIKE ?
               OR id IN (SELECT cliente_id FROM vehiculos WHERE patente LIKE ? OR vin LIKE ?)
            ORDER BY apellido, nombre
        """, (patron, patron, patron, patron, patron, patron))
    else:
        cur.execute("SELECT * FROM clientes ORDER BY apellido, nombre")

    lista = cur.fetchall()
    con.close()

    return render_template("clientes.html", clientes=lista, q=q)


@app.route("/clientes/nuevo", methods=["GET", "POST"])
@login_required
def cliente_nuevo():
    if request.method == "POST":
        nombre_raw = request.form["nombre"]
        apellido_raw = request.form["apellido"]
        telefono = normalizar_telefono(request.form.get("telefono"))
        email = request.form.get("email", "").strip()
        direccion = request.form.get("direccion", "").strip()
        notas = request.form.get("notas", "").strip()
        documento = request.form.get("documento", "").strip()
        razon_social = request.form.get("razon_social", "").strip()

        nombre = nombre_raw.strip().title()
        apellido = apellido_raw.strip().title()

        con = get_con()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO clientes (nombre, apellido, telefono, email, direccion, notas, documento, razon_social)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (nombre, apellido, telefono, email, direccion, notas, documento, razon_social))
        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("clientes"))

    return render_template("cliente_form.html")


@app.route("/clientes/editar/<int:id>", methods=["GET", "POST"])
@login_required
def cliente_editar(id):
    con = get_con()
    cur = con.cursor()

    if request.method == "POST":
        nombre_raw = request.form["nombre"]
        apellido_raw = request.form["apellido"]
        telefono = normalizar_telefono(request.form.get("telefono"))
        email = request.form.get("email", "").strip()
        direccion = request.form.get("direccion", "").strip()
        notas = request.form.get("notas", "").strip()
        documento = request.form.get("documento", "").strip()
        razon_social = request.form.get("razon_social", "").strip()

        nombre = nombre_raw.strip().title()
        apellido = apellido_raw.strip().title()

        cur.execute("""
            UPDATE clientes
            SET nombre=?, apellido=?, telefono=?, email=?, direccion=?, notas=?, documento=?, razon_social=?
            WHERE id=?
        """, (nombre, apellido, telefono, email, direccion, notas, documento, razon_social, id))

        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("clientes"))

    cur.execute("SELECT * FROM clientes WHERE id=?", (id,))
    cliente = cur.fetchone()
    con.close()

    return render_template("cliente_form.html", cliente=cliente)


@app.route("/clientes/eliminar/<int:id>")
@login_required
def cliente_eliminar(id):
    con = get_con()
    cur = con.cursor()
    cur.execute("DELETE FROM clientes WHERE id=?", (id,))
    con.commit()
    con.close()

    backup_db_if_changed()
    return redirect(url_for("clientes"))


# =========================
# Vehículos
# =========================
@app.route("/clientes/<int:cliente_id>/vehiculos")
@login_required
def vehiculos_cliente(cliente_id):
    con = get_con()
    cur = con.cursor()

    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()

    cur.execute("SELECT * FROM vehiculos WHERE cliente_id=?", (cliente_id,))
    vehiculos = cur.fetchall()

    con.close()
    return render_template("vehiculos.html", cliente=cliente, vehiculos=vehiculos)


@app.route("/clientes/<int:cliente_id>/vehiculos/nuevo", methods=["GET", "POST"])
@login_required
def vehiculo_nuevo(cliente_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()
    con.close()

    if request.method == "POST":
        patente = request.form.get("patente", "").strip().upper()
        marca = request.form.get("marca", "").strip()
        modelo = request.form.get("modelo", "").strip()
        anio = request.form.get("anio", "").strip()
        km = request.form.get("km", "").strip()
        vin = limpiar_vin(request.form.get("vin", ""))
        notas = request.form.get("notas", "").strip()

        con = get_con()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO vehiculos (cliente_id, patente, marca, modelo, anio, km, vin, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (cliente_id, patente, marca, modelo, anio, km, vin, notas))
        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("vehiculos_cliente", cliente_id=cliente_id))

    return render_template("vehiculo_form.html", cliente=cliente)


@app.route("/vehiculos/editar/<int:id>", methods=["GET", "POST"])
@login_required
def vehiculo_editar(id):
    con = get_con()
    cur = con.cursor()
    cur.execute("SELECT * FROM vehiculos WHERE id=?", (id,))
    vehiculo = cur.fetchone()

    if not vehiculo:
        con.close()
        return redirect(url_for("clientes"))

    cliente_id = vehiculo["cliente_id"]
    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()

    if request.method == "POST":
        patente = request.form.get("patente", "").strip().upper()
        marca = request.form.get("marca", "").strip()
        modelo = request.form.get("modelo", "").strip()
        anio = request.form.get("anio", "").strip()
        km = request.form.get("km", "").strip()
        vin = limpiar_vin(request.form.get("vin", ""))
        notas = request.form.get("notas", "").strip()

        cur.execute("""
            UPDATE vehiculos
            SET patente=?, marca=?, modelo=?, anio=?, km=?, vin=?, notas=?
            WHERE id=?
        """, (patente, marca, modelo, anio, km, vin, notas, id))

        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("vehiculos_cliente", cliente_id=cliente_id))

    con.close()
    return render_template("vehiculo_form.html", cliente=cliente, vehiculo=vehiculo)


@app.route("/vehiculos/eliminar/<int:id>")
@login_required
def vehiculo_eliminar(id):
    con = get_con()
    cur = con.cursor()
    cur.execute("SELECT cliente_id FROM vehiculos WHERE id=?", (id,))
    row = cur.fetchone()

    if row:
        cliente_id = row[0]
        cur.execute("DELETE FROM vehiculos WHERE id=?", (id,))
        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("vehiculos_cliente", cliente_id=cliente_id))

    con.close()
    return redirect(url_for("clientes"))


# =========================
# Reparaciones
# =========================
@app.route("/vehiculos/<int:vehiculo_id>/reparaciones")
@login_required
def reparaciones_vehiculo(vehiculo_id):
    desde = request.args.get("desde", "").strip()
    hasta = request.args.get("hasta", "").strip()

    con = get_con()
    cur = con.cursor()

    cur.execute("SELECT * FROM vehiculos WHERE id=?", (vehiculo_id,))
    vehiculo = cur.fetchone()
    if not vehiculo:
        con.close()
        return redirect(url_for("clientes"))

    cliente_id = vehiculo["cliente_id"]
    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()

    sql = "SELECT * FROM reparaciones WHERE vehiculo_id=?"
    params = [vehiculo_id]

    if desde:
        sql += " AND fecha >= ?"
        params.append(desde)
    if hasta:
        sql += " AND fecha <= ?"
        params.append(hasta)

    sql += " ORDER BY fecha DESC, id DESC"

    cur.execute(sql, params)
    reparaciones = cur.fetchall()
    con.close()

    return render_template(
        "reparaciones.html",
        cliente=cliente,
        vehiculo=vehiculo,
        reparaciones=reparaciones,
        desde=desde,
        hasta=hasta
    )


@app.route("/vehiculos/<int:vehiculo_id>/reparaciones/nueva", methods=["GET", "POST"])
@login_required
def reparacion_nueva(vehiculo_id):
    con = get_con()
    cur = con.cursor()

    cur.execute("SELECT * FROM vehiculos WHERE id=?", (vehiculo_id,))
    vehiculo = cur.fetchone()
    if not vehiculo:
        con.close()
        return redirect(url_for("clientes"))

    cliente_id = vehiculo["cliente_id"]
    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()

    if request.method == "POST":
        fecha = request.form.get("fecha", "")
        descripcion = request.form.get("descripcion", "").strip()
        notas = request.form.get("notas", "").strip()
        km = request.form.get("km", "").strip()

        estado = (request.form.get("estado") or "Presupuesto").strip()
        if estado not in ESTADOS:
            estado = "Presupuesto"

        cur.execute("""
            INSERT INTO reparaciones (vehiculo_id, fecha, descripcion, notas, estado, km)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (vehiculo_id, fecha, descripcion, notas, estado, km))

        reparacion_id = cur.lastrowid

        km_num = parsear_km(km)
        if km_num is not None:
            actualizar_km_vehiculo_si_corresponde(
                cur,
                vehiculo_id=vehiculo_id,
                km_nuevo=km_num,
                fuente="Carga manual reparación",
                reparacion_id=reparacion_id,
                fecha=fecha
            )

        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))

    con.close()
    return render_template(
        "reparacion_form.html",
        cliente=cliente,
        vehiculo=vehiculo,
        estados=ESTADOS
    )


@app.route("/reparaciones/editar/<int:reparacion_id>", methods=["GET", "POST"])
@login_required
def reparacion_editar(reparacion_id):
    con = get_con()
    cur = con.cursor()

    cur.execute("SELECT * FROM reparaciones WHERE id=?", (reparacion_id,))
    reparacion = cur.fetchone()
    if not reparacion:
        con.close()
        return redirect(url_for("clientes"))

    vehiculo_id = reparacion["vehiculo_id"]
    cur.execute("SELECT * FROM vehiculos WHERE id=?", (vehiculo_id,))
    vehiculo = cur.fetchone()
    cliente_id = vehiculo["cliente_id"]
    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()

    if request.method == "POST":
        fecha = request.form.get("fecha", "")
        descripcion = request.form.get("descripcion", "").strip()
        notas = request.form.get("notas", "").strip()
        km = request.form.get("km", "").strip()
        estado = (request.form.get("estado") or reparacion["estado"] or "Presupuesto").strip()

        if estado not in ESTADOS:
            estado = "Presupuesto"

        cur.execute("""
            UPDATE reparaciones
            SET fecha=?, descripcion=?, notas=?, estado=?, km=?
            WHERE id=?
        """, (fecha, descripcion, notas, estado, km, reparacion_id))

        km_num = parsear_km(km)
        if km_num is not None:
            actualizar_km_vehiculo_si_corresponde(
                cur,
                vehiculo_id=vehiculo_id,
                km_nuevo=km_num,
                fuente="Edición reparación",
                reparacion_id=reparacion_id,
                fecha=fecha
            )

        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))

    con.close()
    return render_template(
        "reparacion_form.html",
        cliente=cliente,
        vehiculo=vehiculo,
        reparacion=reparacion,
        estados=ESTADOS
    )


@app.route("/reparaciones/eliminar/<int:reparacion_id>")
@login_required
def reparacion_eliminar(reparacion_id):
    con = get_con()
    cur = con.cursor()

    cur.execute("SELECT vehiculo_id FROM reparaciones WHERE id=?", (reparacion_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        return redirect(url_for("clientes"))

    vehiculo_id = row[0]

    cur.execute("DELETE FROM reparacion_items WHERE reparacion_id=?", (reparacion_id,))
    cur.execute("DELETE FROM reparacion_imagenes WHERE reparacion_id=?", (reparacion_id,))
    cur.execute("DELETE FROM facturas WHERE reparacion_id=?", (reparacion_id,))
    cur.execute("DELETE FROM gastos WHERE reparacion_id=?", (reparacion_id,))
    cur.execute("UPDATE diagnosticos SET reparacion_id=NULL WHERE reparacion_id=?", (reparacion_id,))
    cur.execute("DELETE FROM reparaciones WHERE id=?", (reparacion_id,))
    con.commit()
    con.close()

    backup_db_if_changed()
    return redirect(url_for("reparaciones_vehiculo", vehiculo_id=vehiculo_id))


@app.route("/reparaciones/<int:reparacion_id>")
@login_required
def reparacion_detalle(reparacion_id):
    con = get_con()
    cur = con.cursor()

    cur.execute("SELECT * FROM reparaciones WHERE id=?", (reparacion_id,))
    reparacion = cur.fetchone()
    if not reparacion:
        con.close()
        return redirect(url_for("clientes"))

    vehiculo_id = reparacion["vehiculo_id"]
    cur.execute("SELECT * FROM vehiculos WHERE id=?", (vehiculo_id,))
    vehiculo = cur.fetchone()
    cliente_id = vehiculo["cliente_id"]
    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()

    cur.execute("SELECT * FROM reparacion_items WHERE reparacion_id=? ORDER BY id ASC", (reparacion_id,))
    items = cur.fetchall()

    cur.execute("""
        SELECT id, filename, descripcion
        FROM reparacion_imagenes
        WHERE reparacion_id=?
        ORDER BY id DESC
    """, (reparacion_id,))
    imagenes = cur.fetchall()

    # diagnósticos vinculados a esta reparación
    cur.execute("""
        SELECT id, fecha_mail, vin, marca, modelo, odometro, filename, subject
        FROM diagnosticos
        WHERE reparacion_id = ?
        ORDER BY id DESC
    """, (reparacion_id,))
    diagnosticos = cur.fetchall()

    total = 0
    for it in items:
        cantidad = it["cantidad"] or 0
        precio = it["precio_unitario"] or 0
        descuento = it["descuento"] or 0
        subtotal = cantidad * precio * (1 - descuento / 100.0)
        total += subtotal

    con.close()

    return render_template(
        "reparacion_detalle.html",
        cliente=cliente,
        vehiculo=vehiculo,
        reparacion=reparacion,
        items=items,
        total=total,
        imagenes=imagenes,
        diagnosticos=diagnosticos
    )

@app.route("/diagnosticos/<int:diag_id>/ver")
@login_required
def diagnostico_ver(diag_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("SELECT filename FROM diagnosticos WHERE id=?", (diag_id,))
    row = cur.fetchone()
    con.close()

    if not row:
        return redirect(url_for("diagnosticos_listado"))

    filename = row[0]

    return send_from_directory(
        UPLOAD_DIAG_FOLDER,
        filename,
        as_attachment=False  # 🔥 clave: lo abre en el navegador
    )

@app.route("/dashboard/gasto_rapido", methods=["POST"])
@login_required
def gasto_rapido():
    fecha = date.today().isoformat()
    categoria = request.form.get("categoria", "").strip()
    descripcion = request.form.get("descripcion", "").strip()
    monto = float(request.form.get("monto") or 0)

    if not categoria or monto <= 0:
        flash("Completá categoría y monto válido.", "warning")
        return redirect(url_for("dashboard"))

    con = get_con()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO gastos (fecha, categoria, descripcion, monto)
        VALUES (?, ?, ?, ?)
    """, (fecha, categoria, descripcion, monto))
    con.commit()
    con.close()

    flash("Gasto guardado.", "success")
    backup_db_if_changed()
    return redirect(url_for("dashboard"))


@app.route("/reparaciones/<int:reparacion_id>/estado", methods=["POST"])
@login_required
def reparacion_cambiar_estado(reparacion_id):
    nuevo_estado = (request.form.get("estado") or "").strip()
    confirm = (request.form.get("confirm") or "").strip()

    if not nuevo_estado:
        return redirect(url_for("dashboard"))

    if nuevo_estado not in ESTADOS:
        flash("Estado inválido.", "warning")
        return redirect(url_for("dashboard"))

    con = get_con()
    cur = con.cursor()

    if nuevo_estado == "Facturado":
        cur.execute("""
            SELECT id, total, es_presupuesto
            FROM facturas
            WHERE reparacion_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (reparacion_id,))
        fac = cur.fetchone()

        if not fac:
            con.close()
            flash("No hay factura/presupuesto generado en esta reparación.", "warning")
            return redirect(url_for("dashboard"))

        factura_id = fac["id"]
        es_presupuesto = fac["es_presupuesto"]

        if (es_presupuesto is None) or int(es_presupuesto) == 1:
            if confirm != "1":
                con.close()
                flash("Confirmación requerida para facturar.", "warning")
                return redirect(url_for("dashboard"))

            cur.execute("UPDATE facturas SET es_presupuesto = 0 WHERE id = ?", (factura_id,))

            cur.execute("""
                SELECT COALESCE(SUM(cantidad * precio_unitario * (1 - COALESCE(descuento,0)/100.0)), 0)
                FROM reparacion_items
                WHERE reparacion_id = ?
                  AND UPPER(COALESCE(tipo,'SERVICIO')) = 'REPUESTO'
            """, (reparacion_id,))
            total_repuestos = float(cur.fetchone()[0] or 0)

            cur.execute("""
                DELETE FROM gastos
                WHERE reparacion_id = ?
                  AND categoria = 'Repuestos'
            """, (reparacion_id,))

            if total_repuestos > 0:
                hoy = date.today().isoformat()

                cur.execute("""
                    SELECT v.patente
                    FROM reparaciones r
                    JOIN vehiculos v ON v.id = r.vehiculo_id
                    WHERE r.id = ?
                """, (reparacion_id,))
                pat = cur.fetchone()
                patente = pat[0] if pat else ""

                descripcion = f"Repuestos reparación #{reparacion_id} - {patente}".strip()

                cur.execute("""
                    INSERT INTO gastos (
                        fecha, categoria, descripcion, monto,
                        pagador, medio_pago, notas,
                        pagado, fecha_pago, reparacion_id
                    )
                    VALUES (?, 'Repuestos', ?, ?, '', NULL, NULL, 0, NULL, ?)
                """, (hoy, descripcion, total_repuestos, reparacion_id))

    cur.execute("UPDATE reparaciones SET estado=? WHERE id=?", (nuevo_estado, reparacion_id))
    con.commit()
    con.close()

    flash("Estado actualizado.", "success")
    backup_db_if_changed()
    return redirect(url_for("dashboard"))


# =========================
# Items
# =========================
@app.route("/reparaciones/<int:reparacion_id>/items/nuevo", methods=["GET", "POST"])
@login_required
def item_nuevo(reparacion_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("SELECT * FROM reparaciones WHERE id=?", (reparacion_id,))
    reparacion = cur.fetchone()

    if not reparacion:
        con.close()
        return redirect(url_for("clientes"))

    if request.method == "POST":
        concepto = request.form.get("concepto", "").strip().upper()
        cantidad = float(request.form.get("cantidad") or 0)
        precio_unitario = float(request.form.get("precio_unitario") or 0)
        descuento = float(request.form.get("descuento") or 0)
        tipo = (request.form.get("tipo") or "SERVICIO").strip().upper()

        if tipo not in ("SERVICIO", "REPUESTO"):
            tipo = "SERVICIO"

        cur.execute("""
            INSERT INTO reparacion_items (reparacion_id, concepto, cantidad, precio_unitario, descuento, tipo)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (reparacion_id, concepto, cantidad, precio_unitario, descuento, tipo))

        cur.execute("INSERT OR IGNORE INTO item_conceptos (nombre) VALUES (?)", (concepto,))
        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))

    cur.execute("SELECT id, nombre FROM item_conceptos ORDER BY nombre")
    conceptos = cur.fetchall()
    con.close()

    return render_template("item_form.html", reparacion=reparacion, conceptos=conceptos)


@app.route("/items/editar/<int:item_id>", methods=["GET", "POST"])
@login_required
def item_editar(item_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("SELECT * FROM reparacion_items WHERE id=?", (item_id,))
    item = cur.fetchone()

    if not item:
        con.close()
        return redirect(url_for("clientes"))

    reparacion_id = item["reparacion_id"]
    cur.execute("SELECT * FROM reparaciones WHERE id=?", (reparacion_id,))
    reparacion = cur.fetchone()

    if request.method == "POST":
        concepto = request.form.get("concepto", "").strip().upper()
        cantidad = float(request.form.get("cantidad") or 0)
        precio_unitario = float(request.form.get("precio_unitario") or 0)
        descuento = float(request.form.get("descuento") or 0)
        tipo = (request.form.get("tipo") or "SERVICIO").strip().upper()

        if tipo not in ("SERVICIO", "REPUESTO"):
            tipo = "SERVICIO"

        cur.execute("""
            UPDATE reparacion_items
            SET concepto=?, cantidad=?, precio_unitario=?, descuento=?, tipo=?
            WHERE id=?
        """, (concepto, cantidad, precio_unitario, descuento, tipo, item_id))

        cur.execute("INSERT OR IGNORE INTO item_conceptos (nombre) VALUES (?)", (concepto,))
        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))

    cur.execute("SELECT id, nombre FROM item_conceptos ORDER BY nombre")
    conceptos = cur.fetchall()
    con.close()

    return render_template("item_form.html", item=item, reparacion=reparacion, conceptos=conceptos)


@app.route("/items/eliminar/<int:item_id>")
@login_required
def item_eliminar(item_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("SELECT reparacion_id FROM reparacion_items WHERE id=?", (item_id,))
    row = cur.fetchone()

    if not row:
        con.close()
        return redirect(url_for("clientes"))

    reparacion_id = row[0]
    cur.execute("DELETE FROM reparacion_items WHERE id=?", (item_id,))
    con.commit()
    con.close()

    backup_db_if_changed()
    return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))


@app.route("/items/concepto/eliminar/<int:concepto_id>")
@login_required
def item_concepto_eliminar(concepto_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("DELETE FROM item_conceptos WHERE id=?", (concepto_id,))
    con.commit()
    con.close()

    backup_db_if_changed()
    return redirect(request.referrer or url_for("facturas_listado"))


# =========================
# Imágenes
# =========================
@app.route("/reparaciones/<int:reparacion_id>/imagenes/nueva", methods=["GET", "POST"])
@login_required
def reparacion_imagen_nueva(reparacion_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("SELECT * FROM reparaciones WHERE id=?", (reparacion_id,))
    reparacion = cur.fetchone()

    if not reparacion:
        con.close()
        return redirect(url_for("clientes"))

    if request.method == "POST":
        files = request.files.getlist("imagenes")
        descripcion = request.form.get("descripcion", "").strip()

        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename or "")
                if not filename:
                    continue

                ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
                unique_name = f"rep_{reparacion_id}_{int(time.time()*1000)}.{ext}"
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)

                file.save(save_path)

                cur.execute("""
                    INSERT INTO reparacion_imagenes (reparacion_id, filename, descripcion)
                    VALUES (?, ?, ?)
                """, (reparacion_id, unique_name, descripcion))

        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))

    con.close()
    return render_template("imagen_form.html", reparacion=reparacion)


@app.route("/reparaciones/imagenes/eliminar/<int:img_id>")
@login_required
def reparacion_imagen_eliminar(img_id):
    con = get_con()
    cur = con.cursor()

    cur.execute("SELECT reparacion_id, filename FROM reparacion_imagenes WHERE id=?", (img_id,))
    row = cur.fetchone()

    if not row:
        con.close()
        return redirect(url_for("clientes"))

    reparacion_id, filename = row

    if filename:
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

    cur.execute("DELETE FROM reparacion_imagenes WHERE id=?", (img_id,))
    con.commit()
    con.close()

    backup_db_if_changed()
    return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))


# =========================
# Factura / Presupuesto
# =========================
@app.route("/reparaciones/<int:reparacion_id>/factura")
@login_required
def reparacion_factura(reparacion_id):
    con = get_con()
    cur = con.cursor()

    cur.execute("SELECT * FROM reparaciones WHERE id=?", (reparacion_id,))
    reparacion = cur.fetchone()
    if not reparacion:
        con.close()
        return redirect(url_for("clientes"))

    vehiculo_id = reparacion["vehiculo_id"]
    cur.execute("SELECT * FROM vehiculos WHERE id=?", (vehiculo_id,))
    vehiculo = cur.fetchone()
    cliente_id = vehiculo["cliente_id"]
    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()

    cur.execute("SELECT * FROM reparacion_items WHERE reparacion_id=? ORDER BY id ASC", (reparacion_id,))
    items = cur.fetchall()

    base_total = 0.0
    subtotal_repuestos = 0.0
    subtotal_servicios = 0.0

    for it in items:
        cantidad = float(it["cantidad"] or 0)
        precio = float(it["precio_unitario"] or 0)
        descuento = float(it["descuento"] or 0)
        subtotal = cantidad * precio * (1 - descuento / 100.0)
        base_total += subtotal

        tipo = (it["tipo"] or "SERVICIO").strip().upper()
        if tipo == "REPUESTO":
            subtotal_repuestos += subtotal
        else:
            subtotal_servicios += subtotal

    cur.execute("""
        SELECT id, reparacion_id, fecha, total, descuento_global, es_presupuesto, total_servicios, total_repuestos
        FROM facturas
        WHERE reparacion_id=?
    """, (reparacion_id,))
    row = cur.fetchone()

    hoy = date.today().isoformat()

    if not row:
        descuento_global = 0.0
        total_final = base_total

        cur.execute("""
            INSERT INTO facturas (reparacion_id, fecha, total, descuento_global, es_presupuesto, total_servicios, total_repuestos)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        """, (reparacion_id, hoy, total_final, descuento_global, subtotal_servicios, subtotal_repuestos))
        con.commit()

        factura_id = cur.lastrowid
        factura_fecha = hoy
        es_presupuesto = 1
    else:
        factura_id = row[0]
        factura_fecha = row[2]
        descuento_global = float(row[4] or 0.0)
        es_presupuesto = int(row[5] or 1)

        total_final = base_total * (1 - descuento_global / 100.0)

        cur.execute("""
            UPDATE facturas
            SET total=?, descuento_global=?, total_servicios=?, total_repuestos=?
            WHERE id=?
        """, (total_final, descuento_global, subtotal_servicios, subtotal_repuestos, factura_id))
        con.commit()

    tel_raw = cliente["telefono"] if "telefono" in cliente.keys() else (cliente[3] or "")
    tel_digits = "".join(ch for ch in (tel_raw or "") if ch.isdigit())
    wa_phone = ""

    if tel_digits:
        if tel_digits.startswith("0"):
            tel_digits = tel_digits[1:]
        if not tel_digits.startswith("54"):
            wa_phone = "54" + tel_digits
        else:
            wa_phone = tel_digits

    presupuesto_url = url_for("reparacion_factura", reparacion_id=reparacion_id, _external=True)

    con.close()

    return render_template(
        "factura.html",
        factura_id=factura_id,
        factura_fecha=factura_fecha,
        cliente=cliente,
        vehiculo=vehiculo,
        reparacion=reparacion,
        items=items,
        base_total=base_total,
        descuento_global=descuento_global,
        total=base_total * (1 - descuento_global / 100.0),
        wa_phone=wa_phone,
        presupuesto_url=presupuesto_url,
        es_presupuesto=es_presupuesto,
        subtotal_repuestos=subtotal_repuestos,
        subtotal_servicios=subtotal_servicios
    )


@app.route("/facturas/<int:factura_id>/descuento", methods=["POST"])
@login_required
def factura_descuento(factura_id):
    con = get_con()
    cur = con.cursor()

    desc = float(request.form.get("descuento_global") or 0)
    cur.execute("UPDATE facturas SET descuento_global=? WHERE id=?", (desc, factura_id))
    con.commit()

    cur.execute("SELECT reparacion_id FROM facturas WHERE id=?", (factura_id,))
    row = cur.fetchone()
    con.close()

    backup_db_if_changed()

    if row:
        return redirect(url_for("reparacion_factura", reparacion_id=row[0]))
    return redirect(url_for("facturas_listado"))


@app.route("/facturas/<int:factura_id>/confirmar", methods=["POST"])
@login_required
def factura_confirmar(factura_id):
    con = get_con()
    cur = con.cursor()

    cur.execute("SELECT reparacion_id FROM facturas WHERE id=?", (factura_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        return redirect(url_for("facturas_listado"))

    reparacion_id = row[0]
    hoy = date.today().isoformat()

    cur.execute("""
        SELECT cantidad, precio_unitario, COALESCE(descuento,0), COALESCE(tipo,'SERVICIO')
        FROM reparacion_items
        WHERE reparacion_id=?
    """, (reparacion_id,))
    items = cur.fetchall()

    total_repuestos = 0.0
    total_servicios = 0.0

    for cant, precio, desc, tipo in items:
        cant = float(cant or 0)
        precio = float(precio or 0)
        desc = float(desc or 0)
        subtotal = cant * precio * (1 - desc / 100.0)

        tipo_norm = (tipo or "SERVICIO").strip().upper()
        if tipo_norm in ("REPUESTO", "REPUESTOS"):
            total_repuestos += subtotal
        else:
            total_servicios += subtotal

    total_final = total_servicios + total_repuestos

    cur.execute("""
        UPDATE facturas
        SET es_presupuesto = 0,
            total_servicios = ?,
            total_repuestos = ?,
            total = ?,
            fecha = ?
        WHERE id=?
    """, (total_servicios, total_repuestos, total_final, hoy, factura_id))

    cur.execute("UPDATE reparaciones SET estado='Facturado' WHERE id=?", (reparacion_id,))

    cur.execute("""
        DELETE FROM gastos
        WHERE reparacion_id = ?
          AND categoria = 'Repuestos'
    """, (reparacion_id,))

    if total_repuestos > 0:
        cur.execute("""
            SELECT v.patente
            FROM reparaciones r
            JOIN vehiculos v ON v.id = r.vehiculo_id
            WHERE r.id = ?
        """, (reparacion_id,))
        pat = cur.fetchone()
        patente = pat[0] if pat else ""

        descripcion = f"Repuestos reparación #{reparacion_id} - {patente}".strip()

        cur.execute("""
            INSERT INTO gastos (
                fecha, categoria, descripcion, monto,
                pagador, medio_pago, notas,
                pagado, fecha_pago, reparacion_id
            )
            VALUES (?, 'Repuestos', ?, ?, '', NULL, NULL, 0, NULL, ?)
        """, (hoy, descripcion, total_repuestos, reparacion_id))

    con.commit()
    con.close()

    backup_db_if_changed()
    return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))


@app.route("/facturas", methods=["GET"])
@login_required
def facturas_listado():
    con = get_con()
    cur = con.cursor()

    desde = request.args.get("desde") or ""
    hasta = request.args.get("hasta") or ""

    sql_f = """
        SELECT 
            f.id,
            f.fecha,
            f.total,
            COALESCE(f.total_servicios, f.total) AS total_servicios,
            COALESCE(f.total_repuestos, 0) AS total_repuestos,
            c.nombre,
            c.apellido,
            v.patente,
            v.marca,
            v.modelo,
            r.id
        FROM facturas f
        JOIN reparaciones r ON r.id = f.reparacion_id
        JOIN vehiculos v ON v.id = r.vehiculo_id
        JOIN clientes c ON c.id = v.cliente_id
        WHERE COALESCE(f.es_presupuesto,1) = 0
    """

    filtros = []
    params = []

    if desde:
        filtros.append("DATE(f.fecha) >= DATE(?)")
        params.append(desde)
    if hasta:
        filtros.append("DATE(f.fecha) <= DATE(?)")
        params.append(hasta)

    if filtros:
        sql_f += " AND " + " AND ".join(filtros)

    sql_f += " ORDER BY f.fecha DESC"
    cur.execute(sql_f, params)
    facturas = cur.fetchall()

    sql_g = """
        SELECT id, fecha, categoria, descripcion, monto, pagador, medio_pago, notas, pagado, fecha_pago, reparacion_id
        FROM gastos
        WHERE 1=1
    """

    filtros_g = []
    params_g = []

    if desde:
        filtros_g.append("DATE(fecha) >= DATE(?)")
        params_g.append(desde)
    if hasta:
        filtros_g.append("DATE(fecha) <= DATE(?)")
        params_g.append(hasta)

    if filtros_g:
        sql_g += " AND " + " AND ".join(filtros_g)

    sql_g += " ORDER BY fecha DESC, id DESC"
    cur.execute(sql_g, params_g)
    gastos = cur.fetchall()

    total_ingresos = sum((f[3] or 0) for f in facturas)
    total_gastos = sum((g[4] or 0) for g in gastos)
    balance_neto = total_ingresos - total_gastos

    con.close()

    return render_template(
        "facturas.html",
        facturas=facturas,
        gastos=gastos,
        desde=desde,
        hasta=hasta,
        total_general=total_ingresos,
        total_gastos=total_gastos,
        balance_neto=balance_neto,
    )


# =========================
# Gastos
# =========================
@app.route("/gastos")
@login_required
def gastos_listado():
    desde = request.args.get("desde", "").strip()
    hasta = request.args.get("hasta", "").strip()

    con = get_con()
    cur = con.cursor()

    sql = """
        SELECT id, fecha, categoria, descripcion, monto,
               pagador, medio_pago, notas, COALESCE(pagado,0), fecha_pago, reparacion_id
        FROM gastos
        WHERE 1=1
    """
    params = []

    if desde:
        sql += " AND fecha >= ?"
        params.append(desde)
    if hasta:
        sql += " AND fecha <= ?"
        params.append(hasta)

    sql += " ORDER BY fecha DESC, id DESC"

    cur.execute(sql, params)
    gastos = cur.fetchall()
    con.close()

    total_gastos = sum((row[4] or 0) for row in gastos) if gastos else 0

    return render_template("gastos.html", gastos=gastos, desde=desde, hasta=hasta, total_gastos=total_gastos)


@app.route("/gastos/nuevo", methods=["POST"])
@login_required
def gasto_nuevo():
    fecha = request.form.get("fecha")
    descripcion = request.form.get("descripcion", "").strip()
    monto = request.form.get("monto")
    categoria = request.form.get("categoria", "").strip()
    pagador = request.form.get("pagador", "").strip()

    try:
        monto = float(monto)
    except Exception:
        monto = 0

    pagado = 0
    fecha_pago = None

    con = get_con()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO gastos
        (fecha, categoria, descripcion, monto, pagador, pagado, fecha_pago)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (fecha, categoria, descripcion, monto, pagador, pagado, fecha_pago))

    con.commit()
    con.close()

    backup_db_if_changed()
    return redirect(url_for("gastos_listado"))


@app.route("/gastos/eliminar/<int:gasto_id>")
@login_required
def gasto_eliminar(gasto_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("DELETE FROM gastos WHERE id=?", (gasto_id,))
    con.commit()
    con.close()

    backup_db_if_changed()
    return redirect(url_for("gastos_listado"))


@app.route("/gastos/<int:gasto_id>/toggle_pagado", methods=["POST"])
@login_required
def gasto_toggle_pagado(gasto_id):
    con = get_con()
    cur = con.cursor()

    cur.execute("""
        UPDATE gastos
        SET pagado = CASE COALESCE(pagado,0)
                        WHEN 0 THEN 1
                        ELSE 0
                     END,
            fecha_pago = CASE COALESCE(pagado,0)
                            WHEN 0 THEN ?
                            ELSE NULL
                         END
        WHERE id = ?
    """, (date.today().isoformat(), gasto_id))

    con.commit()
    con.close()

    backup_db_if_changed()
    return redirect(request.referrer or url_for("gastos_listado"))


# =========================
# Citas
# =========================
@app.route("/citas")
@login_required
def citas_listado():
    desde = request.args.get("desde", "").strip()
    hasta = request.args.get("hasta", "").strip()

    con = get_con()
    cur = con.cursor()

    sql = "SELECT id, fecha, hora, cliente_nombre, telefono, descripcion FROM citas WHERE 1=1"
    params = []

    if desde:
        sql += " AND fecha >= ?"
        params.append(desde)
    if hasta:
        sql += " AND fecha <= ?"
        params.append(hasta)

    sql += " ORDER BY fecha ASC, hora ASC"

    cur.execute(sql, params)
    citas = cur.fetchall()
    con.close()

    return render_template("citas.html", citas=citas, desde=desde, hasta=hasta)


@app.route("/citas/nueva", methods=["GET", "POST"])
@login_required
def cita_nueva():
    if request.method == "POST":
        fecha = request.form.get("fecha", "")
        hora = request.form.get("hora", "")
        cliente_nombre = request.form.get("cliente_nombre", "").strip()
        telefono = request.form.get("telefono", "").strip()
        descripcion = request.form.get("descripcion", "").strip()

        con = get_con()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO citas (fecha, hora, cliente_nombre, telefono, descripcion)
            VALUES (?, ?, ?, ?, ?)
        """, (fecha, hora, cliente_nombre, telefono, descripcion))
        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("citas_listado"))

    return render_template("cita_form.html")


@app.route("/citas/editar/<int:cita_id>", methods=["GET", "POST"])
@login_required
def cita_editar(cita_id):
    con = get_con()
    cur = con.cursor()

    if request.method == "POST":
        fecha = request.form.get("fecha", "")
        hora = request.form.get("hora", "")
        cliente_nombre = request.form.get("cliente_nombre", "").strip()
        telefono = request.form.get("telefono", "").strip()
        descripcion = request.form.get("descripcion", "").strip()

        cur.execute("""
            UPDATE citas
            SET fecha=?, hora=?, cliente_nombre=?, telefono=?, descripcion=?
            WHERE id=?
        """, (fecha, hora, cliente_nombre, telefono, descripcion, cita_id))
        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("citas_listado"))

    cur.execute("SELECT id, fecha, hora, cliente_nombre, telefono, descripcion FROM citas WHERE id=?", (cita_id,))
    cita = cur.fetchone()
    con.close()

    if not cita:
        return redirect(url_for("citas_listado"))

    return render_template("cita_form.html", cita=cita)


@app.route("/citas/eliminar/<int:cita_id>")
@login_required
def cita_eliminar(cita_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("DELETE FROM citas WHERE id=?", (cita_id,))
    con.commit()
    con.close()

    backup_db_if_changed()
    return redirect(url_for("citas_listado"))


# =========================
# Lista de precios
# =========================
@app.route("/precios")
@login_required
def lista_precios():
    q = request.args.get("q", "").strip()
    tipo = request.args.get("tipo", "").strip()

    con = get_con()
    cur = con.cursor()

    sql = """
        SELECT id, concepto, categoria, tipo, precio, notas, activo
        FROM lista_precios
        WHERE 1=1
    """
    params = []

    if q:
        sql += " AND (concepto LIKE ? OR categoria LIKE ?)"
        patron = f"%{q}%"
        params.extend([patron, patron])

    if tipo:
        sql += " AND tipo = ?"
        params.append(tipo)

    sql += " ORDER BY concepto ASC"

    cur.execute(sql, params)
    precios = cur.fetchall()
    con.close()

    return render_template("precios.html", precios=precios, q=q, tipo=tipo)


@app.route("/precios/nuevo", methods=["GET", "POST"])
@login_required
def precio_nuevo():
    if request.method == "POST":
        concepto = request.form["concepto"].strip().upper()
        categoria = request.form.get("categoria", "").strip()
        tipo = request.form.get("tipo", "SERVICIO").strip().upper()
        precio = float(request.form.get("precio") or 0)
        notas = request.form.get("notas", "").strip()
        activo = 1 if request.form.get("activo") == "1" else 0

        con = get_con()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO lista_precios (concepto, categoria, tipo, precio, notas, activo)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (concepto, categoria, tipo, precio, notas, activo))
        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("lista_precios"))

    return render_template("precio_form.html", precio=None)


@app.route("/precios/editar/<int:precio_id>", methods=["GET", "POST"])
@login_required
def precio_editar(precio_id):
    con = get_con()
    cur = con.cursor()

    cur.execute("""
        SELECT id, concepto, categoria, tipo, precio, notas, activo
        FROM lista_precios
        WHERE id=?
    """, (precio_id,))
    precio_item = cur.fetchone()

    if not precio_item:
        con.close()
        return redirect(url_for("lista_precios"))

    if request.method == "POST":
        concepto = request.form["concepto"].strip().upper()
        categoria = request.form.get("categoria", "").strip()
        tipo = request.form.get("tipo", "SERVICIO").strip().upper()
        precio = float(request.form.get("precio") or 0)
        notas = request.form.get("notas", "").strip()
        activo = 1 if request.form.get("activo") == "1" else 0

        cur.execute("""
            UPDATE lista_precios
            SET concepto=?, categoria=?, tipo=?, precio=?, notas=?, activo=?
            WHERE id=?
        """, (concepto, categoria, tipo, precio, notas, activo, precio_id))
        con.commit()
        con.close()

        backup_db_if_changed()
        return redirect(url_for("lista_precios"))

    con.close()
    return render_template("precio_form.html", precio=precio_item)


@app.route("/precios/eliminar/<int:precio_id>")
@login_required
def precio_eliminar(precio_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("DELETE FROM lista_precios WHERE id=?", (precio_id,))
    con.commit()
    con.close()

    backup_db_if_changed()
    return redirect(url_for("lista_precios"))


# =========================
# Arranque
# =========================
def ensure_folders():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(UPLOAD_DIAG_FOLDER, exist_ok=True)
    os.makedirs(BACKUP_FOLDER, exist_ok=True)


try:
    ensure_folders()
    init_db()
except Exception as e:
    print("Startup init error:", e)


# =========================
# Main
# =========================
if __name__ == "__main__":
    ensure_folders()
    init_db()
    backup_db_if_changed()
    app.run(host="0.0.0.0", port=5000, debug=True)