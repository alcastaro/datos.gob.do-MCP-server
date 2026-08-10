# Instalador de datos.gob.do MCP para Claude Desktop en Windows.
#
#   irm https://raw.githubusercontent.com/alcastaro/datos.gob.do-MCP-server/main/scripts/instalar-windows.ps1 | iex
#
# No pide permisos de administrador y no toca nada del sistema: instala uv en
# la carpeta del usuario y añade una entrada al fichero de configuración de
# Claude Desktop, respaldando el que hubiera.

$ErrorActionPreference = 'Stop'

function Paso($n, $texto) { Write-Host "`n[$n/4] $texto" -ForegroundColor Cyan }
function Bien($texto)     { Write-Host "      $texto" -ForegroundColor Green }
function Aviso($texto)    { Write-Host "      $texto" -ForegroundColor Yellow }

Write-Host @"

  Datos Abiertos de la Republica Dominicana
  Servidor MCP para Claude Desktop

"@ -ForegroundColor White

# ---------------------------------------------------------------- 1. Claude
Paso 1 "Comprobando Claude Desktop..."
$appdataClaude = Join-Path $env:APPDATA "Claude"
$claudeInstalado = (Test-Path $appdataClaude) -or
                   (Get-Command "claude" -ErrorAction SilentlyContinue) -or
                   (Test-Path (Join-Path $env:LOCALAPPDATA "AnthropicClaude"))
if (-not $claudeInstalado) {
    Write-Host @"

  No encuentro Claude Desktop en este equipo.

  Instalalo primero desde  https://claude.ai/download
  y vuelve a ejecutar esta linea.

"@ -ForegroundColor Red
    return
}
Bien "encontrado"

# -------------------------------------------------------------------- 2. uv
# uv trae su propio Python, asi que no hay que instalar Python aparte.
Paso 2 "Instalando el gestor de paquetes (uv)..."
$uvx = Join-Path $env:USERPROFILE ".local\bin\uvx.exe"
if (Test-Path $uvx) {
    Bien "ya estaba instalado"
} else {
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Write-Host "`n  No se pudo descargar uv. Revisa tu conexion e intentalo de nuevo.`n" -ForegroundColor Red
        return
    }
    if (-not (Test-Path $uvx)) {
        $encontrado = (Get-Command uvx -ErrorAction SilentlyContinue).Source
        if ($encontrado) { $uvx = $encontrado }
        else {
            Write-Host "`n  uv se instalo pero no lo encuentro. Cierra PowerShell, abrelo de nuevo y repite.`n" -ForegroundColor Red
            return
        }
    }
    Bien "instalado"
}

# --------------------------------------------------------------- 3. servidor
Paso 3 "Descargando el servidor de datos..."
try {
    & $uvx --from dominican-open-data-mcp python -c "import datosgobdo_mcp as m; print('      version ' + m.__version__)"
} catch {
    Write-Host "`n  No se pudo descargar el servidor desde PyPI. Revisa tu conexion.`n" -ForegroundColor Red
    return
}

# ------------------------------------------------------------------ 4. config
# La ruta absoluta a uvx.exe no es opcional: Claude Desktop no hereda el PATH
# del shell, asi que "uvx" a secas no se resuelve.
Paso 4 "Configurando Claude Desktop..."
$cfg = Join-Path $appdataClaude "claude_desktop_config.json"
New-Item -ItemType Directory -Force -Path $appdataClaude | Out-Null

if (Test-Path $cfg) {
    $bak = "$cfg.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Copy-Item $cfg $bak
    Bien "respaldo de tu configuracion anterior en $(Split-Path $bak -Leaf)"
    try {
        $conf = Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Aviso "el fichero de configuracion no era JSON valido; se parte de uno nuevo"
        $conf = [pscustomobject]@{}
    }
} else {
    $conf = [pscustomobject]@{}
}

if (-not $conf.PSObject.Properties.Name.Contains('mcpServers')) {
    $conf | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{}) -Force
}
$conf.mcpServers | Add-Member -NotePropertyName datosgobdo -NotePropertyValue (
    [pscustomobject]@{ command = $uvx; args = @("dominican-open-data-mcp") }
) -Force

# UTF-8 sin BOM: un BOM delante del JSON rompe algunos lectores.
[System.IO.File]::WriteAllText($cfg, ($conf | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding $false))
Bien "listo"

Write-Host @"

  ---------------------------------------------------------------
   FALTA UN PASO, Y HAY QUE HACERLO BIEN:

   Cierra Claude Desktop desde el ICONO DE LA BANDEJA
   (abajo a la derecha, junto al reloj) -> Salir.

   Cerrar la ventana con la X NO basta.

   Al volver a abrirlo, prueba a preguntar:

     Que datos publica el portal dominicano sobre nominas publicas?

  ---------------------------------------------------------------

"@ -ForegroundColor Yellow
