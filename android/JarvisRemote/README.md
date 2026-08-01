# Jarvis Remote Android

Aplicativo Android nativo em Kotlin para o gateway privado do Jarvis.

- pacote: `br.com.jarvis.remote`;
- Android mínimo: 8.0 / API 26;
- Android alvo: API 36;
- transporte: WebSocket seguro (`wss://`) via Tailscale Serve;
- autenticação: pareamento único e HMAC-SHA256;
- segredo local: AES-GCM com chave no Android Keystore;
- tráfego em texto claro: desativado no manifesto.
- entrada por texto ou reconhecimento de voz em português;
- reconexão automática com espera progressiva;
- comandos locais restritos, com confirmação de risco somente no PC.

Consulte [a documentação completa](../../docs/ANDROID_REMOTE.md).

## Build

Após instalar o Android SDK 36 e aceitar manualmente a licença:

```powershell
.\gradlew.bat assembleDebug
```
