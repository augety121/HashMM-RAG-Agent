package com.hashmm.app

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

class ReleasePackagingContractTest {
    @Test
    fun `tink compile-only annotations stay explicitly covered for r8`() {
        val rules = sequenceOf(File("proguard-rules.pro"), File("app/proguard-rules.pro"))
            .firstOrNull { it.isFile }
            ?.readText()
            ?: error("proguard-rules.pro not found")
        listOf(
            "CanIgnoreReturnValue",
            "CheckReturnValue",
            "Immutable",
            "RestrictedApi",
        ).forEach { annotation ->
            assertTrue(
                "missing exact R8 rule for $annotation",
                rules.contains("-dontwarn com.google.errorprone.annotations.$annotation"),
            )
        }
        assertTrue(
            "do not hide unrelated missing Error Prone classes",
            !rules.contains("-dontwarn com.google.errorprone.annotations.**"),
        )
    }
}
