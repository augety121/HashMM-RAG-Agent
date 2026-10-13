package com.hashmm.app.ui.chat

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

class GraphSourceParsingTest {
    @Test
    fun graphEvidenceMethodSurvivesMessageParsing() {
        val payload = Json.parseToJsonElement(
            """[{"id":2,"filename":"evidence.pdf","page":3,"text":"original","method":"graph_evidence"}]""",
        )

        val source = parseSources(payload).single()

        assertEquals(2, source.id)
        assertEquals("evidence.pdf", source.filename)
        assertEquals(3, source.page)
        assertEquals("graph_evidence", source.method)
    }

    @Test
    fun olderMessagesWithoutMethodRemainCompatible() {
        val payload = Json.parseToJsonElement(
            """[{"filename":"legacy.pdf","text":"legacy"}]""",
        )

        assertEquals("", parseSources(payload).single().method)
    }
}
