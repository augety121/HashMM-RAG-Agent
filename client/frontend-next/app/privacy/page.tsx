import type { Metadata } from "next";
import { BackLink } from "@/components/BackLink";

export const metadata: Metadata = { title: "隐私政策 — HashMM-RAG" };

export default function PrivacyPage() {
  return (
    <div style={{ height: "100vh", overflowY: "auto" }}>
      <div style={{ maxWidth: 760, margin: "0 auto", padding: "48px 28px 80px", fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', sans-serif", color: "#1a1a1a", lineHeight: 1.9 }}>
        <h1 style={{ fontSize: 30, fontWeight: 700, marginBottom: 8 }}>隐私政策</h1>
        <p style={{ color: "#888", fontSize: 14, marginBottom: 40 }}>最后更新日期：2026 年 7 月 5 日 · 生效日期：2026 年 7 月 5 日 · 版本 2.1</p>

        <p style={{ fontSize: 14, color: "#444", marginBottom: 28 }}>HashMM-RAG Agent（以下简称「本服务」或「我们」）高度重视用户隐私保护。本隐私政策旨在向您说明我们如何收集、使用、存储和保护您的个人信息。使用本服务即表示您同意本政策中所述的数据实践。</p>

        <S n="1" title="信息收集">
          <p>我们在您使用本服务的过程中收集以下类型的信息：</p>
          <h3>1.1 帐户信息</h3>
          <ul>
            <li>注册时提供的用户名和显示名称</li>
            <li>帐户密码（以 PBKDF2 哈希加盐形式存储，我们无法查看您的明文密码）</li>
            <li>帐户创建时间和角色信息</li>
          </ul>
          <h3>1.2 对话数据</h3>
          <ul>
            <li>您与 AI 助手的对话内容（包括您的输入和 AI 的回答）</li>
            <li>对话元数据（创建时间、标题、标签、项目归属等）</li>
            <li>AI 的思考过程和工具调用记录</li>
            <li>对话反馈（点赞/点踩）</li>
          </ul>
          <h3>1.3 上传文件</h3>
          <ul>
            <li>您上传的文件内容（PDF、Word、Excel、图片、代码文件等）</li>
            <li>AI 解析后的文件摘要和元数据</li>
            <li>AI 为您生成的文件（PPT、Word、Excel、PDF 等）</li>
          </ul>
          <h3>1.4 使用数据</h3>
          <ul>
            <li>功能使用记录（操作审计日志）</li>
            <li>API 调用统计（模型、token 用量）</li>
            <li>登录时间和 IP 地址（记录在审计日志中）</li>
            <li>浏览器偏好设置（主题、语言、字体大小等，存储在 localStorage）</li>
          </ul>
          <h3>1.5 知识库数据</h3>
          <ul>
            <li>管理员索引到知识库中的文档内容</li>
            <li>文档的向量化表示（FAISS 索引）和哈希码</li>
            <li>文档元数据（文件名、模态、切片数等）</li>
          </ul>
        </S>

        <S n="2" title="数据存储与安全">
          <h3>2.1 自部署架构</h3>
          <p>本服务的<strong>核心数据（对话记录、上传文件、知识库、审计与运行日志等）均存储在您自己控制的服务器上</strong>（SQLite，见 2.2），我们不在外部服务器上存储或备份这些核心数据。</p>
          <p>2.1.1 <strong>云端同步（重要，2026-07 更新）：</strong>为实现「多端一致」，在您配置了自己的 Supabase 项目（含 service_role 密钥）后，<strong>以下数据会同步存储到您自己的 Supabase 租户</strong>（不是本项目的服务器——本项目不设任何中转服务器）：</p>
          <ul>
            <li><strong>会话与消息</strong>（chat_conversations / chat_messages）：手机与桌面共享同一份对话，归档、删除随之同步；手机「直连模式」产生的轮次也会写入，供桌面端接力继续；</li>
            <li><strong>跨会话记忆</strong>（user_memory）与<strong>头像、显示资料</strong>；</li>
            <li><strong>零配置项</strong>（app_config）：仅用于向已登录客户端发布后端 HTTPS 地址和非敏感协议元数据。模型 API Key、服务角色密钥和用户令牌不会写入该表，也不会下发给其他客户端。</li>
          </ul>
          <p>知识库文件不上云。未配置 service_role 密钥时，以上同步全部自动停用，一切数据仅留在本机。这些数据受 Supabase 的安全与隐私政策约束（见第 4 节）。</p>
          <h3>2.2 数据库</h3>
          <p>用户数据使用 SQLite 数据库存储在服务器本地磁盘上。数据库文件位于 <code>data/hashmm.db</code>。数据库采用 WAL（Write-Ahead Logging）模式以提供更好的并发性能。</p>
          <h3>2.3 安全措施</h3>
          <p>我们采取以下技术和组织措施来保护您的数据安全：</p>
          <ul>
            <li><strong>密码安全：</strong>用户密码使用 PBKDF2-SHA256 哈希加盐存储，即使数据库泄露也无法恢复明文密码</li>
            <li><strong>API 密钥加密：</strong>LLM API 密钥在数据库中加密存储</li>
            <li><strong>认证机制：</strong>用户认证使用 JWT（JSON Web Token）令牌，令牌具有过期时间</li>
            <li><strong>传输安全：</strong>建议在生产环境中配置 HTTPS 加密传输（通过 Nginx 反向代理或 Caddy）</li>
            <li><strong>代码沙箱：</strong>代码执行环境具有超时限制、内存限制和文件系统访问限制</li>
            <li><strong>输入过滤：</strong>系统对用户输入和 AI 输出进行安全过滤，防止注入攻击和恶意内容</li>
            <li><strong>CORS 策略：</strong>跨域请求限制为指定的域名白名单</li>
            <li><strong>审计日志：</strong>关键操作（登录、注册、模型更改、用户管理等）会记录审计日志</li>
          </ul>
        </S>

        <S n="3" title="数据使用目的">
          <p>我们收集的信息仅用于以下目的：</p>
          <ul>
            <li><strong>提供服务：</strong>处理您的查询、生成回答、执行代码、创建文档</li>
            <li><strong>对话上下文：</strong>在同一对话中维护上下文连贯性</li>
            <li><strong>个性化：</strong>记住您的偏好设置（主题、自定义提示词等）</li>
            <li><strong>知识库检索：</strong>在已索引的文档中搜索相关信息以增强回答质量</li>
            <li><strong>系统改进：</strong>分析使用模式以改进系统性能和用户体验</li>
            <li><strong>安全保障：</strong>检测和防止未经授权的访问或滥用</li>
          </ul>
          <p>我们<strong>不会</strong>将您的数据用于：</p>
          <ul>
            <li>向第三方出售或出租您的个人数据</li>
            <li>投放广告或进行商业营销</li>
            <li>训练我们自己的 AI 模型（数据不会离开您的服务器）</li>
            <li>用户画像或行为追踪</li>
          </ul>
        </S>

        <S n="4" title="第三方服务">
          <p>本服务在运行过程中可能与以下第三方服务交互：</p>
          <h3>4.1 大语言模型 API</h3>
          <p><strong>手机直连模式（2026-07 新增）：</strong>当您的自建后端不在线时，手机 App 可将消息<strong>直接</strong>发送到您配置的模型端点继续问答——数据路径为「手机 → 您填写/同步的模型端点」，不经任何第三方中转；该轮次同样会写入您的 Supabase 会话以保持两端一致。</p>
          <p>当您使用 AI 对话功能时，您的查询内容（以及必要的上下文）会被发送到配置的 LLM API 服务商（如 DeepSeek、OpenAI、Anthropic Claude、Google Gemini、智谱 AI、Mistral、xAI、Moonshot、SiliconFlow 等，具体取决于您在「模型管理」中的配置）。请注意：</p>
          <ul>
            <li>发送的数据包括您的查询文本、对话历史（用于上下文）和系统提示词</li>
            <li>上传的文件内容可能会作为上下文的一部分发送给 LLM</li>
            <li>各 API 服务商有自己的数据处理政策，请参阅其各自的隐私政策；部分服务商位于境外</li>
            <li>您可以通过管理后台选择使用本地部署的模型（如 Ollama、vLLM、LM Studio）来避免数据外传</li>
          </ul>
          <h3>4.2 搜索引擎</h3>
          <p>当 AI 使用网络搜索功能时，搜索查询会被发送到搜索引擎服务（如 DuckDuckGo）。搜索查询不包含用户身份信息。</p>
          <h3>4.3 BGE-M3 编码器</h3>
          <p>文本向量化使用本地部署的 BGE-M3 模型，所有编码过程在本地完成，不涉及外部网络通信。</p>
          <h3>4.4 云端同步服务（Supabase）</h3>
          <p>本服务使用 Supabase（第三方云数据库服务）同步您的<strong>头像、显示资料与跨会话记忆</strong>，以便在网页端、桌面客户端与手机 App 之间保持一致。传输与存储到 Supabase 的数据仅限上述内容，<strong>不包括完整对话记录与知识库文件</strong>。Supabase 有其自身的数据处理与隐私政策，请参阅其官网；其服务器可能位于境外。若您不配置 Supabase，相关数据仅保存在本地。</p>
        </S>

        <S n="5" title="远程控制与屏幕共享">
          <p>本服务的手机 App 提供「远程控制」功能，可在您授权的前提下查看并操作您的桌面客户端所在电脑。使用该功能时请知悉：</p>
          <ul>
            <li><strong>屏幕画面：</strong>开启远程投屏后，桌面端的屏幕画面会通过 WebRTC（点对点，尽量直连）或在直连失败时经中继传输到您的手机 App，用于实时显示。</li>
            <li><strong>控制指令：</strong>您在 App 上的点击、移动、输入等操作会作为控制指令发送到桌面端执行（鼠标、键盘等）。</li>
            <li><strong>授权与范围：</strong>该功能仅在您登录同一帐户并主动发起连接时可用；请仅在您本人拥有或已获授权的设备上使用，并注意所在环境的隐私（屏幕可能包含敏感信息）。</li>
            <li><strong>文件传输：</strong>当您在 App 中要求「发送电脑上的某个文件」时，桌面端会按您的指令读取并发送该文件到您指定的接收端（如 App 内或您已连接的即时通讯渠道）。请仅对您有权访问的文件使用此功能。</li>
          </ul>
          <p>远程控制涉及对真实设备的访问，请妥善保管帐户凭据；如发现异常连接，请立即修改密码并在「用户管理」中强制下线相关会话。</p>
        </S>

        <S n="6" title="Cookie 和本地存储">
          <p>本服务使用浏览器本地存储（localStorage）来保存以下信息：</p>
          <ul>
            <li>用户认证令牌（JWT token）</li>
            <li>用户偏好设置（主题模式、主题色、字体大小、语言）</li>
            <li>自定义系统提示词</li>
            <li>对话历史的本地缓存</li>
            <li>头像颜色偏好</li>
          </ul>
          <p>我们<strong>不使用</strong>第三方追踪 Cookie、分析工具（如 Google Analytics）或广告追踪技术。</p>
        </S>

        <S n="7" title="您的权利">
          <p>根据适用的数据保护法律，您享有以下权利：</p>
          <ul>
            <li><strong>访问权：</strong>您可以随时访问和查看您的对话记录、上传文件和个人信息</li>
            <li><strong>导出权：</strong>您可以通过设置页面的「数据管理」导出您的所有对话数据（JSON 格式）</li>
            <li><strong>删除权：</strong>您可以删除单个对话或全部对话历史；您也可以请求删除您的帐户和所有相关数据</li>
            <li><strong>更正权：</strong>您可以随时修改您的个人信息（如显示名称）</li>
            <li><strong>限制处理权：</strong>您可以通过关闭特定功能来限制数据处理（如不使用网络搜索）</li>
            <li><strong>知情权：</strong>您有权了解我们如何处理您的数据（即本隐私政策所述内容）</li>
          </ul>
          <p>如需行使上述权利，您可以通过系统设置直接操作，或联系系统管理员获取帮助。</p>
        </S>

        <S n="8" title="数据保留">
          <p>7.1 对话数据将一直保留在您的服务器上，直到您主动删除。</p>
          <p>7.2 审计日志默认保留 90 天，之后自动清理。</p>
          <p>7.3 上传的文件保留在对话工作区中，直到对话被删除或文件被手动删除。</p>
          <p>7.4 当帐户被删除时，与该帐户关联的所有数据（对话、文件、偏好设置）将被永久删除。</p>
        </S>

        <S n="9" title="未成年人保护">
          <p>本服务不面向 18 周岁以下的未成年人。我们不会故意收集未成年人的个人信息。如果我们发现无意中收集了未成年人的信息，将立即采取措施删除相关数据。如果您是家长或监护人并发现您的孩子使用了本服务，请联系系统管理员。</p>
        </S>

        <S n="10" title="跨境数据传输">
          <p>由于本服务采用自部署架构，您的数据存储在您指定的服务器上。如果您使用了海外的 LLM API 服务商（如 OpenAI），对话内容可能会被传输到海外服务器进行处理。请根据您的数据合规要求选择合适的 API 服务商，或使用本地部署的模型。</p>
        </S>

        <S n="11" title="政策变更">
          <p>10.1 我们可能会不时更新本隐私政策以反映服务变更或法律要求。更新后的政策将在本页面上发布，并注明最后更新日期。</p>
          <p>10.2 对于重大变更，我们将通过系统通知或在用户下次登录时提示的方式告知您。</p>
          <p>10.3 继续使用本服务即表示您接受修改后的隐私政策。</p>
        </S>

        <S n="12" title="联系方式">
          <p>如有任何关于本隐私政策的问题、意见或投诉，或需要行使您的数据权利，请通过以下方式联系我们：</p>
          <ul>
            <li>通过系统内的「报告错误」功能提交反馈</li>
            <li>联系系统管理员</li>
          </ul>
          <p>我们将在收到您的请求后尽快回复。</p>
        </S>

        <div style={{ borderTop: "1px solid #e5e7eb", paddingTop: 24, marginTop: 48, textAlign: "center" }}>
          <BackLink style={{ color: "#2563eb", fontSize: 13, textDecoration: "none" }} />
          <span style={{ margin: "0 12px", color: "#ddd" }}>|</span>
          <a href="/terms" style={{ color: "#2563eb", fontSize: 13, textDecoration: "none" }}>服务条款</a>
        </div>
      </div>
    </div>
  );
}

function S({ n, title, children }: { n: string; title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: 32 }}>
      <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 10 }}>{n}. {title}</h2>
      <div style={{ fontSize: 14, color: "#444" }}>{children}</div>
      <style>{`
        section ul { padding-left: 22px; margin: 8px 0 12px; }
        section li { margin-bottom: 6px; line-height: 1.8; }
        section p { margin-bottom: 10px; }
        section h3 { font-size: 15px; font-weight: 600; margin: 16px 0 6px; color: #1a1a1a; }
        section code { background: #f3f4f6; padding: 1px 6px; border-radius: 4px; font-size: 13px; }
      `}</style>
    </section>
  );
}
