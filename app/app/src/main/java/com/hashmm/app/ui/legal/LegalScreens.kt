package com.hashmm.app.ui.legal

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private data class Section(val heading: String, val body: String)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LegalScaffold(title: String, updated: String, intro: String, sections: List<Section>, onBack: () -> Unit) {
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text(title, fontWeight = FontWeight.Bold) },
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
            Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(horizontal = 22.dp),
        ) {
            Spacer(Modifier.height(4.dp))
            Text(updated, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(14.dp))
            Text(intro, fontSize = 14.sp, lineHeight = 22.sp, color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.height(8.dp))
            sections.forEach { sec ->
                Spacer(Modifier.height(18.dp))
                Text(sec.heading, fontSize = 16.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface)
                Spacer(Modifier.height(8.dp))
                Text(sec.body, fontSize = 14.sp, lineHeight = 22.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.height(40.dp))
        }
    }
}

@Composable
fun PrivacyScreen(onBack: () -> Unit) {
    LegalScaffold(
        title = "隐私政策",
        updated = "最后更新：2025 年 5 月 1 日",
        intro = "HashMM-RAG Agent（以下简称「本服务」）高度重视用户隐私保护。本政策说明我们如何收集、使用、存储和保护你的个人信息。使用本服务即表示你同意本政策所述的数据实践。",
        sections = listOf(
            Section("一、我们收集的信息",
                "· 账户信息：用户名、显示名称、账户密码（以 PBKDF2 加盐哈希存储，我们无法查看你的明文密码）、账户创建时间与角色。\n" +
                "· 对话数据：你与 AI 的对话内容、对话元数据（标题/标签/时间）、AI 思考过程与工具调用记录、点赞/点踩反馈。\n" +
                "· 上传文件：你上传的文件内容及解析摘要，AI 为你生成的文件。\n" +
                "· 使用数据：功能使用与操作审计日志、API 调用统计、登录时间与 IP、本地偏好（主题/语言/字体）。\n" +
                "· 知识库数据：管理员索引的文档内容、向量化表示与元数据。"),
            Section("二、数据如何存储",
                "本服务采用完全自部署架构。所有数据（对话、文件、用户信息、知识库）均存储在你自己控制的服务器上，使用本地 SQLite 数据库。我们不在任何外部服务器上存储、传输或备份你的数据。"),
            Section("三、安全措施",
                "密码使用 PBKDF2-SHA256 加盐哈希；LLM API 密钥在数据库中加密存储；认证使用具有过期时间的 JWT 令牌；建议生产环境启用 HTTPS；代码执行有超时、内存与文件系统限制；对输入输出做安全过滤；跨域请求限制白名单；关键操作记录审计日志。"),
            Section("四、信息的使用",
                "收集的信息仅用于：提供服务（处理查询、生成回答、执行代码、创建文档）、维护对话上下文、记住你的偏好、知识库检索、系统改进与安全保障。我们不会出售或出租你的数据、不投放广告、不用你的数据训练我们自己的模型、不做用户画像或行为追踪。"),
            Section("五、第三方服务",
                "使用 AI 对话时，你的查询、必要上下文与系统提示词会发送到你所配置的 LLM API 服务商（如 DeepSeek、OpenAI、智谱 AI 等），各服务商有自己的数据政策。你可在管理后台选择本地部署模型以避免数据外传。Web 搜索功能会向搜索引擎发送查询。"),
            Section("六、你的权利",
                "你有权访问、导出或删除你的个人数据。如需行使这些权利，或对本政策有疑问，请联系你所在部署的系统管理员。"),
        ),
        onBack = onBack,
    )
}

@Composable
fun TermsScreen(onBack: () -> Unit) {
    LegalScaffold(
        title = "用户协议",
        updated = "最后更新：2025 年 5 月 1 日",
        intro = "欢迎使用 HashMM-RAG Agent（以下简称「本服务」）。本服务条款构成你与本项目团队之间关于使用本服务的协议。请在使用前仔细阅读，使用本服务即表示你已阅读、理解并同意受本条款约束。",
        sections = listOf(
            Section("一、服务说明",
                "本服务是一个基于多模态哈希检索增强生成（RAG）技术的 AI 助手系统，功能包括：基于本地知识库的智能问答与学术检索、代码生成与沙箱执行、文档自动生成（PPT/Word/Excel/PDF）、多轮对话、文件上传解析、Web 搜索、Agent 自主任务规划。本服务以自部署方式运行在用户自己的服务器上，数据完全由用户控制。"),
            Section("二、账户",
                "你需要创建账户才能使用完整功能，注册时应提供准确完整的信息。你有责任维护账户安全并对账户下的所有活动负责；发现未经授权使用应立即通知管理员。管理员账户拥有额外管理权限，应谨慎使用。"),
            Section("三、使用规范",
                "你须年满 18 周岁或达到所在地法定成年年龄。你同意仅将本服务用于合法目的，不得用于：生成或传播违法、有害、骚扰、诽谤或淫秽内容；侵犯第三方知识产权或隐私；未经授权访问系统或他人账户；反向工程；干扰服务运行；规避安全措施；生成欺诈内容；利用代码执行进行恶意操作（攻击、挖矿、部署恶意软件等）。"),
            Section("四、知识产权",
                "你保留输入到本服务中所有内容的权利。对于 AI 生成的内容，你可在合理范围内使用、修改与分发，但应理解其可能不准确或不完整、使用前应自行验证。知识库中文档的版权归原作者所有。本服务的代码、界面与商标受相关法律保护。"),
            Section("五、代码执行",
                "本服务提供 Python 代码执行沙箱，具有执行时间（默认 15 秒）、内存与文件系统访问限制，并禁止危险系统命令。尽管如此该环境并非完全隔离，请勿在代码中包含敏感信息。你对运行的代码及其后果承担全部责任。"),
            Section("六、免责声明",
                "本服务按「现状」与「可用状态」提供，不提供任何明示或暗示的保证。我们不保证服务不会中断、及时、安全或无错误，亦不保证 AI 生成内容的准确性、完整性或适用性。在法律允许的最大范围内，我们不对因使用本服务而产生的任何间接损失承担责任。"),
            Section("七、条款变更",
                "我们保留随时修改本条款的权利。重大变更会通过适当方式通知。继续使用本服务即表示你接受修改后的条款。"),
        ),
        onBack = onBack,
    )
}
