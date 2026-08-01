# Jarvis Brasileiro com Upgrade

Assistente local em português para Windows, com entrada e saída por voz,
interface flutuante, modelos executados pelo Ollama e uma camada de segurança
para ações no computador.

## Recursos

- compreensão e respostas em português;
- ativação por tecla para evitar escuta contínua;
- voz local com Pocket TTS;
- transcrição local com NVIDIA Parakeet;
- interface flutuante com fundo transparente;
- memória local em SQLite;
- pesquisa na web quando a resposta local não for suficiente;
- abertura de programas instalados;
- criação, leitura, edição e busca de arquivos em pastas autorizadas;
- controle de volume e mídia;
- agente programador com projetos isolados;
- compilação e descompactação com validações de segurança;
- agente instalador limitado a um catálogo confiável do `winget`;
- confirmação obrigatória antes de exclusões e outras ações perigosas.
- aplicativo Android privado com voz, texto e reconexão automática;
- comandos remotos passam pelo mesmo controlador seguro usado no PC.

## Privacidade

Este repositório não inclui gravações de voz, bancos de memória, modelos,
credenciais, logs, caminhos de usuário ou configurações específicas de um
computador. Consulte [PRIVACY.md](PRIVACY.md) antes de publicar alterações.

Por padrão, a memória é armazenada somente no perfil local do usuário. Os
documentos não são indexados automaticamente.

## Requisitos

- Windows 10 ou 11;
- Python 3.11;
- Git;
- Ollama;
- microfone para entrada por voz.

Uma GPU compatível pode melhorar o desempenho, mas não é obrigatória para os
modelos leves.

## Instalação

```powershell
git clone https://github.com/<seu-usuario>/jarvis-brasileiro-com-upgrade.git
cd jarvis-brasileiro-com-upgrade
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Instale o Ollama e prepare os modelos:

```powershell
ollama pull qwen2.5:3b
ollama pull qwen2.5-coder:3b
ollama pull nomic-embed-text
ollama create jarvis:3b-fast -f jarvis_llm/Modelfile-fast.txt
```

O modelo de maior qualidade é opcional:

```powershell
ollama pull qwen3:4b
ollama create jarvis:4b-pt -f jarvis_llm/Modelfile-no-tools.txt
```

## Execução

Interface gráfica, voz e ativação pela barra de espaço:

```powershell
python jarvis.py --input voice --output voice --interface ui --push-to-talk on
```

Modo texto:

```powershell
python jarvis.py --input text --output text --interface cli
```

## Jarvis Remote para Android

O aplicativo nativo está em `android/JarvisRemote`. Ele se conecta fora da
rede local por WebSocket seguro, Tailscale Serve e um segundo pareamento HMAC
exclusivo entre o celular e o Jarvis.

No computador, inicie o gateway e gere o código do primeiro pareamento:

```powershell
.\venv\Scripts\python.exe jarvis_remote_gateway.py --pair
tailscale serve --bg 8766
```

Texto e voz em português usam o mesmo controlador restrito. O celular pode
pedir para abrir ou fechar programas, procurar e manipular arquivos nas pastas
autorizadas, controlar mídia e consultar o computador. Exclusões, instalações,
execução ou compilação de código e outras operações sensíveis exibem uma
confirmação no PC. Uma mensagem remota não consegue aprová-la.

O aplicativo reconecta ao ser aberto e repete a tentativa com espera progressiva
quando a rede oscila. Consulte [o guia completo](docs/ANDROID_REMOTE.md).

## Voz

A voz incorporada `rafael` é usada por padrão. Para usar uma amostra própria,
defina `JARVIS_VOICE_SAMPLE` somente no seu computador:

```powershell
$env:JARVIS_VOICE_SAMPLE = "C:\caminho-local\voz.wav"
```

Não adicione gravações de voz ao Git. Arquivos de áudio são ignorados pelo
`.gitignore`.

## Modelos configuráveis

Os nomes dos modelos podem ser alterados por variáveis de ambiente:

- `JARVIS_CHAT_MODEL`;
- `JARVIS_QUALITY_MODEL`;
- `JARVIS_CODE_MODEL`;
- `JARVIS_EMBED_MODEL`;
- `JARVIS_CONTEXT_WINDOW`;
- `JARVIS_RECENT_INTERACTIONS`.

## Segurança

O texto produzido pelo modelo não é executado diretamente como comando. Ações
locais passam por ferramentas determinísticas, limites de diretório e pedidos
de confirmação. Revise o código e mantenha o Windows atualizado antes de
permitir automações no computador.

O gateway remoto aceita somente `127.0.0.1` e deve ser exposto apenas pelo
Tailscale Serve. Não use Tailscale Funnel. Comandos remotos ficam em um registro
local e toda aprovação perigosa precisa ocorrer fisicamente no computador.

## Testes

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Para validar e gerar o APK Android:

```powershell
cd android\JarvisRemote
.\gradlew.bat testDebugUnitTest assembleDebug lintDebug
```

## Origem do projeto

Este trabalho deriva do projeto público
[tudormatei/jarvis-local](https://github.com/tudormatei/jarvis-local) e inclui
adaptações para português, interface flutuante, memória local, ferramentas do
computador e camadas adicionais de segurança. O projeto original não fornece
um arquivo de licença; verifique as condições aplicáveis antes de redistribuir
ou usar comercialmente.

Os modelos e componentes de terceiros mantêm suas próprias licenças e termos.
