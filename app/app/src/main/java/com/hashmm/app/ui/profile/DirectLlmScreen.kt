package com.hashmm.app.ui.profile

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.FlashOn
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.DirectLlmRepository
import com.hashmm.app.data.settings.SettingsStore
import com.hashmm.app.ui.components.ScreenHeader
import com.hashmm.app.ui.theme.OnWarmBeige
import com.hashmm.app.ui.theme.WarmBeige
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class DirectLlmViewModel @Inject constructor(
    val settings: SettingsStore,
    val direct: DirectLlmRepository,
) : ViewModel() {
    fun save(base: String, model: String, key: String, done: (String) -> Unit) {
        viewModelScope.launch {
            settings.setDirectLlm(base, model, key)
            done(if (base.isBlank() || model.isBlank() || key.isBlank()) "已保存（未配齐三项，兜底不生效）" else "已保存")
        }
    }
    fun test(done: (Boolean, String) -> Unit) {
        viewModelScope.launch { val (ok, msg) = direct.test(); done(ok, msg) }
    }
}

/**
 * 直连模型（V249 离线兜底）：桌面端/自建后端不在线时，手机直接调 OpenAI 兼容端点
 * 继续 agent 问答（Marvis 式独立模式）。Key 只存本机、只发给你自己填的端点。
 */
@Composable
fun DirectLlmScreen(onBack: () -> Unit, vm: DirectLlmViewModel = hiltViewModel()) {
    val cs = MaterialTheme.colorScheme
    var base by remember { mutableStateOf("") }
    var model by remember { mutableStateOf("") }
    var key by remember { mutableStateOf("") }
    var msg by remember { mutableStateOf("") }
    var msgOk by remember { mutableStateOf(true) }
    var testing by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        base = vm.settings.directBaseUrl.first()
        model = vm.settings.directModel.first()
        key = vm.settings.directApiKey.first()
    }

    Scaffold(
        topBar = { ScreenHeader(title = "直连模型", onBack = onBack) },
        containerColor = cs.background,
    ) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // 说明卡（米色 · AI/工具语气）
            Surface(color = WarmBeige, shape = RoundedCornerShape(20.dp), modifier = Modifier.fillMaxWidth()) {
                Row(Modifier.padding(horizontal = 18.dp, vertical = 16.dp), verticalAlignment = Alignment.Top) {
                    Icon(Icons.Outlined.FlashOn, contentDescription = null,
                        tint = cs.onSurface, modifier = Modifier.size(22.dp))
                    Spacer(Modifier.width(13.dp))
                    Column {
                        Text("离线兜底", fontSize = 15.5.sp, fontWeight = FontWeight.Bold,
                            color = cs.onSurface, letterSpacing = (-0.2).sp)
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "桌面端 / 自建后端不在线时，手机直接调你自己的模型端点继续问答（OpenAI 兼容：DeepSeek、Moonshot、通义等均可）。Key 只存本机、只发给下面填的地址。",
                            fontSize = 12.5.sp, color = OnWarmBeige, lineHeight = 18.sp,
                        )
                    }
                }
            }

            // 三项配置（白卡 + 灰容器输入）
            Surface(color = cs.surface, shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(11.dp)) {
                    ConfField("端点 Base URL", base, "https://api.deepseek.com/v1") { base = it }
                    ConfField("模型名", model, "deepseek-chat") { model = it }
                    ConfField("API Key", key, "sk-…", secret = true) { key = it }
                }
            }

            // 操作行：保存（墨黑）· 测试（白胶囊）· 结果
            Row(verticalAlignment = Alignment.CenterVertically) {
                Surface(color = cs.primary, shape = RoundedCornerShape(50),
                    modifier = Modifier.clip(RoundedCornerShape(50)).clickable {
                        vm.save(base, model, key) { m -> msg = m; msgOk = true }
                    }) {
                    Text("保存", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = cs.onPrimary,
                        modifier = Modifier.padding(horizontal = 20.dp, vertical = 9.dp))
                }
                Spacer(Modifier.width(10.dp))
                Surface(color = cs.surface, shape = RoundedCornerShape(50),
                    modifier = Modifier.clip(RoundedCornerShape(50)).clickable(enabled = !testing) {
                        testing = true
                        vm.save(base, model, key) { }
                        vm.test { ok, m -> testing = false; msg = m; msgOk = ok }
                    }) {
                    Row(Modifier.padding(horizontal = 16.dp, vertical = 9.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        if (testing) {
                            CircularProgressIndicator(Modifier.size(13.dp), strokeWidth = 1.6.dp,
                                color = cs.onSurfaceVariant)
                            Spacer(Modifier.width(6.dp))
                        }
                        Text(if (testing) "测试中…" else "测试连接", fontSize = 13.sp,
                            fontWeight = FontWeight.Medium, color = cs.onSurface)
                    }
                }
            }
            if (msg.isNotBlank()) {
                Text(msg, fontSize = 12.5.sp,
                    color = if (msgOk) Color(0xFF15803D) else com.hashmm.app.ui.theme.BrandRed,
                    lineHeight = 18.sp)
            }
            Text(
                "兜底触发时机：发送消息 → 后端流式与非流两级都不可达 → 自动切直连（对话顶部会出现米色横幅；本轮不入后端/云端记录，后端恢复后自动切回）。",
                fontSize = 11.5.sp, color = cs.onSurfaceVariant, lineHeight = 17.sp,
            )
        }
    }
}

@Composable
private fun ConfField(label: String, value: String, hint: String, secret: Boolean = false, onChange: (String) -> Unit) {
    val cs = MaterialTheme.colorScheme
    Column {
        Text(label, fontSize = 11.5.sp, fontWeight = FontWeight.Medium, color = cs.onSurfaceVariant)
        Spacer(Modifier.height(6.dp))
        Surface(color = cs.surfaceVariant.copy(alpha = 0.6f), shape = RoundedCornerShape(12.dp)) {
            Box(Modifier.fillMaxWidth().padding(horizontal = 13.dp, vertical = 11.dp)) {
                if (value.isEmpty()) Text(hint, fontSize = 13.5.sp,
                    color = cs.onSurfaceVariant.copy(alpha = 0.7f))
                BasicTextField(
                    value = value, onValueChange = onChange, singleLine = true,
                    textStyle = TextStyle(fontSize = 13.5.sp, color = cs.onSurface),
                    cursorBrush = SolidColor(cs.onSurface),
                    visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}
