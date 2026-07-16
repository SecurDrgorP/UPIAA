# UPIAA Agentic AI Platform — Complete Node & Expression Guide

Welcome to the definitive guide for all execution and trigger nodes in the UPIAA platform. This document covers the architecture of the workflow engine, how data flows between nodes, how to write expressions, and details every single node type in the system.

---

## 1. UPIAA Core Architecture & Data Flow

UPIAA is a distributed, event-driven, agentic workflow orchestration engine built on top of **Temporal**. 

### How Nodes Execute
* **State Isolation**: Each node runs as a separate Temporal activity. When a node executes, it receives a configuration object (`config`) and a flat-merged dictionary of all preceding outputs (`input_data`), along with the full execution history (`context`).
* **Non-Blocking Long Polling**: Triggers and listener nodes (like Webhooks, Discord, Telegram, and Email receivers) run as long-lived activities (up to 7 days). They perform non-blocking polling loops using `asyncio.sleep()` and periodically send Temporal activity heartbeats (`temporalio.activity.heartbeat`) to avoid activity timeouts.
* **Output Mapping**: Every action node supports an `output_mapping` key. This allows renaming keys or extracting nested properties before forwarding results to downstream nodes.

### Data Flow Contract
When a node completes successfully, its output is stored in the execution state under its unique `node_id` (e.g., `node_3`, `telegram_trigger`).
* **Flat Merging**: The orchestrator merges the outputs of all upstream nodes into a single flat `input_data` dictionary. 
* **Key Collision Protection**: If multiple upstream nodes return the same keys (e.g., `response`), they will overwrite each other in the flat `input_data`. The engine logs a debug warning. You can resolve collisions by referencing the namespaced syntax described below.

---

## 2. The Expression Engine

UPIAA uses a custom regex-based template interpolation engine to inject dynamic data into config fields. Any field marked as `"template": True` supports this syntax.

### Basic Shorthand Syntax (Recommended)
You can directly reference variables in the format `{{node_id.property}}`:
* **Top-Level Field**: `{{node_3.response}}`
* **Nested Object Properties**: `{{node_3.response.user.email}}`
* **List Elements**: `{{node_3.messages[0].content}}` or `{{node_3.pages[2].meta.title}}`

### Advanced Namespaced Syntax
If key collisions occur, or you need to be explicit:
* **`$node` Prefix**: Accesses specific node outputs.
  `{{$node.node_3.response}}`
* **`$input` Prefix**: Accesses the flat-merged input dictionary.
  `{{$input.response}}`

### Self-References & Forward References
On the first iteration of loops or published workflows, some nodes might have self-referential inputs (e.g., a Discord Receive node specifying `After Message ID` as its own previous `last_message_id`). 
* **Safe Resolution**: The engine automatically catches self/forward references to nodes that have not yet run and resolves them to an empty string (`""`) without raising errors or warning logs.

---

## 3. The Condition Evaluator (Control Flow Gating)

Conditional nodes (e.g., `control_flow` and `cron_conditional`) evaluate boolean logic using a safe, restricted Python AST (Abstract Syntax Tree) parser. This restricts execution to safe operators and prevents arbitrary code execution (`eval` is never used).

### Supported Expressions & Operators
* **Constants**: Strings (`'active'`), numbers (`100`, `0.5`), booleans (`True`, `False`), and `None`.
* **Logical Operators**: `and`, `or`, `not`
* **Comparison Operators**: 
  * Equality & Inequality: `==`, `!=`
  * Ordering: `<`, `<=`, `>`, `>=`
  * Identity: `is`, `is not`
  * Membership: `in`, `not in` (e.g., `'error' in input_data['node_2']['response']`)
* **Variables**:
  - `input_data`: Accesses flat-merged inputs. E.g., `input_data['node_3']['status']`
  - `context`: Accesses namespaced execution history. E.g., `context['node_3']['response']`
* **Subscripts / Indexing**:
  - Dictionary Lookups: `input_data['node_3']['key']`
  - List Indexing: `context['node_4']['items'][0]` (returns `None` if the list is empty or index is out of bounds, avoiding index crashes).

### LLM Response Sanitization
When using LLMs to generate conditional logic, they frequently output strings with artifacts like trailing periods, quotation marks, or mixed casing. UPIAA automatically pre-processes conditions to ensure compatibility:
* Strips trailing periods: `True.` $\rightarrow$ `True`
* Strips surrounding quotes: `"True"` $\rightarrow$ `True`
* Standardizes casing: `true` / `false` $\rightarrow$ `True` / `False`

---

## 4. Node Reference Manual

