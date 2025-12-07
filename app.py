from flask import Flask, render_template, request, redirect, session, url_for
import sqlite3
import os
from datetime import date, datetime, timedelta
import shutil
from functools import wraps
from werkzeug.utils import secure_filename
import time
import re


app = Flask(__name__)

# Clave para sesiones (para uso local la dejamos fija)
app.secret_key = "clave_ultra_secreta_local"

DB_NAME = "database.db"
BACKUP_FOLDER = "backups"


UPLOAD_FOLDER = os.path.join("static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# -----------------------------
#  CONEXIÓN Y CREACIÓN DE DB
# -----------------------------
def get_con():
    return sqlite3.connect(DB_NAME)

# -----------------------------
#  TELEFONO DE 10 DIGITOS EN CLIENTES
# -----------------------------
def normalizar_telefono(raw):
    """Deja sólo dígitos en el teléfono."""
    if not raw:
        return None
    solo_digitos = re.sub(r'\D', '', raw)  # saca espacios, +, -, etc.

    # Opcional: si querés quedarte con los ÚLTIMOS 10 dígitos (tipo 11xxxxxxxx)
    # if len(solo_digitos) > 10:
    #     solo_digitos = solo_digitos[-10:]

    return solo_digitos

# ============================
# Helpers de formato de texto
# ============================

def formatear_titulo(texto: str) -> str:
    """
    Devuelve el texto con la primera letra de cada palabra en mayúscula
    y el resto en minúscula. Ej: 'jUaN pEdRo' -> 'Juan Pedro'
    """
    if not texto:
        return ""
    partes = texto.strip().split()
    return " ".join(p.capitalize() for p in partes)

def formatear_mayus(texto: str) -> str:
    """
    Devuelve el texto todo en mayúsculas, sin espacios de más.
    Ej: '  lIttEirNi ' -> 'LITTEIRNI'
    """
    if not texto:
        return ""
    return texto.strip().upper()



def init_db():
    con = get_con()
    cur = con.cursor()

    # USUARIOS
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

    # asegurar usuario admin con pass 1234
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

    # CLIENTES
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

    # VEHÍCULOS
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
        FOREIGN KEY(cliente_id) REFERENCES clientes(id)
    )
    """)

    # REPARACIONES
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reparaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehiculo_id INTEGER,
        fecha TEXT,
        descripcion TEXT,
        notas TEXT,
        estado TEXT,
        FOREIGN KEY(vehiculo_id) REFERENCES vehiculos(id)
    )
    """)
    cur.execute("PRAGMA table_info(reparaciones)")
    cols = [c[1] for c in cur.fetchall()]
    if "estado" not in cols:
        cur.execute("ALTER TABLE reparaciones ADD COLUMN estado TEXT")
        cur.execute("UPDATE reparaciones SET estado = 'Ingresado' WHERE estado IS NULL")

    # ÍTEMS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reparacion_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reparacion_id INTEGER,
        concepto TEXT,
        cantidad REAL,
        precio_unitario REAL,
        FOREIGN KEY(reparacion_id) REFERENCES reparaciones(id)
    )
    """)
    cur.execute("PRAGMA table_info(reparacion_items)")
    cols = [c[1] for c in cur.fetchall()]
    if "descuento" not in cols:
        cur.execute("ALTER TABLE reparacion_items ADD COLUMN descuento REAL")

    # NUEVO: tipo de ítem (SERVICIO / REPUESTO)
    cur.execute("PRAGMA table_info(reparacion_items)")
    cols = [c[1] for c in cur.fetchall()]
    if "tipo" not in cols:
        cur.execute("ALTER TABLE reparacion_items ADD COLUMN tipo TEXT DEFAULT 'SERVICIO'")

    # CONCEPTOS GUARDADOS PARA SUGERENCIAS DE ÍTEMS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS item_conceptos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE
    )
    """)

    # IMÁGENES DE REPARACIONES
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reparacion_imagenes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reparacion_id INTEGER,
        filename TEXT,
        descripcion TEXT,
        FOREIGN KEY(reparacion_id) REFERENCES reparaciones(id)
    )
    """)

    # FACTURAS / PRESUPUESTOS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS facturas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reparacion_id INTEGER,
        fecha TEXT,
        total REAL,
        FOREIGN KEY(reparacion_id) REFERENCES reparaciones(id)
    )
    """)
    cur.execute("PRAGMA table_info(facturas)")
    cols = [c[1] for c in cur.fetchall()]
    if "descuento_global" not in cols:
        cur.execute("ALTER TABLE facturas ADD COLUMN descuento_global REAL")

    # NUEVO: flag para saber si es presupuesto (1) o ya está en balance (0)
    cur.execute("PRAGMA table_info(facturas)")
    cols = [c[1] for c in cur.fetchall()]
    if "es_presupuesto" not in cols:
        cur.execute("ALTER TABLE facturas ADD COLUMN es_presupuesto INTEGER DEFAULT 1")
        # Lo viejo lo consideramos facturado (por si tenías datos históricos)
        cur.execute("UPDATE facturas SET es_presupuesto = 0 WHERE es_presupuesto IS NULL")

    # CITAS
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

    # GASTOS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS gastos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        categoria TEXT,
        descripcion TEXT,
        monto REAL
    )
    """)

    con.commit()
    con.close()

    # BACKUP
def backup_db():
    """
    Crea una copia de seguridad de la base de datos en la carpeta 'backups'
    con nombre backup_YYYYMMDD_HHMMSS.db
    """
    if not os.path.exists(BACKUP_FOLDER):
        os.makedirs(BACKUP_FOLDER, exist_ok=True)

    if os.path.exists(DB_NAME):
        ahora = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{ahora}.db"
        backup_path = os.path.join(BACKUP_FOLDER, backup_name)
        shutil.copy2(DB_NAME, backup_path)
        return backup_path
    return None


def get_last_backup_datetime():
    """
    Devuelve un datetime del último backup, o None si no hay.
    """
    if not os.path.exists(BACKUP_FOLDER):
        return None

    archivos = [
        f for f in os.listdir(BACKUP_FOLDER)
        if f.startswith("backup_") and f.endswith(".db")
    ]
    if not archivos:
        return None

    ultimo = max(archivos)  # por orden alfabético ya queda el más nuevo
    # formato backup_YYYYMMDD_HHMMSS.db
    try:
        stamp = ultimo[len("backup_"):-len(".db")]
        return datetime.strptime(stamp, "%Y%m%d_%H%M%S")
    except Exception:
        return None



# -----------------------------
#  DECORADORES DE SEGURIDAD
# -----------------------------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session or session.get("rol") != "admin":
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return wrapper


# ----------------- RUTAS PRINCIPALES -----------------
@app.route("/")
@login_required
def dashboard():
    con = get_con()
    cur = con.cursor()

    # --- Reparaciones en proceso (para la tabla como ya tenías) ---
    cur.execute("""
        SELECT 
            r.id,
            r.fecha,
            r.estado,
            r.descripcion,
            v.patente,
            v.marca,
            v.modelo,
            c.nombre,
            c.apellido
        FROM reparaciones r
        JOIN vehiculos v ON v.id = r.vehiculo_id
        JOIN clientes c ON c.id = v.cliente_id
        WHERE r.estado = 'En proceso'
        ORDER BY r.fecha DESC, r.id DESC
    """)
    trabajos = cur.fetchall()

    # --- Próximas citas (desde hoy en adelante) ---
    hoy = date.today()
    hoy_str = hoy.isoformat()
    cur.execute("""
        SELECT id, fecha, hora, cliente_nombre, telefono, descripcion
        FROM citas
        WHERE fecha >= ?
        ORDER BY fecha, hora
        LIMIT 10
    """, (hoy_str,))
    citas = cur.fetchall()

    # --- Métricas rápidas para tarjetas ---
    # Reparaciones en proceso
    cur.execute("SELECT COUNT(*) FROM reparaciones WHERE estado = 'En proceso'")
    reparaciones_en_proceso = cur.fetchone()[0] or 0

    # Reparaciones pendientes (no terminadas / entregadas / facturadas)
    cur.execute("""
        SELECT COUNT(*) 
        FROM reparaciones 
        WHERE estado IN ('Ingresado', 'En proceso', 'Esperando repuesto')
    """)
    reparaciones_pendientes = cur.fetchone()[0] or 0

    # Reparaciones con fecha de hoy
    cur.execute("SELECT COUNT(*) FROM reparaciones WHERE fecha = ?", (hoy_str,))
    reparaciones_hoy = cur.fetchone()[0] or 0

    # --- Ingresos y gastos últimos 7 días (para tarjetas y gráfico) ---
    desde_7 = (hoy - timedelta(days=6)).isoformat()
    hasta_7 = hoy_str

    # Total ingresos (facturas NO presupuesto)
    cur.execute("""
        SELECT COALESCE(SUM(total), 0)
        FROM facturas
        WHERE (es_presupuesto IS NULL OR es_presupuesto = 0)
          AND fecha BETWEEN ? AND ?
    """, (desde_7, hasta_7))
    total_ingresos_7 = cur.fetchone()[0] or 0

    # Total gastos
    cur.execute("""
        SELECT COALESCE(SUM(monto), 0)
        FROM gastos
        WHERE fecha BETWEEN ? AND ?
    """, (desde_7, hasta_7))
    total_gastos_7 = cur.fetchone()[0] or 0

    balance_7 = total_ingresos_7 - total_gastos_7

    # --- Datos día a día para el gráfico (últimos 7 días) ---
    labels = []
    ingresos_por_dia = []
    gastos_por_dia = []

    for i in range(6, -1, -1):
        dia = hoy - timedelta(days=i)
        dia_str = dia.isoformat()
        labels.append(dia_str)

        # ingresos
        cur.execute("""
            SELECT COALESCE(SUM(total), 0)
            FROM facturas
            WHERE (es_presupuesto IS NULL OR es_presupuesto = 0)
              AND fecha = ?
        """, (dia_str,))
        ingresos_dia = cur.fetchone()[0] or 0

        # gastos
        cur.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM gastos
            WHERE fecha = ?
        """, (dia_str,))
        gastos_dia = cur.fetchone()[0] or 0

        ingresos_por_dia.append(ingresos_dia)
        gastos_por_dia.append(gastos_dia)

    con.close()

    # --- Último backup ---
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
        labels=labels,
        ingresos_por_dia=ingresos_por_dia,
        gastos_por_dia=gastos_por_dia,
        last_backup_dt=last_backup_dt
    )



