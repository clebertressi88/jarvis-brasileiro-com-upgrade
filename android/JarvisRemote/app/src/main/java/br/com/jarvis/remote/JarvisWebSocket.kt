package br.com.jarvis.remote

import android.os.Handler
import android.os.Looper
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.net.URI
import java.nio.charset.StandardCharsets
import java.util.Base64
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlin.math.min
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

class JarvisWebSocket(
    private val store: SecureStore,
    private val listener: Listener,
) : WebSocketListener() {
    interface Listener {
        fun onStatus(status: String)
        fun onReady()
        fun onChunk(messageId: String, text: String)
        fun onDone(messageId: String)
        fun onError(message: String)
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()
    private val mainHandler = Handler(Looper.getMainLooper())

    @Volatile
    private var socket: WebSocket? = null

    @Volatile
    private var ready = false
    private var pairingCode: String = ""
    private var serverUrl: String = ""
    private var shouldReconnect = false
    private var reconnectAttempts = 0
    private var reconnectRunnable: Runnable? = null

    fun connect(serverInput: String, code: String) {
        val normalizedUrl = normalizeServerUrl(serverInput)
        val normalizedCode = code.trim()
        if (store.loadSecret() == null && !normalizedCode.matches(Regex("^[0-9]{8}$"))) {
            throw IllegalArgumentException("Digite o código de pareamento com 8 dígitos.")
        }
        cancelReconnect()
        shouldReconnect = true
        reconnectAttempts = 0
        serverUrl = normalizedUrl
        pairingCode = normalizedCode
        store.serverUrl = normalizedUrl
        val previous = socket
        socket = null
        ready = false
        previous?.cancel()
        openSocket()
    }

    private fun openSocket() {
        if (!shouldReconnect || serverUrl.isBlank()) return
        listener.onStatus("Conectando com segurança...")
        socket = client.newWebSocket(Request.Builder().url(serverUrl).build(), this)
    }

    fun disconnect() {
        shouldReconnect = false
        cancelReconnect()
        val activeSocket = socket
        socket = null
        ready = false
        activeSocket?.close(1000, "client disconnect")
        listener.onStatus("Desconectado")
    }

    fun sendMessage(text: String): String {
        val trimmed = text.trim()
        require(trimmed.isNotEmpty()) { "Mensagem vazia" }
        require(trimmed.length <= 4_000) { "Mensagem muito longa" }
        val messageId = UUID.randomUUID().toString()
        val activeSocket = socket
        val sent = ready && activeSocket?.send(
            JSONObject()
                .put("type", "message")
                .put("id", messageId)
                .put("text", trimmed)
                .toString(),
        ) == true
        if (!sent) throw IllegalStateException("A conexão não está pronta.")
        return messageId
    }

    override fun onOpen(webSocket: WebSocket, response: Response) {
        if (socket !== webSocket) {
            webSocket.close(1000, "stale connection")
            return
        }
        listener.onStatus("Canal privado aberto. Autenticando...")
    }

    override fun onMessage(webSocket: WebSocket, text: String) {
        if (socket !== webSocket) return
        try {
            val payload = JSONObject(text)
            when (payload.optString("type")) {
                "challenge" -> authenticate(webSocket, payload.getString("nonce"))
                "paired" -> {
                    val secret = decodeBase64Url(payload.getString("secret"))
                    require(secret.size == 32) { "Segredo recebido é inválido" }
                    store.saveSecret(secret)
                    listener.onStatus("Celular pareado com este Jarvis.")
                }
                "ready" -> {
                    ready = true
                    reconnectAttempts = 0
                    cancelReconnect()
                    val capabilities = payload.optJSONArray("capabilities")
                    val computerActions = capabilities != null &&
                        (0 until capabilities.length()).any {
                            capabilities.optString(it) == "computer_actions"
                        }
                    listener.onStatus(
                        if (computerActions) {
                            "Conectado ao Jarvis e aos comandos autorizados."
                        } else {
                            "Conectado somente ao seu Jarvis."
                        },
                    )
                    listener.onReady()
                }
                "chunk" -> listener.onChunk(
                    payload.getString("id"),
                    payload.getString("text"),
                )
                "done" -> listener.onDone(payload.getString("id"))
                "error" -> {
                    val message = payload.optString("message", "Erro remoto")
                    if (payload.isNull("id") && isAuthenticationError(message)) {
                        shouldReconnect = false
                        cancelReconnect()
                    }
                    listener.onError(message)
                }
                else -> listener.onError("Resposta desconhecida do gateway.")
            }
        } catch (exception: Exception) {
            listener.onError("Resposta inválida do gateway: ${exception.message}")
        }
    }

    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
        webSocket.close(code, reason)
    }

    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
        if (socket === webSocket) {
            socket = null
            ready = false
            if (code == 1008) {
                shouldReconnect = false
                listener.onStatus("Autenticação recusada. Verifique o pareamento.")
            } else {
                scheduleReconnect()
            }
        }
    }

    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
        if (socket === webSocket) {
            socket = null
            ready = false
            if (response?.code == 401 || response?.code == 403) {
                shouldReconnect = false
                listener.onError("Autenticação recusada pelo servidor.")
                listener.onStatus("Desconectado")
            } else {
                scheduleReconnect()
            }
        }
    }

    private fun scheduleReconnect() {
        if (!shouldReconnect) {
            listener.onStatus("Desconectado")
            return
        }
        cancelReconnect()
        val exponent = min(reconnectAttempts, 5)
        val delayMillis = min(30_000L, 1_000L shl exponent)
        reconnectAttempts += 1
        val seconds = delayMillis / 1_000L
        listener.onStatus("Conexão perdida. Nova tentativa em ${seconds}s...")
        reconnectRunnable = Runnable {
            reconnectRunnable = null
            openSocket()
        }.also { mainHandler.postDelayed(it, delayMillis) }
    }

    private fun cancelReconnect() {
        reconnectRunnable?.let(mainHandler::removeCallbacks)
        reconnectRunnable = null
    }

    private fun isAuthenticationError(message: String): Boolean {
        val normalized = message.lowercase()
        return normalized.contains("autentica") || normalized.contains("pareamento")
    }

    private fun authenticate(webSocket: WebSocket, challenge: String) {
        val secret = store.loadSecret()
        val payload = if (secret == null) {
            JSONObject()
                .put("type", "pair")
                .put("client_id", store.clientId)
                .put("code", pairingCode)
        } else {
            JSONObject()
                .put("type", "auth")
                .put("client_id", store.clientId)
                .put("proof", proof(secret, store.clientId, challenge))
        }
        webSocket.send(payload.toString())
    }

    companion object {
        private val proofPrefix =
            "jarvis-remote-v1".toByteArray(StandardCharsets.UTF_8) + byteArrayOf(0)

        fun normalizeServerUrl(input: String): String {
            val normalized = when {
                input.trim().startsWith("https://", ignoreCase = true) ->
                    "wss://" + input.trim().substringAfter("://")
                else -> input.trim()
            }.trimEnd('/')
            val uri = URI(normalized)
            require(uri.scheme.equals("wss", ignoreCase = true)) {
                "Use somente o endereço HTTPS/WSS fornecido pelo Tailscale Serve."
            }
            require(uri.host?.endsWith(".ts.net", ignoreCase = true) == true) {
                "O endereço precisa terminar em .ts.net."
            }
            require(uri.userInfo == null && uri.query == null && uri.fragment == null) {
                "Endereço do servidor inválido."
            }
            return normalized
        }

        fun proof(secret: ByteArray, clientId: String, challenge: String): String {
            val data = proofPrefix +
                clientId.toByteArray(StandardCharsets.UTF_8) + byteArrayOf(0) +
                challenge.toByteArray(StandardCharsets.US_ASCII)
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(secret, "HmacSHA256"))
            return Base64.getUrlEncoder()
                .withoutPadding()
                .encodeToString(mac.doFinal(data))
        }

        private fun decodeBase64Url(value: String): ByteArray =
            Base64.getUrlDecoder().decode(value)
    }
}
