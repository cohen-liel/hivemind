# Deep Web Research & Intelligence Skill

Conduct comprehensive, multi-source web research with real-time data. This is a POWER skill — it doesn't just search, it investigates, cross-references, validates, and synthesizes actionable intelligence.

## Triggers
- "research X", "look up X", "find out about X", "what is X"
- "compare X vs Y", "which is better", "pros and cons"
- "what's the latest on X", "news about X", "trends in X"
- Technology evaluations and stack decisions
- Documentation lookups for libraries/frameworks/APIs
- Competitive analysis, market sizing, or industry scans
- "find me data on X", "statistics about X"
- Any question requiring external knowledge beyond training data

## Research Tiers

### Tier 1: Quick Lookup (1-2 searches)
For simple factual questions:
- "What version is React on?"
- "What's the pricing for Vercel?"
- "Is library X still maintained?"

### Tier 2: Standard Research (3-5 searches)
For comparison and evaluation:
- "Compare Prisma vs Drizzle for our use case"
- "Best practices for WebSocket authentication"
- "How does Stripe handle refunds?"

### Tier 3: Deep Investigation (6-12 searches)
For strategic decisions and comprehensive analysis:
- "Should we migrate from REST to GraphQL?"
- "Full competitive analysis of X market"
- "Technology landscape for real-time collaboration"

### Tier 4: Intelligence Report (12+ searches)
For board-level decisions and thorough due diligence:
- "Complete market analysis for entering X space"
- "Technical due diligence on acquiring X company"
- "Build vs buy analysis for X capability"

## Research Methodology

### Phase 1: Scope & Strategy
1. Classify the research tier (1-4)
2. Define the core question and sub-questions
3. Identify target source types (docs, GitHub, blogs, research papers, forums)
4. Plan search queries — start broad, then narrow

### Phase 2: Primary Research
1. **WebSearch** with 3-5 varied query formulations per sub-question
2. **WebFetch** the top 3-5 most relevant URLs per query
3. Extract data points, quotes, statistics, dates
4. Track source URLs and publication dates

### Phase 3: Validation & Cross-Reference
1. Verify key claims across 2+ independent sources
2. Check publication dates — flag anything >12 months old
3. Distinguish primary sources (official docs, SEC filings, company blogs) from secondary (news, analysis)
4. Note conflicting information and assess which source is more reliable

### Phase 4: Synthesis
1. Identify patterns across sources
2. Extract actionable insights (not just summaries)
3. Build comparison frameworks when applicable
4. Formulate clear recommendations with reasoning

### Phase 5: Deliverable
1. Structure per the output format below
2. Include ALL source URLs
3. Add confidence levels to key claims
4. Highlight gaps in available information

## Search Query Techniques

### Effective query patterns:
```
# Exact phrase matching
"error handling" best practices 2025

# Site-specific
site:github.com [library] stars issues

# Comparison queries
[X] vs [Y] benchmark performance 2025

# Problem-specific
[technology] "production issues" OR "lessons learned"

# Documentation
[library] official documentation API reference

# Community sentiment
[technology] reddit "switched from" OR "migrated to"

# Statistics and data
[market] market size TAM 2025 report

# News and recent
[topic] announcement OR release OR launch 2025
```

### Source Priority (highest to lowest):
1. Official documentation and specs
2. GitHub repos (stars, issues, last commit)
3. Peer-reviewed papers and research reports
4. Reputable tech blogs (Martin Fowler, The Pragmatic Engineer, etc.)
5. Conference talks and official blog posts
6. Stack Overflow accepted answers (check date!)
7. Reddit/HN discussions (use for sentiment, not facts)
8. News articles (verify with primary sources)

## Output Formats

### Standard Research Report
```markdown
# Research: [Topic]
**Date**: [today] | **Depth**: Tier [1-4] | **Sources**: [count]

## Executive Summary
[3-5 sentences capturing the key findings and recommendation]

## Key Findings
### Finding 1: [Title]
[Detail with evidence]
- **Source**: [url] (published [date])
- **Confidence**: High/Medium/Low

### Finding 2: [Title]
[Detail with evidence]
- **Source**: [url]
- **Confidence**: High/Medium/Low

## Data & Statistics
| Metric | Value | Source | Date |
|--------|-------|--------|------|
| [metric] | [value] | [source] | [date] |

## Comparison Matrix (if applicable)
| Criteria | Option A | Option B | Option C |
|----------|----------|----------|----------|
| [criteria] | [assessment] | [assessment] | [assessment] |

## Risk Analysis
- **Risk 1**: [description] — **Mitigation**: [approach]
- **Risk 2**: [description] — **Mitigation**: [approach]

## Recommendation
[Clear, actionable recommendation with reasoning chain]

## Information Gaps
- [What we couldn't find or verify]

## Sources
1. [Title](url) — [type: docs/blog/research/news] — [date]
2. [Title](url) — [type] — [date]
```

### Quick Answer Format (Tier 1)
```markdown
**Answer**: [direct answer]
**Source**: [url] (verified [date])
**Note**: [any caveats]
```

### Technology Comparison Format
```markdown
# [X] vs [Y]: Technical Comparison

## TL;DR
[Winner and why in 2 sentences]

## Head-to-Head
| | [X] | [Y] |
|---|---|---|
| GitHub Stars | [n] | [n] |
| Last Release | [date] | [date] |
| npm Weekly Downloads | [n] | [n] |
| Bundle Size | [n KB] | [n KB] |
| TypeScript Support | [native/types/none] | [native/types/none] |
| Learning Curve | [easy/medium/hard] | [easy/medium/hard] |
| Community | [small/medium/large] | [small/medium/large] |
| Enterprise Adoption | [low/medium/high] | [low/medium/high] |

## Detailed Analysis
### Performance
[benchmarks and real-world reports]

### Developer Experience
[API design, docs quality, tooling]

### Ecosystem
[plugins, integrations, community resources]

### Production Readiness
[stability, known issues, migration stories]

## Recommendation
[For your use case, choose [X/Y] because...]
```

## Specialized Research Patterns

### API/Library Documentation Lookup
1. Find official docs URL
2. Fetch getting-started guide
3. Extract key API methods with signatures
4. Find code examples (official + community)
5. Check changelog for breaking changes
6. Look for known issues and workarounds

### Competitive Analysis
1. Identify all players in the space
2. For each: pricing, features, target market, funding, traction
3. Map the competitive landscape (positioning matrix)
4. Identify gaps and opportunities
5. SWOT analysis of top 3 competitors

### Market Sizing (TAM/SAM/SOM)
1. Find industry reports (Gartner, Statista, CB Insights)
2. Top-down: total market × relevant segment %
3. Bottom-up: potential customers × ARPU
4. Cross-check both approaches
5. Always show assumptions and math

## Quality Standards

### Every deliverable MUST:
- [ ] Have at least 3 independent sources for key claims
- [ ] Include publication dates on all sources
- [ ] Flag stale data (>12 months) explicitly
- [ ] Separate facts from opinions from speculation
- [ ] Include contrarian viewpoints when they exist
- [ ] End with a clear, actionable recommendation
- [ ] List information gaps honestly

### Red Flags to Catch:
- Single-source claims presented as fact
- Statistics without methodology or date
- "Best" or "worst" claims without criteria
- Vendor-published benchmarks (always biased)
- Outdated Stack Overflow answers accepted as current
