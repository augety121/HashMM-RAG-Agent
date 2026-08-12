package com.hashmm.app.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class UserFacingAdminUsageTest {
    @Test
    fun usageOverviewParsesTeamTotalsAndBreakdowns() {
        val stat = parseUsageOverview(
            """{"contract":"hashmm.usage-overview.v1","scope":"team","requests":4,"tokens":200,"tokens_in":120,"tokens_out":80,"cost":0.25,"currency":"CNY","by_model":[{"model":"m","requests":4,"tokens":200,"cost":0.25}],"by_user":[{"username":"alice","requests":4,"tokens":200,"cost":0.25}]}"""
        )
        assertEquals("team", stat.scope)
        assertEquals(4, stat.requests)
        assertEquals(200L, stat.tokens)
        assertEquals("alice", stat.byMember.single().username)
        assertEquals("m", stat.byModel.single().model)
    }

    @Test(expected = IllegalArgumentException::class)
    fun usageOverviewRejectsUnknownContractInsteadOfShowingFakeZero() {
        parseUsageOverview("""{"requests":0}""")
    }

    @Test
    fun adminUsersKeepCloudIdentityFields() {
        val users = parseAdminUsers(
            """[{"id":"sb_u1","username":"alice","display_name":"Alice","email":"a@example.com","role":"admin","identity_source":"supabase","last_sign_in_at":"2026-07-19T00:00:00Z"}]"""
        )
        assertEquals(1, users.size)
        assertEquals("a@example.com", users.single().email)
        assertEquals("supabase", users.single().identitySource)
        assertTrue(users.single().lastSignInAt.isNotBlank())
    }

    @Test
    fun adminUsersAcceptLegacyNumericTimestampsWithoutDroppingDirectory() {
        val users = parseAdminUsers(
            """[{"id":"local-1","username":"legacy","created_at":1712345678.5},{"id":"local-2","username":"new","created_at":"2026-07-19T00:00:00Z"}]"""
        )
        assertEquals(2, users.size)
        assertEquals("1712345678.5", users.first().createdAt)
    }

    @Test
    fun adminUsersSkipMalformedRowsAndMergeSupabaseDetails() {
        val backend = parseAdminUsers(
            """[{"id":"sb_u1","username":"alice","role":"user","created_at":1712345678},42,{"username":"missing-id"}]"""
        )
        val cloud = parseAdminUsers(
            """[{"id":"u1","username":"alice","display_name":"Alice","email":"a@example.com","is_admin":true,"created_at":"2026-07-19T00:00:00Z"}]""",
            defaultIdentitySource = "supabase",
            prefixSupabaseIds = true,
        )

        val merged = mergeAdminUsers(backend, cloud)
        assertEquals(1, merged.size)
        assertEquals("Alice", merged.single().displayName)
        assertEquals("a@example.com", merged.single().email)
        assertEquals("admin", merged.single().role)
        assertEquals("supabase", merged.single().identitySource)
    }
}
