# Sistema de Gestión Integral para Despachos Jurídicos

Aplicación web desarrollada en **Python (Flask)** para la administración integral de un despacho de abogados: clientes, expedientes, documentos, pagos, audiencias, citas y tareas, con inicio de sesión por roles y panel de estadísticas.

---

## 1. Requisitos

- Python 3.10 o superior
- Visual Studio Code (con la extensión oficial "Python" de Microsoft, recomendable)

## 2. Instalación (en VS Code)

1. Descomprime este proyecto y ábrelo en VS Code: `Archivo → Abrir carpeta…` y selecciona la carpeta `legalsys`.
2. Abre una terminal integrada: `Terminal → Nueva terminal`.
3. (Recomendado) Crea un entorno virtual:

   ```bash
   python -m venv venv
   ```

   Actívalo:
   - Windows: `venv\Scripts\activate`
   - macOS / Linux: `source venv/bin/activate`

4. Instala las dependencias:

   ```bash
   pip install -r requirements.txt
   ```

## 3. Ejecución

```bash
python app.py
```

Verás un mensaje indicando que el servidor corre en `http://127.0.0.1:5000`. Ábrelo en tu navegador (Ctrl+clic sobre la URL en la terminal de VS Code, o cópiala manualmente).

La primera vez que se ejecuta, el sistema crea automáticamente la base de datos SQLite (`instance/despacho.db`) con **usuarios y datos de ejemplo** para que puedas explorar el sistema de inmediato.

## 4. Cuentas de acceso

El sistema es **multi-despacho**: cada organización que se registra en la pantalla de login (botón "Crear cuenta de despacho nueva") obtiene su propio espacio aislado, con su propia base de clientes, expedientes, pagos, usuarios y documentos.

Al iniciar por primera vez, el sistema crea automáticamente un despacho de ejemplo ("Despacho Demo") con datos de muestra y estas cuentas:

| Rol            | Correo                    | Contraseña     |
|----------------|----------------------------|----------------|
| Administrador  | admin@despacho.com         | admin123       |
| Abogado        | carlos@despacho.com        | abogado123     |
| Abogado        | sofia@despacho.com         | abogado123     |
| Asistente      | asistente@despacho.com     | asistente123   |

**Recomendación:** usa el Despacho Demo solo para explorar el sistema. Para un despacho real, regístralo desde cero en `/registro` — así partirá limpio, sin los datos de ejemplo.

## 5. Estructura del proyecto

```
legalsys/
├── app.py                 # Rutas, lógica de negocio y arranque de la app
├── models.py               # Modelos de base de datos (SQLAlchemy)
├── requirements.txt
├── instance/
│   └── despacho.db         # Base de datos SQLite (se crea automáticamente)
├── uploads/                 # Documentos adjuntos por expediente (se crea automáticamente)
├── static/
│   └── css/style.css        # Estilos del sistema
└── templates/                # Vistas HTML (Jinja2)
```

## 6. Roles del sistema

- **Administrador**: acceso total, incluida la gestión de usuarios y la eliminación de clientes/expedientes.
- **Abogado**: gestiona expedientes, clientes, audiencias, citas, tareas, documentos y pagos.
- **Asistente**: apoyo administrativo con los mismos módulos operativos, sin permisos de eliminación ni gestión de usuarios.

## 7. Módulos incluidos

