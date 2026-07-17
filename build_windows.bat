@echo off
setlocal

echo ================================================================
echo  Sistema de Gestion Integral para Despachos Juridicos
echo  Generando ejecutable (.exe) con PyInstaller
echo ================================================================
echo.

REM 1) Instala las dependencias del proyecto (incluye PyInstaller y Waitress)
echo [1/3] Instalando dependencias...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: No se pudieron instalar las dependencias. Verifica que Python
    echo y pip esten instalados y disponibles en la terminal.
    pause
    exit /b 1
)

REM 2) Limpia compilaciones anteriores
echo.
echo [2/3] Limpiando compilaciones anteriores...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist DespachoJuridico.spec del /q DespachoJuridico.spec

REM 3) Genera el ejecutable
echo.
echo [3/3] Compilando el ejecutable (esto puede tardar 1-3 minutos)...
pyinstaller --noconfirm --onefile --console ^
  --name "DespachoJuridico" ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --hidden-import "flask_login" ^
  --hidden-import "flask_sqlalchemy" ^
  --hidden-import "sqlalchemy.dialects.sqlite" ^
  --hidden-import "waitress" ^
  app.py

if errorlevel 1 (
    echo.
    echo ERROR: La compilacion fallo. Revisa el mensaje de arriba.
    pause
    exit /b 1
)

echo.
echo ================================================================
echo  LISTO. Tu ejecutable esta en:  dist\DespachoJuridico.exe
echo.
echo  IMPORTANTE:
echo  - Copia ese .exe a la carpeta donde quieras usar el sistema
echo    (por ejemplo C:\DespachoJuridico\). La primera vez que lo
echo    ejecutes, creara ahi mismo las carpetas "instance" (base de
echo    datos) y "uploads" (documentos). Esa carpeta es tu respaldo:
echo    cuidala y hazle copia de seguridad periodicamente.
echo  - No lo ejecutes desde "Program Files" o carpetas protegidas
echo    por Windows, podria no tener permiso para crear esas carpetas.
echo  - La primera vez que Windows Defender / tu antivirus lo vea,
echo    puede marcarlo como sospechoso por ser un .exe nuevo sin firma
echo    digital (es normal en ejecutables generados con PyInstaller).
echo    Agregalo como excepcion si confias en el origen del archivo.
echo ================================================================
pause
