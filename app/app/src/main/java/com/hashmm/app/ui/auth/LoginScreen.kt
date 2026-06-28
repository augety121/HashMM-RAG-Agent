package com.hashmm.app.ui.auth

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.ui.components.HashMascot
import com.hashmm.app.ui.theme.Accent

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LoginScreen(
    onLoggedIn: () -> Unit,
    onBack: () -> Unit,
    onOpenTerms: () -> Unit = {},
    onOpenPrivacy: () -> Unit = {},
    viewModel: LoginViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var confirm by remember { mutableStateOf("") }
    var register by remember { mutableStateOf(false) }
    var localErr by remember { mutableStateOf<String?>(null) }

    LaunchedEffectLogin(ui.success, onLoggedIn)

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = {},
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
    ) { padding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .imePadding()
                .padding(horizontal = 28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(24.dp))
            HashMascot(Modifier.size(110.dp))
            Spacer(Modifier.height(18.dp))
            Text(
                if (ui.awaitingCode) "输入验证码" else if (register) "注册 HashMM" else "登录 HashMM",
                fontSize = 24.sp, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                if (ui.awaitingCode) "验证码已发送至 ${ui.pendingEmail}" else if (register) "注册后与桌面客户端共用同一账号" else "与桌面客户端共用同一账号",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            if (ui.awaitingCode) {
                // —— 注册：输入邮箱收到的 6 位验证码 ——
                var code by remember { mutableStateOf("") }
                Spacer(Modifier.height(28.dp))
                OutlinedTextField(
                    value = code,
                    onValueChange = { v -> code = v.filter { it.isDigit() }.take(6); localErr = null; viewModel.clearMessages() },
                    label = { Text("6 位验证码") },
                    singleLine = true,
                    shape = RoundedCornerShape(14.dp),
                    textStyle = androidx.compose.ui.text.TextStyle(
                        fontSize = 22.sp, letterSpacing = 8.sp, fontWeight = FontWeight.Bold,
                        textAlign = TextAlign.Center, color = MaterialTheme.colorScheme.onSurface,
                    ),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                    modifier = Modifier.fillMaxWidth(),
                )

                val shownErr = localErr ?: ui.error
                if (shownErr != null) {
                    Spacer(Modifier.height(12.dp))
                    Text(shownErr, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall, modifier = Modifier.fillMaxWidth())
                }
                if (ui.info != null) {
                    Spacer(Modifier.height(12.dp))
                    Text(ui.info!!, color = Accent, style = MaterialTheme.typography.bodySmall, modifier = Modifier.fillMaxWidth())
                }

                Spacer(Modifier.height(20.dp))
                Button(
                    onClick = { localErr = null; viewModel.verifyCode(code) },
                    enabled = !ui.loading && code.length == 6,
                    shape = RoundedCornerShape(14.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.primary),
                    modifier = Modifier.fillMaxWidth().height(52.dp),
                ) {
                    if (ui.loading) CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                    else Text("完成注册", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                }

                Spacer(Modifier.height(16.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("没收到验证码？", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(
                        "重新发送", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.clickable(enabled = !ui.loading) { viewModel.resendCode() },
                    )
                }
                Spacer(Modifier.height(10.dp))
                Text(
                    "返回修改邮箱", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.clickable { viewModel.backToForm(); register = true; password = ""; confirm = "" },
                )
            } else {
            Spacer(Modifier.height(28.dp))
            OutlinedTextField(
                value = email,
                onValueChange = { email = it; localErr = null; viewModel.clearMessages() },
                label = { Text("邮箱") },
                singleLine = true,
                shape = RoundedCornerShape(14.dp),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = password,
                onValueChange = { password = it; localErr = null; viewModel.clearMessages() },
                label = { Text(if (register) "设置密码（至少 6 位）" else "密码") },
                singleLine = true,
                shape = RoundedCornerShape(14.dp),
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth(),
            )
            if (register) {
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = confirm,
                    onValueChange = { confirm = it; localErr = null; viewModel.clearMessages() },
                    label = { Text("确认密码") },
                    singleLine = true,
                    shape = RoundedCornerShape(14.dp),
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    modifier = Modifier.fillMaxWidth(),
                )
            }

            val shownErr = localErr ?: ui.error
            if (shownErr != null) {
                Spacer(Modifier.height(12.dp))
                Text(shownErr, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall, modifier = Modifier.fillMaxWidth())
            }
            if (ui.info != null) {
                Spacer(Modifier.height(12.dp))
                Text(ui.info!!, color = Accent, style = MaterialTheme.typography.bodySmall, modifier = Modifier.fillMaxWidth())
            }

            Spacer(Modifier.height(20.dp))
            // 协议
            Text("继续即代表你已阅读并同意", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("《用户协议》", fontSize = 12.sp, fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.clickable { onOpenTerms() })
                Text(" 和 ", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("《隐私政策》", fontSize = 12.sp, fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.clickable { onOpenPrivacy() })
            }

            Spacer(Modifier.height(18.dp))
            Button(
                onClick = {
                    localErr = null
                    if (register) {
                        if (password != confirm) localErr = "两次输入的密码不一致"
                        else viewModel.signUp(email, password)
                    } else viewModel.signIn(email, password)
                },
                enabled = !ui.loading,
                shape = RoundedCornerShape(14.dp),
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.primary),
                modifier = Modifier.fillMaxWidth().height(52.dp),
            ) {
                if (ui.loading) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                } else {
                    Text(if (register) "注册" else "登录", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                }
            }

            Spacer(Modifier.height(16.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    if (register) "已有账号？" else "还没有账号？",
                    fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    if (register) "去登录" else "去注册",
                    fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.clickable { register = !register; confirm = ""; localErr = null; viewModel.clearMessages() },
                )
            }
            }
            Spacer(Modifier.height(40.dp))
        }
    }
}

@Composable
private fun LaunchedEffectLogin(success: Boolean, onLoggedIn: () -> Unit) {
    androidx.compose.runtime.LaunchedEffect(success) { if (success) onLoggedIn() }
}
