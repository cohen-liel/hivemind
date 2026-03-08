# Mermaid Diagrams & Architecture Visualization

Generate Mermaid.js diagrams for system architecture, data flows, sequence diagrams, ERDs, and project planning. Use when the user asks to visualize architecture, create flowcharts, draw sequence diagrams, map data flows, or document system design.

## Triggers
- "draw a diagram", "create a flowchart", "architecture diagram"
- "sequence diagram", "data flow", "ERD", "entity relationship"
- "visualize the system", "map the architecture"
- "Mermaid diagram", "system diagram"
- When documenting or explaining complex systems

## Diagram Types

### 1. System Architecture (C4-style)
```mermaid
graph TB
    subgraph Frontend
        UI[React App] --> API[API Gateway]
    end
    subgraph Backend
        API --> Auth[Auth Service]
        API --> Core[Core Service]
        Core --> DB[(PostgreSQL)]
        Core --> Cache[(Redis)]
    end
```

### 2. Sequence Diagrams
```mermaid
sequenceDiagram
    participant U as User
    participant F as Frontend
    participant A as API
    participant D as Database
    U->>F: Click Submit
    F->>A: POST /api/data
    A->>D: INSERT INTO...
    D-->>A: OK
    A-->>F: 201 Created
    F-->>U: Success Toast
```

### 3. Data Flow / Pipeline
```mermaid
flowchart LR
    Input[Raw Data] --> Process[Transform]
    Process --> Validate{Valid?}
    Validate -->|Yes| Store[(Database)]
    Validate -->|No| Error[Error Queue]
    Store --> Notify[Send Notification]
```

### 4. Entity Relationship Diagrams
```mermaid
erDiagram
    USER ||--o{ PROJECT : owns
    PROJECT ||--|{ TASK : contains
    USER ||--o{ TASK : assigned_to
```

### 5. State Diagrams
```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Running: start
    Running --> Paused: pause
    Paused --> Running: resume
    Running --> Done: complete
    Running --> Error: fail
    Error --> Idle: reset
```

### 6. Gantt / Timeline
```mermaid
gantt
    title Project Timeline
    section Phase 1
        Design: 2024-01-01, 2w
        Prototype: 2024-01-15, 1w
    section Phase 2
        Development: 2024-01-22, 4w
        Testing: 2024-02-19, 2w
```

## Best Practices
1. Always use descriptive node labels (not just A, B, C)
2. Group related components in subgraphs
3. Use appropriate arrow styles (solid for sync, dashed for async)
4. Include data annotations on edges where relevant
5. Keep diagrams focused — split large systems into multiple diagrams
6. Save diagrams as `.md` files with embedded Mermaid blocks
7. For HTML output, use `<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>`

## Auto-Generation Pattern
When asked to document an existing codebase:
1. Read the main source files (entry points, routers, models)
2. Identify key components and their relationships
3. Map the request/response flow
4. Generate appropriate diagram type(s)
5. Save to `docs/architecture.md` or similar
