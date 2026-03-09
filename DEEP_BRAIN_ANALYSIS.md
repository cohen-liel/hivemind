# ניתוח מעמיק של "המוח" — web-claude-bot

## מבוסס על 4 מדריכים רשמיים של Anthropic + בדיקת קוד מלאה

---

## חלק א: מה כבר עשינו (Commit 2ded8ee)

### 1. המרת כל הפרומפטים ל-XML Tags ✅
- Orchestrator, Solo Agent, כל ה-Sub-Agents, כל ה-Specialists
- תגיות: `<role>`, `<instructions>`, `<standards>`, `<constraints>`, `<output_format>`

### 2. הוספת `<self_review>` / `<thinking>` ✅
- ב-`_TYPED_CONTRACT_FOOTER` — כל specialist חייב לחשוב לפני JSON output

### 3. צמצום Skills מ-5 ל-2 ✅
- `skills_registry.py` — `max_skills` ברירת מחדל 2
- `dag_executor.py` — לא שולח `max_skills=5` יותר

### 4. תיקון באג DAG Events ✅
- `orchestrator.py` — `agent_started`/`agent_finished` במקום `agent_update`

### 5. UI — Orchestrator Only ✅
- `Controls.tsx` — הסרת dropdown
- `ProjectView.tsx` — הכל עובר דרך Orchestrator

---

## חלק ב: מה עוד צריך לשפר — ניתוח מעמיק

### בעיה 1: ה-Orchestrator Prompt עדיין ארוך מדי (קריטי)

**מצב נוכחי:** ~3,800 טוקנים (252 שורות config.py)

**למה זה בעיה (לפי Anthropic "Context Rot"):**
> "As tokens increase, model's recall ability decreases. Context = finite resource with diminishing marginal returns."

ה-Orchestrator שלנו מקבל:
- System prompt: ~3,800 tokens
- Review prompt (דינמי מ-orchestrator.py): ~1,000-3,000 tokens
- Conversation history: גדל עם כל round
- **סה"כ אחרי 10 rounds: ~30,000-50,000 tokens**

**מה קורה:** ב-round 15+ ה-Orchestrator "שוכח" כללים מתחילת ה-prompt. הוא מפסיק לקרוא את ה-manifest, מפסיק לעקוב אחרי ה-phases, ומתחיל לחזור על delegations.

**פתרון מומלץ:**
```
הפרדה ל-3 שכבות:
1. CORE IDENTITY (תמיד): ~800 tokens — role, agents, delegation format
2. TASK-SPECIFIC (מוזרק דינמית): ~500 tokens — classification + current phase
3. REFERENCE (on-demand): ~2,500 tokens — epic phases, failure handling, completion criteria
   → מוזרק רק כשרלוונטי (למשל epic_initialization רק כשאין manifest)
```

### בעיה 2: PM Agent חסר Prompt ב-config.py (בינוני-גבוה)

**מצב נוכחי:** ב-`pm_agent.py` שורה 42 יש prompt inline:
```python
PM_SYSTEM_PROMPT = """You are the Project Manager (PM) Agent..."""
```

**למה זה בעיה:**
1. לא עבר להמרת XML כמו שאר הפרומפטים
2. ה-PM הוא קריטי — הוא מייצר את ה-TaskGraph שמנהל את כל העבודה
3. אם ה-PM מייצר task graph גרוע, כל ה-agents מקבלים הוראות גרועות

**פתרון:** להעביר את ה-PM prompt ל-config.py, להמיר ל-XML, ולהוסיף דוגמאות (few-shot) של TaskGraph טוב.

### בעיה 3: Memory Agent Prompt לא ב-XML (בינוני)

**מצב נוכחי:** ב-`memory_agent.py` שורה 42:
```python
MEMORY_SYSTEM_PROMPT = """\
You are the Memory Agent — the project's long-term memory...
```

