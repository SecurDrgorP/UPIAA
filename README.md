<div align="center">
  <h1>M8M: Agentic Workflow Automation</h1>
  <p><strong>A production-grade, agentic workflow engine featuring real-time AI execution, resilient LLM interactions, and reactive visual orchestration.</strong></p>

  [![Angular](https://img.shields.io/badge/Angular-DD0031?style=for-the-badge&logo=angular&logoColor=white)](https://angular.io/)
  [![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)
  [![Temporal](https://img.shields.io/badge/Temporal-000000?style=for-the-badge&logo=temporal&logoColor=white)](https://temporal.io/)
  [![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
</div>

---

## Overview & Architecture

M8M is an advanced agentic workflow automation platform designed to reliably execute complex, multi-step processes involving LLMs and external integrations. Built for scale and resilience, it orchestrates automated tasks using Temporal to guarantee state durability across node executions.

The system is structured into four primary tiers:
- **Frontend (Angular 17):** A reactive visual node editor powered by Tailwind CSS and real-time SSE (Server-Sent Events) for live execution feedback and state management.
- **Backend (FastAPI & Python):** The core API layer handling workflow definitions, secure credential management, internal execution routing, and file sync logic.
- **Systems (FastAPI & Python):** A decoupled, highly-secure container isolated for AI orchestration, dynamic node generation, AST validation, and LLM state tracking safely sandboxed from the backend core.
- **Core Engine (Temporal Workers):** Durable, background workers handling complex node execution flows—ensuring resilient retry mechanisms for LLM calls and stateful continuity.
- **Infrastructure (Docker):** Fully containerized production environment integrating PostgreSQL (persistence), Redis (caching), and Nginx (reverse proxy routing).

## Key Features

- **Agentic AI Builder:** Connects securely to local and remote LLMs (Ollama) with enforced structured output utilizing Pydantic.
- **Resilient Workflows:** Backed by Temporal for durable node execution, intelligent retries, and absolute state persistence through any application failure.
- **Real-Time Visibility:** Uses Server-Sent Events (SSE) to stream live progress, status updates, and diagnostic outputs straight to the frontend.
- **Secure Nodes:** Encrypted credential storage (e.g., email app passwords) for external integrations, keeping workflow configurations safe.
- **Visual Orchestrator:** An intuitive Angular-based node interaction graph with reactive group grouping and dynamic output sidebars.
- **Production-Ready Infra:** Hardened `docker-compose` orchestration with health checks and zero-downtime strategies.

## Getting Started

Follow these steps to initialize the complete production stack locally using Docker.

### Prerequisites
- [Docker Engine & Docker Compose](https://docs.docker.com/get-docker/) installed.
- (Optional) Local installation of Node.js 20+ and Python 3.11+ for non-containerized UI/API development.

### Installation

1. **Clone the repository and set up environments:**
   Navigate into the infrastructure folder to spin up the M8M stack:
   ```bash
   cd infra
   
   # Duplicate the example environment file
   cp .env.example .env
   ```
   *(Be sure to update any specific keys like `SECRET_VAULT_KEY` or `OLLAMA_HOST` in `.env` if required.)*

2. **Boot the complete stack:**
   ```bash
   docker-compose up -d --build
   ```
   This will spin up Postgres, Redis, Temporal, the FastAPI Backend, Temporal Workers, the Angular Frontend, and an Nginx reverse proxy.

3. **Verify Health:**
   Wait a moment for all containers to report as healthy. You can check the proxy health status:
   ```bash
   curl http://localhost:80/health
   ```

## Usage

Once the stack is spun up, you can interact with the different layers:

- **Web Interface:** Open `http://localhost:80` in your browser to access the M8M Visual Builder. Here you can drag, drop, and connect execution nodes.
- **API Documentation:** Visit `http://localhost:8000/docs` to test the FastAPI backend endpoints manually.
- **Temporal Web UI:** Access the local Temporal cluster UI typically on `http://localhost:8233` (if exposed, or use `tctl` within the docker network) to inspect workflow execution history.

**Example Local Dev Execution:**
If you prefer running the backend manually for development:
```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

## Repository Structure

```text
M8M/
M8M/
├── backend/            # FastAPI core, Temporal workflows & HTTP routing
│   ├── ai_nodes/       # Auto-loaded AI generated node implementations (bind mounted)
│   ├── core/           # Configuration, security vaults, and registry 
│   ├── nodes/          # Native workflow node logic schemas
│   ├── routes/         # HTTP API definitions protecting internal scopes
│   ├── storage/        # Database storage wrappers & Alembic migrations
│   ├── main.py         # App entry point
│   └── worker.py       # Temporal worker daemon
├── systems/            # Decoupled AI subsystem layer
│   ├── system1/        # Automatic node generator & validation pipeline
│   ├── system2/        # Natural language workflow intent planner
│   ├── system3/        # Hermies conversational AI memory client
│   ├── system4/        # Safe AST code validation components
│   └── shared/         # HTTP and LLM contracts
├── frontend/           # Angular 17 UI & Visual Editor
│   ├── src/            # Components, SSE state services, and page layouts
│   ├── public/         # Static assets
│   ├── package.json    # Dependencies
│   └── tailwind.*      # CSS styling configuration
├── infra/              # Containerization orchestration
│   ├── nginx/          # Reverse proxy configurations
│   ├── postgres/       # Init scripts
│   ├── docker-compose.yml
│   └── .env.example
└── README.md
```