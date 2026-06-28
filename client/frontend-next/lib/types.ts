export interface Source { rank: number; chunk_id: string; doc_id: string; modality: string; text: string; score: number; method?: string; id?: number; filename?: string; page?: number; section?: string; }
export interface TraceStep { node: string; detail: string; }
export interface ToolStep { tool: string; status: string; detail: string; duration_ms?: number; args?: Record<string, unknown>; }
export interface FileInfo { filename: string; download_url: string; size?: number; mtime?: number; created_at?: number; lines?: number; }
export interface ChatResponse { answer: string; session_id: string; sources: Source[]; trace: TraceStep[]; elapsed_ms: number; intent: string; rewritten_query?: string | null; }
export interface Message { id?: string; role: "user" | "assistant"; content: string; sources?: Source[]; trace?: TraceStep[]; ts: number; feedback?: "up" | "down" | null; tokens?: { input: number; output: number }; tokens_in?: number; tokens_out?: number; files?: FileInfo[]; thinking?: string; steps?: ToolStep[]; timeline?: Array<{ kind: string; node: string; detail: string; tool?: string; status: string; elapsed_ms?: number; id: string }>; todo?: Array<{ text: string; status: string }>; suggestions?: string[]; clarify?: { question: string; options: string[] }; orchestration?: { strategy?: string; members: Array<{ id: string; step?: number; role_label?: string; task?: string; status?: "pending" | "running" | "done"; elapsed_ms?: number; preview?: string }> }; created_at?: number; elapsed_ms?: number; }
export interface Session { id: string; title: string; messages: Message[]; created: number; pinned?: boolean; }
export interface Stats { total_chunks: number; hash_bits: number; index_size_kb: number; llm_ready: boolean; cache_size?: number; active_model?: string; modalities?: Record<string, number>; }
export interface User { id: string; username: string; display_name: string; role: "admin" | "user" | "viewer"; created_at?: number; }
export interface ModelConfig { id: string; name: string; provider: string; base_url: string; model_name: string; is_default: number; temperature: number; max_tokens: number; config_json?: string; created_by?: string; created_at?: number; api_key?: string; }
export interface KB { id: string; name: string; description: string; allowed_roles: string; created_by: string; created_at: number; }
export interface AuditLog { id: number; user_id: string; username: string; action: string; detail: string; ip: string; ts: number; }
export interface IndexedDoc { doc_id: string; filename: string; chunks: number; modalities: string[]; }
export interface ConversationMeta { id: string; title: string; created_at: number; updated_at?: number; user_id?: string; pinned?: number; project_id?: string; }
export interface StreamDoneData { sources: Source[]; trace: TraceStep[]; steps?: ToolStep[]; elapsed_ms: number; session_id: string; intent?: string; tokens?: { input: number; output: number }; files?: FileInfo[]; thinking?: string; suggestions?: string[]; iterations?: number; }
export interface SkillData { name: string; description?: string; triggers?: string[]; prompt?: string; tools?: string[]; _path?: string; }

export const COLORS = [
  { name: "蓝", v: "#2563eb" }, { name: "青", v: "#0891b2" }, { name: "紫", v: "#7c3aed" },
  { name: "绿", v: "#059669" }, { name: "橙", v: "#d97706" }, { name: "粉", v: "#db2777" },
];
export const PROVIDERS = [
  { id: "openai", name: "OpenAI", url: "https://api.openai.com/v1" },
  { id: "anthropic", name: "Claude (Anthropic)", url: "https://api.anthropic.com" },
  { id: "deepseek", name: "DeepSeek", url: "https://api.deepseek.com/v1" },
  { id: "qwen", name: "通义千问", url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { id: "zhipu", name: "智谱 GLM", url: "https://open.bigmodel.cn/api/paas/v4" },
  { id: "moonshot", name: "Moonshot", url: "https://api.moonshot.cn/v1" },
  { id: "gemini", name: "Gemini", url: "https://generativelanguage.googleapis.com/v1beta/openai/" },
  { id: "mistral", name: "Mistral", url: "https://api.mistral.ai/v1" },
  { id: "xai", name: "xAI (Grok)", url: "https://api.x.ai/v1" },
  { id: "groq", name: "Groq", url: "https://api.groq.com/openai/v1" },
  { id: "openrouter", name: "OpenRouter", url: "https://openrouter.ai/api/v1" },
  { id: "siliconflow", name: "SiliconFlow 硅基流动", url: "https://api.siliconflow.cn/v1" },
  { id: "together", name: "Together", url: "https://api.together.xyz/v1" },
  { id: "ollama", name: "Ollama", url: "http://localhost:11434/v1" },
  { id: "lmstudio", name: "LM Studio", url: "http://localhost:1234/v1" },
  { id: "vllm", name: "vLLM", url: "http://localhost:8000/v1" },
  { id: "custom", name: "自定义", url: "" },
];