Below is the catalog of all execution and trigger nodes, organized by functional categories.

---

### Category: Control Flow & Triggers

#### 🔀 Control Flow (`control_flow`)
* **Description**: Branches the workflow based on the evaluation of a boolean expression.
* **Config Keys**:
  - `condition` (string, required): The Python AST-compatible boolean expression.
* **Output fields**:
  - `condition` (string): The evaluated expression.
  - `result` (boolean): Evaluation result.
  - `message` (string): Readable summary.

#### 🔗 Webhook Trigger (`webhook_trigger`)
* **Description**: Starts the workflow when an external HTTP request hits its unique URL.
* **Config Keys**:
  - `path` (string, required): The URL identifier segment (default: `"trigger"`).
* **Output fields**:
  - `method` (string): `GET`, `POST`, etc.
  - `path` (string): Webhook path.
  - `workflow_id` (string): Workflow ID.
  - `headers` (dict): HTTP headers.
  - `query` (dict): URL query parameters.
  - `body` (dict): Parsed JSON body.
  - `raw_body` (string): Raw body text.
  - `received_at` (float): Unix timestamp.
  - `client_ip` (string): IP of the caller.

#### ⏰ Timer / Cron Trigger (`timer_cron`)
* **Description**: Blocks workflow execution and waits until the current calendar time matches a recurring schedule (daily, weekly, monthly, or yearly), then releases downstream nodes.
* **Config Keys**:
  - `trigger_time` (string, required): 24-hour format time, e.g., `14:30`.
  - `recurrence` (select, required): `daily`, `weekly`, `monthly`, `yearly`.
  - `week_day` (select): `Monday`, `Tuesday`, etc. (used in weekly).
  - `month_day` (number): Day of month 1-31 (used in monthly).
  - `year_day` (number) / `year_month` (select): Day and month (used in yearly).
  - `timezone` (string): IANA timezone string (e.g., `Africa/Casablanca`).
* **Output fields**:
  - `matched` (boolean): `true` when triggered.
  - `fired_at` (string): ISO timestamp.
  - `recurrence` (string): Selected recurrence.
  - `timezone` (string): Active timezone.
  - `reason` (string): Detailed triggering explanation.
  - `waited_seconds` (number): Total duration spent waiting.

#### ⏰ Timer (`timer`)
* **Description**: Pauses workflow execution for a fixed interval or schedules it using a cron expression.
* **Config Keys**:
  - `schedule_type` (select, required): `interval` or `cron`.
  - `interval_seconds` (number): Duration to sleep (in interval mode).
  - `cron_expression` (string): Cron expression (in cron mode).
* **Output fields**:
  - `executed_at` (string): ISO timestamp.
  - `schedule` (string): Selected schedule type.
  - `interval_seconds` (number): Wait duration.

#### ⏰ Cron Conditional (`cron_conditional`)
* **Description**: A trigger node that schedules executions via cron expressions with an optional condition check gating the workflow.
* **Config Keys**:
  - `cron_expression` (string, required): E.g., `*/5 * * * *`.
  - `condition` (string): Optional boolean gating check.
  - `timezone_offset` (string): Display timezone label.
* **Output fields**:
  - `fired_at` (string): ISO timestamp of execution.
  - `next_fire_at` (string): Next expected fire time.
  - `cron_expression` (string): Applied expression.
  - `condition_passed` (boolean): Whether the condition check succeeded.
  - `condition_expr` (string): The condition checked.

---

### Category: LLM & Model Actions

#### ✨ Gemini Action (`gemini_action`)
* **Description**: Execute text tasks using Google Gemini.
* **Config Keys**:
  - `api_key` (string, required, secret): Gemini API Key.
  - `model` (string): E.g. `gemini-1.5-pro`, `gemini-1.5-flash`.
  - `prompt_template` (string, template): Main prompt. Supports markdown code fences. Auto-parses JSON.
  - `system_prompt` (string): Custom persona instructions.
  - `temperature` (number): Creativity settings (0.0 to 2.0).
  - `inject_context` (boolean): If `ON`, metadata is wrapped in instructions.
* **Output fields**:
  - `response` (dict/string): Model output.
  - `model` (string): Model ID.
  - `input_tokens` (number): Prompt token count.
  - `output_tokens` (number): Candidate token count.
  - `finish_reason` (string): Termination reason.

#### 🦙 Ollama Action (`ollama_action`)
* **Description**: Local LLM text tasks using an Ollama server.
* **Config Keys**:
  - `ollama_url` (string): E.g., `http://host.docker.internal:11434`.
  - `model` (string): E.g., `llama3.1`, `mistral`.
  - `prompt_template` / `system_prompt` / `temperature` / `inject_context`.