@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form["username"]
        password = request.form["password"]

        con = get_con()
        cur = con.cursor()
        cur.execute(
            "SELECT id, rol FROM users WHERE username=? AND password=?",
            (user, password)
        )
        data = cur.fetchone()
        con.close()

        if data:
            session["user_id"] = data[0]
            session["username"] = user
            session["rol"] = data[1] or "operador"
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Credenciales incorrectas")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ----------------- USUARIOS (ADMIN) -----------------
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
        cur.execute(
            "INSERT INTO users (username, password, rol) VALUES (?, ?, ?)",
            (username, password, rol)
        )
        con.commit()
        con.close()
        return redirect(url_for("usuarios"))

    return render_template("usuario_form.html")

@app.route("/backup", methods=["POST"])
@admin_required
def backup_manual():
    backup_db()
    return redirect(url_for("dashboard"))



# ----------------- CLIENTES -----------------
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
               OR id IN (
                    SELECT cliente_id FROM vehiculos WHERE patente LIKE ?
               )
            ORDER BY apellido, nombre
        """, (patron, patron, patron, patron, patron))
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
        telefono = normalizar_telefono(request.form.get('telefono'))
        email = request.form["email"].strip()
        direccion = request.form["direccion"].strip()
        notas = request.form["notas"].strip()
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
        telefono = request.form["telefono"].strip()
        email = request.form["email"].strip()
        direccion = request.form["direccion"].strip()
        notas = request.form["notas"].strip()
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
    return redirect(url_for("clientes"))


# ----------------- VEHÍCULOS -----------------
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
        patente = request.form["patente"].strip().upper()
        marca = request.form["marca"]
        modelo = request.form["modelo"]
        anio = request.form["anio"]
        km = request.form["km"]
        notas = request.form["notas"]

        con = get_con()
        cur = con.cursor()
        cur.execute("""
        INSERT INTO vehiculos (cliente_id, patente, marca, modelo, anio, km, notas)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (cliente_id, patente, marca, modelo, anio, km, notas))
        con.commit()
        con.close()

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

    cliente_id = vehiculo[1]
    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()

    if request.method == "POST":
        patente = request.form["patente"].strip().upper()
        marca = request.form["marca"]
        modelo = request.form["modelo"]
        anio = request.form["anio"]
        km = request.form["km"]
        notas = request.form["notas"]

        cur.execute("""
        UPDATE vehiculos
        SET patente=?, marca=?, modelo=?, anio=?, km=?, notas=?
        WHERE id=?
        """, (patente, marca, modelo, anio, km, notas, id))

        con.commit()
        con.close()
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
        return redirect(url_for("vehiculos_cliente", cliente_id=cliente_id))

    con.close()
    return redirect(url_for("clientes"))