- **Multi-despacho (multi-tenant)**: cada despacho que se registra en `/registro` obtiene su propio espacio completamente aislado — clientes, expedientes, pagos, documentos, tareas, tutoriales, enlaces y bitácora de un despacho nunca son visibles para otro. Un mismo servidor puede alojar varios despachos de forma segura y simultánea.
- **Panel principal**: estadísticas de expedientes, ingresos del mes, pagos pendientes, próximas audiencias/citas, recordatorios de plazos, actividad reciente y **centro de alertas inteligentes** (solo Administrador).
- **Calendario**: vista mensual con audiencias, citas y vencimientos de tareas (respeta la visibilidad de tareas por responsable).
- **Clientes**: alta, edición, ficha con expedientes asociados. Eliminación exclusiva del Administrador.
- **Expedientes**: información del cliente, tipo de caso, estado, prioridad, documentos adjuntos, historial de movimientos (bitácora), audiencias y pagos. Eliminación exclusiva del Administrador.
- **Documentos**: carga y descarga de archivos por expediente, clasificados por categoría. Eliminación exclusiva del Administrador.
- **Audiencias**: programación y seguimiento de resultados. Eliminación exclusiva del Administrador.
- **Citas**: agenda de reuniones con clientes. Eliminación exclusiva del Administrador.
- **Tareas**: asignación a uno o varios responsables; cada tarea solo es visible para el Administrador y sus responsables. Estados: Pendiente, En proceso, En revisión, Completada, Cancelada. Incluye comentarios/avances y ficha de detalle.
- **Pagos** (exclusivo del Administrador — nadie más del despacho ve cifras de dinero): honorarios pendientes y cobrados por expediente. El Administrador puede editar, eliminar pagos individuales, eliminar varios a la vez o vaciar el historial completo (con doble confirmación).
- **Reportes** (exclusivo del Administrador): casos activos, casos concluidos, ingresos por mes, pagos pendientes y actividad por abogado (exportable a PDF desde el navegador).
- **Búsqueda rápida**: localiza expedientes y clientes del propio despacho desde la barra superior.
- **Tutoriales y capacitación**: guías internas organizadas por categoría, con texto enriquecido, imágenes, PDFs, documentos y video embebido. Solo el Administrador puede crear/editar/eliminar; Abogado y Asistente solo consultan.
- **Enlaces jurídicos oficiales**: centro de accesos rápidos a portales gubernamentales y judiciales, organizados por categoría, con buscador, favoritos y enlaces obligatorios. Los enlaces siempre abren en una pestaña nueva.
- **Bitácora de auditoría**: registro de solo lectura, exclusivo del Administrador, de inicios/cierres de sesión y de toda creación, edición o eliminación en cualquier módulo del propio despacho, con usuario, fecha, hora, IP y descripción detallada.
- **Alertas inteligentes**: el sistema analiza automáticamente tareas por vencer o vencidas, tareas estancadas, usuarios con varias tareas vencidas, expedientes sin movimientos recientes, acumulación de pagos pendientes, y audiencias/citas próximas. El Administrador puede marcarlas como revisadas sin perder el historial.

## 8. Matriz de permisos por rol

| Acción                                              | Administrador | Abogado | Asistente |
|------------------------------------------------------|:---:|:---:|:---:|
| Crear/editar clientes, expedientes, audiencias, citas, tareas, documentos | ✅ | ✅ | ✅ |
| Eliminar clientes, expedientes, audiencias, citas, tareas, documentos | ✅ | ❌ | ❌ |
| Ver, crear, editar, eliminar y reiniciar Pagos | ✅ | ❌ | ❌ |
| Ver Reportes (incluye cifras de ingresos) | ✅ | ❌ | ❌ |
| Ver únicamente las tareas donde es responsable | — | ✅ | ✅ |
| Ver todas las tareas del despacho | ✅ | — | — |
| Consultar tutoriales y enlaces jurídicos | ✅ | ✅ | ✅ |
| Crear/editar/eliminar tutoriales y enlaces jurídicos | ✅ | ❌ | ❌ |
| Ver la bitácora de auditoría | ✅ | ❌ | ❌ |
| Ver y gestionar el centro de alertas | ✅ | ❌ | ❌ |
| Crear, editar rol y eliminar usuarios (de su propio despacho) | ✅ | ❌ | ❌ |

## 9. Notas técnicas

