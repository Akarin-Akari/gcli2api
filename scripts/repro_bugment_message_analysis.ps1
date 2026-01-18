$ErrorActionPreference = "Stop"

$baseUrl = "http://127.0.0.1:7861/gateway/chat-stream"
$apiKey = "Bearer sk-REPLACE_ME"

$analysisPrompt = @"
### ENTER MESSAGE ANALYSIS MODE
You MUST return ONLY JSON.
Return JSON with keys: explanation, worthRemembering, content
"@

$body = @{
  model = "claude-sonnet-4-5-thinking"
  mode = "CHAT"
  message = $analysisPrompt
  chat_history = @()
  conversation_id = "repro-analysis"
} | ConvertTo-Json -Depth 20

Write-Host "POST $baseUrl"
Write-Host "Body:" $body

Invoke-WebRequest `
  -Method Post `
  -Uri $baseUrl `
  -Headers @{ Authorization = $apiKey; "Content-Type" = "application/json"; "User-Agent" = "bugment-repro/1.0" } `
  -Body $body `
  -TimeoutSec 60 `
| Select-Object -ExpandProperty Content

