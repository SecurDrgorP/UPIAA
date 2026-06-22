/* ─── Workflow Models ──────────────────────────────────────────────── */

export type NodeType =
    | 'model_action'
    | 'ollama_action'
    | 'openai_action'
    | 'gemini_action'
    | 'openrouter_action'
    | 'if_else'
    | 'for_loop'
    | 'while_loop'
    | 'http_request'
    | 'send_email'
    | 'receive_email'
    | 'word_document'
    | 'excel'
    | 'ocr'
    | 'timer'
    | 'cron_conditional'
    | 'timer_cron'
    | 'web_scrape'
    | 'web_search_scrape'
    | 'webhook_trigger'
    | 'telegram_trigger'
    | 'discord_receive_messages'
    | 'discord_notify'
    | 'discord_send_attachment'
    | 'telegram_send_document'
    | 'comment'
    | 'group';

export type NodeCategory = 'Model Actions' | 'LLM Actions' | 'Logic' | 'Actions' | 'Communication' | 'Triggers' | 'Control Flow';

export interface WorkflowNode {
    id: string;
    type: NodeType;
    label: string;
    config: Record<string, any>;
    position: { x: number; y: number };
}

export interface WorkflowEdge {
    id: string;
    source: string;
    target: string;
    sourceHandle?: string;
}

export interface Workflow {
    id: string;
    name: string;
    nodes: WorkflowNode[];
    edges: WorkflowEdge[];
    is_published?: boolean;
    created_at?: string;
    updated_at?: string;
}

export type ExecutionStatusType = 'idle' | 'running' | 'success' | 'failed' | 'error' | 'complete' | 'skipped';

export interface ExecutionStatus {
    node_id: string;
    node_label: string;
    status: ExecutionStatusType;
    result?: any;
    error?: string;
    error_type?: string;
    message?: string;
    invalid_config_fields?: string[];
}

export interface NodeConfigField {
    key?: string;
    type: string;
    default?: any;
    required?: boolean;
    template?: boolean;
    dotpath?: boolean;
    label?: string;
    description?: string;
    placeholder?: string;
    secret?: boolean;
    multiline?: boolean;
    options?: any[];
    enum?: string[];
    visibleWhen?: Record<string, any>;
    min?: number;
    max?: number;
}

/**
 * Normalized field descriptor used by the universal sidebar auto-renderer.
 * Built from either dict-based or list-based config_schema.
 */
export interface ConfigFieldDescriptor {
    key: string;
    type: string;
    label: string;
    description: string;
    placeholder: string;
    required: boolean;
    secret: boolean;
    default?: any;
    options?: { value: string; label: string; description?: string }[] | null;
    multiline: boolean;
    visibleWhen?: Record<string, any> | null;
    template: boolean;
    dotpath?: boolean;
    min?: number;
    max?: number;
}

export interface NodeTypeInfo {
    type: NodeType;
    label: string;
    icon: string;
    description: string;
    category: NodeCategory;
    default_config: Record<string, any>;
    example_output?: Record<string, any>;
    config_schema?: any;  // Dict or Array — handled by buildConfigFields()
}

/* ─── Canvas types ───────────────────────────────────────────────── */

export interface GraphNode {
    id: string;
    label: string;
    data: {
        type: NodeType;
        config: Record<string, any>;
        status: ExecutionStatusType;
        result?: any;
    };
    position?: { x: number; y: number };
}

export interface GraphLink {
    id: string;
    source: string;
    target: string;
    sourceHandle?: string;
}
