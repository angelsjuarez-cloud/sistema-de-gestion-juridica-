# -*- coding: utf-8 -*-
"""
Migración: agrega las columnas de plan/pago a la tabla 'despachos'
SIN borrar ni afectar ningún dato existente (clientes, expedientes, etc.).

Ejecútalo UNA sola vez, después de actualizar el código con `git pull`
y ANTES de recargar la aplicación (botón Reload en PythonAnywhere).

Uso (desde la carpeta del proyecto, en la consola Bash):
    python migrar_db.py
"""
import os
import sqlite3

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "instance", "despacho.db")

COLUMNAS_NUEVAS = [
    ("plan", "VARCHAR(30) DEFAULT 'Piloto'"),
    ("estado_pago", "VARCHAR(20) DEFAULT 'activo'"),
    ("fecha_fin_prueba", "DATE"),
    ("stripe_customer_id", "VARCHAR(120)"),
    ("stripe_subscription_id", "VARCHAR(120)"),
    ("acepto_aviso_privacidad", "BOOLEAN DEFAULT 0"),
    ("fecha_aceptacion_aviso", "DATETIME"),
]


def columnas_existentes(cursor, tabla):
    cursor.execute(f"PRAGMA table_info({tabla})")
    return {fila[1] for fila in cursor.fetchall()}


def main():
    if not os.path.exists(DB_PATH):
        print(f"No se encontró la base de datos en: {DB_PATH}")
        print("Si tu base de datos vive en otra ruta, edita DB_PATH en este script.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    existentes = columnas_existentes(cursor, "despachos")

    agregadas = []
    for nombre, definicion in COLUMNAS_NUEVAS:
        if nombre not in existentes:
            cursor.execute(f"ALTER TABLE despachos ADD COLUMN {nombre} {definicion}")
            agregadas.append(nombre)

    conn.commit()
    conn.close()

    if agregadas:
        print("Listo. Columnas agregadas a 'despachos':", ", ".join(agregadas))
    else:
        print("La base de datos ya estaba actualizada, no se hizo ningún cambio.")


if __name__ == "__main__":
    main()