# ----------------- REPARACIONES -----------------
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

    cliente_id = vehiculo[1]
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
    sql += " ORDER BY fecha DESC"

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

    cliente_id = vehiculo[1]
    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()

    if request.method == "POST":
        fecha = request.form["fecha"]
        descripcion = request.form["descripcion"]
        notas = request.form["notas"]
        estado = request.form.get("estado") or "Ingresado"

        cur.execute("""
        INSERT INTO reparaciones (vehiculo_id, fecha, descripcion, notas, estado)
        VALUES (?, ?, ?, ?, ?)
        """, (vehiculo_id, fecha, descripcion, notas, estado))
        con.commit()

        reparacion_id = cur.lastrowid
        con.close()
        return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))

    con.close()
    return render_template("reparacion_form.html", cliente=cliente, vehiculo=vehiculo)


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

    vehiculo_id = reparacion[1]
    cur.execute("SELECT * FROM vehiculos WHERE id=?", (vehiculo_id,))
    vehiculo = cur.fetchone()
    cliente_id = vehiculo[1]
    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()

    if request.method == "POST":
        fecha = request.form["fecha"]
        descripcion = request.form["descripcion"]
        notas = request.form["notas"]
        estado = request.form.get("estado") or "Ingresado"

        cur.execute("""
        UPDATE reparaciones
        SET fecha=?, descripcion=?, notas=?, estado=?
        WHERE id=?
        """, (fecha, descripcion, notas, estado, reparacion_id))

        con.commit()
        con.close()
        return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))

    con.close()
    return render_template("reparacion_form.html", cliente=cliente, vehiculo=vehiculo, reparacion=reparacion)


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
    cur.execute("DELETE FROM reparaciones WHERE id=?", (reparacion_id,))

    con.commit()
    con.close()

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

    vehiculo_id = reparacion[1]
    cur.execute("SELECT * FROM vehiculos WHERE id=?", (vehiculo_id,))
    vehiculo = cur.fetchone()
    cliente_id = vehiculo[1]
    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()

    cur.execute("SELECT * FROM reparacion_items WHERE reparacion_id=?", (reparacion_id,))
    items = cur.fetchall()

    # Imágenes asociadas
    cur.execute("""
        SELECT id, filename, descripcion
        FROM reparacion_imagenes
        WHERE reparacion_id=?
        ORDER BY id DESC
    """, (reparacion_id,))
    imagenes = cur.fetchall()

    total = 0
    for it in items:
        cantidad = it[3] or 0
        precio = it[4] or 0
        descuento = 0
        if len(it) > 5 and it[5] is not None:
            descuento = it[5]
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
        imagenes=imagenes
    )


