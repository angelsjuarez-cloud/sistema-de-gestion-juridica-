# -*- coding: utf-8 -*-
"""
Modelos de datos del Sistema de Gestión Integral para Despachos Jurídicos.
"""
from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ---------------------------------------------------------------------------
# Despachos (organizaciones / tenants) — cada despacho tiene sus propios
# clientes, expedientes, pagos, etc. completamente aislados de los demás.
# ---------------------------------------------------------------------------
class Despacho(db.Model):
    __tablename__ = "despachos"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    slug = db.Column(db.String(60), unique=True, nullable=False)  # identificador corto y legible
    activo = db.Column(db.Boolean, default=True)  # permite suspender un despacho sin borrar sus datos
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)

    # --- Preparación para cobro futuro (no se usa/valida todavía en ningún lado) ---
    plan = db.Column(db.String(30), default="Piloto")
    estado_pago = db.Column(db.String(20), default="activo")
    fecha_fin_prueba = db.Column(db.Date, nullable=True)
    stripe_customer_id = db.Column(db.String(120), nullable=True)
    stripe_subscription_id = db.Column(db.String(120), nullable=True)
# --- Constancia de aceptación del Aviso de Privacidad ---
    acepto_aviso_privacidad = db.Column(db.Boolean, default=False)
    fecha_aceptacion_aviso = db.Column(db.DateTime, nullable=True)
    usuarios = db.relationship("Usuario", backref="despacho", lazy="dynamic")

# ---------------------------------------------------------------------------
# Tabla de asociación: tareas <-> responsables (varios a varios)
# ---------------------------------------------------------------------------
tarea_responsables = db.Table(
    "tarea_responsables",
    db.Column("tarea_id", db.Integer, db.ForeignKey("tareas.id"), primary_key=True),
    db.Column("usuario_id", db.Integer, db.ForeignKey("usuarios.id"), primary_key=True),
)


# ---------------------------------------------------------------------------
# Usuarios y roles
# ---------------------------------------------------------------------------
class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    despacho_id = db.Column(db.Integer, db.ForeignKey("despachos.id"), nullable=False)
    nombre = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default="Asistente")  # Administrador, Abogado, Asistente
    activo = db.Column(db.Boolean, default=True)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)

    expedientes = db.relationship("Expediente", backref="abogado_responsable", lazy="dynamic",
                                   foreign_keys="Expediente.abogado_id")
    tareas = db.relationship("Tarea", backref="asignado", lazy="dynamic",
                              foreign_keys="Tarea.usuario_id")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def es_admin(self):
        return self.rol == "Administrador"

    @property
    def es_abogado(self):
        return self.rol == "Abogado"

    def __repr__(self):
        return f"<Usuario {self.email} ({self.rol})>"


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------
class Cliente(db.Model):
    __tablename__ = "clientes"

    id = db.Column(db.Integer, primary_key=True)
    despacho_id = db.Column(db.Integer, db.ForeignKey("despachos.id"), nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    tipo = db.Column(db.String(20), default="Persona física")  # Persona física / Persona moral
    identificacion = db.Column(db.String(60))  # RFC / CURP / cédula
    telefono = db.Column(db.String(30))
    email = db.Column(db.String(150))
    direccion = db.Column(db.String(255))
    notas = db.Column(db.Text)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)

    expedientes = db.relationship("Expediente", backref="cliente", lazy="dynamic",
                                   cascade="all, delete-orphan")

    @property
    def expedientes_activos(self):
        return self.expedientes.filter(Expediente.estado != "Concluido").count()


