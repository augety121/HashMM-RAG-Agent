package com.hashmm.app.data.remote

import org.junit.Assert.assertEquals
import org.junit.Test

class CanvasRepositoryTest {
    @Test
    fun relativeArtifactResolvesInsideBackendOrigin() {
        assertEquals(
            "https://example.com:9443/api/conversations/c1/download/a.html",
            resolveCanvasUrl(
                "https://example.com:9443",
                "/api/conversations/c1/download/a.html",
            ),
        )
    }

    @Test
    fun sameOriginAbsoluteArtifactIsAccepted() {
        assertEquals(
            "https://example.com/a.html",
            resolveCanvasUrl("https://example.com", "https://example.com/a.html"),
        )
    }

    @Test
    fun offOriginOrPortChangedArtifactIsRejectedBeforeNativeBridgeLoads() {
        assertEquals("", resolveCanvasUrl("https://example.com", "https://evil.example/a.html"))
        assertEquals("", resolveCanvasUrl("https://example.com", "https://example.com:8443/a.html"))
        assertEquals("", resolveCanvasUrl("https://example.com", "http://example.com/a.html"))
    }
}
