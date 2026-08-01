package br.com.jarvis.remote

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.speech.RecognizerIntent
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

class MainActivity : Activity(), JarvisWebSocket.Listener {
    private lateinit var store: SecureStore
    private lateinit var connection: JarvisWebSocket
    private lateinit var statusText: TextView
    private lateinit var serverInput: EditText
    private lateinit var pairingCodeInput: EditText
    private lateinit var messageInput: EditText
    private lateinit var sendButton: Button
    private lateinit var chatScroll: ScrollView
    private lateinit var chatMessages: LinearLayout
    private val responseViews = mutableMapOf<String, TextView>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        configureKeyboardInsets()

        store = SecureStore(this)
        connection = JarvisWebSocket(store, this)
        statusText = findViewById(R.id.statusText)
        serverInput = findViewById(R.id.serverInput)
        pairingCodeInput = findViewById(R.id.pairingCodeInput)
        messageInput = findViewById(R.id.messageInput)
        sendButton = findViewById(R.id.sendButton)
        chatScroll = findViewById(R.id.chatScroll)
        chatMessages = findViewById(R.id.chatMessages)

        serverInput.setText(store.serverUrl)
        updatePairingVisibility()

        findViewById<Button>(R.id.connectButton).setOnClickListener {
            try {
                sendButton.isEnabled = false
                connection.connect(serverInput.text.toString(), pairingCodeInput.text.toString())
            } catch (exception: Exception) {
                showError(exception.message ?: "Não foi possível conectar.")
            }
        }
        findViewById<Button>(R.id.disconnectButton).setOnClickListener {
            connection.disconnect()
            sendButton.isEnabled = false
        }
        findViewById<Button>(R.id.clearPairingButton).setOnClickListener {
            connection.disconnect()
            store.clearPairing()
            sendButton.isEnabled = false
            updatePairingVisibility()
            statusText.text = "Pareamento local apagado. Reconfigure também no computador."
        }
        findViewById<Button>(R.id.voiceButton).setOnClickListener { startVoiceInput() }
        sendButton.setOnClickListener { sendCurrentMessage() }

        if (store.loadSecret() != null && store.serverUrl.isNotBlank()) {
            serverInput.post {
                try {
                    connection.connect(store.serverUrl, "")
                } catch (exception: Exception) {
                    showError(exception.message ?: "Não foi possível reconectar.")
                }
            }
        }
    }

    private fun configureKeyboardInsets() {
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return

        window.setDecorFitsSystemWindows(false)
        val root = findViewById<View>(R.id.rootLayout)
        val baseLeft = root.paddingLeft
        val baseTop = root.paddingTop
        val baseRight = root.paddingRight
        val baseBottom = root.paddingBottom

        root.setOnApplyWindowInsetsListener { view, windowInsets ->
            val systemBars = windowInsets.getInsets(WindowInsets.Type.systemBars())
            val keyboard = windowInsets.getInsets(WindowInsets.Type.ime())
            view.setPadding(
                baseLeft + systemBars.left,
                baseTop + systemBars.top,
                baseRight + systemBars.right,
                baseBottom + maxOf(systemBars.bottom, keyboard.bottom),
            )
            windowInsets
        }
        root.requestApplyInsets()
    }

    override fun onDestroy() {
        connection.disconnect()
        super.onDestroy()
    }

    private fun sendCurrentMessage() {
        val text = messageInput.text.toString().trim()
        if (text.isEmpty()) return
        try {
            val messageId = connection.sendMessage(text)
            addMessage("Você", text, Gravity.END, Color.rgb(22, 76, 94))
            responseViews[messageId] = addMessage(
                "Jarvis",
                "",
                Gravity.START,
                Color.rgb(10, 38, 56),
            )
            messageInput.text.clear()
        } catch (exception: Exception) {
            showError(exception.message ?: "Não foi possível enviar.")
        }
    }

    private fun startVoiceInput() {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
            )
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "pt-BR")
            putExtra(RecognizerIntent.EXTRA_PROMPT, getString(R.string.voice_prompt))
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        }
        try {
            @Suppress("DEPRECATION")
            startActivityForResult(intent, REQUEST_SPEECH)
        } catch (_: ActivityNotFoundException) {
            showError(getString(R.string.voice_unavailable))
        }
    }

    @Deprecated("Deprecated in Android")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQUEST_SPEECH || resultCode != RESULT_OK) return
        val result = data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
            ?.firstOrNull()
            ?.trim()
            .orEmpty()
        if (result.isEmpty()) return
        messageInput.setText(result)
        messageInput.setSelection(result.length)
        if (sendButton.isEnabled) sendCurrentMessage()
    }

    private fun addMessage(
        author: String,
        text: String,
        gravity: Int,
        backgroundColor: Int,
    ): TextView {
        val view = TextView(this).apply {
            this.text = if (text.isEmpty()) "$author: " else "$author: $text"
            setTextColor(Color.rgb(231, 249, 255))
            setBackgroundColor(backgroundColor)
            setPadding(16, 12, 16, 12)
        }
        val params = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply {
            this.gravity = gravity
            setMargins(0, 6, 0, 6)
        }
        chatMessages.addView(view, params)
        chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
        return view
    }

    private fun updatePairingVisibility() {
        pairingCodeInput.visibility = if (store.loadSecret() == null) View.VISIBLE else View.GONE
    }

    override fun onStatus(status: String) = runOnUiThread {
        statusText.text = status
        if (!status.startsWith("Conectado ao") &&
            !status.startsWith("Conectado somente")) {
            sendButton.isEnabled = false
        }
    }

    override fun onReady() = runOnUiThread {
        sendButton.isEnabled = true
        pairingCodeInput.text.clear()
        updatePairingVisibility()
    }

    override fun onChunk(messageId: String, text: String) = runOnUiThread {
        val view = responseViews[messageId] ?: return@runOnUiThread
        val current = view.text.toString().removePrefix("Jarvis: ")
        view.text = "Jarvis: " + listOf(current, text)
            .filter { it.isNotBlank() }
            .joinToString(" ")
        chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
    }

    override fun onDone(messageId: String) = runOnUiThread {
        responseViews.remove(messageId)
    }

    override fun onError(message: String) = runOnUiThread {
        showError(message)
    }

    private fun showError(message: String) {
        statusText.text = message
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
    }

    companion object {
        private const val REQUEST_SPEECH = 1001
    }
}