# ---------------------------------------------------------------------------
# Expedientes (casos)
# ---------------------------------------------------------------------------
class Expediente(db.Model):
    __tablename__ = "expedientes"
    __table_args__ = (
        db.UniqueConstraint("numero", "despacho_id", name="uq_expediente_numero_por_despacho"),
    )

    id = db.Column(db.Integer, primary_key=True)
    despacho_id = db.Column(db.Integer, db.ForeignKey("despachos.id"), nullable=False)
    numero = db.Column(db.String(40), nullable=False)
    titulo = db.Column(db.String(200), nullable=False)
    tipo_caso = db.Column(db.String(80), nullable=False)  # Civil, Penal, Laboral, Mercantil, Familiar, etc.
    estado = db.Column(db.String(30), default="Abierto")  # Abierto, En proceso, En espera, Concluido, Archivado
    prioridad = db.Column(db.String(15), default="Media")  # Alta, Media, Baja
    juzgado = db.Column(db.String(150))
    contraparte = db.Column(db.String(150))
    descripcion = db.Column(db.Text)
    fecha_apertura = db.Column(db.Date, default=datetime.utcnow)
    fecha_cierre = db.Column(db.Date, nullable=True)
    monto_estimado = db.Column(db.Float, default=0)

    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    abogado_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)

    documentos = db.relationship("Documento", backref="expediente", lazy="dynamic",
                                  cascade="all, delete-orphan")
    movimientos = db.relationship("Movimiento", backref="expediente", lazy="dynamic",
                                   cascade="all, delete-orphan", order_by="desc(Movimiento.fecha)")
    audiencias = db.relationship("Audiencia", backref="expediente", lazy="dynamic",
                                  cascade="all, delete-orphan")
    pagos = db.relationship("Pago", backref="expediente", lazy="dynamic",
                             cascade="all, delete-orphan")
    tareas = db.relationship("Tarea", backref="expediente", lazy="dynamic",
                              cascade="all, delete-orphan")

    @property
    def total_pagado(self):
        return sum(p.monto for p in self.pagos if p.estado == "Pagado")

    @property
    def total_pendiente(self):
        return sum(p.monto for p in self.pagos if p.estado == "Pendiente")


class Movimiento(db.Model):
    """Historial de movimientos / bitácora del expediente."""
    __tablename__ = "movimientos"

    id = db.Column(db.Integer, primary_key=True)
    expediente_id = db.Column(db.Integer, db.ForeignKey("expedientes.id"), nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)
    descripcion = db.Column(db.Text, nullable=False)
    tipo = db.Column(db.String(40), default="Actuación")  # Actuación, Observación, Cambio de estado, etc.
    autor = db.Column(db.String(120))


class Documento(db.Model):
    __tablename__ = "documentos"

    id = db.Column(db.Integer, primary_key=True)
    expediente_id = db.Column(db.Integer, db.ForeignKey("expedientes.id"), nullable=False)
    nombre_archivo = db.Column(db.String(255), nullable=False)
    ruta = db.Column(db.String(500), nullable=False)
    categoria = db.Column(db.String(60), default="General")  # Demanda, Contrato, Prueba, Resolución, etc.
    subido_en = db.Column(db.DateTime, default=datetime.utcnow)
    subido_por = db.Column(db.String(120))
    tamano_kb = db.Column(db.Integer, default=0)


# ---------------------------------------------------------------------------
# Audiencias
# ---------------------------------------------------------------------------
class Audiencia(db.Model):
    __tablename__ = "audiencias"

    id = db.Column(db.Integer, primary_key=True)
    despacho_id = db.Column(db.Integer, db.ForeignKey("despachos.id"), nullable=False)
    expediente_id = db.Column(db.Integer, db.ForeignKey("expedientes.id"), nullable=False)
    tipo = db.Column(db.String(80), nullable=False)  # Conciliación, Juicio, Preliminar, etc.
    fecha_hora = db.Column(db.DateTime, nullable=False)
    lugar = db.Column(db.String(200))
    estado = db.Column(db.String(20), default="Programada")  # Programada, Realizada, Reprogramada, Cancelada
    resultado = db.Column(db.Text)


