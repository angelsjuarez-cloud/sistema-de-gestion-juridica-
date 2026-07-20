# -*- coding: utf-8 -*-
"""
Sistema de Gestión Integral para Despachos Jurídicos
Aplicación principal Flask.

Ejecutar en modo desarrollo:  python app.py
Luego abrir:                  http://127.0.0.1:5000

Este archivo también está preparado para funcionar empaquetado como .exe
(PyInstaller): detecta automáticamente si corre "congelado" y ajusta las
rutas de plantillas/estáticos (de solo lectura, empaquetados dentro del
.exe) y las rutas de datos (base de datos y documentos, que deben quedar
en una carpeta escribible junto al .exe para que la información persista
entre ejecuciones).
"""
import os
import sys
import threading
import webbrowser
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (Flask, render_template, redirect, url_for, request,
                    flash, jsonify, abort, send_from_directory)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                          current_user)
from werkzeug.utils import secure_filename
from sqlalchemy import func, or_

from models import (db, Despacho, Usuario, Cliente, Expediente, Movimiento, Documento,
                     Audiencia, Cita, Tarea, TareaComentario, Pago,
                     Tutorial, TutorialArchivo, Auditoria, Alerta, EnlaceJuridico)

# ---------------------------------------------------------------------------
# Resolución de rutas: modo normal (python app.py) vs. .exe empaquetado
# ---------------------------------------------------------------------------
APP_CONGELADA = getattr(sys, "frozen", False)  # True cuando corre como .exe (PyInstaller)

if APP_CONGELADA:
    # Plantillas y estáticos van empaquetados dentro del .exe (solo lectura)
    RECURSOS_DIR = sys._MEIPASS
    # Los datos (BD, documentos) deben vivir junto al .exe, en una carpeta
    # escribible que persista entre ejecuciones y actualizaciones del .exe
    DATOS_DIR = os.path.dirname(sys.executable)
else:
    RECURSOS_DIR = os.path.abspath(os.path.dirname(__file__))
    DATOS_DIR = RECURSOS_DIR

TEMPLATE_DIR = os.path.join(RECURSOS_DIR, "templates")
STATIC_DIR = os.path.join(RECURSOS_DIR, "static")

BASE_DIR = DATOS_DIR
UPLOAD_FOLDER = os.path.join(DATOS_DIR, "uploads")
INSTANCE_FOLDER = os.path.join(DATOS_DIR, "instance")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(INSTANCE_FOLDER, exist_ok=True)

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.config["SECRET_KEY"] = "cambia-esta-clave-en-produccion-por-una-segura"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(INSTANCE_FOLDER, "despacho.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB por archivo

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Por favor inicia sesión para continuar."
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Usuario, int(user_id))


