# Web Research & Search Skill

Conduct deep web research, technology comparisons, documentation lookups, and competitive analysis using available tools. Use when the user asks to research a topic, compare technologies, find documentation, analyze competitors, or gather information from the web.

## Triggers
- "research X", "look up X", "find out about X"
- "compare X vs Y", "which is better X or Y"
- Technology evaluations and stack decisions
- "what's the latest on X", "how does X work"
- Documentation lookups for libraries/frameworks
- Competitive analysis or market intelligence

## Workflow

### 1. Search Strategy
- Use `WebSearch` tool to find relevant sources
- Use `WebFetch` to extract detailed content from key URLs
- Cross-reference multiple sources for accuracy
- Prioritize official documentation, GitHub repos, and reputable tech blogs

### 2. Research Output Format
Always structure findings as:

```markdown
# Research: [Topic]

## TL;DR
[2-3 sentence executive summary]

## Key Findings
- [Finding 1 with source]
- [Finding 2 with source]
- [Finding 3 with source]

## Comparison Table (if applicable)
| Criteria | Option A | Option B |
|----------|----------|----------|
| Performance | ... | ... |
| Cost | ... | ... |
| Ecosystem | ... | ... |

## Recommendation
[Clear recommendation with reasoning]

## Sources
- [Source 1](url)
- [Source 2](url)
```

### 3. Technology Comparison Pattern
When comparing technologies:
1. Search for benchmarks and real-world usage reports
2. Check GitHub stars, recent activity, and community size
3. Look at npm/pip download stats
4. Find migration guides and gotchas
5. Check pricing (if SaaS)

### 4. Documentation Lookup Pattern
When looking up library/API docs:
1. Find the official docs URL
2. Fetch the getting-started guide
3. Extract key API methods/endpoints
4. Find code examples
5. Check for known issues or breaking changes

### 5. Boilerplate Generation
When asked to learn a new library and generate boilerplate:
1. Fetch the official quickstart/tutorial
2. Identify core concepts and patterns
3. Generate production-ready starter code
4. Include error handling and TypeScript types
5. Add inline comments explaining the API
