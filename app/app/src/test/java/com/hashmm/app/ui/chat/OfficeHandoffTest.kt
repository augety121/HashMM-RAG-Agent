package com.hashmm.app.ui.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class OfficeHandoffTest {
    @Test
    fun `maps only supported openxml office formats`() {
        assertEquals(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            officeMimeForFilename("REPORT.DOCX"),
        )
        assertEquals(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            officeMimeForFilename("book.xlsx"),
        )
        assertEquals(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            officeMimeForFilename("deck.pptx"),
        )
        assertNull(officeMimeForFilename("archive.zip"))
    }

    @Test
    fun `extracts office filename before authenticated preview suffix`() {
        assertEquals(
            "report.docx",
            officeFilenameFromUrl("https://example.test/api/files/report.docx/view?token=secret"),
        )
        assertNull(officeFilenameFromUrl("https://example.test/api/files/readme.txt/view"))
    }
}