**למה זה בעיה:** כמו ה-PM — prompt inline שלא עבר להמרת XML.

### בעיה 4: חסרות דוגמאות (Few-Shot) — הבעיה הכי גדולה אחרי XML

**מה Anthropic אומרים:**
> "3-5 well-crafted examples (few-shot prompting). Relevant, Diverse, Structured (wrap in <example> tags)"

**מצב נוכחי:** אף prompt לא מכיל דוגמאות של פלט רצוי, חוץ מה-delegation format של ה-Orchestrator.

**איפה זה הכי כואב:**
1. **_TYPED_CONTRACT_FOOTER** — הסוכנים צריכים לייצר JSON מורכב עם structured_artifacts. בלי דוגמה הם מנחשים את המבנה.
2. **PM Agent** — צריך לייצר TaskGraph JSON. בלי דוגמה הוא מייצר goals לא ברורים ו-acceptance_criteria גנריים.
3. **Orchestrator delegation** — יש דוגמה אחת, צריך 2-3 דוגמאות מגוונות (simple, medium, epic).

### בעיה 5: Context Passing בין סוכנים — חלקי (בינוני)

**מצב נוכחי ב-contracts.py `task_input_to_prompt`:**
```python
# מעביר artifacts כ-JSON string
for art in output.structured_artifacts:
    parts.append(f"  Artifact ({art.type.value}): {art.title}")
    parts.append(f"  Summary: {art.summary}")
    if art.data:
        data_str = json.dumps(art.data, indent=2)[:1500]
        parts.append(f"  Data: {data_str}")
```

**למה זה בעיה:**
1. `[:1500]` חותך data באמצע — JSON שבור שהסוכן לא יכול לפרסר
2. אין XML wrapping — הסוכן לא יודע איפה מתחיל ונגמר ה-context מסוכן קודם
3. אין סיכום מובנה — הסוכן מקבל "wall of text" במקום context ממוקד

**פתרון:**
```python
# Wrap in XML + truncate at JSON boundary
parts.append(f"<upstream_artifact type='{art.type.value}' from='{tid}'>")
parts.append(f"  <title>{art.title}</title>")
parts.append(f"  <summary>{art.summary}</summary>")
if art.data:
    data_str = json.dumps(art.data, indent=2)
    if len(data_str) > 1500:
        data_str = _truncate_json_safely(data_str, 1500)
    parts.append(f"  <data>{data_str}</data>")
parts.append("</upstream_artifact>")
```

### בעיה 6: ה-Review Prompt ב-orchestrator.py לא ב-XML (בינוני)

**מצב נוכחי:** ב-`orchestrator.py` ה-review prompt שנבנה דינמית (`_build_review_prompt`) משתמש ב-Markdown headers (`## Agent Results`, `### task_001`) במקום XML.

**למה זה בעיה:** ה-Orchestrator system prompt עכשיו ב-XML, אבל ה-review prompt שמוזרק כ-user message הוא ב-Markdown. חוסר עקביות = בלבול.

### בעיה 7: Skills Injection — Timing לא אופטימלי (בינוני)

**מצב נוכחי:** Skills נבחרים ב-`dag_executor.py` לפי `role` + `goal` (keyword matching).

**למה זה בעיה:**
1. בחירה סטטית — לא מתחשבת בשלב הנוכחי של הפרויקט
2. אותם skills מוזרקים שוב ושוב גם אם הסוכן כבר השתמש בהם

**פתרון מתקדם:**
```python
# Dynamic skill selection based on:
# 1. Task role + goal (current)
# 2. Project phase (from memory_snapshot)
# 3. Previous task outputs (what already worked/failed)
# 4. Never inject same skill twice to same agent in same session
```

### בעיה 8: Orchestrator לא מקבל Feedback מובנה על כישלונות (בינוני)