@app.route("/reparaciones/<int:reparacion_id>/estado", methods=["POST"])
@login_required
def reparacion_cambiar_estado(reparacion_id):
    nuevo_estado = request.form.get("estado", "").strip()
    if not nuevo_estado:
        return redirect(url_for("dashboard"))

    con = get_con()
    cur = con.cursor()
    cur.execute(
        "UPDATE reparaciones SET estado=? WHERE id=?",
        (nuevo_estado, reparacion_id)
    )
    con.commit()
    con.close()

    # volvemos al detalle de la reparación
    return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))


# ----------------- ÍTEMS DE REPARACIÓN -----------------
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
        concepto = request.form["concepto"].strip().upper()
        cantidad = float(request.form["cantidad"] or 0)
        precio_unitario = float(request.form["precio_unitario"] or 0)
        descuento = float(request.form.get("descuento") or 0)
        tipo = request.form.get("tipo") or "SERVICIO"

        # Guardar ítem
        cur.execute("""
        INSERT INTO reparacion_items (reparacion_id, concepto, cantidad, precio_unitario, descuento, tipo)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (reparacion_id, concepto, cantidad, precio_unitario, descuento, tipo))

        # Guardar concepto en sugerencias (si no existe)
        cur.execute("""
        INSERT OR IGNORE INTO item_conceptos (nombre) VALUES (?)
        """, (concepto,))

        con.commit()
        con.close()
        return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))

    # GET: traer conceptos guardados para el formulario
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

    reparacion_id = item[1]
    cur.execute("SELECT * FROM reparaciones WHERE id=?", (reparacion_id,))
    reparacion = cur.fetchone()

    if request.method == "POST":
        concepto = request.form["concepto"].strip().upper()
        cantidad = float(request.form["cantidad"] or 0)
        precio_unitario = float(request.form["precio_unitario"] or 0)
        descuento = float(request.form.get("descuento") or 0)
        tipo = request.form.get("tipo") or "SERVICIO"

        cur.execute("""
        UPDATE reparacion_items
        SET concepto=?, cantidad=?, precio_unitario=?, descuento=?, tipo=?
        WHERE id=?
        """, (concepto, cantidad, precio_unitario, descuento, tipo, item_id))

        # también lo guardamos en sugerencias
        cur.execute("""
        INSERT OR IGNORE INTO item_conceptos (nombre) VALUES (?)
        """, (concepto,))

        con.commit()
        con.close()
        return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))

    # GET: traer conceptos guardados
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

    return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))


@app.route("/items/concepto/eliminar/<int:concepto_id>")
@login_required
def item_concepto_eliminar(concepto_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("DELETE FROM item_conceptos WHERE id=?", (concepto_id,))
    con.commit()
    con.close()
    # Volver a la pantalla anterior (formulario de ítem)
    return redirect(request.referrer or url_for("facturas_listado"))


# ----------------- IMÁGENES DE REPARACIÓN -----------------
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
        # ahora usamos una lista de archivos
        files = request.files.getlist("imagenes")
        descripcion = request.form.get("descripcion", "").strip()

        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                ext = filename.rsplit(".", 1)[1].lower()
                unique_name = f"rep_{reparacion_id}_{int(time.time())}_{filename}"
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                file.save(save_path)

                cur.execute("""
                    INSERT INTO reparacion_imagenes (reparacion_id, filename, descripcion)
                    VALUES (?, ?, ?)
                """, (reparacion_id, unique_name, descripcion))

        con.commit()
        con.close()
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
            except:
                pass

    cur.execute("DELETE FROM reparacion_imagenes WHERE id=?", (img_id,))
    con.commit()
    con.close()

    return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))


# ----------------- FACTURAS / PRESUPUESTOS -----------------
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

    vehiculo_id = reparacion[1]
    cur.execute("SELECT * FROM vehiculos WHERE id=?", (vehiculo_id,))
    vehiculo = cur.fetchone()
    cliente_id = vehiculo[1]
    cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cur.fetchone()

    cur.execute("SELECT * FROM reparacion_items WHERE reparacion_id=?", (reparacion_id,))
    items = cur.fetchall()

    # subtotal con descuentos por ítem
    base_total = 0
    for it in items:
        cantidad = it[3] or 0
        precio = it[4] or 0
        descuento = 0
        if len(it) > 5 and it[5] is not None:
            descuento = it[5]
        base_total += cantidad * precio * (1 - descuento / 100.0)

    # buscar / crear registro de factura-presupuesto
    cur.execute(
        "SELECT id, reparacion_id, fecha, total, descuento_global, es_presupuesto "
        "FROM facturas WHERE reparacion_id=?",
        (reparacion_id,)
    )
    row = cur.fetchone()

    if not row:
        hoy = date.today().isoformat()
        descuento_global = 0.0
        total_final = base_total
        # la creamos como PRESUPUESTO
        cur.execute("""
            INSERT INTO facturas (reparacion_id, fecha, total, descuento_global, es_presupuesto)
            VALUES (?, ?, ?, ?, 1)
        """, (reparacion_id, hoy, total_final, descuento_global))
        con.commit()
        factura_id = cur.lastrowid
        factura_fecha = hoy
        es_presupuesto = 1
    else:
        factura_id, _, factura_fecha, total_guardado, descuento_global, es_presupuesto = row
        if descuento_global is None:
            descuento_global = 0.0
        if es_presupuesto is None:
            es_presupuesto = 1
        total_final = base_total * (1 - descuento_global / 100.0)
        cur.execute(
            "UPDATE facturas SET total=?, descuento_global=? WHERE id=?",
            (total_final, descuento_global, factura_id)
        )
        con.commit()

    # teléfono formateado para WhatsApp
    tel_raw = cliente[3] or ""
    tel_digits = "".join(ch for ch in tel_raw if ch.isdigit())
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
        total=total_final,
        wa_phone=wa_phone,
        presupuesto_url=presupuesto_url,
        es_presupuesto=es_presupuesto
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

    # marcar como NO presupuesto → ya entra al balance
    cur.execute("UPDATE facturas SET es_presupuesto = 0 WHERE id=?", (factura_id,))
    # marcar reparación como facturada
    cur.execute("UPDATE reparaciones SET estado = 'Facturada' WHERE id=?", (reparacion_id,))

    con.commit()
    con.close()

    return redirect(url_for("reparacion_detalle", reparacion_id=reparacion_id))


@app.route("/facturas", methods=["GET"])
@login_required
def facturas_listado():
    # Usamos tu helper de conexión
    con = get_con()
    cur = con.cursor()

    # Filtros de fecha desde/hasta (opcionales)
    desde = request.args.get("desde") or ""
    hasta = request.args.get("hasta") or ""

    # ============================
    # FACTURAS (INGRESOS)
    # ============================
    sql_f = """
        SELECT 
            f.id,            -- 0 id factura
            f.fecha,         -- 1 fecha
            f.total,         -- 2 total
            c.nombre,        -- 3 nombre cliente
            c.apellido,      -- 4 apellido cliente
            v.patente,       -- 5 patente
            v.marca,         -- 6 marca
            v.modelo,        -- 7 modelo
            r.id,            -- 8 id reparacion
            f.es_presupuesto -- 9 flag presupuesto (0 = factura real)
        FROM facturas f
        JOIN reparaciones r ON r.id = f.reparacion_id
        JOIN vehiculos v ON v.id = r.vehiculo_id
        JOIN clientes c ON c.id = v.cliente_id
        WHERE f.es_presupuesto = 0
    """

    filtros_f = []
    params_f = []

    if desde:
        filtros_f.append("DATE(f.fecha) >= DATE(?)")
        params_f.append(desde)
    if hasta:
        filtros_f.append("DATE(f.fecha) <= DATE(?)")
        params_f.append(hasta)

    if filtros_f:
        sql_f += " AND " + " AND ".join(filtros_f)

    sql_f += " ORDER BY f.fecha DESC"

    cur.execute(sql_f, params_f)
    facturas = cur.fetchall()

    # ============================
    # GASTOS
    # ============================
    # Tu tabla gastos es: (id, fecha, categoria, descripcion, monto)
    sql_g = """
        SELECT 
            id,         -- 0
            fecha,      -- 1
            categoria,  -- 2
            descripcion,-- 3
            monto       -- 4
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

    sql_g += " ORDER BY fecha DESC"

    cur.execute(sql_g, params_g)
    gastos = cur.fetchall()

    # ============================
    # TOTALES
    # ============================
    total_ingresos = sum((f[2] or 0) for f in facturas)  # f[2] = total
    total_gastos = sum((g[4] or 0) for g in gastos)      # g[4] = monto
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


# ----------------- GASTOS -----------------
@app.route("/gastos")
@login_required
def gastos_listado():
    desde = request.args.get("desde", "").strip()
    hasta = request.args.get("hasta", "").strip()

    con = get_con()
    cur = con.cursor()

    sql = "SELECT id, fecha, categoria, descripcion, monto FROM gastos WHERE 1=1"
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

    total_gastos = sum(row[4] for row in gastos) if gastos else 0

    return render_template(
        "gastos.html",
        gastos=gastos,
        desde=desde,
        hasta=hasta,
        total_gastos=total_gastos
    )


@app.route("/gastos/nuevo", methods=["GET", "POST"])
@login_required
def gasto_nuevo():
    if request.method == "POST":
        fecha = request.form["fecha"]
        categoria = request.form["categoria"].strip()
        descripcion = request.form["descripcion"].strip()
        monto = float(request.form["monto"] or 0)

        con = get_con()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO gastos (fecha, categoria, descripcion, monto)
            VALUES (?, ?, ?, ?)
        """, (fecha, categoria, descripcion, monto))
        con.commit()
        con.close()

        return redirect(url_for("gastos_listado"))

    return render_template("gasto_form.html")


@app.route("/gastos/eliminar/<int:gasto_id>")
@login_required
def gasto_eliminar(gasto_id):
    con = get_con()
    cur = con.cursor()
    cur.execute("DELETE FROM gastos WHERE id=?", (gasto_id,))
    con.commit()
    con.close()
    return redirect(url_for("gastos_listado"))


# ----------------- CITAS -----------------
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
        fecha = request.form["fecha"]
        hora = request.form["hora"]
        cliente_nombre = request.form["cliente_nombre"].strip()
        telefono = request.form["telefono"].strip()
        descripcion = request.form["descripcion"].strip()

        con = get_con()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO citas (fecha, hora, cliente_nombre, telefono, descripcion)
            VALUES (?, ?, ?, ?, ?)
        """, (fecha, hora, cliente_nombre, telefono, descripcion))
        con.commit()
        con.close()

        return redirect(url_for("citas_listado"))

    return render_template("cita_form.html")


@app.route("/citas/editar/<int:cita_id>", methods=["GET", "POST"])
@login_required
def cita_editar(cita_id):
    con = get_con()
    cur = con.cursor()

    if request.method == "POST":
        fecha = request.form["fecha"]
        hora = request.form["hora"]
        cliente_nombre = request.form["cliente_nombre"].strip()
        telefono = request.form["telefono"].strip()
        descripcion = request.form["descripcion"].strip()

        cur.execute("""
            UPDATE citas
            SET fecha=?, hora=?, cliente_nombre=?, telefono=?, descripcion=?
            WHERE id=?
        """, (fecha, hora, cliente_nombre, telefono, descripcion, cita_id))
        con.commit()
        con.close()
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
    return redirect(url_for("citas_listado"))


# ----------------- MAIN -----------------
if __name__ == "__main__":
    if not os.path.exists("static"):
        os.mkdir("static")
    if not os.path.exists(os.path.join("static", "uploads")):
        os.mkdir(os.path.join("static", "uploads"))
    if not os.path.exists(BACKUP_FOLDER):
        os.mkdir(BACKUP_FOLDER)

    # Backup automático antes de cualquier cambio en la DB
    backup_db()

    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
