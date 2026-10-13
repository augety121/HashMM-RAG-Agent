package com.hashmm.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.hashmm.app.ui.theme.OnWarmBeige
import com.hashmm.app.ui.theme.WarmBeige

/** Shared information-page language for About, Help, Privacy and Terms. */
@Composable
fun InfoSectionLabel(
    title: String,
    modifier: Modifier = Modifier,
    hint: String = "",
) {
    Row(
        modifier.fillMaxWidth().padding(start = 3.dp, end = 3.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            title,
            fontSize = 12.5.sp,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            letterSpacing = 0.4.sp,
            modifier = Modifier.weight(1f),
        )
        if (hint.isNotBlank()) {
            Text(hint, fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
fun InfoGroup(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(18.dp),
        modifier = modifier.fillMaxWidth(),
    ) {
        Column(content = content)
    }
}

@Composable
fun InfoDivider(start: Dp = 16.dp) {
    Box(
        Modifier.fillMaxWidth().padding(start = start).height(0.5.dp)
            .background(MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.65f))
    )
}

@Composable
fun InfoIntroCard(
    title: String,
    body: String,
    modifier: Modifier = Modifier,
    meta: String = "",
) {
    Surface(
        color = WarmBeige,
        shape = RoundedCornerShape(20.dp),
        modifier = modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(horizontal = 18.dp, vertical = 17.dp)) {
            Text(
                title,
                fontSize = 15.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurface,
                letterSpacing = (-0.2).sp,
            )
            Spacer(Modifier.height(6.dp))
            Text(body, fontSize = 13.sp, lineHeight = 20.sp, color = OnWarmBeige)
            if (meta.isNotBlank()) {
                Spacer(Modifier.height(10.dp))
                Text(meta, fontSize = 11.5.sp, color = OnWarmBeige.copy(alpha = 0.9f))
            }
        }
    }
}
