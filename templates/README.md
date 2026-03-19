# Hivemind Project Templates

Pre-built DAG configurations for common project types. Pick a template and let the team build it.

## Available Templates

| Template | Description | Team Size | Est. Time |
|---|---|---|---|
| **SaaS Starter** | Full-stack SaaS with auth, billing, dashboard | Full Team | ~30 min |
| **REST API** | Production-ready FastAPI backend with PostgreSQL | Team | ~15 min |
| **React Dashboard** | Admin dashboard with charts, tables, dark mode | Team | ~20 min |
| **CLI Tool** | Professional Python CLI with Click | Solo | ~10 min |
| **Mobile App** | Cross-platform Expo + React Native app | Full Team | ~25 min |

## Usage

### From the Dashboard

1. Click **"+ New Project"**
2. Select **"From Template"**
3. Choose a template
4. The DAG is pre-configured — just hit **Execute**

### From the API

```bash
curl -X POST http://localhost:8080/api/projects \
  -H "Content-Type: application/json" \
  -d '{"template": "saas-starter", "working_dir": "/path/to/project"}'
```

## Creating Custom Templates

Templates are JSON files with this structure:

```json
{
  "name": "My Template",
  "description": "What this template builds",
  "version": "1.0.0",
  "tags": ["tag1", "tag2"],
  "estimated_time_minutes": 20,
  "team_size": "solo | team | full",
  "prompt": "The main prompt describing what to build",
  "dag_override": {
    "tasks": [
      {
        "id": "t1",
        "title": "Task description",
        "role": "agent_role",
        "dependencies": [],
        "artifacts": ["expected/output/files"]
      }
    ]
  }
}
```

Drop your JSON file in this directory and it will appear in the dashboard.

## Contributing Templates

We welcome community templates! See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines.
