package com.hashmm.app.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PersonalModelParsingTest {
    @Test
    fun parsesOwnerScopedEnvelopeAndPreferredModel() {
        val models = parseMyModels(
            """{"models":[{"id":"m1","name":"Mine","provider":"openai","model_name":"gpt-x","wire_api":"responses","is_preferred":true}],"preferred":"m1"}""",
        )

        assertEquals(1, models.size)
        assertEquals("m1", models.single().id)
        assertEquals("responses", models.single().wireApi)
        assertTrue(models.single().isDefault)
    }
}
