package com.hashmm.app.ui.legal

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.hashmm.app.ui.components.InfoDivider
import com.hashmm.app.ui.components.InfoGroup
import com.hashmm.app.ui.components.InfoIntroCard
import com.hashmm.app.ui.components.InfoSectionLabel
import com.hashmm.app.ui.components.ScreenHeader

private data class Section(val heading: String, val body: String)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LegalScaffold(title: String, updated: String, intro: String, sections: List<Section>, onBack: () -> Unit) {
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = { ScreenHeader(title, onBack) },
    ) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
        ) {
            Spacer(Modifier.height(6.dp))
            InfoIntroCard(
                title = "阅读前说明",
                body = intro,
                meta = updated,
            )
            Spacer(Modifier.height(12.dp))
            InfoSectionLabel("正文", hint = "共 ${sections.size} 节")
            InfoGroup {
                sections.forEachIndexed { index, sec ->
                    if (index > 0) InfoDivider(start = 16.dp)
                    Column(Modifier.padding(horizontal = 17.dp, vertical = 16.dp)) {
                        Text(sec.heading, fontSize = 15.sp, fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onSurface)
                        Spacer(Modifier.height(8.dp))
                        Text(sec.body, fontSize = 13.5.sp, lineHeight = 21.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            Spacer(Modifier.height(28.dp))
        }
    }
}

@Composable
fun PrivacyScreen(onBack: () -> Unit) {
    LegalScaffold(
        title = "隐私政策",
        updated = "最后更新：2026 年 7 月 18 日 · 版本 3.0",
        intro = "HashMM 是自部署、跨端协同的 RAG-Agent 工作空间。本政策按 App 本机、自建后端、自有 Supabase 项目、桌面端和外部模型服务商五条真实路径说明数据处理，不把“自部署”简单等同于“所有数据永不联网”。",
        sections = listOf(
            Section("一、适用范围与角色",
                "本政策适用于 HashMM Android App、桌面端与自建后端之间的协同。部署服务器、Supabase 项目和模型账号的人是数据控制者；登录账号的使用者是数据主体。若你使用单位或他人部署的实例，应同时遵守该部署者公布的保留、备份和访问规则。"),
            Section("二、处理的数据类别",
                "服务可能处理账号标识、头像昵称、对话与反馈、上传文件、知识库切片、长期记忆、任务指令、计划、工具调用与审批记录、运行轨迹、生成产物、连接错误和用量统计。只有在你启用相应功能时才处理相应数据；完整逐项清单可在「个人信息采集清单」查看。"),
            Section("三、App 本机数据与权限",
                "App 在本机保存登录状态、后端地址、界面偏好、必要缓存和你选择的直连模型配置。网络权限用于连接自建后端、Supabase 和所选模型端点；麦克风仅在语音输入时使用；相册权限仅在选择图片或明确同意电脑端取图请求后使用。App 当前不申请定位、通讯录或短信权限。语音录音先写入临时缓存，需要后端转写时上传，回调完成后删除临时文件。"),
            Section("四、自建后端与桌面端",
                "对话工作区、知识库、索引、任务状态、审计记录和生成文件主要保存在你控制的后端数据库与文件目录。浏览器、文件、Shell、Computer Use 和多智能体任务可能在桌面端访问你授权的本地资源；结果、摘要和产物会回填到会话。高风险操作应由权限策略或人工审批限制，审计记录用于说明谁在何时调用了什么能力。"),
            Section("五、自有 Supabase 项目",
                "跨端能力启用后，Supabase Auth 负责账号会话；对话与消息以自建 HashMM 后端为权威数据源，Supabase 中的 chat_conversations、chat_messages 仅作为受 RLS 保护的镜像和实时变更信号，profiles、user_memory 与 app_config 承担各自明确的数据用途。数据位于部署者自己的 Supabase 项目中，访问边界取决于该项目的 RLS、密钥管理和备份配置。部署者不得把 service_role 密钥放进 App，并可在控制台撤销会话或删除记录。"),
            Section("六、模型、搜索与其他外部服务",
                "当使用云端模型时，为完成本轮任务所需的用户输入、系统指令、检索片段、附件摘要或工具结果会发送给你选择的模型 API 服务商；手机直连模式由 App 直接连接该端点。Web 搜索或浏览器任务会把查询词发送给目标网站或搜索服务。各外部服务按其自身条款处理数据；需要完全本地化时应选择本地模型并关闭外部搜索。"),
            Section("七、使用目的与禁止用途",
                "数据仅用于身份验证、连续对话、RAG 检索、个性化记忆、长任务执行、跨端同步、故障诊断、安全审计和用量展示。HashMM 本身不以出售个人信息、广告投放或训练公共模型为产品目的；但你配置的第三方模型或搜索服务是否用于日志与训练，应以其政策和你的账号设置为准。"),
            Section("八、安全边界",
                "项目通过账号归属校验、RLS、短期令牌、权限分级、工具审批、超时、并发限制、审计和输入输出过滤降低风险，但任何自部署系统都不能承诺绝对安全。部署者应启用 HTTPS、妥善保存 API Key 与 service_role 密钥、及时更新依赖、限制公网暴露，并定期检查审计与备份。"),
            Section("九、保留、删除与导出",
                "App 缓存可通过系统清理或卸载移除；会话、记忆与文件可从产品入口或服务器侧删除；Supabase 数据可由项目所有者在控制台导出或删除。删除在离线设备、备份或同步队列中可能存在延迟，部署者应定义备份保留期并在恢复备份时避免把已删除数据重新同步回来。"),
            Section("十、你的选择与联系",
                "你可以拒绝可选权限、关闭云端同步或手机直连、切换本地模型、取消任务、退出登录，并要求部署者提供访问、导出、更正或删除支持。政策内容发生影响数据路径的重大变化时，应更新日期和版本。问题请联系你使用实例的部署管理员。"),
        ),
        onBack = onBack,
    )
}

@Composable
fun TermsScreen(onBack: () -> Unit) {
    LegalScaffold(
        title = "用户协议",
        updated = "最后更新：2026 年 7 月 18 日 · 版本 3.0",
        intro = "本协议说明自部署 HashMM 工作空间的功能边界、账号责任、Agent 输出限制以及浏览器、文件、Shell、Computer Use 与多智能体任务的执行责任。部署者还应根据实际运营主体和所在地法律补充主体名称、联系方式与争议条款。",
        sections = listOf(
            Section("一、服务说明",
                "HashMM 提供对话、知识库检索、记忆、文件理解、文档生成、Web/浏览器任务、代码与工具执行、长任务计划、多智能体协作、桌面端控制和跨端同步。不同部署可能关闭部分模块；页面出现入口不代表相应后端、模型、权限或桌面执行器一定可用。"),
            Section("二、账号与部署责任",
                "完整功能需要登录部署者配置的账号系统。你应保护账号、设备、API Key 和审批凭据，不得共享管理员令牌。部署者负责服务器、域名、TLS、Supabase 项目、模型账号、备份、升级和用户权限；发现异常访问应及时撤销会话并检查审计记录。"),
            Section("三、Agent 输出与人工复核",
                "模型可能生成错误、过时、缺少依据或不完整的内容；检索引用、工具输出和“任务完成”状态也可能受网络、权限或外部页面影响。医疗、法律、财务、安全、生产变更等高风险场景必须由具备资质的人复核，不应把模型文字当作已执行或已验证的证据。"),
            Section("四、工具、审批与长任务",
                "浏览器、文件、Shell、代码、Computer Use、多智能体和远程控制可能读取数据、修改文件、访问网络或操作真实设备。系统会根据工具类型和策略请求审批，但审批机制不能替代你的判断。你应核对目标、范围、风险和预期产物；任务可被中断、超时、重试或部分完成，恢复前应检查已有副作用，避免重复执行。"),
            Section("五、合法使用",
                "你只能在自己拥有或已获明确授权的账号、设备、网站和数据上使用本服务。不得用于未授权访问、恶意代码、破坏或规避安全控制、侵犯隐私或知识产权、欺诈、骚扰、违法内容传播，或以其他方式违反适用法律和第三方服务条款。"),
            Section("六、数据与第三方服务",
                "自部署不意味着所有数据都只在本机。启用 Supabase、云端模型、搜索、目标网站或其他插件时，必要数据会发送到相应服务。你负责选择供应商、配置保留与训练选项并承担其条款和费用。具体数据路径以隐私政策和个人信息采集清单为准。"),
            Section("七、知识产权与生成内容",
                "你应确保有权提交输入、文档和代码，并保留对合法输入内容的权利。生成内容可能与第三方内容相似或不具备排他性；在发布、商用或合并代码前，应自行检查许可、署名、商标、专利、安全和事实准确性。项目代码与第三方依赖分别适用其许可证。"),
            Section("八、可用性、费用与变更",
                "服务按现状提供，可能因后端离线、模型限流、外部网站变化、设备休眠、权限拒绝或版本不兼容而降级。服务器、Supabase、模型和网络费用由相应账号持有人承担。部署者可升级、暂停或关闭功能，但应在可能时保护数据可导出性并说明不兼容变化。"),
            Section("九、责任边界与停止使用",
                "在适用法律允许范围内，项目贡献者不对模型错误、任务副作用、数据丢失、第三方服务中断或间接损失作超出法定范围的保证。不同地区可能不允许部分免责，最终责任以适用法律和部署者与你之间的协议为准。不同意本协议时，应停止使用并联系部署者导出或删除数据。"),
        ),
        onBack = onBack,
    )
}
