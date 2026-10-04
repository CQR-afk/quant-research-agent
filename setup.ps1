$ErrorActionPreference = "Stop"

if (Get-Command uv -ErrorAction SilentlyContinue) {
    $env:UV_CACHE_DIR = Join-Path $PSScriptRoot ".uv-cache"
    if (-not (Test-Path .\.venv\Scripts\python.exe)) {
        uv venv .venv --python 3
        if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment with uv." }
    }
    uv pip install --python .\.venv\Scripts\python.exe -e '.[quant-stack]'
    if ($LASTEXITCODE -ne 0) { throw "Could not install project dependencies. Check network access and Python compatibility." }
} else {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw "Python launcher 'py' was not found. Install Python 3.11+ and enable the launcher."
    }
    if (-not (Test-Path .\.venv\Scripts\python.exe)) {
        py -3 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment. Install Python 3.11+ and pip." }
    }
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip." }
    & .\.venv\Scripts\python.exe -m pip install -e '.[quant-stack]'
    if ($LASTEXITCODE -ne 0) { throw "Could not install project dependencies. Check network access and Python compatibility." }
}

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}

Write-Host "Environment ready. Edit .env before using the optional GPT news command."

