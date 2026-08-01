package br.com.jarvis.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class JarvisWebSocketTest {
    @Test
    fun proofMatchesPythonGatewayVector() {
        val secret = ByteArray(32) { it.toByte() }
        val proof = JarvisWebSocket.proof(
            secret,
            "12345678-1234-1234-1234-123456789abc",
            "challenge-value",
        )

        assertEquals("zze_q4BF6faFdTTkJZgsXxJ8FXDVWdiPuyc8BCJG_w8", proof)
    }

    @Test
    fun serverUrlMustUseTailscaleHttps() {
        assertEquals(
            "wss://jarvis.example.ts.net",
            JarvisWebSocket.normalizeServerUrl("https://jarvis.example.ts.net/"),
        )
        assertThrows(IllegalArgumentException::class.java) {
            JarvisWebSocket.normalizeServerUrl("ws://192.168.1.20:8766")
        }
        assertThrows(IllegalArgumentException::class.java) {
            JarvisWebSocket.normalizeServerUrl("wss://public.example.com")
        }
    }
}
