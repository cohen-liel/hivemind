# דוח ניתוח System Prompts: פרויקט web-claude-bot

**מאת:** Manus AI
**תאריך:** מרץ 2026

מסמך זה מציג ניתוח מעמיק של ארכיטקטורת הפרומפטים (System Prompts) בפרויקט `web-claude-bot`, לאור המחקרים והמדריכים הרשמיים שפרסמה חברת Anthropic בנושא "Prompt Engineering" ו-"Building Effective Agents" [1] [2].

---

## 1. מבט על: ארכיטקטורת הסוכנים הנוכחית

הפרויקט בנוי בצורה מרשימה מאוד כ-Multi-Agent System, ומיישם בפועל את אחת התבניות המומלצות ביותר על ידי Anthropic: **Orchestrator-Workers** [1]. 

המערכת מחולקת לשלוש שכבות מרכזיות (כפי שמתואר ב-`pm_agent.py`):
1. **שכבת הניהול (Brain):** `PM Agent`, `Orchestrator`, `Memory Agent`
2. **שכבת הביצוע (Hands):** `frontend_developer`, `backend_developer`, `database_expert`, `devops`
3. **שכבת האיכות (Quality):** `security_auditor`, `test_engineer`, `reviewer`, `researcher`

### נקודות חוזק קיימות (Aligned with Anthropic)
* **תקשורת מבוססת חוזים (Typed Contracts):** המעבר ל-DAG ול-`TaskInput`/`TaskOutput` מובנים תואם לחלוטין את ההמלצה של Anthropic להגדיר "חוזה ברור בין סוכנים למרחב הפעולה שלהם" [3].
* **ארטיפקטים מובנים (Structured Artifacts):** העברת מידע מובנה (כמו `api_contract`, `schema`) בין סוכנים במקום טקסט חופשי מונעת אובדן מידע קריטי [2].
* **ניהול זיכרון נפרד:** קיומו של `Memory Agent` ששומר "צילום מצב" (`MemorySnapshot`) עוזר להתמודד עם בעיית ה"Context Rot" (ריקבון הקשר) שעליה מדברים ב-Anthropic [2].

---

## 2. ניתוח פרטני והמלצות שיפור לפי Anthropic

למרות הארכיטקטורה המצוינת, ישנם מספר פערים משמעותיים ברמת ה**ניסוח של הפרומפטים עצמם**, שעלולים לפגוע בביצועים של Claude.

### א. שימוש בתגיות XML (ההמלצה החשובה ביותר)

**המצב הקיים:** 
הפרומפטים (במיוחד ב-`config.py`) משתמשים בעיצוב מבוסס כותרות טקסט, למשל:
```text
═══ YOUR ROLE ═══
You are a THINKER, INSPECTOR, and COORDINATOR.
...
═══ TASK SCALE AWARENESS ═══
```

**הנחיית Anthropic:** 
ההנחיה החד-משמעית של Anthropic [4] היא ש-Claude אומן וכויל במיוחד להבין ולעבד מבני **XML**. שימוש ב-XML מאפשר ל-Claude להפריד בבירור בין הנחיות, הקשר, דוגמאות וקלט.

**המלצה ליישום:**
יש להמיר את כל כותרות ה-`═══` לתגיות XML.
לדוגמה, ב-`ORCHESTRATOR_SYSTEM_PROMPT`:
```xml
<role>
You are the Orchestrator — the strategic brain of a multi-agent software engineering team.
You are a THINKER, INSPECTOR, and COORDINATOR.
</role>

<tools_usage>
You have READ-ONLY tools: Read, Glob, Grep, LS, and limited Bash...
</tools_usage>

<task_scale_awareness>
Before your first delegation, classify the task...
</task_scale_awareness>
```

### ב. בעיית ה-Context Rot (אורך הפרומפטים)

**המצב הקיים:**
בדיקה של קובץ `config.py` חושפת שה-`ORCHESTRATOR_SYSTEM_PROMPT` מכיל מעל 13,400 תווים (~3,800 טוקנים). הוא עמוס בכללי "Anti-Quitting", תהליכי חשיבה, ורשימות של חוקים קריטיים. 

**הנחיית Anthropic:** 
במאמר "Effective Context Engineering" [2], Anthropic מזהירים מפני העמסת יתר של ה-System Prompt. הם קוראים לזה "Goldilocks zone" (לא ספציפי מדי, לא כללי מדי). פרומפט ארוך מדי גורם למודל "לשכוח" או להתעלם מהנחיות שנמצאות באמצע (Context Rot).

