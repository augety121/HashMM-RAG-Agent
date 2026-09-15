import type { Metadata } from "next";
import { BackLink } from "@/components/BackLink";

export const metadata: Metadata = { title: "服务条款 — HashMM-RAG" };

export default function TermsPage() {
  return (
    <div style={{ height: "100vh", overflowY: "auto" }}>
      <div style={{ maxWidth: 760, margin: "0 auto", padding: "48px 28px 80px", fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', sans-serif", color: "#1a1a1a", lineHeight: 1.9 }}>
        <h1 style={{ fontSize: 30, fontWeight: 700, marginBottom: 8 }}>服务条款</h1>
        <p style={{ color: "#888", fontSize: 14, marginBottom: 40 }}>最后更新日期：2026 年 7 月 5 日 · 生效日期：2026 年 7 月 5 日 · 版本 2.1</p>

        <p style={{ fontSize: 14, color: "#444", marginBottom: 28 }}>欢迎使用 HashMM-RAG Agent（以下简称「本服务」或「HashMM」）。本服务条款（以下简称「本条款」）构成您（以下简称「用户」或「您」）与 HashMM-RAG 项目团队（以下简称「我们」）之间关于使用本服务的法律协议。请在使用本服务前仔细阅读本条款。使用本服务即表示您已阅读、理解并同意受本条款的约束。</p>

        <Section n="1" title="服务概述">
          <p>HashMM-RAG Agent 是一个基于多模态哈希检索增强生成（Retrieval-Augmented Generation）技术的人工智能助手系统，主要功能包括但不限于：</p>
          <ul>
            <li>基于本地知识库的智能问答和学术检索</li>
            <li>代码生成、执行与调试（支持 Python 沙箱运行）</li>
            <li>文档自动生成（PowerPoint 演示文稿、Word 文档、Excel 电子表格、PDF 文件）</li>
            <li>多轮对话与上下文理解</li>
            <li>文件上传、解析与管理</li>
            <li>Web 搜索与信息检索</li>
            <li>Agent 自主任务规划与执行</li>
          </ul>
          <p>本服务通过自部署方式运行在用户自己的服务器上，所有数据完全由用户控制。</p>
        </Section>

        <Section n="2" title="帐户与注册">
          <p>2.1 您需要创建帐户才能使用本服务的完整功能。注册时，您应提供准确、完整的信息。</p>
          <p>2.2 您有责任维护帐户的安全性，包括保护您的密码不被他人获取。您同意对您帐户下发生的所有活动承担责任。</p>
          <p>2.3 如果您发现帐户存在任何安全漏洞或未经授权的使用，请立即通知系统管理员。</p>
          <p>2.4 我们保留在合理怀疑帐户被未经授权使用时暂停或终止帐户的权利。</p>
          <p>2.5 管理员帐户拥有额外的系统管理权限（如用户管理、模型配置、知识库管理等），管理员应谨慎使用这些权限。</p>
        </Section>

        <Section n="3" title="使用条件与限制">
          <p>3.1 您必须年满 18 周岁或达到您所在司法管辖区的法定成年年龄方可使用本服务。</p>
          <p>3.2 您同意仅将本服务用于合法目的，并遵守所有适用的法律法规。您不得将本服务用于：</p>
          <ul>
            <li>生成、传播或存储任何违法、有害、威胁性、滥用性、骚扰性、诽谤性、淫秽或其他令人反感的内容</li>
            <li>侵犯任何第三方的知识产权、隐私权或其他合法权益</li>
            <li>尝试未经授权访问本服务的任何部分、其他用户的帐户或与本服务连接的计算机系统或网络</li>
            <li>对本服务进行反向工程、反编译或反汇编</li>
            <li>干扰或破坏本服务的正常运行、服务器或网络</li>
            <li>规避本服务的任何安全措施或访问控制</li>
            <li>利用本服务生成虚假、误导性或欺诈性内容</li>
            <li>利用代码执行功能进行恶意操作（如攻击其他系统、挖矿、部署恶意软件等）</li>
          </ul>
          <p>3.3 我们保留随时修改、暂停或终止本服务（或其任何部分）的权利，恕不另行通知。</p>
        </Section>

        <Section n="4" title="知识产权">
          <p>4.1 <strong>您的内容：</strong>您保留对您输入到本服务中的所有内容（包括问题、上传的文件、自定义提示词等）的所有权利。</p>
          <p>4.2 <strong>AI 生成的内容：</strong>对于 AI 生成的输出内容（包括文本回答、代码、文档等），您可以在合理范围内使用、修改和分发，但应理解：</p>
          <ul>
            <li>此类内容可能不完全准确或完整，使用前应自行验证</li>
            <li>此类内容可能与其他用户或第三方的输出存在相似之处</li>
            <li>我们不对 AI 生成内容的准确性、完整性或适用性做任何保证</li>
          </ul>
          <p>4.3 <strong>知识库内容：</strong>知识库中索引的文档版权归原作者所有。本服务仅提供检索和引用功能，不构成对文档内容的任何权利主张。</p>
          <p>4.4 <strong>本服务的知识产权：</strong>本服务的软件代码、界面设计、商标和其他知识产权均受相关法律保护。</p>
        </Section>

        <Section n="5" title="代码执行环境">
          <p>5.1 本服务提供 Python 代码执行沙箱环境。该环境具有以下安全限制：</p>
          <ul>
            <li>执行时间限制（默认 15 秒超时）</li>
            <li>内存使用限制</li>
            <li>文件系统访问限制（仅限工作区目录）</li>
            <li>禁止执行危险系统命令</li>
          </ul>
          <p>5.2 尽管我们采取了安全措施，但代码执行环境并非完全隔离。用户应避免在代码中包含敏感信息（如 API 密钥、密码等）。</p>
          <p>5.3 用户对在代码执行环境中运行的代码及其产生的后果承担全部责任。</p>
        </Section>

        <Section n="6" title="远程控制与文件传输">
          <p>6.1 本服务的手机 App 提供「远程控制」与「远程文件传输」功能。您理解并同意：</p>
          <ul>
            <li>您仅在<strong>您本人拥有或已获得合法授权</strong>的设备上使用远程控制与屏幕共享功能；</li>
            <li>当您在 App 中要求发送电脑上的文件时，系统会按您的指令读取并发送相应文件，您应确保对该文件拥有访问与分发的权利；</li>
            <li>因屏幕共享可能暴露敏感信息，您应自行注意所处环境与接收渠道的安全；</li>
            <li>您对通过远程控制发起的一切操作及其后果负责，并应妥善保管帐户凭据；</li>
            <li>6.2 <strong>自动化任务（2026-07 新增）：</strong>云上派活、多步规划与多智能体协作会在您的设备上执行真实操作（浏览器、文件、屏幕控制等）。您应仅对自己拥有或获授权的设备下达任务，并对任务指令及其操作后果负责；</li>
            <li>6.3 <strong>费用自担：</strong>本服务为自部署形态，服务器、Supabase 云用量与模型 API 调用（含手机直连与模型容灾链消耗的额度）均由您直接向对应服务商支付；本项目不含任何内购、订阅或付费墙。</li>
          </ul>
          <p>6.2 如发现异常或未授权的远程连接，您应立即修改密码并在管理后台强制下线相关会话。</p>
        </Section>

        <Section n="7" title="免责声明">
          <p>6.1 本服务按「现状」和「可用状态」提供，不提供任何明示或暗示的保证，包括但不限于对适销性、特定用途适用性、不侵权性的暗示保证。</p>
          <p>6.2 我们不保证：</p>
          <ul>
            <li>本服务不会中断、及时提供、安全或无错误</li>
            <li>AI 生成的结果准确、可靠或完整</li>
            <li>任何错误或缺陷将被纠正</li>
            <li>本服务在所有环境和配置下均能正常运行</li>
          </ul>
          <p>6.3 AI 生成的内容仅供参考，不构成专业建议。在医疗、法律、金融等专业领域，用户应咨询相关专业人士。</p>
        </Section>

        <Section n="8" title="责任限制">
          <p>在法律允许的最大范围内，我们对因使用或无法使用本服务而产生的任何直接、间接、附带、特殊、惩罚性或后果性损害不承担责任，无论此类损害是基于合同、侵权、严格责任或其他任何法律理论。</p>
        </Section>

        <Section n="9" title="第三方服务">
          <p>8.1 本服务可能集成或依赖第三方服务（如大语言模型 API、搜索引擎 API 等）。使用这些功能时，您同时受到相关第三方服务条款的约束。</p>
          <p>8.2 我们不对第三方服务的可用性、准确性或安全性承担责任。第三方 API 的价格、配额和使用条款可能随时变化。</p>
        </Section>

        <Section n="10" title="修改与终止">
          <p>9.1 我们保留随时修改本条款的权利。修改后的条款将在发布时立即生效。继续使用本服务即表示您接受修改后的条款。</p>
          <p>9.2 如果您不同意修改后的条款，您应停止使用本服务并可请求删除您的帐户。</p>
          <p>9.3 我们可以在任何时候以任何原因终止或暂停您对本服务的访问，恕不另行通知。</p>
        </Section>

        <Section n="11" title="争议解决">
          <p>因本条款引起的或与本条款有关的任何争议，应首先通过友好协商解决。如协商不成，任何一方均可将争议提交至有管辖权的人民法院解决。本条款受中华人民共和国法律管辖。</p>
        </Section>

        <Section n="12" title="联系方式">
          <p>如有任何关于本条款的问题或意见，请联系系统管理员，或通过系统内的「报告错误」功能提交反馈。</p>
        </Section>

        <div style={{ borderTop: "1px solid #e5e7eb", paddingTop: 24, marginTop: 48, textAlign: "center" }}>
          <BackLink style={{ color: "#2563eb", fontSize: 13, textDecoration: "none" }} />
        </div>
      </div>
    </div>
  );
}

function Section({ n, title, children }: { n: string; title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: 32 }}>
      <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 10 }}>{n}. {title}</h2>
      <div style={{ fontSize: 14, color: "#444" }}>{children}</div>
      <style>{`
        section ul { padding-left: 22px; margin: 8px 0; }
        section li { margin-bottom: 6px; }
        section p { margin-bottom: 10px; }
      `}</style>
    </section>
  );
}
