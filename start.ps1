# Lance l'interface YouTube Automate.
# Clic droit sur ce fichier -> "Executer avec PowerShell"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path "client_secret.json")) {
    Write-Host ""
    Write-Host "  Fichier client_secret.json introuvable." -ForegroundColor Yellow
    Write-Host "  Voir l'etape 2 du README.md." -ForegroundColor Yellow
    Write-Host ""
}

python app.py

Write-Host ""
Read-Host "Appuyez sur Entree pour fermer"