**המלצה ליישום:**
1. **צמצום והפרדה:** ה-Orchestrator מקבל כרגע גם חוקים של משימות Epic, גם כללי שגיאות, וגם רשימות תיוג (Checklists). כדאי להעביר חלק מהמידע הדינמי ל-Context שמוזרק בזמן אמת (למשל, להזריק את כללי ה-Epic רק אם המשימה סווגה כ-Epic).
2. **ניסוח חיובי:** במקום רשימה ארוכה של `✗ NEVER...`, עדיף לנסח כללים חיוביים (מה *כן* לעשות). Anthropic ממליצים להגיד ל-Claude מה לעשות במקום מה לא לעשות [4].

### ג. חוסר בדוגמאות (Few-Shot Prompting)

**המצב הקיים:**
הפרומפטים של המומחים (Specialists) מפרטים מה הסטנדרטים, אבל כמעט ואין דוגמאות קונקרטיות של הפלט הרצוי, למעט ה-JSON הסופי.
לדוגמה, ב-`frontend_developer`:
```text
STANDARDS:
- Every prop has a type, every function has a return type
- Prefer `interface` for objects...
```

**הנחיית Anthropic:** 
"שימוש ב-3-5 דוגמאות מנוסחות היטב (Few-shot) הוא הדרך היעילה ביותר לשפר ביצועים" [4]. דוגמאות חזקות יותר מכל תיאור מילולי של "איך הקוד צריך להיראות".

**המלצה ליישום:**
הוספת תגית `<examples>` לכל סוכן מומחה.
לדוגמה, ב-`frontend_developer`:
```xml
<examples>
  <example>
    <description>Proper React Component with TypeScript interfaces</description>
    <code>
      // דוגמת קוד קצרה שמדגימה את הסטנדרטים
    </code>
  </example>
</examples>
```

### ד. שיפור ה-Typed Contract Protocol

**המצב הקיים:**
ה-`_TYPED_CONTRACT_FOOTER` מכריח את הסוכנים להחזיר JSON בפורמט מסוים בתוך בלוק ` ```json `.

**הנחיית Anthropic:**
Claude מצטיין בייצור JSON כשהוא מתבקש לעשות זאת בתוך תגיות ספציפיות, וכאשר נותנים לו מרחב לחשוב (Chain of Thought) *לפני* יצירת ה-JSON [3].

**המלצה ליישום:**
במקום רק לבקש את ה-JSON בסוף, כדאי להנחות את הסוכן לחשוב בתוך תגית `<thinking>` לפני שהוא מייצר את הפלט הסופי.
למשל ב-`contracts.py` (בפונקציה `task_input_to_prompt`):
```text
Before producing your final JSON output, you MUST write your thought process inside <thinking> tags. 
Analyze the input, plan your file changes, and decide on the structured artifacts you will output.
After closing the </thinking> tag, output ONLY the JSON block.
```

---

## 3. טבלת סיכום פעולות מומלצות

| רכיב | בעיה נוכחית | פתרון מומלץ (Anthropic) | עדיפות |
|------|-------------|-------------------------|---------|
| **עיצוב פרומפטים** | שימוש בכותרות טקסט (`═══`) | המרה מלאה לתגיות `<xml>` בכל ה-`config.py` ו-`pm_agent.py` | גבוהה מאוד |
| **אורך Orchestrator** | ארוך מדי (~3,800 טוקנים), סכנת Context Rot | קיצור, מעבר לניסוח חיובי, הזרקת מידע ספציפי רק כשצריך | גבוהה |
| **מומחים (Specialists)** | חסרות דוגמאות קוד (Few-Shot) | הוספת תגית `<examples>` לכל מומחה עם דוגמה אחת לפחות | בינונית |
| **מנגנון JSON** | סוכנים קופצים ישר לפתרון | הוספת דרישה ל-`<thinking>` לפני יצירת ה-TaskOutput JSON | גבוהה |

---

## סיכום

התשתית של `web-claude-bot` היא מהמתקדמות שראיתי, והיא כבר מיישמת הלכה למעשה את הקונספטים המורכבים ביותר של סוכנים אוטונומיים (DAG, Artifacts, Memory). 

הפער היחיד הוא שפת התקשורת עם המודל. מעבר ל"שפת האם" של Claude (תגיות XML, דוגמאות, והפרדת חשיבה מפעולה) יהפוך את הסוכנים למדויקים יותר, יפחית שגיאות (כמו "hallucinations" של קוד לא תקין), ויחסוך סבבי תיקונים מיותרים.

---
### References
[1] Anthropic, "Building Effective Agents", Dec 2024. https://www.anthropic.com/engineering/building-effective-agents
[2] Anthropic, "Effective Context Engineering for AI Agents", Sep 2025. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
[3] Anthropic, "Writing Effective Tools for AI Agents", Sep 2025. https://www.anthropic.com/engineering/writing-tools-for-agents
[4] Anthropic, "Claude Prompting Best Practices", Claude API Documentation. https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
