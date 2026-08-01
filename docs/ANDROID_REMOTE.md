# Jarvis Remote para Android

O Jarvis Remote é um aplicativo Android nativo em Kotlin para conversar e
enviar comandos seguros ao Jarvis quando o celular está fora da rede doméstica.

## Arquitetura privada

```text
Android nativo
  -> WebSocket seguro (WSS)
  -> Tailscale no celular
  -> túnel WireGuard da tailnet
  -> Tailscale Serve no computador
  -> 127.0.0.1:8766
  -> gateway autenticado
  -> controlador seguro de ações ou Ollama local
```

O gateway nunca escuta no Wi-Fi ou na Ethernet. Ele aceita somente endereços de
loopback e deve ser publicado privadamente com o Tailscale Serve. Não use
Tailscale Funnel, porque Funnel tornaria o endereço público.

Além da identidade da tailnet, existe um segundo pareamento no próprio Jarvis:

- um código temporário de oito dígitos, válido por cinco minutos;
- no máximo cinco tentativas;
- somente um `client_id` Android registrado;
- segredo aleatório de 256 bits;
- desafio HMAC-SHA256 em cada nova conexão;
- segredo protegido pelo DPAPI no Windows;
- segredo protegido por AES-GCM e Android Keystore no celular.

Texto e voz usam exatamente o mesmo controlador de ações do Jarvis no PC. O
gateway não transforma texto em comandos de terminal: ele aceita somente as
operações tipadas e restritas que o controlador local reconhece. Operações
sensíveis criam uma janela de confirmação no computador. A confirmação não
pode ser enviada pelo celular.

Comandos disponíveis incluem:

- abrir e fechar aplicativos registrados pelo Windows;
- procurar, abrir, ler, criar e editar arquivos nas pastas autorizadas;
- controlar volume, reprodução e música;
- consultar informações do computador;
- usar os fluxos restritos de programação, compilação, descompactação e
  instalação.

Apagar ou substituir arquivos, fechar certos programas, executar ou compilar
código, descompactar e instalar exigem aprovação local. Cada comando remoto é
registrado apenas no PC em `%LOCALAPPDATA%\Jarvis\remote-actions.jsonl`.

## 1. Instalar e conectar o Tailscale

Instale o Tailscale no computador Windows:

<https://tailscale.com/download/windows>

Instale o Tailscale no Android pela Play Store e entre na mesma tailnet:

<https://tailscale.com/docs/install/android>

No Android, aceite a criação da conexão VPN. Essa autorização precisa ser feita
manualmente pelo usuário.

## 2. Iniciar o gateway no computador

Abra um PowerShell na pasta do projeto:

```powershell
.\venv\Scripts\python.exe jarvis_remote_gateway.py --pair
```

O terminal mostrará um código temporário. Mantenha essa janela aberta.

Em outro PowerShell, configure o proxy privado HTTPS:

```powershell
tailscale serve --bg 8766
tailscale serve status
```

Na primeira utilização, o Tailscale pode abrir uma página para habilitar
certificados HTTPS na tailnet. Essa autorização também deve ser feita
manualmente. Copie o endereço completo terminado em `.ts.net` mostrado pelo
comando de status.

## 3. Compilar o aplicativo Android

O projeto Android está em:

```text
android/JarvisRemote
```

Instale o Android Studio pela página oficial:

<https://developer.android.com/studio>

Depois:

1. abra a pasta `android/JarvisRemote` no Android Studio;
2. aguarde a sincronização do Gradle;
3. instale o SDK 36 quando o Android Studio solicitar;
4. aceite manualmente a licença do SDK;
5. use **Build > Build APK(s)**;
6. instale o APK no seu aparelho Android.

Também é possível compilar pelo terminal depois de instalar e licenciar o SDK:

```powershell
cd android\JarvisRemote
.\gradlew.bat assembleDebug
```

O APK de desenvolvimento será criado em:

```text
app\build\outputs\apk\debug\app-debug.apk
```

## 4. Parear o celular

1. mantenha o Tailscale conectado no Android;
2. abra o Jarvis Remote;
3. informe o endereço `https://nome-do-pc.nome-da-tailnet.ts.net`;
4. informe o código de oito dígitos mostrado no computador;
5. toque em **Conectar / parear**.

O código é usado uma única vez. Nas conexões seguintes o aplicativo usa o
segredo protegido pelo Android Keystore. Ele também reconecta automaticamente
ao abrir e, se a rede cair, repete a conexão com intervalos progressivos de até
30 segundos.

## 5. Usar texto e voz

- Digite um pedido e toque em **Enviar**; ou
- toque em **Falar**, dite em português e confirme o reconhecimento do Android.

O texto reconhecido é enviado pelo mesmo canal autenticado do campo de texto.
O reconhecimento de voz é fornecido pelo serviço instalado no Android e pode
usar a internet, conforme a configuração do aparelho. A interpretação e a
execução do comando continuam no PC.

## 6. Usar fora da rede local

Para conversar usando 4G, 5G ou outro Wi-Fi:

- o computador precisa estar ligado;
- Ollama precisa estar disponível;
- o gateway precisa estar em execução;
- Tailscale precisa estar conectado nos dois dispositivos;
- o Tailscale Serve precisa continuar ativo.

## Trocar de celular

No computador:

```powershell
.\venv\Scripts\python.exe jarvis_remote_gateway.py --replace-pairing
```

Digite `RECONFIGURAR` quando solicitado. Isso só substitui o aparelho depois
que o novo celular concluir o pareamento.

No aparelho antigo, use **Trocar celular** para apagar a credencial local.

## Desativar o acesso remoto

Feche o gateway e execute:

```powershell
tailscale serve off
```

## Limitações

- um único celular pareado;
- não há publicação na Play Store.

## Referências técnicas

- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
- [Comando `tailscale serve`](https://tailscale.com/docs/reference/tailscale-cli/serve)
- [Android Keystore](https://developer.android.com/privacy-and-security/keystore)
- [Compilar Android pelo terminal](https://developer.android.com/build/building-cmdline)