# ---------------------------------------------------------------------------
# Citas (con clientes)
# ---------------------------------------------------------------------------
class Cita(db.Model):
    __tablename__ = "citas"

    id = db.Column(db.Integer, primary_key=True)
    despacho_id = db.Column(db.Integer, db.ForeignKey("despachos.id"), nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    titulo = db.Column(db.String(200), nullable=False)
    fecha_hora = db.Column(db.DateTime, nullable=False)
    lugar = db.Column(db.String(200))
    estado = db.Column(db.String(20), default="Programada")  # Programada, Completada, Cancelada
    notas = db.Column(db.Text)

    cliente = db.relationship("Cliente")
    usuario = db.relationship("Usuario")


# ---------------------------------------------------------------------------
# Tareas
# ---------------------------------------------------------------------------
class Tarea(db.Model):
    __tablename__ = "tareas"

    id = db.Column(db.Integer, primary_key=True)
    despacho_id = db.Column(db.Integer, db.ForeignKey("despachos.id"), nullable=False)
    titulo = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.Text)
    fecha_limite = db.Column(db.Date, nullable=True)
    prioridad = db.Column(db.String(15), default="Media")
    # Pendiente, En proceso, En revisión, Completada, Cancelada
    estado = db.Column(db.String(20), default="Pendiente")
    expediente_id = db.Column(db.Integer, db.ForeignKey("expedientes.id"), nullable=True)
    # usuario_id se conserva por compatibilidad (creador / responsable principal histórico)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    creado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)
    actualizado_en = db.Column(db.DateTime, default=datetime.utcnow)

    responsables = db.relationship("Usuario", secondary=tarea_responsables,
                                    backref=db.backref("tareas_asignadas", lazy="dynamic"))
    comentarios = db.relationship("TareaComentario", backref="tarea", lazy="dynamic",
                                   cascade="all, delete-orphan", order_by="TareaComentario.fecha")
    creado_por = db.relationship("Usuario", foreign_keys=[creado_por_id])

    def visible_para(self, usuario):
        """Una tarea solo es visible para el Administrador y sus responsables asignados."""
        if usuario.es_admin:
            return True
        return usuario in self.responsables

    @property
    def dias_sin_actualizar(self):
        return (datetime.utcnow() - self.actualizado_en).days

    @property
    def vencida(self):
        return bool(self.fecha_limite and self.fecha_limite < date.today() and self.estado not in ("Completada", "Cancelada"))