# ---------------------------------------------------------------------------
# Control de acceso por roles
# ---------------------------------------------------------------------------
def roles_requeridos(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.rol not in roles:
                flash("No tienes permisos para acceder a esta sección.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Aislamiento multi-despacho (multi-tenant)
# ---------------------------------------------------------------------------
def despacho_actual_id():
    """ID del despacho del usuario que tiene la sesión iniciada."""
    return current_user.despacho_id if current_user.is_authenticated else None


def verificar_pertenencia(objeto, despacho_id):
    """Aborta con 404 (no 403, para no revelar que el registro existe) si el
    objeto no pertenece al despacho del usuario en sesión."""
    if objeto is None or despacho_id is None or getattr(objeto, "despacho_id", despacho_id) != despacho_id:
        abort(404)
    return objeto


# ---------------------------------------------------------------------------
# Bitácora de auditoría
# ---------------------------------------------------------------------------
def registrar_auditoria(accion, modulo, registro_id=None, registro_desc="", descripcion=""):
    """Crea un registro en la bitácora de auditoría. No hace commit por sí mismo
    para poder agruparse con la operación principal, pero si no hay una transacción
    en curso, la confirma de inmediato."""
    try:
        usuario_id = current_user.id if current_user.is_authenticated else None
        usuario_nombre = current_user.nombre if current_user.is_authenticated else "Sistema"
        despacho_id = current_user.despacho_id if current_user.is_authenticated else None
    except Exception:
        usuario_id, usuario_nombre, despacho_id = None, "Sistema", None

    ip = request.headers.get("X-Forwarded-For", request.remote_addr) if request else None
    entrada = Auditoria(
        despacho_id=despacho_id,
        usuario_id=usuario_id,
        usuario_nombre=usuario_nombre,
        ip=ip,
        accion=accion,
        modulo=modulo,
        registro_id=registro_id,
        registro_desc=registro_desc,
        descripcion=descripcion,
    )
    db.session.add(entrada)
    db.session.commit()


# ---------------------------------------------------------------------------
# Motor de alertas inteligentes
# ---------------------------------------------------------------------------
def _upsert_alerta(despacho_id, clave, tipo, prioridad, mensaje, usuario_id=None, expediente_id=None,
                    tarea_id=None, fecha_referencia=None):
    existente = Alerta.query.filter_by(clave=clave, despacho_id=despacho_id).first()
    if existente:
        if not existente.revisada:
            existente.mensaje = mensaje
            existente.prioridad = prioridad
            existente.actualizada_en = datetime.utcnow()
        return
    db.session.add(Alerta(
        despacho_id=despacho_id, clave=clave, tipo=tipo, prioridad=prioridad, mensaje=mensaje,
        usuario_id=usuario_id, expediente_id=expediente_id, tarea_id=tarea_id,
        fecha_referencia=fecha_referencia,
    ))


def generar_alertas(despacho_id):
    """Analiza el estado de UN despacho específico y genera/actualiza sus alertas."""
    hoy = date.today()
    ahora = datetime.utcnow()

    # Tareas próximas a vencer (siguientes 3 días) y tareas vencidas
    tareas_abiertas = Tarea.query.filter(
        Tarea.despacho_id == despacho_id, Tarea.estado.notin_(["Completada", "Cancelada"])
    ).all()
    vencidas_por_usuario = {}
    for t in tareas_abiertas:
        responsables_txt = ", ".join(r.nombre for r in t.responsables) or "Sin asignar"
        if t.fecha_limite:
            if t.fecha_limite < hoy:
                _upsert_alerta(
                    despacho_id, clave=f"tarea_vencida_{t.id}", tipo="tarea_vencida", prioridad="Alta",
                    mensaje=f"La tarea «{t.titulo}» venció el {t.fecha_limite.strftime('%d/%m/%Y')} (responsable: {responsables_txt}).",
                    tarea_id=t.id, expediente_id=t.expediente_id, fecha_referencia=t.fecha_limite,
                )
                for r in t.responsables:
                    vencidas_por_usuario[r.id] = vencidas_por_usuario.get(r.id, 0) + 1
            elif t.fecha_limite <= hoy + timedelta(days=3):
                _upsert_alerta(
                    despacho_id, clave=f"tarea_por_vencer_{t.id}", tipo="tarea_por_vencer", prioridad="Media",
                    mensaje=f"La tarea «{t.titulo}» vence el {t.fecha_limite.strftime('%d/%m/%Y')} (responsable: {responsables_txt}).",
                    tarea_id=t.id, expediente_id=t.expediente_id, fecha_referencia=t.fecha_limite,
                )
        # Pendiente por mucho tiempo
        if t.estado == "Pendiente" and (ahora - t.creado_en).days >= 5:
            _upsert_alerta(
                despacho_id, clave=f"tarea_pendiente_larga_{t.id}", tipo="tarea_pendiente_larga", prioridad="Media",
                mensaje=f"La tarea «{t.titulo}» lleva {(ahora - t.creado_en).days} días en estado Pendiente (responsable: {responsables_txt}).",
                tarea_id=t.id, expediente_id=t.expediente_id,
            )
        # Sin actualizaciones recientes
        if t.dias_sin_actualizar >= 5:
            _upsert_alerta(
                despacho_id, clave=f"tarea_sin_actualizar_{t.id}", tipo="tarea_sin_actualizar", prioridad="Baja",
                mensaje=f"La tarea «{t.titulo}» no ha tenido actualizaciones en {t.dias_sin_actualizar} días (responsable: {responsables_txt}).",
                tarea_id=t.id, expediente_id=t.expediente_id,
            )

    for usuario_id, cantidad in vencidas_por_usuario.items():
        if cantidad >= 2:
            u = db.session.get(Usuario, usuario_id)
            if u:
                _upsert_alerta(
                    despacho_id, clave=f"usuario_vencidas_{usuario_id}", tipo="usuario_vencidas", prioridad="Alta",
                    mensaje=f"{u.nombre} tiene {cantidad} tarea(s) vencida(s) sin resolver.",
                    usuario_id=usuario_id,
                )

    # Expedientes activos sin movimientos recientes (15 días)
    activos = Expediente.query.filter(
        Expediente.despacho_id == despacho_id, Expediente.estado.notin_(["Concluido", "Archivado"])
    ).all()
    for e in activos:
        ultimo = e.movimientos.first()
        referencia = ultimo.fecha if ultimo else None
        base_fecha = referencia or datetime.combine(e.fecha_apertura, datetime.min.time())
        if (ahora - base_fecha).days >= 15:
            _upsert_alerta(
                despacho_id, clave=f"expediente_inactivo_{e.id}", tipo="expediente_inactivo", prioridad="Media",
                mensaje=f"El expediente {e.numero} ({e.titulo}) no registra movimientos desde hace {(ahora - base_fecha).days} días.",
                expediente_id=e.id,
            )

    # Pagos pendientes acumulados
    pendientes = Pago.query.filter_by(estado="Pendiente", despacho_id=despacho_id).all()
    if len(pendientes) >= 3:
        total = sum(p.monto for p in pendientes)
        _upsert_alerta(
            despacho_id, clave="pagos_pendientes_acumulados", tipo="pagos_pendientes", prioridad="Alta",
            mensaje=f"Hay {len(pendientes)} pagos pendientes de cobro por un total de ${total:,.2f}.",
        )

    # Audiencias y citas próximas (siguientes 2 días)
    limite = ahora + timedelta(days=2)
    for a in Audiencia.query.filter(Audiencia.despacho_id == despacho_id, Audiencia.estado == "Programada",
                                     Audiencia.fecha_hora >= ahora,
                                     Audiencia.fecha_hora <= limite).all():
        _upsert_alerta(
            despacho_id, clave=f"audiencia_proxima_{a.id}", tipo="audiencia_proxima", prioridad="Baja",
            mensaje=f"Audiencia «{a.tipo}» del expediente {a.expediente.numero} programada para el {a.fecha_hora.strftime('%d/%m/%Y %H:%M')}.",
            expediente_id=a.expediente_id, fecha_referencia=a.fecha_hora.date(),
        )
    for c in Cita.query.filter(Cita.despacho_id == despacho_id, Cita.estado == "Programada",
                                Cita.fecha_hora >= ahora, Cita.fecha_hora <= limite).all():
        _upsert_alerta(
            despacho_id, clave=f"cita_proxima_{c.id}", tipo="cita_proxima", prioridad="Baja",
            mensaje=f"Cita «{c.titulo}» programada para el {c.fecha_hora.strftime('%d/%m/%Y %H:%M')}.",
            fecha_referencia=c.fecha_hora.date(),
        )

    db.session.commit()


@app.context_processor
def inject_globals():
    tareas_pendientes = 0
    alertas_sin_revisar = 0
    if current_user.is_authenticated:
        if current_user.es_admin:
            tareas_pendientes = Tarea.query.filter(
                Tarea.despacho_id == current_user.despacho_id,
                Tarea.estado.notin_(["Completada", "Cancelada"])
            ).count()
            alertas_sin_revisar = Alerta.query.filter_by(revisada=False, despacho_id=current_user.despacho_id).count()
        else:
            tareas_pendientes = Tarea.query.filter(
                Tarea.despacho_id == current_user.despacho_id,
                Tarea.estado.notin_(["Completada", "Cancelada"]),
                Tarea.responsables.any(Usuario.id == current_user.id)
            ).count()
    return dict(hoy=date.today(), tareas_pendientes=tareas_pendientes, alertas_sin_revisar=alertas_sin_revisar)


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        usuario = Usuario.query.filter_by(email=email).first()
        if usuario and usuario.activo and usuario.despacho.activo and usuario.check_password(password):
            login_user(usuario)
            registrar_auditoria("Inicio de sesión", "Autenticación", registro_id=usuario.id,
                                 registro_desc=usuario.email, descripcion=f"{usuario.nombre} inició sesión.")
            flash(f"Bienvenido(a), {usuario.nombre}.", "success")
            siguiente = request.args.get("next")
            return redirect(siguiente or url_for("dashboard"))
        registrar_auditoria("Intento fallido", "Autenticación", registro_desc=email,
                             descripcion="Intento de inicio de sesión con credenciales incorrectas.")
        flash("Credenciales incorrectas, usuario inactivo o despacho suspendido.", "danger")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    registrar_auditoria("Cierre de sesión", "Autenticación", registro_id=current_user.id,
                         registro_desc=current_user.email, descripcion=f"{current_user.nombre} cerró sesión.")
    logout_user()
    flash("Sesión cerrada correctamente.", "info")
    return redirect(url_for("login"))


def _generar_slug(nombre):
    import re
    import unicodedata
    base = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower() or "despacho"
    slug = base
    contador = 1
    while Despacho.query.filter_by(slug=slug).first():
        contador += 1
        slug = f"{base}-{contador}"
    return slug


@app.route("/registro", methods=["GET", "POST"])
def registro_despacho():
    """Auto-registro de un nuevo despacho (tenant) con su primer usuario Administrador."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        nombre_despacho = request.form.get("nombre_despacho", "").strip()
        nombre_admin = request.form.get("nombre_admin", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirmar = request.form.get("confirmar", "")

        errores = []
        if not request.form.get("acepto_privacidad"):
            errores.append("Debes leer y aceptar el Aviso de Privacidad para continuar.")
        if not nombre_despacho or not nombre_admin or not email or not password:
            errores.append("Todos los campos son obligatorios.")
        if password != confirmar:
            errores.append("Las contraseñas no coinciden.")
        if password and len(password) < 6:
            errores.append("La contraseña debe tener al menos 6 caracteres.")
        if Usuario.query.filter_by(email=email).first():
            errores.append("Ya existe una cuenta registrada con ese correo.")

        if errores:
            for e in errores:
                flash(e, "danger")
            return render_template("registro.html", nombre_despacho=nombre_despacho,
                                    nombre_admin=nombre_admin, email=email)

        despacho = Despacho(
            nombre=nombre_despacho,
            slug=_generar_slug(nombre_despacho),
            plan="Piloto",
            estado_pago="activo",  # no se valida en ningún lado todavía, es solo referencia
            fecha_fin_prueba=date.today() + timedelta(days=60),
            acepto_aviso_privacidad=True,
            fecha_aceptacion_aviso=datetime.utcnow(),
    )

        admin = Usuario(despacho_id=despacho.id, nombre=nombre_admin, email=email, rol="Administrador")
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()

        _sembrar_datos_iniciales_despacho(despacho.id)

        registrar_auditoria_para(despacho.id, admin, "Creación", "Despacho", registro_id=despacho.id,
                                  registro_desc=despacho.nombre,
                                  descripcion=f"Se registró el despacho «{despacho.nombre}» con administrador {admin.email}.")

        login_user(admin)
        flash(f"¡Despacho «{despacho.nombre}» creado con éxito! Esta es tu cuenta de Administrador.", "success")
        return redirect(url_for("dashboard"))

    return render_template("registro.html", nombre_despacho="", nombre_admin="", email="")
@app.route("/privacidad")
def aviso_privacidad():
    """Página pública del Aviso de Privacidad (no requiere sesión iniciada)."""
    return render_template("aviso_privacidad.html", hoy=date.today())    

def registrar_auditoria_para(despacho_id, usuario, accion, modulo, registro_id=None, registro_desc="", descripcion=""):
    """Variante de registrar_auditoria() para eventos donde el usuario aún no tiene
    sesión iniciada (por ejemplo, el instante en que se crea su propio despacho)."""
    db.session.add(Auditoria(
        despacho_id=despacho_id, usuario_id=usuario.id, usuario_nombre=usuario.nombre,
        ip=request.headers.get("X-Forwarded-For", request.remote_addr) if request else None,
        accion=accion, modulo=modulo, registro_id=registro_id,
        registro_desc=registro_desc, descripcion=descripcion,
    ))
    db.session.commit()


# ---------------------------------------------------------------------------
# Panel principal / Dashboard
# ---------------------------------------------------------------------------
@app.route("/")
@login_required
def dashboard():
    d_id = despacho_actual_id()
    total_expedientes = Expediente.query.filter_by(despacho_id=d_id).count()
    expedientes_activos = Expediente.query.filter(
        Expediente.despacho_id == d_id, Expediente.estado.notin_(["Concluido", "Archivado"])
    ).count()
    expedientes_concluidos = Expediente.query.filter_by(despacho_id=d_id, estado="Concluido").count()
    total_clientes = Cliente.query.filter_by(despacho_id=d_id).count()

    ingresos_mes = None
    pagos_pendientes_total = None
    mis_tareas_pendientes = None
    if current_user.es_admin:
        ingresos_mes = db.session.query(func.coalesce(func.sum(Pago.monto), 0)).filter(
            Pago.despacho_id == d_id,
            Pago.estado == "Pagado",
            func.strftime("%Y-%m", Pago.fecha_pago) == date.today().strftime("%Y-%m")
        ).scalar()

        pagos_pendientes_total = db.session.query(func.coalesce(func.sum(Pago.monto), 0)).filter(
            Pago.despacho_id == d_id, Pago.estado == "Pendiente"
        ).scalar()
    else:
        mis_tareas_pendientes = Tarea.query.filter(
            Tarea.despacho_id == d_id,
            Tarea.estado.notin_(["Completada", "Cancelada"]),
            Tarea.responsables.any(Usuario.id == current_user.id)
        ).count()

    proximos_7 = date.today() + timedelta(days=7)
    proximas_audiencias = Audiencia.query.filter(
        Audiencia.despacho_id == d_id,
        Audiencia.fecha_hora >= datetime.now(),
        Audiencia.fecha_hora <= datetime.combine(proximos_7, datetime.max.time()),
        Audiencia.estado == "Programada"
    ).order_by(Audiencia.fecha_hora).limit(6).all()

    proximas_citas = Cita.query.filter(
        Cita.despacho_id == d_id,
        Cita.fecha_hora >= datetime.now(),
        Cita.estado == "Programada"
    ).order_by(Cita.fecha_hora).limit(6).all()

    if current_user.es_admin:
        tareas_urgentes = Tarea.query.filter(
            Tarea.despacho_id == d_id,
            Tarea.estado.notin_(["Completada", "Cancelada"]),
            Tarea.fecha_limite.isnot(None),
            Tarea.fecha_limite <= proximos_7
        ).order_by(Tarea.fecha_limite).limit(8).all()
    else:
        tareas_urgentes = Tarea.query.filter(
            Tarea.despacho_id == d_id,
            Tarea.estado.notin_(["Completada", "Cancelada"]),
            Tarea.fecha_limite.isnot(None),
            Tarea.fecha_limite <= proximos_7,
            Tarea.responsables.any(Usuario.id == current_user.id)
        ).order_by(Tarea.fecha_limite).limit(8).all()

    por_tipo = db.session.query(Expediente.tipo_caso, func.count(Expediente.id)).filter(
        Expediente.despacho_id == d_id
    ).group_by(Expediente.tipo_caso).all()
    por_estado = db.session.query(Expediente.estado, func.count(Expediente.id)).filter(
        Expediente.despacho_id == d_id
    ).group_by(Expediente.estado).all()

    ultimos_movimientos = Movimiento.query.join(Expediente).filter(
        Expediente.despacho_id == d_id
    ).order_by(Movimiento.fecha.desc()).limit(8).all()

    alertas_recientes = []
    if current_user.es_admin:
        generar_alertas(d_id)
        orden_prioridad = {"Alta": 0, "Media": 1, "Baja": 2}
        alertas_recientes = sorted(
            Alerta.query.filter_by(revisada=False, despacho_id=d_id).all(),
            key=lambda a: (orden_prioridad.get(a.prioridad, 3), -a.generada_en.timestamp())
        )[:8]

    return render_template(
        "dashboard.html",
        total_expedientes=total_expedientes,
        expedientes_activos=expedientes_activos,
        expedientes_concluidos=expedientes_concluidos,
        total_clientes=total_clientes,
        ingresos_mes=ingresos_mes,
        pagos_pendientes_total=pagos_pendientes_total,
        mis_tareas_pendientes=mis_tareas_pendientes,
        proximas_audiencias=proximas_audiencias,
        proximas_citas=proximas_citas,
        tareas_urgentes=tareas_urgentes,
        por_tipo=por_tipo,
        por_estado=por_estado,
        ultimos_movimientos=ultimos_movimientos,
        alertas_recientes=alertas_recientes,
    )


@app.route("/buscar")
@login_required
def buscar():
    q = request.args.get("q", "").strip()
    d_id = despacho_actual_id()
    expedientes, clientes = [], []
    if q:
        like = f"%{q}%"
        expedientes = Expediente.query.filter(
            Expediente.despacho_id == d_id,
            or_(Expediente.numero.ilike(like), Expediente.titulo.ilike(like),
                Expediente.contraparte.ilike(like))
        ).limit(25).all()
        clientes = Cliente.query.filter(
            Cliente.despacho_id == d_id,
            or_(Cliente.nombre.ilike(like), Cliente.identificacion.ilike(like))
        ).limit(25).all()
    return render_template("buscar.html", q=q, expedientes=expedientes, clientes=clientes)


# ---------------------------------------------------------------------------
# ALERTAS INTELIGENTES (solo Administrador)
# ---------------------------------------------------------------------------
@app.route("/alertas")
@login_required
@roles_requeridos("Administrador")
def alertas():
    d_id = despacho_actual_id()
    generar_alertas(d_id)
    filtro = request.args.get("estado", "pendientes")
    query = Alerta.query.filter_by(despacho_id=d_id)
    if filtro == "pendientes":
        query = query.filter_by(revisada=False)
    elif filtro == "revisadas":
        query = query.filter_by(revisada=True)
    orden_prioridad = {"Alta": 0, "Media": 1, "Baja": 2}
    lista = sorted(query.all(), key=lambda a: (orden_prioridad.get(a.prioridad, 3), -a.generada_en.timestamp()))
    return render_template("alertas.html", alertas=lista, filtro=filtro)


@app.route("/alertas/<int:id>/revisar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def revisar_alerta(id):
    a = verificar_pertenencia(Alerta.query.get_or_404(id), despacho_actual_id())
    a.revisada = True
    a.revisada_en = datetime.utcnow()
    db.session.commit()
    flash("Alerta marcada como revisada.", "success")
    return redirect(request.referrer or url_for("alertas"))


# ---------------------------------------------------------------------------
# Calendario (audiencias + citas + plazos de tareas)
# ---------------------------------------------------------------------------
@app.route("/calendario")
@login_required
def calendario():
    return render_template("calendario.html")


@app.route("/api/eventos")
@login_required
def api_eventos():
    d_id = despacho_actual_id()
    eventos = []
    for a in Audiencia.query.filter_by(despacho_id=d_id).all():
        eventos.append({
            "title": f"⚖ Audiencia: {a.tipo} ({a.expediente.numero})",
            "start": a.fecha_hora.isoformat(),
            "color": "#8a1f2b" if a.estado == "Programada" else "#9aa0a6",
            "url": url_for("ver_expediente", id=a.expediente_id)
        })
    for c in Cita.query.filter_by(despacho_id=d_id).all():
        eventos.append({
            "title": f"📅 Cita: {c.titulo}",
            "start": c.fecha_hora.isoformat(),
            "color": "#c9a24b",
        })
    if current_user.es_admin:
        tareas_visibles = Tarea.query.filter(Tarea.despacho_id == d_id, Tarea.fecha_limite.isnot(None)).all()
    else:
        tareas_visibles = Tarea.query.filter(
            Tarea.despacho_id == d_id,
            Tarea.fecha_limite.isnot(None),
            Tarea.responsables.any(Usuario.id == current_user.id)
        ).all()
    for t in tareas_visibles:
        eventos.append({
            "title": f"✅ Tarea: {t.titulo}",
            "start": t.fecha_limite.isoformat(),
            "color": "#2c6e49" if t.estado not in ("Completada", "Cancelada") else "#c8c8c8",
        })
    return jsonify(eventos)


# ---------------------------------------------------------------------------
# CLIENTES
# ---------------------------------------------------------------------------
@app.route("/clientes")
@login_required
def clientes():
    q = request.args.get("q", "")
    query = Cliente.query.filter_by(despacho_id=despacho_actual_id())
    if q:
        query = query.filter(Cliente.nombre.ilike(f"%{q}%"))
    lista = query.order_by(Cliente.nombre).all()
    return render_template("clientes.html", clientes=lista, q=q)


@app.route("/clientes/nuevo", methods=["GET", "POST"])
@login_required
def nuevo_cliente():
    if request.method == "POST":
        c = Cliente(
            despacho_id=despacho_actual_id(),
            nombre=request.form["nombre"],
            tipo=request.form.get("tipo"),
            identificacion=request.form.get("identificacion"),
            telefono=request.form.get("telefono"),
            email=request.form.get("email"),
            direccion=request.form.get("direccion"),
            notas=request.form.get("notas"),
        )
        db.session.add(c)
        db.session.commit()
        registrar_auditoria("Creación", "Clientes", registro_id=c.id, registro_desc=c.nombre,
                             descripcion=f"Se registró al cliente «{c.nombre}».")
        flash("Cliente registrado correctamente.", "success")
        return redirect(url_for("clientes"))
    return render_template("cliente_form.html", cliente=None)


@app.route("/clientes/<int:id>")
@login_required
def ver_cliente(id):
    c = verificar_pertenencia(Cliente.query.get_or_404(id), despacho_actual_id())
    return render_template("cliente_detalle.html", cliente=c)


@app.route("/clientes/<int:id>/editar", methods=["GET", "POST"])
@login_required
def editar_cliente(id):
    c = verificar_pertenencia(Cliente.query.get_or_404(id), despacho_actual_id())
    if request.method == "POST":
        c.nombre = request.form["nombre"]
        c.tipo = request.form.get("tipo")
        c.identificacion = request.form.get("identificacion")
        c.telefono = request.form.get("telefono")
        c.email = request.form.get("email")
        c.direccion = request.form.get("direccion")
        c.notas = request.form.get("notas")
        db.session.commit()
        registrar_auditoria("Edición", "Clientes", registro_id=c.id, registro_desc=c.nombre,
                             descripcion=f"Se actualizó la información del cliente «{c.nombre}».")
        flash("Cliente actualizado.", "success")
        return redirect(url_for("ver_cliente", id=c.id))
    return render_template("cliente_form.html", cliente=c)


@app.route("/clientes/<int:id>/eliminar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def eliminar_cliente(id):
    c = verificar_pertenencia(Cliente.query.get_or_404(id), despacho_actual_id())
    nombre = c.nombre
    db.session.delete(c)
    db.session.commit()
    registrar_auditoria("Eliminación", "Clientes", registro_id=id, registro_desc=nombre,
                         descripcion=f"Se eliminó al cliente «{nombre}» y sus expedientes asociados.")
    flash("Cliente eliminado.", "info")
    return redirect(url_for("clientes"))


# ---------------------------------------------------------------------------
# EXPEDIENTES
# ---------------------------------------------------------------------------
@app.route("/expedientes")
@login_required
def expedientes():
    estado = request.args.get("estado", "")
    tipo = request.args.get("tipo", "")
    q = request.args.get("q", "")
    d_id = despacho_actual_id()
    query = Expediente.query.filter_by(despacho_id=d_id)
    if estado:
        query = query.filter_by(estado=estado)
    if tipo:
        query = query.filter_by(tipo_caso=tipo)
    if q:
        query = query.filter(or_(Expediente.numero.ilike(f"%{q}%"), Expediente.titulo.ilike(f"%{q}%")))
    lista = query.order_by(Expediente.fecha_apertura.desc()).all()
    tipos = [r[0] for r in db.session.query(Expediente.tipo_caso).filter_by(despacho_id=d_id).distinct().all()]
    return render_template("expedientes.html", expedientes=lista, estado=estado, tipo=tipo, q=q, tipos=tipos)


@app.route("/expedientes/nuevo", methods=["GET", "POST"])
@login_required
def nuevo_expediente():
    d_id = despacho_actual_id()
    if request.method == "POST":
        numero = request.form["numero"].strip()
        if Expediente.query.filter_by(numero=numero, despacho_id=d_id).first():
            flash("Ya existe un expediente con ese número.", "danger")
            return redirect(url_for("nuevo_expediente"))
        cliente = verificar_pertenencia(Cliente.query.get_or_404(request.form["cliente_id"]), d_id)
        e = Expediente(
            despacho_id=d_id,
            numero=numero,
            titulo=request.form["titulo"],
            tipo_caso=request.form["tipo_caso"],
            estado=request.form.get("estado", "Abierto"),
            prioridad=request.form.get("prioridad", "Media"),
            juzgado=request.form.get("juzgado"),
            contraparte=request.form.get("contraparte"),
            descripcion=request.form.get("descripcion"),
            monto_estimado=float(request.form.get("monto_estimado") or 0),
            cliente_id=cliente.id,
            abogado_id=request.form.get("abogado_id") or None,
        )
        db.session.add(e)
        db.session.flush()
        db.session.add(Movimiento(expediente_id=e.id, descripcion="Apertura del expediente.",
                                   tipo="Apertura", autor=current_user.nombre))
        db.session.commit()
        registrar_auditoria("Creación", "Expedientes", registro_id=e.id, registro_desc=e.numero,
                             descripcion=f"Se abrió el expediente {e.numero} — {e.titulo}.")
        flash("Expediente creado correctamente.", "success")
        return redirect(url_for("ver_expediente", id=e.id))
    clientes_lista = Cliente.query.filter_by(despacho_id=d_id).order_by(Cliente.nombre).all()
    abogados = Usuario.query.filter(
        Usuario.despacho_id == d_id, Usuario.rol.in_(["Abogado", "Administrador"])
    ).order_by(Usuario.nombre).all()
    return render_template("expediente_form.html", expediente=None, clientes=clientes_lista, abogados=abogados)


@app.route("/expedientes/<int:id>")
@login_required
def ver_expediente(id):
    e = verificar_pertenencia(Expediente.query.get_or_404(id), despacho_actual_id())
    return render_template("expediente_detalle.html", e=e)


@app.route("/expedientes/<int:id>/editar", methods=["GET", "POST"])
@login_required
def editar_expediente(id):
    d_id = despacho_actual_id()
    e = verificar_pertenencia(Expediente.query.get_or_404(id), d_id)
    if request.method == "POST":
        estado_anterior = e.estado
        cliente = verificar_pertenencia(Cliente.query.get_or_404(request.form["cliente_id"]), d_id)
        e.titulo = request.form["titulo"]
        e.tipo_caso = request.form["tipo_caso"]
        e.estado = request.form.get("estado", e.estado)
        e.prioridad = request.form.get("prioridad", e.prioridad)
        e.juzgado = request.form.get("juzgado")
        e.contraparte = request.form.get("contraparte")
        e.descripcion = request.form.get("descripcion")
        e.monto_estimado = float(request.form.get("monto_estimado") or 0)
        e.cliente_id = cliente.id
        e.abogado_id = request.form.get("abogado_id") or None
        if e.estado == "Concluido" and estado_anterior != "Concluido":
            e.fecha_cierre = date.today()
        if estado_anterior != e.estado:
            db.session.add(Movimiento(expediente_id=e.id,
                                       descripcion=f"Cambio de estado: {estado_anterior} → {e.estado}",
                                       tipo="Cambio de estado", autor=current_user.nombre))
        db.session.commit()
        registrar_auditoria("Edición", "Expedientes", registro_id=e.id, registro_desc=e.numero,
                             descripcion=f"Se actualizó el expediente {e.numero}.")
        flash("Expediente actualizado.", "success")
        return redirect(url_for("ver_expediente", id=e.id))
    clientes_lista = Cliente.query.filter_by(despacho_id=d_id).order_by(Cliente.nombre).all()
    abogados = Usuario.query.filter(
        Usuario.despacho_id == d_id, Usuario.rol.in_(["Abogado", "Administrador"])
    ).order_by(Usuario.nombre).all()
    return render_template("expediente_form.html", expediente=e, clientes=clientes_lista, abogados=abogados)


@app.route("/expedientes/<int:id>/eliminar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def eliminar_expediente(id):
    e = verificar_pertenencia(Expediente.query.get_or_404(id), despacho_actual_id())
    numero = e.numero
    db.session.delete(e)
    db.session.commit()
    registrar_auditoria("Eliminación", "Expedientes", registro_id=id, registro_desc=numero,
                         descripcion=f"Se eliminó el expediente {numero} de forma permanente.")
    flash("Expediente eliminado.", "info")
    return redirect(url_for("expedientes"))


@app.route("/expedientes/<int:id>/movimiento", methods=["POST"])
@login_required
def agregar_movimiento(id):
    e = verificar_pertenencia(Expediente.query.get_or_404(id), despacho_actual_id())
    desc = request.form.get("descripcion", "").strip()
    if desc:
        db.session.add(Movimiento(expediente_id=e.id, descripcion=desc,
                                   tipo=request.form.get("tipo", "Observación"),
                                   autor=current_user.nombre))
        db.session.commit()
        registrar_auditoria("Creación", "Bitácora de expediente", registro_id=e.id, registro_desc=e.numero,
                             descripcion=f"Movimiento agregado en {e.numero}: {desc[:120]}")
        flash("Movimiento agregado al expediente.", "success")
    return redirect(url_for("ver_expediente", id=id))


@app.route("/expedientes/<int:id>/documentos", methods=["POST"])
@login_required
def subir_documento(id):
    e = verificar_pertenencia(Expediente.query.get_or_404(id), despacho_actual_id())
    archivo = request.files.get("archivo")
    if archivo and archivo.filename:
        filename = secure_filename(archivo.filename)
        carpeta = os.path.join(app.config["UPLOAD_FOLDER"], str(e.despacho_id), str(e.id))
        os.makedirs(carpeta, exist_ok=True)
        ruta_completa = os.path.join(carpeta, filename)
        archivo.save(ruta_completa)
        tam_kb = os.path.getsize(ruta_completa) // 1024
        doc = Documento(
            expediente_id=e.id,
            nombre_archivo=filename,
            ruta=os.path.join(str(e.despacho_id), str(e.id), filename),
            categoria=request.form.get("categoria", "General"),
            subido_por=current_user.nombre,
            tamano_kb=tam_kb,
        )
        db.session.add(doc)
        db.session.add(Movimiento(expediente_id=e.id, descripcion=f"Documento adjuntado: {filename}",
                                   tipo="Documento", autor=current_user.nombre))
        db.session.commit()
        registrar_auditoria("Subida de documento", "Documentos", registro_id=doc.id, registro_desc=filename,
                             descripcion=f"Se subió «{filename}» al expediente {e.numero}.")
        flash("Documento subido correctamente.", "success")
    else:
        flash("Selecciona un archivo válido.", "danger")
    return redirect(url_for("ver_expediente", id=id))


@app.route("/documentos/<int:doc_id>/descargar")
@login_required
def descargar_documento(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    verificar_pertenencia(doc.expediente, despacho_actual_id())
    registrar_auditoria("Descarga de documento", "Documentos", registro_id=doc.id, registro_desc=doc.nombre_archivo,
                         descripcion=f"Se descargó «{doc.nombre_archivo}» del expediente {doc.expediente.numero}.")
    return send_from_directory(app.config["UPLOAD_FOLDER"], doc.ruta, as_attachment=True)


@app.route("/documentos/<int:doc_id>/eliminar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def eliminar_documento(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    verificar_pertenencia(doc.expediente, despacho_actual_id())
    exp_id = doc.expediente_id
    nombre_doc = doc.nombre_archivo
    numero_exp = doc.expediente.numero
    try:
        os.remove(os.path.join(app.config["UPLOAD_FOLDER"], doc.ruta))
    except OSError:
        pass
    db.session.delete(doc)
    db.session.commit()
    registrar_auditoria("Eliminación de documento", "Documentos", registro_id=doc_id, registro_desc=nombre_doc,
                         descripcion=f"Se eliminó «{nombre_doc}» del expediente {numero_exp}.")
    flash("Documento eliminado.", "info")
    return redirect(url_for("ver_expediente", id=exp_id))


# ---------------------------------------------------------------------------
# AUDIENCIAS
# ---------------------------------------------------------------------------
@app.route("/audiencias")
@login_required
def audiencias():
    lista = Audiencia.query.filter_by(despacho_id=despacho_actual_id()).order_by(Audiencia.fecha_hora).all()
    return render_template("audiencias.html", audiencias=lista)


@app.route("/expedientes/<int:id>/audiencias/nueva", methods=["POST"])
@login_required
def nueva_audiencia(id):
    e = verificar_pertenencia(Expediente.query.get_or_404(id), despacho_actual_id())
    fecha_str = request.form["fecha_hora"]
    a = Audiencia(
        despacho_id=e.despacho_id,
        expediente_id=e.id,
        tipo=request.form["tipo"],
        fecha_hora=datetime.fromisoformat(fecha_str),
        lugar=request.form.get("lugar"),
    )
    db.session.add(a)
    db.session.add(Movimiento(expediente_id=e.id, descripcion=f"Audiencia programada: {a.tipo}",
                               tipo="Audiencia", autor=current_user.nombre))
    db.session.commit()
    registrar_auditoria("Creación", "Audiencias", registro_id=a.id, registro_desc=a.tipo,
                         descripcion=f"Se programó la audiencia «{a.tipo}» para el expediente {e.numero}.")
    flash("Audiencia programada.", "success")
    return redirect(url_for("ver_expediente", id=id))


@app.route("/audiencias/<int:id>/actualizar", methods=["POST"])
@login_required
def actualizar_audiencia(id):
    a = verificar_pertenencia(Audiencia.query.get_or_404(id), despacho_actual_id())
    a.estado = request.form.get("estado", a.estado)
    a.resultado = request.form.get("resultado", a.resultado)
    db.session.commit()
    registrar_auditoria("Edición", "Audiencias", registro_id=a.id, registro_desc=a.tipo,
                         descripcion=f"Se actualizó la audiencia «{a.tipo}» a estado {a.estado}.")
    flash("Audiencia actualizada.", "success")
    return redirect(url_for("ver_expediente", id=a.expediente_id))


@app.route("/audiencias/<int:id>/eliminar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def eliminar_audiencia(id):
    a = verificar_pertenencia(Audiencia.query.get_or_404(id), despacho_actual_id())
    exp_id = a.expediente_id
    tipo = a.tipo
    db.session.delete(a)
    db.session.commit()
    registrar_auditoria("Eliminación", "Audiencias", registro_id=id, registro_desc=tipo,
                         descripcion=f"Se eliminó la audiencia «{tipo}» del expediente.")
    flash("Audiencia eliminada.", "info")
    return redirect(url_for("ver_expediente", id=exp_id))


# ---------------------------------------------------------------------------
# CITAS
# ---------------------------------------------------------------------------
@app.route("/citas")
@login_required
def citas():
    d_id = despacho_actual_id()
    lista = Cita.query.filter_by(despacho_id=d_id).order_by(Cita.fecha_hora).all()
    clientes_lista = Cliente.query.filter_by(despacho_id=d_id).order_by(Cliente.nombre).all()
    usuarios = Usuario.query.filter_by(despacho_id=d_id).order_by(Usuario.nombre).all()
    return render_template("citas.html", citas=lista, clientes=clientes_lista, usuarios=usuarios)


@app.route("/citas/nueva", methods=["POST"])
@login_required
def nueva_cita():
    d_id = despacho_actual_id()
    cliente_id = request.form.get("cliente_id") or None
    usuario_id = request.form.get("usuario_id") or None
    if cliente_id:
        verificar_pertenencia(Cliente.query.get_or_404(cliente_id), d_id)
    if usuario_id:
        verificar_pertenencia(Usuario.query.get_or_404(usuario_id), d_id)
    c = Cita(
        despacho_id=d_id,
        titulo=request.form["titulo"],
        fecha_hora=datetime.fromisoformat(request.form["fecha_hora"]),
        lugar=request.form.get("lugar"),
        cliente_id=cliente_id,
        usuario_id=usuario_id,
        notas=request.form.get("notas"),
    )
    db.session.add(c)
    db.session.commit()
    registrar_auditoria("Creación", "Citas", registro_id=c.id, registro_desc=c.titulo,
                         descripcion=f"Se agendó la cita «{c.titulo}».")
    flash("Cita registrada.", "success")
    return redirect(url_for("citas"))


@app.route("/citas/<int:id>/estado", methods=["POST"])
@login_required
def actualizar_cita(id):
    c = verificar_pertenencia(Cita.query.get_or_404(id), despacho_actual_id())
    c.estado = request.form.get("estado", c.estado)
    db.session.commit()
    registrar_auditoria("Edición", "Citas", registro_id=c.id, registro_desc=c.titulo,
                         descripcion=f"Se actualizó la cita «{c.titulo}» a estado {c.estado}.")
    return redirect(url_for("citas"))


@app.route("/citas/<int:id>/eliminar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def eliminar_cita(id):
    c = verificar_pertenencia(Cita.query.get_or_404(id), despacho_actual_id())
    titulo = c.titulo
    db.session.delete(c)
    db.session.commit()
    registrar_auditoria("Eliminación", "Citas", registro_id=id, registro_desc=titulo,
                         descripcion=f"Se eliminó la cita «{titulo}».")
    flash("Cita eliminada.", "info")
    return redirect(url_for("citas"))


# ---------------------------------------------------------------------------
# TAREAS
# ---------------------------------------------------------------------------
ESTADOS_TAREA = ["Pendiente", "En proceso", "En revisión", "Completada", "Cancelada"]


@app.route("/tareas")
@login_required
def tareas():
    d_id = despacho_actual_id()
    if current_user.es_admin:
        query = Tarea.query.filter_by(despacho_id=d_id)
    else:
        query = Tarea.query.filter(Tarea.despacho_id == d_id, Tarea.responsables.any(Usuario.id == current_user.id))

    estado_filtro = request.args.get("estado", "")
    if estado_filtro:
        query = query.filter_by(estado=estado_filtro)

    lista = query.order_by(Tarea.fecha_limite.is_(None), Tarea.fecha_limite).all()
    usuarios = Usuario.query.filter_by(activo=True, despacho_id=d_id).order_by(Usuario.nombre).all()
    expedientes_lista = Expediente.query.filter_by(despacho_id=d_id).order_by(Expediente.numero).all()
    return render_template("tareas.html", tareas=lista, usuarios=usuarios, expedientes=expedientes_lista,
                            estados=ESTADOS_TAREA, estado_filtro=estado_filtro)


@app.route("/tareas/nueva", methods=["POST"])
@login_required
def nueva_tarea():
    d_id = despacho_actual_id()
    fecha_limite = request.form.get("fecha_limite") or None
    responsables_ids = request.form.getlist("responsables")
    expediente_id = request.form.get("expediente_id") or None
    if expediente_id:
        verificar_pertenencia(Expediente.query.get_or_404(expediente_id), d_id)
    t = Tarea(
        despacho_id=d_id,
        titulo=request.form["titulo"],
        descripcion=request.form.get("descripcion"),
        fecha_limite=date.fromisoformat(fecha_limite) if fecha_limite else None,
        prioridad=request.form.get("prioridad", "Media"),
        expediente_id=expediente_id,
        creado_por_id=current_user.id,
    )
    if responsables_ids:
        # Solo se permiten responsables que pertenezcan al mismo despacho
        t.responsables = Usuario.query.filter(Usuario.id.in_(responsables_ids), Usuario.despacho_id == d_id).all()
        if t.responsables:
            t.usuario_id = t.responsables[0].id  # compatibilidad con vistas antiguas
    db.session.add(t)
    db.session.commit()
    nombres = ", ".join(u.nombre for u in t.responsables) or "sin asignar"
    registrar_auditoria("Creación", "Tareas", registro_id=t.id, registro_desc=t.titulo,
                         descripcion=f"Se creó la tarea «{t.titulo}» asignada a: {nombres}.")
    flash("Tarea creada.", "success")
    return redirect(url_for("tareas"))


@app.route("/tareas/<int:id>")
@login_required
def ver_tarea(id):
    t = verificar_pertenencia(Tarea.query.get_or_404(id), despacho_actual_id())
    if not t.visible_para(current_user):
        flash("No tienes acceso a esta tarea.", "danger")
        return redirect(url_for("tareas"))
    return render_template("tarea_detalle.html", t=t, estados=ESTADOS_TAREA)


@app.route("/tareas/<int:id>/estado", methods=["POST"])
@login_required
def actualizar_tarea(id):
    t = verificar_pertenencia(Tarea.query.get_or_404(id), despacho_actual_id())
    if not t.visible_para(current_user):
        flash("No tienes acceso a esta tarea.", "danger")
        return redirect(url_for("tareas"))
    estado_anterior = t.estado
    t.estado = request.form.get("estado", t.estado)
    t.actualizado_en = datetime.utcnow()
    db.session.commit()
    registrar_auditoria("Edición", "Tareas", registro_id=t.id, registro_desc=t.titulo,
                         descripcion=f"Tarea «{t.titulo}»: {estado_anterior} → {t.estado} (por {current_user.nombre}).")
    flash("Estado de la tarea actualizado.", "success")
    return redirect(request.referrer or url_for("tareas"))


@app.route("/tareas/<int:id>/comentario", methods=["POST"])
@login_required
def agregar_comentario_tarea(id):
    t = verificar_pertenencia(Tarea.query.get_or_404(id), despacho_actual_id())
    if not t.visible_para(current_user):
        flash("No tienes acceso a esta tarea.", "danger")
        return redirect(url_for("tareas"))
    texto = request.form.get("texto", "").strip()
    if texto:
        db.session.add(TareaComentario(tarea_id=t.id, usuario_id=current_user.id, texto=texto))
        t.actualizado_en = datetime.utcnow()
        db.session.commit()
        registrar_auditoria("Comentario", "Tareas", registro_id=t.id, registro_desc=t.titulo,
                             descripcion=f"{current_user.nombre} comentó en «{t.titulo}»: {texto[:120]}")
        flash("Comentario agregado.", "success")
    return redirect(url_for("ver_tarea", id=id))


@app.route("/tareas/<int:id>/editar", methods=["GET", "POST"])
@login_required
def editar_tarea(id):
    d_id = despacho_actual_id()
    t = verificar_pertenencia(Tarea.query.get_or_404(id), d_id)
    if not t.visible_para(current_user):
        flash("No tienes acceso a esta tarea.", "danger")
        return redirect(url_for("tareas"))
    if request.method == "POST":
        t.titulo = request.form["titulo"]
        t.descripcion = request.form.get("descripcion")
        fecha_limite = request.form.get("fecha_limite") or None
        t.fecha_limite = date.fromisoformat(fecha_limite) if fecha_limite else None
        t.prioridad = request.form.get("prioridad", t.prioridad)
        expediente_id = request.form.get("expediente_id") or None
        if expediente_id:
            verificar_pertenencia(Expediente.query.get_or_404(expediente_id), d_id)
        t.expediente_id = expediente_id
        responsables_ids = request.form.getlist("responsables")
        if responsables_ids:
            t.responsables = Usuario.query.filter(Usuario.id.in_(responsables_ids), Usuario.despacho_id == d_id).all()
            if t.responsables:
                t.usuario_id = t.responsables[0].id
        t.actualizado_en = datetime.utcnow()
        db.session.commit()
        registrar_auditoria("Edición", "Tareas", registro_id=t.id, registro_desc=t.titulo,
                             descripcion=f"Se editó la tarea «{t.titulo}».")
        flash("Tarea actualizada.", "success")
        return redirect(url_for("ver_tarea", id=t.id))
    usuarios = Usuario.query.filter_by(activo=True, despacho_id=d_id).order_by(Usuario.nombre).all()
    expedientes_lista = Expediente.query.filter_by(despacho_id=d_id).order_by(Expediente.numero).all()
    return render_template("tarea_form.html", t=t, usuarios=usuarios, expedientes=expedientes_lista)


@app.route("/tareas/<int:id>/eliminar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def eliminar_tarea(id):
    t = verificar_pertenencia(Tarea.query.get_or_404(id), despacho_actual_id())
    titulo = t.titulo
    db.session.delete(t)
    db.session.commit()
    registrar_auditoria("Eliminación", "Tareas", registro_id=id, registro_desc=titulo,
                         descripcion=f"Se eliminó la tarea «{titulo}».")
    flash("Tarea eliminada.", "info")
    return redirect(url_for("tareas"))


# ---------------------------------------------------------------------------
# PAGOS
# ---------------------------------------------------------------------------
@app.route("/pagos")
@login_required
@roles_requeridos("Administrador")
def pagos():
    d_id = despacho_actual_id()
    estado = request.args.get("estado", "")
    query = Pago.query.filter_by(despacho_id=d_id)
    if estado:
        query = query.filter_by(estado=estado)
    lista = query.order_by(Pago.fecha_emision.desc()).all()
    expedientes_lista = Expediente.query.filter_by(despacho_id=d_id).order_by(Expediente.numero).all()
    return render_template("pagos.html", pagos=lista, estado=estado, expedientes=expedientes_lista)


@app.route("/pagos/nuevo", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def nuevo_pago():
    d_id = despacho_actual_id()
    e = verificar_pertenencia(Expediente.query.get_or_404(request.form["expediente_id"]), d_id)
    p = Pago(
        despacho_id=d_id,
        expediente_id=e.id,
        concepto=request.form["concepto"],
        monto=float(request.form["monto"]),
        estado=request.form.get("estado", "Pendiente"),
        metodo=request.form.get("metodo"),
    )
    if p.estado == "Pagado":
        p.fecha_pago = date.today()
    db.session.add(p)
    db.session.commit()
    registrar_auditoria("Creación", "Pagos", registro_id=p.id, registro_desc=p.concepto,
                         descripcion=f"Se registró el pago «{p.concepto}» por ${p.monto:,.2f} (expediente {p.expediente.numero}).")
    flash("Pago registrado.", "success")
    return redirect(url_for("pagos"))


@app.route("/pagos/<int:id>/marcar_pagado", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def marcar_pagado(id):
    p = verificar_pertenencia(Pago.query.get_or_404(id), despacho_actual_id())
    p.estado = "Pagado"
    p.fecha_pago = date.today()
    p.metodo = request.form.get("metodo", p.metodo)
    db.session.commit()
    registrar_auditoria("Edición", "Pagos", registro_id=p.id, registro_desc=p.concepto,
                         descripcion=f"Se marcó como cobrado el pago «{p.concepto}» (${p.monto:,.2f}).")
    flash("Pago marcado como cobrado.", "success")
    return redirect(url_for("pagos"))


@app.route("/pagos/<int:id>/editar", methods=["GET", "POST"])
@login_required
@roles_requeridos("Administrador")
def editar_pago(id):
    d_id = despacho_actual_id()
    p = verificar_pertenencia(Pago.query.get_or_404(id), d_id)
    if request.method == "POST":
        e = verificar_pertenencia(Expediente.query.get_or_404(request.form["expediente_id"]), d_id)
        p.expediente_id = e.id
        p.concepto = request.form["concepto"]
        p.monto = float(request.form["monto"])
        p.estado = request.form.get("estado", p.estado)
        p.metodo = request.form.get("metodo")
        fecha_pago = request.form.get("fecha_pago") or None
        p.fecha_pago = date.fromisoformat(fecha_pago) if fecha_pago else (date.today() if p.estado == "Pagado" and not p.fecha_pago else p.fecha_pago)
        db.session.commit()
        registrar_auditoria("Edición", "Pagos", registro_id=p.id, registro_desc=p.concepto,
                             descripcion=f"Se editó el pago «{p.concepto}» (expediente {p.expediente.numero}).")
        flash("Pago actualizado.", "success")
        return redirect(url_for("pagos"))
    expedientes_lista = Expediente.query.filter_by(despacho_id=d_id).order_by(Expediente.numero).all()
    return render_template("pago_form.html", p=p, expedientes=expedientes_lista)


@app.route("/pagos/<int:id>/eliminar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def eliminar_pago(id):
    p = verificar_pertenencia(Pago.query.get_or_404(id), despacho_actual_id())
    concepto = p.concepto
    monto = p.monto
    db.session.delete(p)
    db.session.commit()
    registrar_auditoria("Eliminación", "Pagos", registro_id=id, registro_desc=concepto,
                         descripcion=f"Se eliminó el pago «{concepto}» (${monto:,.2f}).")
    flash("Pago eliminado.", "info")
    return redirect(url_for("pagos"))


@app.route("/pagos/eliminar_multiples", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def eliminar_pagos_multiples():
    d_id = despacho_actual_id()
    ids = request.form.getlist("pago_ids")
    if not ids:
        flash("No seleccionaste ningún pago.", "warning")
        return redirect(url_for("pagos"))
    pagos_sel = Pago.query.filter(Pago.id.in_(ids), Pago.despacho_id == d_id).all()
    cantidad = len(pagos_sel)
    for p in pagos_sel:
        db.session.delete(p)
    db.session.commit()
    registrar_auditoria("Eliminación masiva", "Pagos", registro_desc=f"{cantidad} pago(s)",
                         descripcion=f"{current_user.nombre} eliminó {cantidad} pago(s) seleccionados manualmente.")
    flash(f"Se eliminaron {cantidad} pago(s).", "info")
    return redirect(url_for("pagos"))


@app.route("/pagos/vaciar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def vaciar_pagos():
    d_id = despacho_actual_id()
    cantidad = Pago.query.filter_by(despacho_id=d_id).count()
    Pago.query.filter_by(despacho_id=d_id).delete()
    db.session.commit()
    registrar_auditoria("Reinicio de módulo", "Pagos", registro_desc=f"{cantidad} pago(s)",
                         descripcion=f"{current_user.nombre} vació por completo el historial de pagos ({cantidad} registros eliminados).")
    flash("El historial de pagos se reinició por completo.", "info")
    return redirect(url_for("pagos"))


# ---------------------------------------------------------------------------
# REPORTES
# ---------------------------------------------------------------------------
@app.route("/reportes")
@login_required
@roles_requeridos("Administrador")
def reportes():
    d_id = despacho_actual_id()
    activos = Expediente.query.filter(
        Expediente.despacho_id == d_id, Expediente.estado.notin_(["Concluido", "Archivado"])
    ).all()
    concluidos = Expediente.query.filter_by(despacho_id=d_id, estado="Concluido").all()

    ingresos_por_mes = db.session.query(
        func.strftime("%Y-%m", Pago.fecha_pago), func.sum(Pago.monto)
    ).filter(Pago.despacho_id == d_id, Pago.estado == "Pagado").group_by(
        func.strftime("%Y-%m", Pago.fecha_pago)).order_by(func.strftime("%Y-%m", Pago.fecha_pago)).all()

    pagos_pendientes = Pago.query.filter_by(estado="Pendiente", despacho_id=d_id).order_by(Pago.fecha_emision.desc()).all()

    actividad_abogados = db.session.query(
        Usuario.nombre, func.count(Expediente.id)
    ).join(Expediente, Expediente.abogado_id == Usuario.id).filter(
        Expediente.despacho_id == d_id
    ).group_by(Usuario.nombre).all()

    total_ingresos = sum(m for _, m in ingresos_por_mes)
    total_pendiente = sum(p.monto for p in pagos_pendientes)

    return render_template(
        "reportes.html",
        activos=activos,
        concluidos=concluidos,
        ingresos_por_mes=ingresos_por_mes,
        pagos_pendientes=pagos_pendientes,
        actividad_abogados=actividad_abogados,
        total_ingresos=total_ingresos,
        total_pendiente=total_pendiente,
    )


# ---------------------------------------------------------------------------
# TUTORIALES Y CAPACITACIÓN
# ---------------------------------------------------------------------------
@app.route("/tutoriales")
@login_required
def tutoriales():
    categoria = request.args.get("categoria", "")
    q = request.args.get("q", "")
    query = Tutorial.query.filter_by(despacho_id=despacho_actual_id())
    if categoria:
        query = query.filter_by(categoria=categoria)
    if q:
        query = query.filter(Tutorial.titulo.ilike(f"%{q}%"))
    lista = query.order_by(Tutorial.destacado.desc(), Tutorial.actualizado_en.desc()).all()
    return render_template("tutoriales.html", tutoriales=lista, categoria=categoria, q=q,
                            categorias=Tutorial.CATEGORIAS)


@app.route("/tutoriales/nuevo", methods=["GET", "POST"])
@login_required
@roles_requeridos("Administrador")
def nuevo_tutorial():
    if request.method == "POST":
        t = Tutorial(
            despacho_id=despacho_actual_id(),
            titulo=request.form["titulo"],
            categoria=request.form.get("categoria", "Uso del sistema"),
            contenido=request.form.get("contenido", ""),
            video_url=request.form.get("video_url") or None,
            destacado=bool(request.form.get("destacado")),
            obligatorio=bool(request.form.get("obligatorio")),
            autor_id=current_user.id,
        )
        db.session.add(t)
        db.session.flush()
        _guardar_archivos_tutorial(t)
        db.session.commit()
        registrar_auditoria("Creación", "Tutoriales", registro_id=t.id, registro_desc=t.titulo,
                             descripcion=f"Se publicó el tutorial «{t.titulo}» ({t.categoria}).")
        flash("Tutorial publicado correctamente.", "success")
        return redirect(url_for("ver_tutorial", id=t.id))
    return render_template("tutorial_form.html", t=None, categorias=Tutorial.CATEGORIAS)


def _guardar_archivos_tutorial(t):
    archivos = request.files.getlist("archivos")
    for archivo in archivos:
        if archivo and archivo.filename:
            filename = secure_filename(archivo.filename)
            carpeta = os.path.join(app.config["UPLOAD_FOLDER"], str(t.despacho_id), "tutoriales", str(t.id))
            os.makedirs(carpeta, exist_ok=True)
            archivo.save(os.path.join(carpeta, filename))
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            tipo = "imagen" if ext in ("png", "jpg", "jpeg", "gif", "webp") else ("pdf" if ext == "pdf" else "documento")
            db.session.add(TutorialArchivo(
                tutorial_id=t.id, tipo=tipo, nombre_archivo=filename,
                ruta=os.path.join(str(t.despacho_id), "tutoriales", str(t.id), filename),
            ))


@app.route("/tutoriales/<int:id>")
@login_required
def ver_tutorial(id):
    t = verificar_pertenencia(Tutorial.query.get_or_404(id), despacho_actual_id())
    return render_template("tutorial_detalle.html", t=t)


@app.route("/tutoriales/<int:id>/editar", methods=["GET", "POST"])
@login_required
@roles_requeridos("Administrador")
def editar_tutorial(id):
    t = verificar_pertenencia(Tutorial.query.get_or_404(id), despacho_actual_id())
    if request.method == "POST":
        t.titulo = request.form["titulo"]
        t.categoria = request.form.get("categoria", t.categoria)
        t.contenido = request.form.get("contenido", "")
        t.video_url = request.form.get("video_url") or None
        t.destacado = bool(request.form.get("destacado"))
        t.obligatorio = bool(request.form.get("obligatorio"))
        t.actualizado_en = datetime.utcnow()
        _guardar_archivos_tutorial(t)
        db.session.commit()
        registrar_auditoria("Edición", "Tutoriales", registro_id=t.id, registro_desc=t.titulo,
                             descripcion=f"Se actualizó el tutorial «{t.titulo}».")
        flash("Tutorial actualizado.", "success")
        return redirect(url_for("ver_tutorial", id=t.id))
    return render_template("tutorial_form.html", t=t, categorias=Tutorial.CATEGORIAS)


@app.route("/tutoriales/<int:id>/eliminar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def eliminar_tutorial(id):
    t = verificar_pertenencia(Tutorial.query.get_or_404(id), despacho_actual_id())
    titulo = t.titulo
    try:
        carpeta = os.path.join(app.config["UPLOAD_FOLDER"], str(t.despacho_id), "tutoriales", str(t.id))
        if os.path.isdir(carpeta):
            import shutil
            shutil.rmtree(carpeta)
    except OSError:
        pass
    db.session.delete(t)
    db.session.commit()
    registrar_auditoria("Eliminación", "Tutoriales", registro_id=id, registro_desc=titulo,
                         descripcion=f"Se eliminó el tutorial «{titulo}».")
    flash("Tutorial eliminado.", "info")
    return redirect(url_for("tutoriales"))


@app.route("/tutoriales/archivos/<int:archivo_id>/descargar")
@login_required
def descargar_archivo_tutorial(archivo_id):
    archivo = TutorialArchivo.query.get_or_404(archivo_id)
    verificar_pertenencia(archivo.tutorial, despacho_actual_id())
    return send_from_directory(app.config["UPLOAD_FOLDER"], archivo.ruta, as_attachment=True)


@app.route("/tutoriales/archivos/<int:archivo_id>/eliminar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def eliminar_archivo_tutorial(archivo_id):
    archivo = TutorialArchivo.query.get_or_404(archivo_id)
    verificar_pertenencia(archivo.tutorial, despacho_actual_id())
    tutorial_id = archivo.tutorial_id
    try:
        os.remove(os.path.join(app.config["UPLOAD_FOLDER"], archivo.ruta))
    except OSError:
        pass
    db.session.delete(archivo)
    db.session.commit()
    flash("Archivo eliminado del tutorial.", "info")
    return redirect(url_for("editar_tutorial", id=tutorial_id))


# ---------------------------------------------------------------------------
# ENLACES JURÍDICOS OFICIALES
# ---------------------------------------------------------------------------
@app.route("/enlaces")
@login_required
def enlaces():
    q = request.args.get("q", "")
    categoria = request.args.get("categoria", "")
    solo_activos = not current_user.es_admin  # abogados/asistentes solo ven enlaces activos
    query = EnlaceJuridico.query.filter_by(despacho_id=despacho_actual_id())
    if solo_activos:
        query = query.filter_by(activo=True)
    if categoria:
        query = query.filter_by(categoria=categoria)
    if q:
        query = query.filter(or_(EnlaceJuridico.nombre.ilike(f"%{q}%"),
                                  EnlaceJuridico.institucion.ilike(f"%{q}%")))
    lista = query.order_by(EnlaceJuridico.favorito.desc(), EnlaceJuridico.categoria, EnlaceJuridico.nombre).all()
    categorias = EnlaceJuridico.CATEGORIAS
    return render_template("enlaces.html", enlaces=lista, q=q, categoria=categoria, categorias=categorias)


@app.route("/enlaces/nuevo", methods=["GET", "POST"])
@login_required
@roles_requeridos("Administrador")
def nuevo_enlace():
    if request.method == "POST":
        e = EnlaceJuridico(
            despacho_id=despacho_actual_id(),
            nombre=request.form["nombre"],
            institucion=request.form.get("institucion"),
            descripcion=request.form.get("descripcion"),
            url=request.form["url"],
            categoria=request.form.get("categoria", "Otros servicios gubernamentales"),
            icono=request.form.get("icono") or "🔗",
            favorito=bool(request.form.get("favorito")),
            obligatorio=bool(request.form.get("obligatorio")),
        )
        db.session.add(e)
        db.session.commit()
        registrar_auditoria("Creación", "Enlaces Jurídicos", registro_id=e.id, registro_desc=e.nombre,
                             descripcion=f"Se agregó el enlace «{e.nombre}» ({e.categoria}).")
        flash("Enlace agregado correctamente.", "success")
        return redirect(url_for("enlaces"))
    return render_template("enlace_form.html", e=None, categorias=EnlaceJuridico.CATEGORIAS)


@app.route("/enlaces/<int:id>/editar", methods=["GET", "POST"])
@login_required
@roles_requeridos("Administrador")
def editar_enlace(id):
    e = verificar_pertenencia(EnlaceJuridico.query.get_or_404(id), despacho_actual_id())
    if request.method == "POST":
        e.nombre = request.form["nombre"]
        e.institucion = request.form.get("institucion")
        e.descripcion = request.form.get("descripcion")
        e.url = request.form["url"]
        e.categoria = request.form.get("categoria", e.categoria)
        e.icono = request.form.get("icono") or "🔗"
        e.favorito = bool(request.form.get("favorito"))
        e.obligatorio = bool(request.form.get("obligatorio"))
        e.actualizado_en = datetime.utcnow()
        db.session.commit()
        registrar_auditoria("Edición", "Enlaces Jurídicos", registro_id=e.id, registro_desc=e.nombre,
                             descripcion=f"Se actualizó el enlace «{e.nombre}».")
        flash("Enlace actualizado.", "success")
        return redirect(url_for("enlaces"))
    return render_template("enlace_form.html", e=e, categorias=EnlaceJuridico.CATEGORIAS)


@app.route("/enlaces/<int:id>/estado", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def cambiar_estado_enlace(id):
    e = verificar_pertenencia(EnlaceJuridico.query.get_or_404(id), despacho_actual_id())
    e.activo = not e.activo
    db.session.commit()
    registrar_auditoria("Edición", "Enlaces Jurídicos", registro_id=e.id, registro_desc=e.nombre,
                         descripcion=f"Se {'activó' if e.activo else 'desactivó'} el enlace «{e.nombre}».")
    return redirect(url_for("enlaces"))


@app.route("/enlaces/<int:id>/eliminar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def eliminar_enlace(id):
    e = verificar_pertenencia(EnlaceJuridico.query.get_or_404(id), despacho_actual_id())
    nombre = e.nombre
    db.session.delete(e)
    db.session.commit()
    registrar_auditoria("Eliminación", "Enlaces Jurídicos", registro_id=id, registro_desc=nombre,
                         descripcion=f"Se eliminó el enlace «{nombre}».")
    flash("Enlace eliminado.", "info")
    return redirect(url_for("enlaces"))


# ---------------------------------------------------------------------------
# BITÁCORA DE AUDITORÍA (solo lectura, exclusiva del Administrador)
# ---------------------------------------------------------------------------
@app.route("/auditoria")
@login_required
@roles_requeridos("Administrador")
def auditoria():
    d_id = despacho_actual_id()
    modulo = request.args.get("modulo", "")
    accion = request.args.get("accion", "")
    usuario_id = request.args.get("usuario_id", "")
    query = Auditoria.query.filter_by(despacho_id=d_id)
    if modulo:
        query = query.filter_by(modulo=modulo)
    if accion:
        query = query.filter_by(accion=accion)
    if usuario_id:
        query = query.filter_by(usuario_id=usuario_id)
    lista = query.order_by(Auditoria.fecha.desc()).limit(400).all()
    modulos = [r[0] for r in db.session.query(Auditoria.modulo).filter_by(despacho_id=d_id).distinct().order_by(Auditoria.modulo).all()]
    acciones = [r[0] for r in db.session.query(Auditoria.accion).filter_by(despacho_id=d_id).distinct().order_by(Auditoria.accion).all()]
    usuarios_lista = Usuario.query.filter_by(despacho_id=d_id).order_by(Usuario.nombre).all()
    return render_template("auditoria.html", registros=lista, modulos=modulos, acciones=acciones,
                            usuarios=usuarios_lista, modulo=modulo, accion=accion, usuario_id=usuario_id)


# ---------------------------------------------------------------------------
# USUARIOS (solo Administrador)
# ---------------------------------------------------------------------------
@app.route("/usuarios")
@login_required
@roles_requeridos("Administrador")
def usuarios():
    lista = Usuario.query.filter_by(despacho_id=despacho_actual_id()).order_by(Usuario.nombre).all()
    return render_template("usuarios.html", usuarios=lista)


@app.route("/usuarios/nuevo", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def nuevo_usuario():
    email = request.form["email"].strip().lower()
    if Usuario.query.filter_by(email=email).first():
        flash("Ya existe una cuenta registrada con ese correo (los correos son únicos en todo el sistema).", "danger")
        return redirect(url_for("usuarios"))
    u = Usuario(despacho_id=despacho_actual_id(), nombre=request.form["nombre"], email=email,
                rol=request.form.get("rol", "Asistente"))
    u.set_password(request.form["password"])
    db.session.add(u)
    db.session.commit()
    registrar_auditoria("Creación", "Usuarios", registro_id=u.id, registro_desc=u.email,
                         descripcion=f"Se creó el usuario «{u.nombre}» con rol {u.rol}.")
    flash("Usuario creado correctamente.", "success")
    return redirect(url_for("usuarios"))


@app.route("/usuarios/<int:id>/estado", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def cambiar_estado_usuario(id):
    u = verificar_pertenencia(Usuario.query.get_or_404(id), despacho_actual_id())
    if u.id == current_user.id:
        flash("No puedes desactivar tu propia cuenta.", "warning")
        return redirect(url_for("usuarios"))
    u.activo = not u.activo
    db.session.commit()
    registrar_auditoria("Edición", "Usuarios", registro_id=u.id, registro_desc=u.email,
                         descripcion=f"Se {'activó' if u.activo else 'desactivó'} la cuenta de «{u.nombre}».")
    return redirect(url_for("usuarios"))


@app.route("/usuarios/<int:id>/rol", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def cambiar_rol_usuario(id):
    u = verificar_pertenencia(Usuario.query.get_or_404(id), despacho_actual_id())
    if u.id == current_user.id:
        flash("No puedes cambiar el rol de tu propia cuenta.", "warning")
        return redirect(url_for("usuarios"))
    rol_anterior = u.rol
    nuevo_rol = request.form.get("rol")
    if nuevo_rol in ("Administrador", "Abogado", "Asistente"):
        u.rol = nuevo_rol
        db.session.commit()
        registrar_auditoria("Cambio de permisos", "Usuarios", registro_id=u.id, registro_desc=u.email,
                             descripcion=f"Se cambió el rol de «{u.nombre}»: {rol_anterior} → {nuevo_rol}.")
        flash("Rol actualizado correctamente.", "success")
    return redirect(url_for("usuarios"))


@app.route("/usuarios/<int:id>/eliminar", methods=["POST"])
@login_required
@roles_requeridos("Administrador")
def eliminar_usuario(id):
    u = verificar_pertenencia(Usuario.query.get_or_404(id), despacho_actual_id())
    if u.id == current_user.id:
        flash("No puedes eliminar tu propia cuenta.", "warning")
        return redirect(url_for("usuarios"))
    if u.expedientes.count() > 0 or u.tareas_asignadas.count() > 0:
        flash("No se puede eliminar: el usuario tiene expedientes o tareas asignadas. "
              "Reasigna esos registros o desactiva la cuenta en su lugar.", "danger")
        return redirect(url_for("usuarios"))
    nombre, email = u.nombre, u.email
    db.session.delete(u)
    db.session.commit()
    registrar_auditoria("Eliminación", "Usuarios", registro_id=id, registro_desc=email,
                         descripcion=f"Se eliminó la cuenta de «{nombre}».")
    flash("Usuario eliminado.", "info")
    return redirect(url_for("usuarios"))


# ---------------------------------------------------------------------------
# Manejo de errores
# ---------------------------------------------------------------------------
@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", codigo=403, mensaje="No tienes permiso para ver esta página."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", codigo=404, mensaje="La página que buscas no existe."), 404


# ---------------------------------------------------------------------------
# Inicialización de la base de datos con datos de ejemplo
# ---------------------------------------------------------------------------
def _sembrar_datos_iniciales_despacho(despacho_id):
    """Contenido de referencia (no datos del cliente) para que un despacho recién
    registrado no arranque completamente vacío: enlaces jurídicos oficiales y un
    tutorial de bienvenida. NO crea clientes, expedientes ni información real."""
    db.session.add(Tutorial(
        despacho_id=despacho_id,
        titulo="Bienvenida: primeros pasos en el sistema",
        categoria="Uso del sistema",
        contenido="<p>¡Bienvenido(a) a tu despacho digital! Algunos primeros pasos recomendados:</p>"
                   "<p>1. Da de alta a tu equipo desde <b>Usuarios y roles</b>.<br>"
                   "2. Registra tus primeros <b>Clientes</b>.<br>"
                   "3. Abre tu primer <b>Expediente</b> y adjunta sus documentos.<br>"
                   "4. Explora el <b>Calendario</b> y las <b>Alertas</b> para no perder de vista plazos y audiencias.</p>",
        destacado=True, obligatorio=True,
    ))
    for e in _enlaces_juridicos_base(despacho_id):
        db.session.add(e)
    db.session.commit()


def _enlaces_juridicos_base(despacho_id):
    return [
        EnlaceJuridico(despacho_id=despacho_id, nombre="Sistema de Consulta de Sentencias del Poder Judicial de la Federación",
                        institucion="Suprema Corte de Justicia de la Nación",
                        descripcion="Consulta de tesis, jurisprudencia y sentencias del Poder Judicial Federal.",
                        url="https://www.scjn.gob.mx", categoria="Jurisprudencia",
                        icono="⚖️", favorito=True, obligatorio=True),
        EnlaceJuridico(despacho_id=despacho_id, nombre="Buscador de Jurisprudencia (SJF)",
                        institucion="Suprema Corte de Justicia de la Nación",
                        descripcion="Semanario Judicial de la Federación: tesis y jurisprudencias vigentes.",
                        url="https://sjf2.scjn.gob.mx", categoria="Jurisprudencia",
                        icono="📚", favorito=True),
        EnlaceJuridico(despacho_id=despacho_id, nombre="Diario Oficial de la Federación",
                        institucion="Gobierno de México",
                        descripcion="Publicación de leyes, reglamentos, decretos y acuerdos federales.",
                        url="https://www.dof.gob.mx", categoria="Diario Oficial de la Federación",
                        icono="📰", favorito=True, obligatorio=True),
        EnlaceJuridico(despacho_id=despacho_id, nombre="Portal de Trámites y Servicios del SAT",
                        institucion="Servicio de Administración Tributaria",
                        descripcion="Consulta de RFC, facturación electrónica y trámites fiscales.",
                        url="https://www.sat.gob.mx", categoria="SAT", icono="💰"),
        EnlaceJuridico(despacho_id=despacho_id, nombre="IMSS Digital",
                        institucion="Instituto Mexicano del Seguro Social",
                        descripcion="Consulta de vigencia de derechos y trámites patronales.",
                        url="https://www.imss.gob.mx", categoria="IMSS", icono="🏥"),
        EnlaceJuridico(despacho_id=despacho_id, nombre="Portal INFONAVIT",
                        institucion="Instituto del Fondo Nacional de la Vivienda para los Trabajadores",
                        descripcion="Consulta de créditos, precalificación y trámites de vivienda.",
                        url="https://www.infonavit.org.mx", categoria="INFONAVIT", icono="🏠"),
        EnlaceJuridico(despacho_id=despacho_id, nombre="RENAPO — Registro Nacional de Población",
                        institucion="Secretaría de Gobernación",
                        descripcion="Validación de CURP y consulta de registros de identidad.",
                        url="https://www.gob.mx/curp", categoria="RENAPO", icono="🪪"),
        EnlaceJuridico(despacho_id=despacho_id, nombre="Diario/Periódico Oficial de tu estado",
                        institucion="Gobierno estatal",
                        descripcion="Edítalo con el enlace oficial de tu entidad; lo dejamos como referencia inicial.",
                        url="https://www.gob.mx", categoria="Periódicos Oficiales Estatales", icono="📰"),
        EnlaceJuridico(despacho_id=despacho_id, nombre="Plataforma Nacional de Transparencia",
                        institucion="INAI / Órganos garantes",
                        descripcion="Solicitudes de acceso a la información y transparencia gubernamental.",
                        url="https://www.plataformadetransparencia.org.mx", categoria="Transparencia",
                        icono="📄"),
    ]


def inicializar_datos():
    db.create_all()
    if Despacho.query.first():
        return  # ya inicializado

    demo = Despacho(nombre="Despacho Demo", slug="despacho-demo")
    db.session.add(demo)
    db.session.commit()

    admin = Usuario(despacho_id=demo.id, nombre="Ana Martínez", email="admin@despacho.com", rol="Administrador")
    admin.set_password("admin123")
    abogado1 = Usuario(despacho_id=demo.id, nombre="Lic. Carlos Rivera", email="carlos@despacho.com", rol="Abogado")
    abogado1.set_password("abogado123")
    abogado2 = Usuario(despacho_id=demo.id, nombre="Lic. Sofía Torres", email="sofia@despacho.com", rol="Abogado")
    abogado2.set_password("abogado123")
    asistente = Usuario(despacho_id=demo.id, nombre="Diego Pérez", email="asistente@despacho.com", rol="Asistente")
    asistente.set_password("asistente123")
    db.session.add_all([admin, abogado1, abogado2, asistente])
    db.session.commit()

    d = demo.id  # atajo

    c1 = Cliente(despacho_id=d, nombre="Grupo Industrial del Norte S.A. de C.V.", tipo="Persona moral",
                 identificacion="GIN010203ABC", telefono="228-555-0110",
                 email="contacto@gindustrial.com", direccion="Av. Reforma 120, Veracruz, Ver.")
    c2 = Cliente(despacho_id=d, nombre="María Fernanda López Gutiérrez", tipo="Persona física",
                 identificacion="LOGM850312MVZ", telefono="229-555-0223",
                 email="mflopez@email.com", direccion="Calle Hidalgo 45, Xalapa, Ver.")
    c3 = Cliente(despacho_id=d, nombre="Roberto Sánchez Domínguez", tipo="Persona física",
                 identificacion="SADR780925HVZ", telefono="229-555-0987",
                 email="rsanchez@email.com", direccion="Blvd. Ávila Camacho 300, Veracruz, Ver.")
    db.session.add_all([c1, c2, c3])
    db.session.commit()

    e1 = Expediente(despacho_id=d, numero="EXP-2026-001", titulo="Demanda por incumplimiento de contrato mercantil",
                     tipo_caso="Mercantil", estado="En proceso", prioridad="Alta",
                     juzgado="Juzgado 3° de lo Civil de Veracruz", contraparte="Proveedores Unidos S.A.",
                     descripcion="Incumplimiento de contrato de suministro industrial.",
                     monto_estimado=850000, cliente_id=c1.id, abogado_id=abogado1.id)
    e2 = Expediente(despacho_id=d, numero="EXP-2026-002", titulo="Juicio de divorcio incausado",
                     tipo_caso="Familiar", estado="Abierto", prioridad="Media",
                     juzgado="Juzgado Familiar de Xalapa", contraparte="N/A",
                     descripcion="Solicitud de divorcio incausado y régimen de convivencia.",
                     monto_estimado=0, cliente_id=c2.id, abogado_id=abogado2.id)
    e3 = Expediente(despacho_id=d, numero="EXP-2025-118", titulo="Demanda laboral por despido injustificado",
                     tipo_caso="Laboral", estado="Concluido", prioridad="Media",
                     juzgado="Tribunal Laboral de Veracruz", contraparte="Constructora del Golfo",
                     descripcion="Reclamo de indemnización por despido injustificado.",
                     monto_estimado=210000, cliente_id=c3.id, abogado_id=abogado1.id,
                     fecha_cierre=date.today() - timedelta(days=15))
    db.session.add_all([e1, e2, e3])
    db.session.commit()

    db.session.add_all([
        Movimiento(expediente_id=e1.id, descripcion="Apertura del expediente.", tipo="Apertura", autor=admin.nombre),
        Movimiento(expediente_id=e1.id, descripcion="Se presentó escrito de demanda ante el juzgado.",
                   tipo="Actuación", autor=abogado1.nombre),
        Movimiento(expediente_id=e2.id, descripcion="Apertura del expediente.", tipo="Apertura", autor=admin.nombre),
        Movimiento(expediente_id=e3.id, descripcion="Sentencia favorable, expediente concluido.",
                   tipo="Cambio de estado", autor=abogado1.nombre),
    ])

    db.session.add_all([
        Audiencia(despacho_id=d, expediente_id=e1.id, tipo="Audiencia de conciliación",
                  fecha_hora=datetime.now() + timedelta(days=3, hours=2), lugar="Sala 4, Juzgado 3° Civil"),
        Audiencia(despacho_id=d, expediente_id=e2.id, tipo="Audiencia preliminar",
                  fecha_hora=datetime.now() + timedelta(days=6, hours=1), lugar="Sala 2, Juzgado Familiar"),
    ])

    db.session.add_all([
        Cita(despacho_id=d, cliente_id=c1.id, usuario_id=abogado1.id, titulo="Revisión de estrategia procesal",
             fecha_hora=datetime.now() + timedelta(days=1, hours=3), lugar="Oficina principal"),
        Cita(despacho_id=d, cliente_id=c3.id, usuario_id=abogado1.id, titulo="Entrega de documentación final",
             fecha_hora=datetime.now() + timedelta(days=2, hours=1), lugar="Oficina principal"),
    ])

    t1 = Tarea(despacho_id=d, titulo="Preparar alegatos finales", expediente_id=e1.id,
               fecha_limite=date.today() + timedelta(days=4), prioridad="Alta",
               creado_por_id=admin.id, usuario_id=abogado1.id)
    t2 = Tarea(despacho_id=d, titulo="Solicitar copias certificadas", expediente_id=e2.id,
               fecha_limite=date.today() + timedelta(days=2), prioridad="Media",
               creado_por_id=admin.id, usuario_id=asistente.id)
    t3 = Tarea(despacho_id=d, titulo="Archivar expediente físico", expediente_id=e3.id,
               fecha_limite=date.today() - timedelta(days=2), prioridad="Baja",
               creado_por_id=admin.id, usuario_id=asistente.id)
    db.session.add_all([t1, t2, t3])
    db.session.flush()
    t1.responsables = [abogado1]
    t2.responsables = [asistente, abogado2]
    t3.responsables = [asistente]

    db.session.add_all([
        Pago(despacho_id=d, expediente_id=e1.id, concepto="Honorarios etapa inicial", monto=45000,
             estado="Pagado", metodo="Transferencia", fecha_pago=date.today() - timedelta(days=10)),
        Pago(despacho_id=d, expediente_id=e1.id, concepto="Segunda ministración de honorarios", monto=45000,
             estado="Pendiente"),
        Pago(despacho_id=d, expediente_id=e2.id, concepto="Honorarios fijos", monto=25000,
             estado="Pagado", metodo="Efectivo", fecha_pago=date.today() - timedelta(days=5)),
        Pago(despacho_id=d, expediente_id=e3.id, concepto="Honorarios de éxito", monto=63000,
             estado="Pagado", metodo="Transferencia", fecha_pago=date.today() - timedelta(days=14)),
    ])

    # Tutoriales de capacitación
    db.session.add_all([
        Tutorial(
            despacho_id=d,
            titulo="Cómo crear un expediente nuevo",
            categoria="Uso del sistema",
            contenido="<p>Para crear un expediente, ve al módulo <b>Expedientes</b> y haz clic en "
                       "<b>Nuevo expediente</b>. Completa el número, cliente, tipo de caso y abogado "
                       "responsable. Recuerda registrar el juzgado y la contraparte cuando aplique.</p>"
                       "<p>Una vez creado, podrás adjuntar documentos, programar audiencias y llevar "
                       "la bitácora de movimientos desde la misma pantalla.</p>",
            destacado=True, obligatorio=True, autor_id=admin.id,
        ),
        Tutorial(
            despacho_id=d,
            titulo="Estructura básica de una demanda civil",
            categoria="Demandas",
            contenido="<p>Toda demanda debe incluir: proemio, hechos, fundamentos de derecho, "
                       "petitorio y firma. Verifica siempre la competencia del juzgado antes de "
                       "presentar el escrito.</p><p>Consulta con el abogado responsable del área "
                       "antes de presentar demandas con cuantías superiores a $500,000 MXN.</p>",
            destacado=True, obligatorio=False, autor_id=admin.id,
        ),
        Tutorial(
            despacho_id=d,
            titulo="Elaboración de contestación de demanda",
            categoria="Contestaciones",
            contenido="<p>La contestación debe responder cada hecho de forma categórica (afirmando, "
                       "negando o expresando desconocimiento), oponer excepciones y ofrecer pruebas "
                       "dentro del plazo legal correspondiente.</p>",
            destacado=False, obligatorio=False, autor_id=admin.id,
        ),
        Tutorial(
            despacho_id=d,
            titulo="Checklist para contratos mercantiles",
            categoria="Contratos",
            contenido="<p>Antes de enviar un contrato a firma, verifica: partes correctamente "
                       "identificadas, objeto claro, obligaciones recíprocas, cláusulas de "
                       "penalización, jurisdicción y firmas autógrafas o electrónicas válidas.</p>",
            destacado=False, obligatorio=True, autor_id=admin.id,
        ),
        Tutorial(
            despacho_id=d,
            titulo="Cómo subir y clasificar documentos en un expediente",
            categoria="Uso del sistema",
            contenido="<p>Dentro de un expediente, abre la pestaña <b>Documentos</b>, selecciona la "
                       "categoría correspondiente (Demanda, Contrato, Prueba, Resolución, etc.) y "
                       "adjunta el archivo. El sistema conserva un historial de quién subió cada "
                       "documento y cuándo.</p>",
            destacado=False, obligatorio=False, autor_id=admin.id,
        ),
    ])

    # Enlaces jurídicos oficiales
    db.session.add_all(_enlaces_juridicos_base(d))
    fiscalia_demo = EnlaceJuridico(despacho_id=d, nombre="Poder Judicial del Estado de Veracruz",
                                    institucion="Poder Judicial de Veracruz",
                                    descripcion="Consulta de expedientes, acuerdos y boletín judicial estatal.",
                                    url="https://www.pjeveracruz.gob.mx", categoria="Poderes Judiciales Estatales",
                                    icono="🏛️", favorito=True, obligatorio=True)
    fiscalia_demo2 = EnlaceJuridico(despacho_id=d, nombre="Fiscalía General del Estado de Veracruz",
                                     institucion="Fiscalía General del Estado",
                                     descripcion="Denuncias, carpetas de investigación y trámites del Ministerio Público.",
                                     url="https://www.fiscaliaveracruz.gob.mx", categoria="Fiscalía", icono="🔎")
    db.session.add_all([fiscalia_demo, fiscalia_demo2])

    db.session.commit()


with app.app_context():
    inicializar_datos()


def _abrir_navegador(puerto):
    """Espera a que el servidor esté listo y abre el navegador predeterminado."""
    import time
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{puerto}")


if __name__ == "__main__":
    PUERTO = 5000

    if APP_CONGELADA:
        # --- Modo .exe: servidor de producción (Waitress), sin recargador,
        #     sin modo debug, y abre el navegador automáticamente. ---
        threading.Thread(target=_abrir_navegador, args=(PUERTO,), daemon=True).start()
        print("=" * 60)
        print(" Sistema de Gestión Integral para Despachos Jurídicos")
        print(" Iniciando servidor local... esto puede tardar unos segundos.")
        print(f" Si el navegador no se abre solo, entra manualmente a:")
        print(f"   http://127.0.0.1:{PUERTO}")
        print(" No cierres esta ventana mientras uses el sistema.")
        print("=" * 60)
        from waitress import serve
        serve(app, host="127.0.0.1", port=PUERTO)
    else:
        # --- Modo desarrollo: recarga automática y depurador de Flask ---
        threading.Timer(1.2, _abrir_navegador, args=(PUERTO,)).start()
        app.run(debug=True, host="127.0.0.1", port=PUERTO, use_reloader=False)