* **Output fields**: Identical to Gemini action.

#### 🧠 OpenAI Action (`openai_action`)
* **Description**: Text tasks using OpenAI GPT models.
* **Config Keys**:
  - `api_key` (string, required, secret): OpenAI API Key.
  - `model` (string): E.g., `gpt-4o`, `gpt-4o-mini`.
  - `prompt_template` / `system_prompt` / `temperature` / `inject_context`.
* **Output fields**: Identical to Gemini action.

#### 🌐 OpenRouter Action (`openrouter_action`)
* **Description**: Multi-provider inference via OpenRouter APIs.
* **Config Keys**:
  - `api_key` (string, required, secret): OpenRouter API Key.
  - `model` (string): E.g., `meta-llama/llama-3-70b-instruct`.
  - `prompt_template` / `system_prompt` / `temperature` / `inject_context`.
* **Output fields**: Identical to Gemini action.

#### 🧠 ML Model (`model_action`)
* **Description**: Runs predictive inference using uploaded scikit-learn, ONNX, PyTorch, or Keras models.
* **Config Keys**:
  - `model_name` (string, required): Identifier label.
  - `algorithm` (select, required): E.g., `RandomForest`, `SVM`, `NeuralNetwork`.
  - `task_type` (select): `classification`, `regression`, `clustering`, `anomaly_detection`.
  - `input_type` (select): `features`, `text`, `image`, `json`.
  - `features` (string): Comma-separated names for tabular columns.
  - `model_path` (file upload): Model file binary.
  - `preprocessing` (code): JSON configuration.
* **Output fields**:
  - `model_name` / `algorithm` / `task_type` / `input_type` / `status` (string).
  - `prediction` (string/number): Prediction result.
  - `confidence` (number): Confidence percentage.
  - `probabilities` (dict): Key-value pairs for class chances.

---

### Category: System & Integration Actions

#### 🌐 HTTP Request (`http_request`)
* **Description**: SSRF-safe client that hits external web APIs.
* **Config Keys**:
  - `url` (string, required, template): Target API.
  - `method` (select, required): `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`.
  - `headers` (object): Headers dict.
  - `body` (object): Payload dict.
* **Output fields**:
  - `status_code` (number): HTTP status.
  - `response_body` (dict/string): Decoded body.
  - `response_headers` (dict): Headers dictionary.
  - `elapsed_ms` (number): Latency.

#### 🕷️ Web Scrape (`web_scrape`)
* **Description**: Scrapes URLs, crawling recursively, to extract semantic blocks, metadata, emails, and images.
* **Config Keys**:
  - `url` (string, required, template): Starting URL.
  - `extra_urls` (string): Optional extra urls, one per line.
  - `crawl_depth` (number): Maximum link-hops to follow (0-3).
  - `same_domain_only` (boolean): Crawl on same domain only.
  - `max_pages` (number): Limit total pages (default 10, max 50).
  - `concurrency` (number): Concurrency throttle (default 3, max 10).
  - `output_format` (select): `full`, `summary_only`, `blocks_only`.
* **Output fields**:
  - `page_count` (number): Pages processed.
  - `pages` (list): Trimmed page dict array.
  - `meta` / `content` / `extracted` / `semantic` (dict, single URL shortcut).

#### 🔍 Web Search & Scrape (`web_search_scrape`)
* **Description**: Performs queries via search APIs and parses results.
* **Config Keys**:
  - `query` (string, required, template): Search keyword.
  - `search_provider` (select, required): `brave`, `serpapi`, `google_cse`.
  - `api_key` (string, required, secret): Provider token.
  - `google_cx` (string): Google Search engine ID.
  - `result_count` (number): Results to list (default 5, max 20).
  - `scrape_results` (boolean): Scrape resulting websites.
* **Output fields**:
  - `query` (string): Searched term.
  - `provider` (string): Selected search platform.
  - `result_count` (number): Results count.
  - `search_results` (list): Array of URL details.
  - `scraped_pages` (list): Array of parsed pages.

#### 🔍 OCR Image (`ocr_image`)
* **Description**: Extracts strings from files/URLs using Tesseract OCR.
* **Config Keys**:
  - `source_type` (select, required): `file`, `url`, `base64`.
  - `image_path` / `image_url` / `image_base64` (string).
  - `language` (string): Language code, e.g. `eng`.
* **Output fields**:
  - `text` (string): Recognized text.
  - `word_count` (number): Total words.
  - `source_type` (string): Selected source format.
  - `language` (string): Applied language code.