- Motor de base de datos: **SQLite** (archivo local, sin necesidad de instalar un servidor de base de datos aparte). Todos los despachos comparten el mismo archivo de base de datos, pero cada registro está etiquetado con su `despacho_id` y toda consulta del sistema filtra por ese campo — es el mecanismo que garantiza el aislamiento entre despachos.
- Autenticación de sesión con **Flask-Login**; las contraseñas se almacenan con hash (`werkzeug.security`), nunca en texto plano.
- El correo electrónico de cada usuario es único en **todo el sistema** (no solo dentro de su despacho); así el inicio de sesión no necesita preguntar "¿de qué despacho eres?", el sistema ya lo sabe por tu cuenta. Ten esto en cuenta si dos despachos distintos quisieran usar el mismo correo (no es posible en la versión actual).
- La bitácora de auditoría se alimenta automáticamente desde cada ruta relevante mediante la función `registrar_auditoria()` en `app.py`; no requiere configuración adicional.
- Las alertas se recalculan por despacho cada vez que su Administrador visita el panel principal o el módulo de Alertas, y se identifican con una clave única para evitar duplicados; al marcarlas como revisadas quedan en el historial en lugar de eliminarse.
- Antes de usarse en producción real, cambia `app.config["SECRET_KEY"]` en `app.py` por una clave segura y aleatoria, y desactiva `debug=True`.

## 10. Cómo generar el archivo .exe (Windows)

El proyecto ya está preparado para empaquetarse como ejecutable: detecta automáticamente si corre "congelado" (como .exe) y ajusta sus rutas — las plantillas y estilos van empaquetados dentro del .exe (solo lectura), mientras que la base de datos y los documentos subidos se guardan en una carpeta junto al .exe para que la información persista entre ejecuciones y actualizaciones.

### Pasos

1. Abre una terminal (CMD o PowerShell) dentro de la carpeta `legalsys`, en la misma máquina Windows donde vas a usar el sistema (PyInstaller genera ejecutables solo para el sistema operativo en el que se compila).
2. Ejecuta:

   ```bat
   build_windows.bat
   ```

   Este script instala las dependencias (incluye `pyinstaller` y `waitress`), limpia compilaciones anteriores y genera el ejecutable.
3. Al terminar, encontrarás el archivo en `dist\DespachoJuridico.exe`.
4. Copia ese único archivo `.exe` a la carpeta donde quieras usarlo de forma permanente, por ejemplo `C:\DespachoJuridico\`. **No lo dejes en `Descargas` ni en carpetas temporales**: la primera vez que lo ejecutes, junto a él se crearán las carpetas `instance\` (base de datos) y `uploads\` (documentos), que son tus datos reales del despacho.
5. Haz doble clic en `DespachoJuridico.exe`. Verás una ventana de consola indicando que el servidor está iniciando, y el navegador se abrirá solo en `http://127.0.0.1:5000`. **No cierres esa ventana de consola** mientras uses el sistema; ciérrala solo cuando termines de trabajar (eso apaga el servidor).

### Por qué el .exe se ve distinto a "python app.py"

En modo desarrollo (`python app.py`) Flask usa su servidor de depuración, con recarga automática y el mensaje `Debug mode: on`. Ese servidor **no está pensado para el uso diario**, así que el .exe usa en su lugar **Waitress**, un servidor de producción estable, sin recargador ni pantalla de depuración, y abre el navegador automáticamente por ti.

### Notas y solución de problemas

