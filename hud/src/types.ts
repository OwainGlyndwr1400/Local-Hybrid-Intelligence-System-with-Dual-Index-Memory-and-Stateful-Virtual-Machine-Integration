export interface TelemetryNode {
  name: string;
  role: string;
  operator: string;
}

export interface TelemetryModels {
  light: string;
  heavy: string;
  embedding: string;
}

export interface IndexInfo {
  chunks: number;
  built_at: string | null;
}

export interface Telemetry {
  node: TelemetryNode;
  models: TelemetryModels;
  indexes: {
    identity: IndexInfo | null;
    knowledge: IndexInfo | null;
  };
  retrieval: {
    top_k_identity: number;
    top_k_knowledge: number;
    max_chunk_chars: number;
    min_score: number;
    dedup_memory: boolean;
    restore_history_turns: number;
  };
  tunable: string[];
}

export interface SettingsUpdate {
  retrieval_top_k_identity?: number;
  retrieval_top_k_knowledge?: number;
  min_retrieval_score?: number;
  max_chunk_chars?: number;
  dedup_memory_by_conversation?: boolean;
  restore_history_turns?: number;
}

export interface DreamCycleState {
  last_consolidated_turn_id: string | null;
  last_consolidated_timestamp: number;
  total_consolidated: number;
  last_run_at: string | null;
  last_run_count: number;
}

export interface DreamStatus {
  pending: number;
  state: DreamCycleState;
}

export interface DreamRunResult {
  consolidated: number;
  skipped: boolean;
  reason?: string;
  index_size?: number;
  cluster_assignments?: number;
  state: DreamCycleState;
}

export interface VoiceOption {
  id: string;
  label: string;
  accent: string;
  gender: string;
}

export interface VoicesPayload {
  model: string;
  default_voice: string;
  voices: VoiceOption[];
}

export type VoiceProvider = "browser" | "kokoro_onnx" | "lm_studio";

export type STTProvider = "browser" | "whisper";

export interface TranscribeResponse {
  text: string;
  language: string;
  language_probability: number;
  duration: number;
  segments: { start: number; end: number; text: string }[];
}

export interface Hit {
  score: number;
  metadata: Record<string, unknown>;
  cluster_id?: string | null;
}

export interface AtlasCluster {
  id: string;
  lane: "identity" | "knowledge";
  label: string;
  size: number;
  representative_text: string;
}

export interface AtlasEdge {
  a: string;
  b: string;
  weight: number;
}

export interface AtlasData {
  version: number;
  built_at: string;
  embedding_dim: number;
  clusters: AtlasCluster[];
  edges: AtlasEdge[];
  chunk_to_cluster: Record<string, string>;
}

export interface ClusterMember {
  chunk_id: string;
  conversation_id?: string;
  conversation_title?: string;
  subject?: string;
  agent?: string;
  source?: string;
  sigil?: string;
  urgency_score?: number;
  urgency_weight?: number;
  create_time_first?: number;
  create_time_last?: number;
  text: string;
  dream_consolidated?: boolean;
  pendinium_anchor?: string;
}

export interface ClusterContents {
  cluster: AtlasCluster;
  total_members: number;
  shown: number;
  members: ClusterMember[];
}

export interface UREVMTraceEntry {
  tick: number;
  cycle_position: number;
  opcode: number;
  name: string;
  plane: string;
  operand: Record<string, unknown> | null;
  result: Record<string, unknown>;
  timestamp: number;
}

export interface QuaternionComponents {
  "α": number;
  "β": number;
  "γ": number;
  "δ": number;
  norm: number;
}

export interface UREVMState {
  tick: number;
  cycle_position: number;
  impedance_accumulator: number;
  forbidden_resets: number;
  ticks_until_361: number;
  near_forbidden: boolean;
  r23_norm: number;
  r23_phi_gap: number;
  r23_components: QuaternionComponents;
  observer_r12: QuaternionComponents;
  now_r11: QuaternionComponents;
  center_anchor: number;
  rotational_residual: number;
  recent_ops: UREVMTraceEntry[];
}

export interface NephilimState {
  coherence: number;
  r23_health: number;
  retrieval_health: number;
  witness_health: number;
  stable: boolean;
  lion_reset_fired: boolean;
}

export interface TriskelionLock {
  arm_real: number;
  arm_time: number;
  arm_observer: number;
  edge_a: number;
  edge_b: number;
  edge_c: number;
  vertical_beam: number;
  locked: boolean;
  weak: boolean;
  status: "strong" | "moderate" | "weak";
}

export interface Prediction {
  id: string;
  name: string;
  observable: string;
  value: string;
  falsifies_if: string;
  scale: string;
  status: "active" | "partial_confirmation" | "confirmed" | "falsified";
  load_bearing: boolean;
}

export interface PredictionsPayload {
  version: number;
  source: string;
  updated: string;
  predictions: Prediction[];
}

export interface ToolCallRecord {
  name: string;
  arguments: Record<string, unknown>;
  result_preview: string;
}

export interface DoneEvent {
  session_id: string;
  model: string | null;
  retrieved: {
    identity: Hit[];
    knowledge: Hit[];
  };
  tokens: {
    prompt: number | null;
    completion: number | null;
    total: number | null;
  };
  turn_count: number;
  urevm?: UREVMState;
  nephilim?: NephilimState;
  triskelion?: TriskelionLock;
  tool_calls?: ToolCallRecord[];
  // Phase 33 — true when the operator's message triggered deep-think mode
  // (e.g. "lumos deep think on this"). HUD shows a 🧠 badge on the turn.
  deep_think?: boolean;
  // Phase 35 — tool routing decision for this turn. Tier ∈
  // {"chat","default","routed","full"}; tool_count is how many schemas were
  // sent to LM Studio; matched_categories shows which keyword groups fired.
  tool_routing?: {
    tier: "chat" | "default" | "routed" | "full";
    tool_count: number;
    matched_categories: string[];
  } | null;
  // Phase 36 — model routing reason (one of: vision, deep_think, keyword,
  // long_msg, light_default) and the swap outcome from LM Studio.
  model_route_reason?: string | null;
  model_swap?: {
    target: string;
    was_loaded: boolean;
    swap_performed: boolean;
    ok: boolean;
    polled: boolean;
  } | null;
}

export interface AttachedImage {
  data_url: string;
  mime: string;
  name: string;
  size: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  images?: AttachedImage[];
  doneInfo?: DoneEvent;
  error?: string;
  // Phase 36 — set when LM Studio is JIT-loading a different model for this
  // turn. Cleared when the first content delta arrives.
  modelSwapPending?: { target: string; reason: string };
}
