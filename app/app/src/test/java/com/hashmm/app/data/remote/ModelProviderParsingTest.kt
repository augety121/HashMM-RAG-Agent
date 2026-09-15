package com.hashmm.app.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ModelProviderParsingTest {
    @Test
    fun parsesServerOwnedWireAndEndpointMetadata() {
        val items = parseModelProviders(
            """{"providers":[
              {"id":"openai","name":"OpenAI","base_url":"https://api.openai.com/v1",
               "wire_apis":["responses","chat_completions"],"default_wire_api":"chat_completions",
               "auth":"bearer","local":false,"endpoint_note":"","model_hints":[]},
              {"id":"ollama","name":"Ollama","base_url":"http://127.0.0.1:11434/v1",
               "wire_apis":["chat_completions"],"default_wire_api":"chat_completions",
               "auth":"optional","local":true,"endpoint_note":"本地","model_hints":[]}
            ]}"""
        )
        assertEquals(2, items.size)
        assertEquals(listOf("responses", "chat_completions"), items[0].wireApis)
        assertFalse(items[0].authOptional)
        assertTrue(items[1].authOptional)
        assertTrue(items[1].local)
    }

    @Test
    fun ignoresEntriesWithoutStableId() {
        val items = parseModelProviders("""{"providers":[{"name":"broken"}]}""")
        assertTrue(items.isEmpty())
    }
}