class TareaComentario(db.Model):
    __tablename__ = "tarea_comentarios"

    id = db.Column(db.Integer, primary_key=True)
    tarea_id = db.Column(db.Integer, db.ForeignKey("tareas.id"), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    texto = db.Column(db.Text, nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

    usuario = db.relationship("Usuario")


# ---------------------------------------------------------------------------
# Pagos
# ---------------------------------------------------------------------------
class Pago(db.Model):
    __tablename__ = "pagos"

    id = db.Column(db.Integer, primary_key=True)
    despacho_id = db.Column(db.Integer, db.ForeignKey("despachos.id"), nullable=False)
    expediente_id = db.Column(db.Integer, db.ForeignKey("expedientes.id"), nullable=False)
    concepto = db.Column(db.String(200), nullable=False)
    monto = db.Column(db.Float, nullable=False)
    fecha_emision = db.Column(db.Date, default=datetime.utcnow)
    fecha_pago = db.Column(db.Date, nullable=True)
    estado = db.Column(db.String(20), default="Pendiente")  # Pendiente, Pagado, Vencido, Cancelado
    metodo = db.Column(db.String(40))  # Transferencia, Efectivo, Tarjeta, etc.


# ---------------------------------------------------------------------------
# Tutoriales y capacitación
# ---------------------------------------------------------------------------
class Tutorial(db.Model):
    __tablename__ = "tutoriales"

    id = db.Column(db.Integer, primary_key=True)
    despacho_id = db.Column(db.Integer, db.ForeignKey("despachos.id"), nullable=False)
    titulo = db.Column(db.String(200), nullable=False)
    categoria = db.Column(db.String(60), nullable=False, default="Uso del sistema")
    contenido = db.Column(db.Text, nullable=False)  # texto enriquecido (HTML simple)
    video_url = db.Column(db.String(400))  # enlace embebible (YouTube, Vimeo, etc.)
    destacado = db.Column(db.Boolean, default=False)
    obligatorio = db.Column(db.Boolean, default=False)
    autor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)
    actualizado_en = db.Column(db.DateTime, default=datetime.utcnow)

    autor = db.relationship("Usuario")
    archivos = db.relationship("TutorialArchivo", backref="tutorial", lazy="dynamic",
                                cascade="all, delete-orphan")

    CATEGORIAS = ["Demandas", "Contestaciones", "Contratos", "Juicios", "Amparos",
                  "Procedimientos", "Uso del sistema", "Otro"]


class TutorialArchivo(db.Model):
    __tablename__ = "tutorial_archivos"

    id = db.Column(db.Integer, primary_key=True)
    tutorial_id = db.Column(db.Integer, db.ForeignKey("tutoriales.id"), nullable=False)
    tipo = db.Column(db.String(20), default="documento")  # imagen, pdf, documento
    nombre_archivo = db.Column(db.String(255), nullable=False)
    ruta = db.Column(db.String(500), nullable=False)
    subido_en = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Bitácora de auditoría
# ---------------------------------------------------------------------------
class Auditoria(db.Model):
    __tablename__ = "auditoria"

    id = db.Column(db.Integer, primary_key=True)
    despacho_id = db.Column(db.Integer, db.ForeignKey("despachos.id"), nullable=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    usuario_nombre = db.Column(db.String(120))  # se conserva aunque el usuario sea eliminado
    fecha = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    ip = db.Column(db.String(60))
    accion = db.Column(db.String(40), nullable=False)   # Creación, Edición, Eliminación, Inicio de sesión, etc.
    modulo = db.Column(db.String(60), nullable=False)   # Clientes, Expedientes, Pagos, Usuarios, etc.
    registro_id = db.Column(db.Integer, nullable=True)
    registro_desc = db.Column(db.String(255))
    descripcion = db.Column(db.Text)

    usuario = db.relationship("Usuario")


# ---------------------------------------------------------------------------
# Alertas inteligentes
# ---------------------------------------------------------------------------
class Alerta(db.Model):
    __tablename__ = "alertas"
    __table_args__ = (
        db.UniqueConstraint("clave", "despacho_id", name="uq_alerta_clave_por_despacho"),
    )

    id = db.Column(db.Integer, primary_key=True)
    despacho_id = db.Column(db.Integer, db.ForeignKey("despachos.id"), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)  # tarea_por_vencer, tarea_vencida, pagos_pendientes, etc.
    clave = db.Column(db.String(120), nullable=False)  # identificador único (por despacho) para evitar duplicados
    prioridad = db.Column(db.String(15), default="Media")  # Alta, Media, Baja
    mensaje = db.Column(db.String(400), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    expediente_id = db.Column(db.Integer, db.ForeignKey("expedientes.id"), nullable=True)
    tarea_id = db.Column(db.Integer, db.ForeignKey("tareas.id"), nullable=True)
    fecha_referencia = db.Column(db.Date, nullable=True)  # fecha límite / fecha relevante
    generada_en = db.Column(db.DateTime, default=datetime.utcnow)
    actualizada_en = db.Column(db.DateTime, default=datetime.utcnow)
    revisada = db.Column(db.Boolean, default=False)
    revisada_en = db.Column(db.DateTime, nullable=True)

    usuario = db.relationship("Usuario")
    expediente = db.relationship("Expediente")
    tarea = db.relationship("Tarea")


# ---------------------------------------------------------------------------
# Centro de enlaces jurídicos oficiales
# ---------------------------------------------------------------------------
class EnlaceJuridico(db.Model):
    __tablename__ = "enlaces_juridicos"

    id = db.Column(db.Integer, primary_key=True)
    despacho_id = db.Column(db.Integer, db.ForeignKey("despachos.id"), nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    institucion = db.Column(db.String(150))
    descripcion = db.Column(db.Text)
    url = db.Column(db.String(500), nullable=False)
    categoria = db.Column(db.String(60), nullable=False, default="Otros servicios gubernamentales")
    icono = db.Column(db.String(10), default="🔗")  # emoji simple como ícono
    favorito = db.Column(db.Boolean, default=False)
    obligatorio = db.Column(db.Boolean, default=False)
    activo = db.Column(db.Boolean, default=True)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)
    actualizado_en = db.Column(db.DateTime, default=datetime.utcnow)

    CATEGORIAS = [
        "Poder Judicial Federal", "Poderes Judiciales Estatales", "Tribunales", "Fiscalía",
        "Registro Público de la Propiedad", "SAT", "IMSS", "INFONAVIT", "RENAPO",
        "Diario Oficial de la Federación", "Periódicos Oficiales Estatales", "Legislación",
        "Jurisprudencia", "Consulta de Expedientes", "Notificaciones Electrónicas",
        "Transparencia", "Otros servicios gubernamentales",
    ]