#### 📊 Excel Document (`excel_document`)
* **Description**: Reads or creates Microsoft Excel spreadsheet files.
* **Config Keys**:
  - `mode` (select, required): `read` or `write`.
  - `file_path` (string): Input sheet path (read mode).
  - `output_path` (string): Save path (write mode).
  - `sheet_name` (string): Sheet tab identifier.
  - `rows_json` (string): Array to insert.
* **Output fields** (Read mode):
  - `mode` / `file_path` (string).
  - `rows` (list): Row list dict.
  - `row_count` (number): Rows count.
  - `headers` (list): Col headers.

#### 📄 Word Document (`word_document`)
* **Description**: Reads or creates Microsoft Word (.docx) files.
* **Config Keys**:
  - `mode` (select, required): `read` or `write`.
  - `file_path` (string): Input document path.
  - `output_path` (string): Output save path.
  - `content` (string): Text content to write.
* **Output fields** (Read mode):
  - `mode` (string).
  - `text` (string): Extracted text.
  - `word_count` (number): Text word count.

---

### Category: Communication & Notification

#### 📨 Discord: Receive Messages (`discord_receive_messages`)
* **Description**: Fetch channel text streams or wait/poll for a new message.
* **Config Keys**:
  - `bot_token` (string, secret): Discord bot token.
  - `channel_id` (string, required): Discord Channel ID.
  - `wait_for_new` (boolean): blocks/polls for new messages.
  - `limit` (number): Number of messages to retrieve (Snapshot mode).
  - `after_message_id` (string): Cursor anchor.
* **Output fields**:
  - `messages` (list): Array of Discord messages.
  - `count` (number): Message count.
  - `last_message_id` (string): ID of the newest message in this batch.
  - `channel_id` (string): Monitored channel.
  - `timed_out` (boolean): Wait timeout flag.

#### 📬 Email Listener (IMAP) (`receive_email`)
* **Description**: Blocking IMAP monitor that waits for incoming emails matching filter rules.
* **Config Keys**:
  - `imap_host` / `imap_port` (string/number): Defaults to `imap.gmail.com` / `993`.
  - `email` / `app_password` (string/secret): IMAP login.
  - `folder` (string): Mail folder (default `INBOX`).
  - `subject_filter_mode` (select): `none`, `contains`, `exact`, `regex`.
  - `subject_filter_value` (string): Text/regex subject query.
  - `subject_case_sensitive` (boolean): Filter casing flag.
  - `include_attachments` (boolean): Save file attachments.
* **Output fields**:
  - `subject` / `from` / `to` / `date` / `body` / `uid` / `folder` (string).
  - `matched_filter` (string): Filter mode match.
  - `wait_time_seconds` (number): Total wait.
  - `attachments` (list): Attachment array (base64).

#### 🎮 Discord Notifier (`discord_notify`)
* **Description**: Sends messages or rich embeds to Discord.
* **Config Keys**:
  - `bot_token` (string, required, secret) / `channel_id` (string, required).
  - `message` (string, required, template): Markdown content.
  - `embed_enabled` (boolean): rich card embed toggle.
  - `embed_title` (string) / `embed_color` (number).
* **Output fields**:
  - `sent` (boolean): `true`.
  - `message_id` (string): Discord message ID.
  - `channel_id` (string): Target channel ID.

#### 📎 Discord: Send File (`discord_send_attachment`)
* **Description**: Sends a local file as an attachment to a Discord channel via multipart upload. Designed to follow document-generation nodes (`word_document`, `excel_document`) in the workflow.
* **Config Keys**:
  - `bot_token` (string, required, secret): Discord Bot token.
  - `channel_id` (string, required, template): Target Discord channel ID.
  - `file_path` (string, required, template): Absolute path to the file. Must be under `/tmp/UPIAA_docs/`. E.g. `/tmp/UPIAA_docs/{{node_d1.response}}.docx`.
  - `message` (string, optional, template): Caption text above the attachment.
  - `embed_enabled` (boolean): Add a rich embed card alongside the file.
  - `embed_title` (string, template) / `embed_color` (number): Embed customization.
* **Output fields**:
  - `sent` (boolean): `true`.
  - `message_id` (string): Discord message ID.
  - `file_name` (string): Basename of the uploaded file.
  - `channel_id` (string): Target channel ID.
* **Limits**: Max 25 MB file size (Discord upload limit). Files must be under `/tmp/UPIAA_docs/`.