- **Antivirus / SmartScreen:** los ejecutables generados con PyInstaller no llevan firma digital, así que Windows Defender o tu antivirus pueden marcarlo como "app desconocida" la primera vez. Es un falso positivo común en este tipo de herramienta; agrégalo como excepción si confías en el origen.
- **"No se pudo crear la carpeta instance/uploads":** ocurre si ejecutas el .exe desde una carpeta protegida (como `Archivos de programa`). Muévelo a una carpeta normal, por ejemplo `C:\DespachoJuridico\`.
- **Quieres ocultar la ventana de consola:** una vez que confirmes que todo funciona, puedes volver a compilar cambiando `--console` por `--windowed` en `build_windows.bat`. Ten en cuenta que así no verás mensajes si algo falla al iniciar.
- **Icono personalizado:** agrega `--icon="ruta\a\tu_icono.ico"` a la línea de `pyinstaller` dentro de `build_windows.bat`.
- **Respaldo de datos:** haz copias periódicas de las carpetas `instance\` y `uploads\` que se generan junto al .exe — ahí vive toda la información real del despacho (ver sección de dudas frecuentes más abajo, o pide un script de respaldo automático).
- **Actualizar el sistema más adelante:** si mejoras el código y generas un nuevo `.exe`, solo reemplaza el archivo `.exe` viejo por el nuevo dentro de la misma carpeta; como los datos están en `instance\` y `uploads\` (no dentro del .exe), se conservan intactos.

## 11. Cómo publicarlo en un servidor gratuito para pruebas piloto

Para que varios despachos externos puedan probar el sistema desde sus propias computadoras (sin que tú instales nada en cada una), necesitas un hosting con **almacenamiento persistente** — es decir, que los archivos de la base de datos y los documentos subidos no se borren cada vez que el servidor se reinicia. Muchos planes "gratuitos" actuales (Render, Railway, Fly.io) usan almacenamiento efímero en su capa gratuita, lo cual borraría los datos de los despachos piloto sin previo aviso. Por eso, para esta etapa recomendamos **PythonAnywhere**, que sí ofrece almacenamiento persistente en su plan gratuito y no pide tarjeta de crédito.

**Limitaciones a tener presentes en el plan gratuito de PythonAnywhere:** el sitio queda en una dirección tipo `tuusuario.pythonanywhere.com` (no hay dominio propio), solo se permite una aplicación web, y el acceso saliente a internet está restringido a un listado de dominios — esto no afecta al sistema porque no se conecta a servicios externos. Es exactamente el tipo de límite razonable para una prueba piloto con pocos despachos.

### Pasos

1. Crea una cuenta gratuita en pythonanywhere.com (con tu correo, sin tarjeta).
2. Abre una **consola Bash** desde el dashboard y sube el proyecto. La forma más simple es comprimir la carpeta `legalsys` y subirla desde la pestaña **Files**, o clonarlo si lo tienes en GitHub:
   ```bash
   git clone https://github.com/tu-usuario/tu-repositorio.git legalsys
   ```
3. En esa misma consola Bash, instala las dependencias (PythonAnywhere ya trae Python instalado):
   ```bash
   cd legalsys
   pip install --user -r requirements.txt
   ```
4. Ve a la pestaña **Web** → **Add a new web app** → elige **Flask** y la versión de Python que coincida con la que usaste arriba.
5. Cuando te pida la ruta de tu archivo Flask, edita el **archivo WSGI** que te genera automáticamente (el enlace aparece en la misma pestaña Web) y reemplaza su contenido por:
   ```python
   import sys
   path = '/home/tuusuario/legalsys'
   if path not in sys.path:
       sys.path.insert(0, path)

   from app import app as application
   ```
   (cambia `tuusuario` por tu nombre de usuario real de PythonAnywhere).
6. En la pestaña Web, botón verde **Reload**. Tu sistema ya está publicado en `https://tuusuario.pythonanywhere.com`.
7. Comparte esa URL con los despachos piloto. Cada despacho debe entrar a `/registro` para crear su propia cuenta — sus datos quedarán completamente separados de los demás gracias al aislamiento por despacho explicado en la sección 8.

### Notas

- **HTTPS ya viene incluido** en el subdominio `pythonanywhere.com`, así que las contraseñas y datos viajan cifrados sin configuración extra.
- **Respaldo de datos:** desde la pestaña Files puedes descargar `legalsys/instance/despacho.db` y la carpeta `legalsys/uploads/` periódicamente como respaldo mientras dure el piloto.
- **Actualizar el código:** tras subir cambios (por ejemplo con `git pull` en la consola Bash), solo necesitas volver a presionar **Reload** en la pestaña Web — los datos de los despachos no se ven afectados porque viven en `instance/` y `uploads/`, fuera del código.
- **Cuando el piloto crezca:** si en algún momento tienes más despachos activos de los que el plan gratuito soporta cómodamente, PythonAnywhere y otros proveedores (Render, Railway, un VPS propio) ofrecen planes pagos desde unos pocos dólares al mes con más recursos y disco persistente garantizado — la migración es sencilla porque el proyecto no depende de nada específico de PythonAnywhere.