**מצב נוכחי:** כשסוכן נכשל, ה-DAG executor מטפל ב-retry/remediation אוטומטית. אבל ה-Orchestrator לא מקבל feedback מובנה על מה נכשל ולמה.

**למה זה בעיה:** ה-Orchestrator לא יכול ללמוד מכישלונות ולשנות אסטרטגיה.

---

## חלק ג: סדר עדיפויות ליישום

| עדיפות | בעיה | מאמץ | השפעה |
|---------|-------|------|--------|
| 🔴 1 | Few-Shot דוגמאות ל-Typed Contract + PM | בינוני | גבוהה מאוד |
| 🔴 2 | PM Agent prompt → XML + דוגמאות | בינוני | גבוהה מאוד |
| 🔴 3 | Context passing ב-contracts.py — XML wrapping + safe truncation | קטן | גבוהה |
| 🟡 4 | Memory Agent prompt → XML | קטן | בינונית |
| 🟡 5 | Review prompt ב-orchestrator.py → XML | בינוני | בינונית |
| 🟡 6 | Orchestrator prompt layering (core/task/reference) | גדול | גבוהה (לטווח ארוך) |
| 🟢 7 | Dynamic skills injection | בינוני | בינונית |
| 🟢 8 | Failure feedback loop ל-Orchestrator | בינוני | בינונית |

---

## חלק ד: WebSocket — האם מספיק?

**תשובה קצרה: כן, WebSocket מספיק למערכת הנוכחית.**

**למה:**
- WebSocket נותן bidirectional real-time — בדיוק מה שצריך לסטטוס סוכנים
- SSE (Server-Sent Events) הוא חד-כיווני — לא מתאים כי צריך גם לשלוח הודעות
- A2A/ACP/MCP הם פרוטוקולים בין סוכנים, לא בין UI לשרת

**מה כן צריך לשפר ב-WebSocket:**
1. **Heartbeat** — לוודא שהחיבור חי (כל 30 שניות)
2. **Reconnect** — auto-reconnect עם exponential backoff
3. **Event queue** — אם ה-client מתנתק, לשמור events ולשלוח כשחוזר
4. **Typed events** — schema ברור לכל סוג event (agent_started, agent_finished, etc.)

---

## חלק ה: חזון — איך להפוך כל סוכן ל"הכי טוב בעולם בתחום שלו"

### עיקרון 1: Specialist = Domain Expert + Tool Expert
כל סוכן צריך:
- **Domain knowledge** ב-prompt (כבר יש — ה-XML standards)
- **Dedicated tools** — כלים ייעודיים לתחום שלו:
  - Test Engineer → `run_pytest`, `check_coverage`
  - Security Auditor → `run_bandit`, `run_semgrep`, `check_dependencies`
  - Reviewer → `get_diff`, `get_blame`, `get_complexity`
  - DevOps → `docker_build`, `check_ports`, `validate_compose`

### עיקרון 2: Self-Reflection (Evaluator-Optimizer)
לפני שסוכן מחזיר תוצאה:
1. הוא מייצר draft
2. "Critic" פנימי (אותו סוכן, prompt שונה) בודק את ה-draft
3. הסוכן מתקן לפי ה-feedback
4. רק אז מחזיר את התוצאה הסופית

### עיקרון 3: Memory-Driven Planning
ה-PM Agent צריך לקבל:
- `memory_snapshot.json` — מה כבר נבנה
- `decision_log.md` — מה כבר הוחלט
- `SECURITY_AUDIT.md` — מה כבר נבדק
- **Lessons Learned** — מה נכשל בעבר ואיך תוקן

### עיקרון 4: Progressive Disclosure
במקום לתת לסוכן את כל ה-context מראש:
1. Round 1: רק goal + acceptance criteria
2. Round 2: + upstream artifacts
3. Round 3: + relevant skills (אם נתקע)
4. Round 4: + memory snapshot (אם עדיין נתקע)

זה מונע Context Rot ומאפשר לסוכן להתמקד.