#### 💬 Telegram Trigger (`telegram_trigger`)
* **Description**: Listens for incoming Telegram messages using long-polling (`getUpdates`). Place this node at the **start** of a workflow — it blocks until a qualifying message arrives, then passes it downstream.
* **Setup**: Talk to **@BotFather** on Telegram → create a bot → copy the token (shaped like `123456:ABC-DEF...`). Paste it into the Bot Token field, or save it once as a credential bundle and reference by Credential ID.
* **Config Keys**:
  - `credential_id` (string): Optional encrypted credential bundle containing `bot_token`.
  - `bot_token` (string, secret): Manual bot token — only shown when no credential bundle is selected.
  - `allowed_chat_ids` (string): Comma-separated list of numeric chat IDs to accept. Leave empty = accept from anyone.
  - `allowed_usernames` (string): Comma-separated list of usernames (without @) to accept. Leave empty = accept from anyone.
  - `filter_message_types` (select): `text` (default) or `all`. Text-only skips photos, stickers, documents.
  - `poll_timeout` (number): Long-poll timeout in seconds (default 25, max 30).
* **Output fields**:
  - `message_id` (number): Telegram message ID.
  - `chat_id` (number): Chat the message came from — pass this to `telegram_send_message` to reply.
  - `chat_type` (string): `"private"`, `"group"`, `"supergroup"`, or `"channel"`.
  - `from_user_id` (number): Sender's Telegram user ID.
  - `from_username` (string): Sender's @username (if set).
  - `from_name` (string): Sender's display name.
  - `text` (string): The message text.
  - `date` (number): Unix timestamp.
  - `raw_update` (dict): Full Telegram Update object for advanced use.
* **Typical pairing**: Wire `{{telegram_trigger.chat_id}}` into a downstream `telegram_send_message` node's Chat ID field to reply into the same conversation.

#### ✈️ Telegram: Send Message (`telegram_send_message`)
* **Description**: Sends messages to a Telegram chat.
* **Config Keys**:
  - `credential_id` (string): Optional encrypted credential bundle containing `bot_token`.
  - `bot_token` (string, secret) / `chat_id` (string, required, template).
  - `message` (string, required, template): Message body.
  - `parse_mode` (select): `Markdown`, `MarkdownV2`, `HTML`, or plain text.
  - `disable_notification` (boolean): Silent delivery.
  - `reply_to_message_id` (string).
* **Output fields**:
  - `sent` (boolean): `true`.
  - `message_id` (number): Telegram message ID.
  - `chat_id` (string): Sent chat.
  - `text` (string): Sent text.

#### 📎 Telegram: Send Document (`telegram_send_document`)
* **Description**: Sends a local file as a document to a Telegram chat via multipart upload. Designed to follow document-generation nodes in the workflow.
* **Config Keys**:
  - `credential_id` (string): Optional encrypted credential bundle containing `bot_token`.
  - `bot_token` (string, secret): Manual bot token — only shown when no credential bundle is selected.
  - `chat_id` (string, required, template): Target chat, group, or channel ID.
  - `file_path` (string, required, template): Absolute path to the file. Must be under `/tmp/UPIAA_docs/`.
  - `caption` (string, optional, template): Caption sent with the document.
  - `parse_mode` (select): `Markdown`, `MarkdownV2`, `HTML`, or plain text.
  - `disable_notification` (boolean): Silent delivery.
* **Output fields**:
  - `sent` (boolean): `true`.
  - `message_id` (number): Telegram message ID.
  - `file_name` (string): Basename of the uploaded file.
  - `chat_id` (string): Target chat ID.
* **Limits**: Max 50 MB file size (Telegram Bot API limit). Files must be under `/tmp/UPIAA_docs/`.

#### 📤 Send Email (`send_email`)
* **Description**: Sends emails via SMTP hosts, with optional file attachments.
* **Config Keys**:
  - `smtp_host` / `smtp_port` (string/number): Defaults to `smtp.gmail.com` / `587`.
  - `email` / `app_password` (string/secret): SMTP authentication.
  - `to` (string, required, template): Recipient addresses (comma-separated).
  - `subject` / `body` (string, template).
  - `content_type` (select): `plain` or `html`.
  - `attachments` (string, template): JSON array of local file paths to attach. E.g. `["/tmp/UPIAA_docs/report.docx"]`. Files must be under `/tmp/UPIAA_docs/`. Parsed with `json.loads()` (consistent with `excel_document`'s `rows_json` pattern).
* **Output fields**:
  - `sent` (boolean): `true`.
  - `from` / `to` / `subject` (string).
  - `attachment_count` (number): Number of attached files (when attachments provided).
  - `attachments` (list): List of attached filenames (when attachments provided).
